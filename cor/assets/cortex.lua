---@diagnostic disable: lowercase-global

-- Cortex integration for Neovim.
--
-- Division of labour:
--   * READS stay in Lua (fd/rg/snacks.picker) - instant, no process startup.
--   * WRITES go through the `cor` CLI, asynchronously. Frontmatter edits touch
--     archive-relative link prefixes, parent checkboxes and status cascades;
--     only MaintenanceRunner gets that right. Re-implementing it here would be
--     a second source of truth that drifts.

local M = {}

local function get_vault()
  if vim.env.COR_VAULT then return vim.env.COR_VAULT end
  local cfg = vim.fn.expand("~/.config/cor/config.yaml")
  if vim.fn.filereadable(cfg) == 1 then
    for _, line in ipairs(vim.fn.readfile(cfg)) do
      local vault = line:match("^vault:%s*(.+)")
      if vault then return vim.fn.expand(vim.trim(vault)) end
    end
  end
  return vim.fn.expand("~/vault")
end

--- True when the current buffer lives inside the vault.
local function in_vault()
  local file = vim.fn.expand("%:p")
  if file == "" then return false end
  local vault = vim.fn.resolve(get_vault())
  return vim.startswith(vim.fn.resolve(file), vault)
end

--- Stem of the current buffer, which is what every `cor` command takes.
local function current_stem()
  return vim.fn.fnamemodify(vim.fn.expand("%"), ":t:r")
end

--- Run a `cor` command asynchronously and reload anything it rewrote.
local function run(args, on_success)
  vim.system(
    vim.list_extend({ "cor" }, args),
    { cwd = get_vault(), text = true },
    vim.schedule_wrap(function(r)
      if r.code ~= 0 then
        local msg = vim.trim((r.stderr or "") .. "\n" .. (r.stdout or ""))
        vim.notify(msg ~= "" and msg or "cor failed", vim.log.levels.ERROR)
        return
      end
      -- `cor` rewrote frontmatter on disk; pull it into open buffers.
      vim.cmd("checktime")
      local out = vim.trim(r.stdout or "")
      if out ~= "" then vim.notify(out) end
      if on_success then on_success(r) end
    end)
  )
end

-- --- Vault scanning (reads) -------------------------------------------------

--- List vault markdown files, newest first.
local function list_files(include_archive)
  local vault = get_vault()
  local files
  -- templates/ is always excluded: template files carry `type: project` and
  -- would otherwise show up as a real project in every picker.
  if vim.fn.executable("fd") == 1 then
    local excl = " --exclude templates"
    if not include_archive then excl = excl .. " --exclude archive" end
    files = vim.fn.systemlist(
      "cd " .. vim.fn.shellescape(vault) .. " && fd --extension md" .. excl)
  else
    files = vim.fn.globpath(vault, "**/*.md", false, true)
    files = vim.tbl_map(function(f)
      return vim.fn.fnamemodify(f, ":." )
    end, files)
    files = vim.tbl_filter(function(f) return not f:match("^templates/") end, files)
    if not include_archive then
      files = vim.tbl_filter(function(f) return not f:match("^archive/") end, files)
    end
  end
  table.sort(files, function(a, b)
    return vim.fn.getftime(vault .. "/" .. a) > vim.fn.getftime(vault .. "/" .. b)
  end)
  return files
end

--- Read the frontmatter block and H1 of a note.
---
--- Only the head of each file is read: enough for type/status/tags/title,
--- cheap enough to run across a whole vault inside a picker.
local function read_meta(path)
  local lines = vim.fn.readfile(path, "", 40)
  local meta = { status = nil, type = nil, tags = {}, title = nil }
  local in_fm, fm_done = false, false
  -- `cor` writes block-style lists (tags:\n- a\n- b); hand-edited files often
  -- use the inline form. Both have to work.
  local in_tag_block = false

  for i, line in ipairs(lines) do
    if i == 1 and line == "---" then
      in_fm = true
    elseif in_fm and line == "---" then
      in_fm, fm_done = false, true
    elseif in_fm then
      local item = line:match("^%s*-%s+(.+)$")
      if in_tag_block and item then
        table.insert(meta.tags, vim.trim(item))
      else
        in_tag_block = false
        local key, value = line:match("^(%w[%w_]*):%s*(.*)$")
        if key == "status" then
          meta.status = vim.trim(value)
        elseif key == "type" then
          meta.type = vim.trim(value)
        elseif key == "tags" then
          local inner = value:match("^%[(.*)%]$")
          if inner then
            for tag in inner:gmatch("[^,%s]+") do
              table.insert(meta.tags, tag)
            end
          elseif vim.trim(value) == "" then
            in_tag_block = true
          else
            table.insert(meta.tags, vim.trim(value))  -- scalar: tags: foo
          end
        end
      end
    elseif fm_done and not meta.title then
      local title = line:match("^#%s+(.+)$")
      if title then meta.title = vim.trim(title) end
    end
  end

  return meta
end

--- Build picker items with frontmatter attached.
---
--- `opts.include_archive` includes archive/, `opts.type` filters by note type.
local function collect_notes(opts)
  opts = opts or {}
  local vault = get_vault()
  local items = {}

  for _, rel in ipairs(list_files(opts.include_archive)) do
    local abs = vault .. "/" .. rel
    local ok, meta = pcall(read_meta, abs)
    if ok then
      if not opts.type or meta.type == opts.type then
        local stem = vim.fn.fnamemodify(rel, ":t:r")
        table.insert(items, {
          rel = rel,
          stem = stem,
          title = meta.title or stem,
          status = meta.status,
          type = meta.type,
          tags = meta.tags,
          archived = rel:match("^archive/") ~= nil,
        })
      end
    end
  end

  return items
end

--- One-line label: status, title, tags and stem, all searchable.
local function format_item(item)
  local parts = {}
  table.insert(parts, string.format("%-10s", "[" .. (item.status or " ") .. "]"))
  table.insert(parts, item.title)
  if #item.tags > 0 then
    table.insert(parts, "#" .. table.concat(item.tags, " #"))
  end
  table.insert(parts, "(" .. item.stem .. ")")
  if item.archived then table.insert(parts, "[archived]") end
  return table.concat(parts, " ")
end

--- Pick a note, showing frontmatter rather than bare filenames.
---
--- Uses vim.ui.select so it works with snacks.picker, fzf-lua or telescope
--- without adding a dependency.
local function pick_note(opts, prompt, callback)
  local items = collect_notes(opts)
  if #items == 0 then
    vim.notify("No matching notes in vault", vim.log.levels.WARN)
    return
  end
  vim.ui.select(items, {
    prompt = prompt,
    format_item = format_item,
  }, function(choice)
    if choice then callback(choice) end
  end)
end

-- --- Features ---------------------------------------------------------------

--- Insert a markdown link to a picked note.
local function insert_cor_link(include_archive)
  return function()
    pick_note(
      { include_archive = include_archive },
      include_archive and "Insert Link (with archive)" or "Insert Note Link",
      function(item)
        vim.api.nvim_put({ "[" .. item.title .. "](" .. item.rel .. ")" }, "c", true, true)
      end
    )
  end
end

local function show_backlinks()
  local vault = get_vault()
  local stem = current_stem()
  local search = vim.fn.shellescape("](" .. stem .. ".md)")
  local current_file = vim.fn.expand("%:t")

  local results = vim.fn.systemlist(
    "cd " .. vim.fn.shellescape(vault) .. " && rg --with-filename --line-number " .. search
    .. " --glob '!" .. current_file .. "'"
  )

  if #results == 0 then
    vim.notify("No backlinks to " .. stem, vim.log.levels.INFO)
    return
  end

  vim.ui.select(results, {
    prompt = "Backlinks to " .. stem,
  }, function(choice)
    if not choice then return end
    local file, line = choice:match("^([^:]+):(%d+):")
    if file then
      vim.cmd("edit " .. vim.fn.fnameescape(vault .. "/" .. file))
      vim.api.nvim_win_set_cursor(0, { tonumber(line), 0 })
    end
  end)
end

-- Status sets mirror cor/assets/schema.yaml.
local TASK_STATUS = { "todo", "active", "blocked", "waiting", "done", "dropped" }
local PROJECT_STATUS = { "planning", "active", "paused", "done" }

local function set_status()
  local meta = read_meta(vim.fn.expand("%:p"))
  local choices = meta.type == "project" and PROJECT_STATUS or TASK_STATUS
  vim.ui.select(choices, { prompt = "Set status" }, function(status)
    if status then run({ "mark", current_stem(), status }) end
  end)
end

--- Tags already used in the vault, for completion.
local function vault_tags()
  local seen, tags = {}, {}
  for _, item in ipairs(collect_notes({ include_archive = true })) do
    for _, tag in ipairs(item.tags) do
      if not seen[tag] then
        seen[tag] = true
        table.insert(tags, tag)
      end
    end
  end
  table.sort(tags)
  return tags
end

local function add_tag()
  local tags = vault_tags()
  table.insert(tags, 1, "<new tag>")
  vim.ui.select(tags, { prompt = "Add tag" }, function(choice)
    if not choice then return end
    if choice == "<new tag>" then
      vim.ui.input({ prompt = "New tag: " }, function(tag)
        if tag and tag ~= "" then run({ "tag", current_stem(), tag }) end
      end)
    else
      run({ "tag", current_stem(), choice })
    end
  end)
end

local function set_due()
  vim.ui.input({ prompt = "Due (natural language): " }, function(text)
    if text and text ~= "" then
      run(vim.list_extend({ "due", current_stem() }, vim.split(text, "%s+")))
    end
  end)
end

--- Link the current project to a predecessor it continues.
local function add_continues()
  pick_note(
    { include_archive = true, type = "project" },
    "Continues which project?",
    function(item)
      run({ "rel", "add", current_stem(), item.stem, "--as", "continues" })
    end
  )
end

--- Link the current note to a related note (symmetric).
local function add_related()
  pick_note(
    { include_archive = true },
    "Related to?",
    function(item)
      run({ "rel", "add", current_stem(), item.stem, "--as", "related" })
    end
  )
end

local function show_relations()
  run({ "rel", "show", current_stem() })
end

-- --- Keymaps ----------------------------------------------------------------

vim.api.nvim_create_autocmd("FileType", {
  pattern = "markdown",
  callback = function()
    -- Only bind inside the vault; plain markdown elsewhere is untouched.
    if not in_vault() then return end

    local function map(mode, lhs, rhs, desc)
      vim.keymap.set(mode, lhs, rhs, { buffer = true, desc = "Cor: " .. desc })
    end

    map("i", "<C-l>", insert_cor_link(false), "insert note link")
    map("i", "<C-S-l>", insert_cor_link(true), "insert note link (with archive)")
    map("n", "<leader>bl", show_backlinks, "show backlinks")

    -- <leader>cs is LazyVim's "Symbols (Trouble)", which is genuinely useful
    -- on a long note; <leader>cm matches the `cor mark` command anyway.
    map("n", "<leader>cm", set_status, "set status (mark)")
    map("n", "<leader>ct", add_tag, "add tag")
    map("n", "<leader>cd", set_due, "set due date")
    map("n", "<leader>cc", add_continues, "continues project")
    map("n", "<leader>cr", add_related, "add related note")
    map("n", "<leader>cR", show_relations, "show relations")
  end,
})

M.run = run
M.pick_note = pick_note
M.collect_notes = collect_notes

return {}

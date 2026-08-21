---
modified: 2026-07-31 23:54
---

# Nvim Integration

Cortex uses `[Title](stem.md)` for internal link targets, which enables
native editor navigation without any plugins. Lazy.nvim plugin is recommended.

  git clone https://github.com/LazyVim/starter ~/.config/nvim

The plugin uses `vim.ui.select`, so it works with any LazyVim picker
(snacks.picker, fzf-lua, or telescope) without installing anything extra.

**Requirements:** `fd` (file listing) and `rg` (backlinks search).

## Design: reads in Lua, writes through `cor`

The plugin splits along one line:

- **Reads** (listing notes, backlinks) are pure Lua over `fd`/`rg`. No process
  startup, instant.
- **Writes** (status, tags, due dates, relations) shell out to the `cor` CLI
  asynchronously via `vim.system()`, then `:checktime` reloads the buffer.

Writes go through the CLI because changing frontmatter also touches
archive-relative link prefixes, parent checkboxes and status cascades — logic
that only `MaintenanceRunner` gets right. Reimplementing it in Lua would create
a second source of truth that drifts from the pre-commit hook.

Nothing blocks the editor: commands run in the background and notify when done.

## Link navigation (`gf`)

With `.md` in link targets, `gf` in nvim jumps directly to the linked file.
No configuration needed — it works out of the box. VSCode also navigates
`.md` links natively.

## Keymaps

Buffer-local, markdown files **inside the vault** only.

### Navigation and linking (read-only)

| Key | Mode | Action |
|-----|------|--------|
| `<C-l>` | Insert | Insert link to note (excludes archive) |
| `<C-S-l>` | Insert | Insert link to note (includes archive) |
| `<leader>bl` | Normal | Show files that link to current file |

### Editing (runs `cor` in the background)

| Key | Mode | Action | Runs |
|-----|------|--------|------|
| `<leader>cm` | Normal | Set status (mark) | `cor mark` |
| `<leader>ct` | Normal | Add a tag | `cor tag` |
| `<leader>cd` | Normal | Set due date (natural language) | `cor due` |
| `<leader>cc` | Normal | Link a project this one continues | `cor rel add --as continues` |
| `<leader>cr` | Normal | Link a related note | `cor rel add --as related` |
| `<leader>cR` | Normal | Show all relations for this note | `cor rel show` |

`<leader>cm` offers the task or project status set depending on the buffer's
`type:`. `<leader>ct` completes from tags already used in the vault.
`<leader>cd` accepts anything `cor due` accepts — `friday`, `in 2 weeks`,
`tomorrow 8pm`.

Bindings are buffer-local and only apply to markdown files **inside the vault**,
so LazyVim's own mappings are untouched everywhere else. Inside the vault,
`<leader>bl` shadows "Delete Buffers to the Left" (as the original plugin did).
`<leader>cs` is deliberately left alone — it stays LazyVim's Trouble Symbols,
which is useful for navigating a long note.

## How it works

### The note picker

`<C-l>` and the relation pickers show frontmatter, not bare filenames:

```
[planning] Glycoclip #cellco (glycoclip)
[active]   Beca leonardo (beca_leonardo)
[done]     Old screening run (old-screening) [archived]
```

Status, title, tags and stem are all on the line, so you can narrow by typing a
tag or a status word instead of guessing the filename. The picker reads only the
first ~40 lines of each file, so it stays fast across a large vault.

`templates/` is always excluded — template files carry `type: project` and would
otherwise appear as a real project.

### Link format

Links are inserted with the **relative path** from vault root:

```markdown
- [paper](ref/paper.md)      ← references in subfolders keep their path
- [ideas](project.ideas.md)  ← flat notes work too
```

### Backlinks

The `<leader>bl` command searches for all files containing links to the current
file. It uses `rg` (ripgrep) under the hood, so it's fast even in large vaults.

This is a **read-only** operation — unlike some note-taking tools, Cortex does
not auto-modify files to add backlink sections. This keeps your files clean and
avoids git noise.

## Plugin source

The plugin is installed by `cor init` to `~/.config/nvim/lua/plugins/cortex.lua`.
Re-run `cor init` to update an existing install; it will prompt before
overwriting.
---
modified: 2026-07-31 23:54
---

<div align="center">

<img src="logo.png" alt="Cor Logo" width="120" height="120">


**Plain text knowledge management for the terminal**

[![PyPI](https://img.shields.io/pypi/v/cor-text)](https://pypi.org/project/cor-text/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

Track projects, tasks, ideas, and progress using markdown files and git.


## Philosophy

- **Plain text + git + minimal tooling** - Simple, using open formats. 
- **Writing is thinking** - Tools assist, not replace. The editor is the main interface. The writing and thinking should be the main driver to organize the content.
- **Files over folders** - Flat structure with dot notation (`project.group.task.md`). `archive` folder serve to hide unactive files. 

## Installation

```bash
pip install cor-text
```

Or install from source:

```bash
pip install -e .
```

### Dependencies

- **ripgrep** (rg) - Required for content search (`cor search`)
  ```bash
  # Ubuntu/Debian
  sudo apt-get install ripgrep

  # Arch Linux
  sudo pacman -S ripgrep
  
  # Via conda
  conda install -c conda-forge ripgrep
  ```

- **fd** - Required only by the Neovim plugin (`cor init` offers to install it),
  which lists notes and finds backlinks in Lua rather than shelling out to `cor`.
  See [docs/nvim.md](docs/nvim.md).
  ```bash
  sudo apt-get install fd-find    # Ubuntu/Debian (binary is `fdfind`)
  sudo pacman -S fd               # Arch Linux
  ```

## Development

```bash
pip install -e ".[dev]"  # Install with test dependencies
pytest                   # Run all tests
pytest tests/test_cli.py # Run CLI tests only
pytest tests/test_precommit.py # Run pre-commit hook tests
pytest -v                # Verbose output
```

### Test Coverage

The test suite covers:
- **CLI commands**: `init`, `new`, `edit`, `mark`, `sync`, `projects`, `tree`, (`rename` | `move`)
- **Pre-commit hook**: Status sync, archiving, unarchiving, task groups, separators
- **Edge cases**: Multi-task commits, group status propagation, link updates

## Quick Start

```bash
# 1. Create a directory for your vault
mkdir ~/notes
cd ~/notes

# 2. Initialize Cortex vault
# This creates the vault structure, initializes git, and installs git hooks
cor init

# Or create an example vault to explore features
cor example-vault

# Create a new project
cor new project my-project

# Create a task under a project (use dot notation + tab completion)
cor new task my-project.implement-feature

# Create a standalone note
cor new note meeting-notes

# Check what needs attention
cor daily

# Summarize recent work
cor weekly

# Git sync
cor sync
```

## File Types

| Pattern | Type | Purpose |
|---------|------|---------|
| `project.md` | Project | Main project file with goals, scope, done criteria |
| `project.task.md` | Task | Actionable item within a project |
| `project.group.md` | Task Group | Organizes related tasks (also a task itself) |
| `project.group.task.md` | Task | Nested task under a task group |
| `project.group.smaller_group.task.md` | Task | Deeply nested task (supports any depth) |
| `project.note.md` | Note | Reference/thinking, not actionable |
| `backlog.md` | Backlog | Unsorted inbox for capture |

## Metadata Reference

All files use YAML frontmatter. See `schema.yaml` for full specification.

### Common Fields

| Field | Type | Description |
|-------|------|-------------|
| `created` | date | Auto-set by `cor new` |
| `modified` | date | Auto-updated by git hook |
| `due` | date | Deadline (optional) |
| `priority` | enum | `low` \| `medium` \| `high` |
| `tags` | list | Freeform tags |

### Project Status

| Value | Description |
|-------|-------------|
| `planning` | Defining goals/scope |
| `active` | Currently being worked on |
| `paused` | Temporarily on hold |
| `done` | Done, goals achieved |

### Task Status

| Value | Symbol | Description |
|-------|--------|-------------|
| `todo` | `[ ]` | Ready to start |
| `active` | `[.]` | Currently in progress |
| `blocked` | `[o]` | Blocked, cannot proceed |
| `waiting` | `[/]` | Waiting on external input (other people/AIs) |
| `done` | `[x]` | Completed (archived) |
| `dropped` | `[~]` | Cancelled/abandoned |

### Example

```yaml
---
created: 2025-12-30
modified: 2025-12-30
status: active
due: 2025-01-15
priority: high
tags: [coding, urgent]
---
```

## Commands

| Command | Description |
|---------|-------------|
| `cor init` | Initialize vault (creates structure, initializes git, installs hooks) |
| `cor example-vault` | Create a sample vault to explore features |
| `cor new <type> <name>` | Create file from template (project, task, note) |
| `cor expand <task>` | Expand task checklist into individual subtasks |
| `cor edit <name>` | Open existing file in editor (use `-a` to include archived) |
| `cor delete <name>` (`del`) | Delete a file |
| `cor mark <name> <status>` | Change task status (also parses `due`/`tag` from trailing text) |
| `cor tag <name> <tags…>` | Add (or `-d` remove) tags on a note |
| `cor due <name> <date…>` | Set (or `-d` clear) a due date using natural language |
| `cor link <query>` | Print a `[Title](stem.md)` markdown link (for piping) |
| `cor depend <add\|remove\|list> …` | Manage soft task/project dependencies |
| `cor rel <add\|rm\|show> …` | Manage relations: `continues`, `related`, `requires` |
| `cor sync` | Commit all changes, pull, and push to remote (`--no-pull`, `--no-push`) |
| `cor daily [tag]` | Show today's tasks; optional `tag` filters by project/task/project tags |
| `cor weekly` | Show this week's summary |
| `cor projects` | List active projects with status and last activity (from children) |
| `cor status` | Vault statistics and overview |
| `cor tree <project>` | Show task tree (`-i` for interactive vim-key navigation) |
| `cor focus [project\|off]` | Set/show the focused project (defaults other commands to it) |
| `cor rename <old> <new>` (`move`) | Rename/move project/task with all dependencies |
| `cor group <project.group> <tasks>` | Group existing tasks under a new task group |
| `cor inbox add <text>` | Append a line to the backlog inbox |
| `cor inbox pull` | Pull messages from the configured Telegram bot |
| `cor inbox process` | Interactively file backlog items into projects |
| `cor ref <add\|list\|show\|edit\|del\|search\|validate> …` | Manage bibliography references |
| `cor config [vault\|inbox\|verbosity\|timezone] …` | Show or set configuration |
| `cor vault <list\|add\|rm\|default> …` | Manage named vaults |
| `cor` / `cor shell` | Open the interactive shell pinned to one vault |
| `cor calendar <auth\|sync\|status\|logout>` | Google Calendar integration (needs `cor-text[calendar]`) |
| `cor maintenance sync` | Manually run archive/status sync |
| `cor maintenance hooks <install\|uninstall>` | Install/remove the pre-commit hook and completion |
| `cor search <query>` | Full-text content search (supports filters: `status:`, `#tag`, `project:`) |

### Bulk Operations

Perform operations on multiple files at once using glob patterns:

```bash
# Bulk status update
cor mark "project.*" done              # Mark all project tasks as done
cor mark "project.group.*" active      # Mark group tasks as active
cor mark -s done "project.*"           # Alternative syntax with --status flag

# Bulk move/rename  
cor move "project.old-*" "project.new-*"    # Rename matching tasks
cor move "p1.*" "p2.*"                        # Move all tasks to different project
cor move "*.old.*" "*.new.*" --dry-run        # Preview changes without applying
```

**Pattern wildcards:**
- `*` matches any sequence of characters
- `?` matches a single character
- `[abc]` matches any character in brackets

**Confirmation:** Operations affecting more than 3 files require confirmation. Use `--dry-run` to preview changes first.

### Search

Full-text content search using ripgrep (fast, no indexing required):

```bash
# Basic content search
cor search "neural network"

# Search with filters
cor search "status:active ML"
cor search "#urgent"
cor search "project:foundation_model"

# Include archived files
cor search "experiments" -a

# Limit results
cor search "training" -n 50

# Compact output (no context lines)
cor search "TODO" --no-context
```

### Sync & Conflict Resolution

When working across multiple machines, sync conflicts can occur:

```bash
# Normal sync (commit → pull → push)
cor sync

# Skip pull (commit + push only)
cor sync --no-pull

# Commit only, don't push
cor sync --no-push
```

If the pull produces a merge conflict, `cor sync` exits with the list of conflicting files. Resolve them, run `git add <files>` and `git commit`, then re-run `cor sync`.

### Natural Language Dates and Tags

The `cor new task` command supports natural language date and tag parsing:

**Due Dates**: Use `due <date>` to set a due date with natural language
```bash
cor new task project.taskname description due tomorrow
cor new task project.taskname description due next friday
cor new task project.taskname description due 2026-02-15
```

**Tags**: Use `tag <tag1> <tag2>` to add tags
```bash
cor new task project.taskname description tag urgent
cor new task project.taskname description tag ml nlp research
```

**Combined**: Use both in the same command
```bash
cor new task project.taskname finish the pipeline due tomorrow tag urgent ml
cor new task project.taskname code review due next friday tag review quality
```

Supported date formats include: tomorrow, today, next friday, in 3 days, 2026-02-15, and many more natural language expressions.

### References (Bibliography)

Manage bibliography as markdown notes in `ref/` and a BibLaTeX file `ref/references.bib`.

- **Add**: Add a reference from a DOI or URL.
   - Command: `cor ref add <identifier> [--key KEY] [--tags TAG ...] [--no-edit]`
   - Identifier can be:
      - A DOI: `10.xxxx/abcd.2024`
      - A DOI URL: `https://doi.org/10.xxxx/abcd.2024` or publisher paths containing a DOI (e.g., `https://www.biorxiv.org/content/10.1101/...`)
      - An arXiv URL or ID: `https://arxiv.org/abs/1706.03762` or `1706.03762` (mapped to `10.48550/arXiv.<id>`)
   - Behavior: creates `ref/<citekey>.md` and updates `ref/references.bib`.
   - Note: does not scrape publisher pages. If the URL does not contain a DOI, a friendly error explains how to supply one.

- **List**: Show all references.
   - Command: `cor ref list [--format table|short]`

- **Show**: Display details for a reference.
   - Command: `cor ref show <citekey>`

- **Edit**: Open the reference note.
   - Command: `cor ref edit <citekey>`

- **Delete**: Remove the reference note.
   - Command: `cor ref del <citekey> [--force]`

- **Search** (experimental): Text search across stored reference metadata.
   - Command: `cor ref search <query> [--limit N]`

Examples:

```bash
# Add from DOI
cor ref add 10.1101/2025.07.24.666581

# Add from DOI URL
cor ref add https://doi.org/10.1101/2025.07.24.666581

# Add from publisher URL (DOI embedded in path)
cor ref add https://www.biorxiv.org/content/10.1101/2025.07.24.666581v1

# Add from arXiv ID
cor ref add 1706.03762

# Custom citekey and tags
cor ref add 10.1101/2025.07.24.666581 --key smith2026transformers --tags ml --tags nlp
```

## Configuration

### Vaults

`cor init` registers the directory as a vault in `~/.config/cor/config.yaml`. You can have as many vaults as you like:

```bash
cor vault add work ~/notes/work
cor vault add personal ~/notes/personal
cor vault list                      # shows which is active, and why
cor vault default work              # used when nothing else says otherwise
```

**Which vault am I in?** Cor resolves it in this order, and the first rule that matches wins:

1. **cwd** — the nearest ancestor directory containing `backlog.md` (the vault marker)
2. **`COR_VAULT`** environment variable
3. **the default vault** from the config file

Rule 1 means `cd ~/notes/work && cor daily` always does the obvious thing. It also means a directory that happens to contain a `backlog.md` is treated as a vault, so `cor` run inside an unrelated checkout may not target what you expect. `cor vault list` and `cor status` both print the active vault and which rule produced it.

Rule 3 is a guess: nothing about your current location said which vault you meant. So **commands that write refuse to guess**. Outside a vault, `cor new task adas` asks which vault to use, and in a script or git hook — where there is nobody to ask — it fails with an error rather than writing to the wrong place. Read-only commands like `status` and `search` still fall back to the default silently.

To avoid being asked at all, use the interactive shell, `cd` into a vault, or set `COR_VAULT`.

### Interactive shell

Run `cor` with no command to open a shell pinned to one vault:

```
$ cor

  vault: work  (/home/user/notes/work)
  42 notes, 6 projects
  Type a command without the 'cor' prefix.  :help for shell commands, :quit to exit.

cor(work)> new task adas
Created task at /home/user/notes/work/adas.md
cor(work)> :vault personal
Switched to personal  (/home/user/notes/personal)
cor(personal)>
```

The vault name is in the prompt on every line, so there is never a question about where a command will land. Commands are typed without the `cor` prefix and Tab-completion works exactly as it does in your shell. `cor shell` does the same thing explicitly.

Shell commands are prefixed with `:` to keep them distinct from cor's own:

| Command | Description |
|---------|-------------|
| `:vault [name]` | Show the active vault, or switch to another |
| `:vaults` | List registered vaults |
| `:cd <path>` | Move within the vault |
| `:pwd` | Show the current directory |
| `:help` | Shell command help |
| `:quit` | Exit (also Ctrl-D, `exit`, `quit`) |

The shell is additive — every command still works as `cor <command>` from scripts, git hooks, and the Neovim plugin.

### Config File Format

`~/.config/cor/config.yaml`:
```yaml
vaults:                        # Named vaults
  work: /home/user/notes/work
  personal: /home/user/notes/personal
default: work                  # Used when cwd and COR_VAULT say nothing
vault: /home/user/notes/work   # Legacy key, mirrors the default
verbosity: 1                   # 0=silent, 1=normal, 2=verbose, 3=debug
timezone: UTC                  # For calendar event times
remote_inbox: 123456:ABC...    # Telegram bot token (optional)
vault_state:                   # Per-vault state, so focus doesn't leak
  work:
    focus: myproject
```

A config with only the old single `vault:` key keeps working — it is read as a one-entry registry.

### Configuration Commands

```bash
cor config                  # Display current config
cor config vault            # Show the active vault and resolution order
cor config vault <path>     # Set vault path
cor config verbosity <0-3>  # Set verbosity level
cor config inbox <token>    # Configure Telegram inbox
cor vault list              # List named vaults
cor vault add <name> <path> # Register a vault
cor vault rm <name>         # Unregister a vault (files untouched)
cor vault default <name>    # Set the fallback vault
```

### Mobile Inbox via Telegram

Capture notes from your phone by sending messages to a Telegram bot. Messages are automatically pulled into your backlog during `cor sync`.

#### Setup (2 minutes)

1. **Create a Telegram bot**:
   - Open Telegram and message [@BotFather](https://t.me/BotFather)
   - Send `/newbot` and follow the prompts to create your bot
   - Copy the bot token (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

2. **Configure Cortex**:
   ```bash
   cor config inbox <your-bot-token>
   ```

3. **Initialize the bot**:
   - Find your bot in Telegram (search for the username you created)
   - Send `/start` to your bot
   - Send a test message

4. **Test the connection**:
   ```bash
   cor inbox pull --dry-run  # Show pending messages without modifying the backlog
   ```

5. **Sync to pull messages**:
   ```bash
   cor sync   # Pulls messages into backlog and clears them from Telegram
   ```


### Google Calendar Integration

Sync task due dates to Google Calendar. Events are created automatically when you run `cor sync` (if authenticated).

2. **Authenticate**:
   ```bash
   cor calendar auth
   ```
   - This opens a browser for Google sign-in
   - Grant permission to manage your calendar
   - The refresh token is stored securely (you won't need to do this again)

3. **Verify**:
   ```bash
   cor calendar status   # Should show "✓ Authenticated"
   ```

#### Usage

```bash
# Sync due dates manually
cor calendar sync

# Sync to a different calendar
cor calendar sync -c "My Work Calendar"

# Check authentication status
cor calendar status

# Logout and remove stored credentials
cor calendar logout
```

**Auto-sync**: When you run `cor sync`, calendar events are updated automatically if you're authenticated.

**How it works**:
- Creates events for tasks with `due:` dates that are not done/dropped
- Updates existing events when due dates change
- Creates a "Cor Tasks" calendar if it doesn't exist
- Events include task status in the title: `[active] Task name`


### Relations Between Notes

Beyond hierarchy, notes can carry three relations. All hold **bare stems**, so
they survive a note moving in or out of `archive/`.

| Field | Meaning | Inverse |
|---|---|---|
| `continues` | This project picks up finished work | `continued_by` |
| `related` | Symmetric "see also" | itself |
| `requires` | Soft dependency | `blocks` |

Only the forward edge is ever stored. Inverses are computed by scanning, so the
two directions cannot drift apart and archived targets are never rewritten.

**Continuing a finished project.** When work resumes on a project you already
closed, don't resurrect it from the archive — that erases the `done` state and
its closing Summary. Create a new project linked to the old one:

```bash
cor new project screening-v2 -c screening-v1     # at creation time
cor rel add screening-v2 screening-v1 --as continues   # or later
```

The predecessor stays archived and stays `done`. Its **Goal** is copied into a
`## Continues` section in the new project, with a link back to the full file:

```markdown
## Continues

[< Continues: Screening V1](archive/screening-v1.md)

**Goal (Screening V1):**

Ship the first screening pipeline.
```

A project can continue several predecessors (`-c old-a -c old-b`), and one
predecessor can be continued by several successors — useful when a project
splits. `cor rel show <name>` prints every relation in both directions.

Which sections get copied is one constant, `COPIED_SECTIONS` in
`cor/core/continuation.py`. Sections that are missing, or that still hold only
the template's placeholder comment, are skipped rather than copied empty.

### File Hierarchy & Linking

Cor uses **dot notation** for hierarchy: `project.group.task.md`

**Forward links** (parent → children):
```markdown
## Tasks
- [ ] [Implement API](project.implement-api)
- [.] [Testing group](project.testing)
```

**Backlinks** (child → parent):
```markdown
[< Project Name](project)
```

Links use **relative paths** to maintain compatibility when files are archived:
- Active child → Active parent: `[< Parent](parent)`
- Archived child → Active parent: `[< Parent](../parent)`
- Active child → Archived parent: Not typical, but supported

### Renaming & Moving Files

Use `cor rename` or `cor move` to safely refactor your vault. The hook automatically:
- Updates all forward links in parents
- Updates all backlinks in children  
- Updates `parent` field in child frontmatter
- Updates all descendants' parent chains
- Moves files to/from archive as needed

```bash
# Rename a project (updates all tasks)
cor rename old-project new-project

# Rename a task
cor rename project.old-task project.new-task

# Move a group to a different project
cor move p1.experiments p2.experiments

# Preview changes before committing
cor rename old-project new-project --dry-run

# Commit the changes
git add -A && git commit -m "Rename project"
```

### Converting Tasks to Groups

When designing complex features, you might start with a single task and then realize it needs to be broken down. Cortex makes this easy by expanding checklist items into individual subtasks:

**Before** - Single task with checklist (`my-project.feature.md`):
```markdown
## Description

Implement new authentication feature:

- [ ] design-api
- [ ] implement-backend
- [ ] write-tests
- [ ] update-documentation
```

**After running** `cor expand my-project.feature`:
- Creates `my-project.feature.design-api.md`
- Creates `my-project.feature.implement-backend.md`
- Creates `my-project.feature.write-tests.md`
- Creates `my-project.feature.update-documentation.md`
- Updates `my-project.feature.md` with proper task links
- Removes the original checklist

The task becomes a proper task group with full hierarchy and linking support.

### Completion Configuration

Control fuzzy completion behavior via environment variable:

```bash
# Allow cycling through all 100%-score matches (default, recommended)
export COR_COMPLETE_COLLAPSE_100=0

# Collapse to shortest match only (faster single-result completion)
export COR_COMPLETE_COLLAPSE_100=1
```

## Shell Setup

Shell completion is automatically configured when you run `cor init`. The setup detects your shell (zsh or bash) and adds the necessary completion code to your shell config file.

### Zsh

After running `cor init`, optionally add to your `~/.zshrc` to enable Tab cycling through suggestions:

Then reload:
```zsh
source ~/.zshrc # or .bashrc
```

## Directory Structure

### User Configuration & Vault

```
~/.config/cor/
├── config.yaml             # Global config (vault path, verbosity)
└── google_credentials.pickle  # Google Calendar auth (chmod 600)

~/.zshrc or ~/.bashrc       # Shell completion automatically added here

your-vault/                 # Your notes directory
├── .git/                   # Git repository (auto-initialized by cor init)
│   └── hooks/
│       └── pre-commit      # Auto-maintenance hook
├── backlog.md              # Unsorted inbox for capture (vault marker)
├── archive/                # Completed/archived items
│   ├── old-project.md
│   ├── project.old-task.md
│   └── ...
└── templates/              # File templates
    ├── project.md
    ├── task.md
    └── note.md
```

### Project Source (Development)

```
cortex_pkm/                 # Repository root
├── cor/                    # Main package
│   ├── __init__.py         # Version
│   ├── config.py           # Vault path resolution & user config
│   ├── schema.py           # Schema constants (loaded from assets/schema.yaml)
│   ├── exceptions.py       # Custom exception hierarchy
│   ├── utils.py            # Utility functions (incl. NLP date/tag parsing)
│   ├── completions.py      # Shell completion logic
│   ├── crossref.py         # Crossref/arXiv DOI lookup
│   ├── bibtex.py           # references.bib read/write
│   ├── dependencies.py     # Task/project dependency logic
│   ├── cli/                # CLI entry point & command groups
│   │   ├── __init__.py     # `cli` group, registration, hook install
│   │   ├── init.py         # init / example-vault
│   │   ├── notes.py        # new, edit, mark, expand, ...
│   │   ├── config.py       # config, focus, inbox
│   │   ├── maintenance.py  # sync, maintenance, hooks
│   │   └── search_cmd.py   # search
│   ├── commands/           # Additional command modules
│   │   ├── status.py       # daily, weekly, projects, tree, status
│   │   ├── refactor.py     # rename/move/group
│   │   ├── process.py      # backlog processing
│   │   ├── refs.py         # bibliography (cor ref)
│   │   ├── dependencies.py # cor depend
│   │   ├── calendar.py     # Google Calendar integration
│   │   ├── inbox.py        # Telegram inbox
│   │   └── log.py          # backlog capture
│   ├── core/               # Core business logic
│   │   ├── files.py        # FileIterator / NoteFileManager
│   │   ├── notes.py        # note parsing
│   │   ├── links.py        # link parsing & rewriting
│   │   ├── archive.py      # ArchiveManager
│   │   └── refs.py         # reference metadata
│   ├── search/             # Search & completion
│   │   ├── content.py      # ripgrep content search
│   │   ├── fuzzy.py        # fuzzy matching
│   │   └── completion.py   # completion helpers
│   ├── sync/
│   │   └── runner.py       # MaintenanceRunner (hook/sync logic)
│   ├── tui/
│   │   └── tree_app.py     # interactive tree (cor tree -i)
│   ├── hooks/
│   │   └── pre-commit      # Pre-commit hook script
│   └── assets/             # Built-in templates, schema, nvim plugin
│       ├── schema.yaml
│       ├── project.md / task.md / note.md / backlog.md / ref.md
│       └── cortex.lua      # Neovim/LazyVim plugin
├── tests/                  # Test suite
├── docs/                   # Additional docs (nvim.md)
├── pyproject.toml          # Project config & dependencies
├── README.md               # This file
├── LICENSE                 # MIT License
└── MANIFEST.in             # Package manifest
```

## Git Hooks & Automation

The pre-commit hook automatically runs on every commit to keep your vault consistent. It is automatically installed when you run `cor init`.

### What the Hook Does

**Validation & Consistency:**
1. Validates frontmatter - Checks status/priority values against schema
2. Detects broken links - Blocks commits with missing link targets
3. Prevents orphan files - Files must have valid parent references
4. Detects partial renames - Ensures all related files are renamed together

**Automatic Updates:**
5. Updates modified dates - Sets `modified` field to current timestamp (YYYY-MM-DD HH:MM)
6. Handles file renames - When you rename a file:
   - Updates all parent links (adds/removes task entries)
   - Updates all child parent references
   - Updates backlinks with new parent title
   - Preserves link semantics (relative paths for archive)
7. Archives completed items - Moves to `archive/` when `status: done`
8. Unarchives reactivated items - Moves back from archive when status changes from done
9. Syncs task status - Updates parent checkboxes to match task status:
   - `[ ]` = todo, `[.]` = active, `[o]` = blocked, `[/]` = waiting, `[x]` = done, `[~]` = dropped

**Hierarchy & Organization:**
10. Updates task group status - Calculates from children (blocked > active > done > todo)
11. Updates project status - Sets to `active` if any task is active, back to `planning` when none
12. Sorts tasks - By status (blocked, active, waiting, todo, then done, dropped)
13. Adds separators - Inserts `---` between active and completed tasks for readability

### How It Works

The hook uses `git diff --cached` to detect changes, so it only processes modified files:

```bash
git add my-project.task.md         # Stage changes
git commit -m "Update task"        # Hook runs automatically
```

### Disabling Temporarily

```bash
git commit --no-verify -m "Skip hook"  # Bypass hook for this commit
```

### Manual Sync

To manually run the sync logic on all files (useful after bulk edits):

```bash
cor maintenance sync        # Preview changes
cor maintenance sync --all  # Sync all files (not just modified)
```
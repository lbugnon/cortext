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
| `root.md` | Root | Dashboard/digest of current state |

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
| `cor new <type> <name>` | Create file from template (project, task, note) |
| `cor expand <task>` | Expand task checklist into individual subtasks |
| `cor edit <name>` | Open existing file in editor (use `-a` to include archived) |
| `cor mark <name> <status>` | Change task status (todo, active, blocked, done, dropped) |
| `cor sync` | Commit all changes, pull, and push to remote (`--no-pull`, `--no-push`) |
| `cor daily [tag]` | Show today's tasks; when `tag` is provided, only tasks matching the tag (by project name, task tags, or project tags) |
| `cor weekly` | Show this week's summary |
| `cor projects` | List active projects with status and last activity (from children) |
| `cor tree <project>` | Show task tree for a project with status symbols |
| `cor review` | Interactive review of stale/blocked items |
| `cor rename <old> <new>` | Rename project/task with all dependencies |
| `cor move <old> <new>` | Alias of rename (conceptually better for moving groups/tasks) |
| `cor group <project.group> <tasks>` | Group existing tasks under a new task group |
| `cor process` | Process backlog items into projects |
| `cor hooks install` | Install pre-commit hook and shell completion |
| `cor hooks uninstall` | Remove git hooks |
| `cor config vault <path>` | Set global vault path |
| `cor config inbox <token>` | Configure Telegram bot for mobile inbox |
| `cor config` | Show current configuration |
| `cor inbox` | Test Telegram connection and show pending messages |
| `cor calendar auth` | Authenticate with Google Calendar |
| `cor calendar sync` | Sync due dates to Google Calendar |
| `cor calendar status` | Check calendar authentication status |
| `cor calendar logout` | Remove Google Calendar credentials |
| `cor maintenance sync` | Manually run archive/status sync |
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

### Vault Path Setup

Cor automatically configures your vault path in `~/.config/cor/config.yaml` when you run `cor init`. You can change it anytime:

```bash
# Set during initial setup
cor init

# Or reconfigure later
cor config vault /path/to/notes
```

Once configured, you can run `cor` commands from any directory:

```bash
# Commands work from anywhere after init
cd /tmp
cor daily
cor new task my-project.quick-idea
```

### Config File Format

`~/.config/cor/config.yaml`:
```yaml
vault: /home/user/notes        # Vault path (required)
verbosity: 1                   # 0=silent, 1=normal, 2=verbose, 3=debug
remote_inbox: 123456:ABC...    # Telegram bot token (optional)
```
```

### Configuration Commands

```bash
cor config                # Display current config
cor config vault <path>   # Set vault path
cor config verbosity <0-3> # Set verbosity level
cor config inbox <token>  # Configure Telegram inbox
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
   cor inbox  # Shows pending messages
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
~/.config/cortex/
├── config.yaml             # Global config (vault path, verbosity)
└── google_credentials.pickle  # Google Calendar auth (chmod 600)

~/.zshrc or ~/.bashrc       # Shell completion automatically added here

your-vault/                 # Your notes directory
├── .git/                   # Git repository (auto-initialized by cor init)
│   └── hooks/
│       └── pre-commit      # Auto-maintenance hook
├── root.md                 # Dashboard/digest of current state
├── backlog.md              # Unsorted inbox for capture
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
cor/                        # Repository root
├── cor/                    # Main package
│   ├── __init__.py
│   ├── cli.py              # Command-line interface
│   ├── config.py           # Vault path resolution
│   ├── parser.py           # YAML/markdown parsing
│   ├── schema.py           # Data schema & validation
│   ├── utils.py            # Utility functions
│   ├── completions.py      # Shell completion logic
│   ├── fuzzy.py            # Fuzzy matching for search
│   ├── maintenance.py      # Auto-sync & archiving
│   ├── commands/           # Command implementations
│   │   ├── __init__.py
│   │   ├── process.py      # Backlog processing
│   │   ├── refactor.py     # Rename/move operations
│   │   └── status.py       # Status display
│   ├── hooks/              # Git integration
│   │   └── pre-commit      # Pre-commit hook script
│   └── assets/             # Built-in templates & schema
│       ├── schema.yaml
│       ├── project.md
│       ├── task.md
│       ├── note.md
│       ├── backlog.md
│       └── root.md
├── tests/                  # Test suite
│   ├── conftest.py         # Test configuration
│   ├── test_cli.py         # CLI command tests
│   ├── test_delete.py      # Delete operation tests
│   ├── test_maintenance.py # Hook & sync tests
│   ├── test_precommit.py   # Pre-commit hook tests
│   ├── test_rename_*.py    # Rename/move tests
│   └── __pycache__/
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

# AGENTS.md - CortexPKM Developer Guide

This file provides essential context for AI coding agents working on the CortexPKM project.

> **This file is about developing cor. It is not a vault's rules.** If you are *using* a vault,
> run `cor rules` in it and follow what that vault says -- it overrides every default here. A
> vault is selected from the working directory, so running a command from the wrong directory
> writes to the wrong vault without complaint.

## Project Overview

**CortexPKM** is a plain-text knowledge management system that uses markdown files and git to track projects, tasks, ideas, and progress. It follows a philosophy of "writing is thinking" - the editor is the main interface, not a web UI.

### Key Characteristics

- **Language**: Python 3.10+
- **Architecture**: CLI tool built with Click framework
- **Data Storage**: YAML frontmatter + Markdown content in flat file structure
- **Version Control**: Git with pre-commit hooks for automation
- **Configuration**: YAML file at `~/.config/cortex/config.yaml`

### Philosophy

1. **Plain text + git + minimal tooling** - Simple, using open formats
2. **Writing is thinking** - Tools assist, don't replace human thinking
3. **Files over folders** - Flat structure with dot notation (`project.group.task.md`), `archive/` folder for inactive items

## Project Structure

```
cortex_pkm/
├── cor/                        # Main Python package
│   ├── __init__.py             # Version info (0.1.4)
│   ├── schema.py               # Schema constants loaded from schema.yaml
│   ├── config.py               # Vault resolution, named registry & user config
│   ├── repl.py                 # Interactive shell (bare `cor` / `cor shell`)
│   ├── exceptions.py           # Custom exception hierarchy
│   ├── utils.py                # Utility functions
│   ├── completions.py          # Shell completion logic
│   ├── cli/                    # CLI command implementations
│   │   ├── __init__.py         # CLI group registration & hook management
│   │   ├── init.py             # Vault initialization
│   │   ├── notes.py            # Note/task management commands
│   │   ├── config.py           # Config commands
│   │   ├── maintenance.py      # Sync and hooks commands
│   │   └── search_cmd.py       # Search command wrapper
│   ├── commands/               # Additional command modules
│   │   ├── refactor.py         # Rename/move operations
│   │   ├── process.py          # Backlog processing
│   │   ├── status.py           # Daily/weekly/project views
│   │   ├── calendar.py         # Google Calendar integration
│   │   ├── inbox.py            # Telegram bot integration
│   │   ├── refs.py             # Bibliography management
│   │   ├── dependencies.py     # Task dependency tracking
│   │   └── log.py              # Activity logging
│   ├── core/                   # Core business logic
│   │   ├── files.py            # File iteration and I/O operations
│   │   ├── links.py            # Link management patterns
│   │   ├── archive.py          # Archive operations
│   │   ├── notes.py            # Note parsing and operations
│   │   └── refs.py             # Reference/bibliography handling
│   ├── search/                 # Search functionality
│   │   ├── fuzzy.py            # Fuzzy matching
│   │   ├── content.py          # Content search (ripgrep wrapper)
│   │   └── completion.py       # Shell completion helpers
│   ├── sync/                   # Maintenance operations
│   │   └── runner.py           # MaintenanceRunner - main sync logic
│   ├── hooks/                  # Git integration
│   │   └── pre-commit          # Pre-commit hook script (Python)
│   └── assets/                 # Built-in templates and schema
│       ├── schema.yaml         # Metadata schema definition
│       ├── project.md          # Project template
│       ├── task.md             # Task template
│       ├── note.md             # Note template
│       ├── backlog.md          # Backlog template
│       ├── ref.md              # Reference template
│       └── cortex.lua          # Neovim/LazyVim plugin (installed by cor init)
├── tests/                      # Test suite
│   ├── conftest.py             # Pytest fixtures (temp_vault, project_with_tasks, etc.)
│   ├── test_cli.py             # CLI command tests
│   ├── test_precommit.py       # Pre-commit hook tests
│   ├── test_maintenance.py     # Maintenance/sync tests
│   ├── test_rename_*.py        # Rename/move operation tests
│   └── ...                     # Other test modules
├── templates/                  # (empty - templates in cor/assets/)
├── hooks/                      # (empty - hook in cor/hooks/)
├── example_notes/              # Example vault for exploration
├── pyproject.toml              # Project configuration & dependencies
├── README.md                   # Comprehensive user documentation
└── AGENTS.md                   # This file
```

## Build and Development

### Installation

```bash
# Development install
pip install -e ".[dev]"

# Production install
pip install -e .
```

### Dependencies

**Core Dependencies** (from `pyproject.toml`):
- `click>=8.0` - CLI framework
- `python-frontmatter>=1.0` - YAML frontmatter parsing
- `rapidfuzz>=3.0` - Fuzzy string matching
- `simple-term-menu>=1.6` - Interactive terminal menus
- `habanero>=1.2` - Crossref API client
- `arxiv>=2.0.0` - arXiv API client
- `bibtexparser>=1.4` - BibTeX parsing
- `dateparser>=1.2` - Natural language date parsing
- `google-auth-oauthlib>=1.0` - Google Calendar auth
- `google-api-python-client>=2.0` - Google Calendar API
- `ripgrepy>=2.0` - Python wrapper for ripgrep binary

**External Binary Dependency**:
- **ripgrep (rg)** - Required for `cor search` command

**Dev Dependencies**:
- `pytest>=7.0` - Testing framework

### Testing

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_cli.py
pytest tests/test_precommit.py

# Verbose output
pytest -v
```

**Test Fixtures** (in `conftest.py`):
- `temp_vault` - Creates temporary vault with git initialized
- `project_with_tasks` - Creates project with 3 tasks
- `project_with_group` - Creates project with task group and subtasks
- Helper functions: `stage_files()`, `run_precommit()`, `get_frontmatter()`, `set_status()`

## Code Style Guidelines

### Python Conventions

1. **Type hints**: Use throughout, especially for function parameters and return types
2. **Docstrings**: Google-style docstrings with Args/Returns sections
3. **Imports**: Group by stdlib, third-party, local; use absolute imports within package
4. **Exceptions**: Always use custom exceptions from `cor.exceptions`, catch at CLI level

### Naming Conventions

- **Files**: Use `snake_case.py` for modules
- **Classes**: `PascalCase` for classes
- **Functions**: `snake_case` for functions/methods
- **Constants**: `UPPER_CASE` for module-level constants
- **Private**: `_leading_underscore` for internal functions

### File Organization Patterns

- CLI commands: Group related commands in modules under `cor/cli/` or `cor/commands/`
- Core logic: Place reusable business logic in `cor/core/`
- One class per file when class is substantial (e.g., `MaintenanceRunner`, `FileIterator`)

## Architecture Details

### CLI Architecture

The CLI uses Click's group-based organization:

```python
# Entry point in pyproject.toml
cor = "cor.cli:cli"

# Main group in cor/cli/__init__.py
@click.group(cls=CortexCLI, ...)
def cli(ctx, verbose: int):
    """Cortex - Plain text knowledge management."""
    ...

# Commands registered via cli.add_command()
cli.add_command(init)
cli.add_command(new)
# ... etc
```

**Error Handling**: The `CortexCLI` class catches `CortexError` exceptions and formats them nicely. All commands should raise appropriate exceptions rather than calling `sys.exit()`.

### Data Model

**Note Types** (defined in `cor/assets/schema.yaml`):

| File Pattern | Type | Description |
|-------------|------|-------------|
| `project.md` | Project | Main project file |
| `project.task.md` | Task | Actionable item |
| `project.group.md` | Task Group | Organizes related tasks |
| `project.group.task.md` | Task | Nested task |
| `*.note.md` | Note | Reference/thinking |
| `backlog.md` | Backlog | Inbox for capture |

**Metadata Schema** (YAML frontmatter):

Common fields:
- `created: YYYY-MM-DD HH:MM` - Auto-set by `cor new`
- `modified: YYYY-MM-DD HH:MM` - Updated by git hook
- `tags: [tag1, tag2]` - Freeform tags
- `priority: low|medium|high` - Urgency
- `due: YYYY-MM-DD` - Deadline

Project-specific:
- `status: planning|active|paused|done`
- `requires: [other_project]` - Soft dependencies

Task-specific:
- `status: todo|active|blocked|done|dropped|waiting`
- `parent: project_name` - Parent reference
- `requires: [other_task]` - Soft dependencies

### Key Classes

**FileIterator** (`cor/core/files.py`):
- Centralizes file iteration patterns
- Methods: `iter_all_notes()`, `iter_projects()`, `iter_children()`, etc.
- Handles archive directory automatically

**NoteFileManager** (`cor/core/files.py`):
- Centralized file I/O with frontmatter
- Methods: `load_note()`, `save_note()`, `find_note()`, `exists()`

**MaintenanceRunner** (`cor/sync/runner.py`):
- Core maintenance logic (1000+ lines)
- Handles: archive/unarchive, link updates, status sync, validation
- Entry point: `sync(files, renamed, deleted)`

**LinkManager** (`cor/core/links.py`):
- Manages markdown link parsing and updates
- Handles relative paths for archive/ folder

**ArchiveManager** (`cor/core/archive.py`):
- Handles moving files to/from archive
- Manages path translations

### Pre-commit Hook

The pre-commit hook (`cor/hooks/pre-commit`) runs on every git commit:

1. **Validation**:
   - Validates frontmatter against schema
   - Validates links point to existing files
   - Prevents marking project/group as done with incomplete children

2. **Automatic Updates**:
   - Updates `modified` timestamps
   - Archives completed items (status=done → move to archive/)
   - Unarchives reactivated items
   - Syncs task checkboxes to parent files
   - Updates task group status from children
   - Updates project status from active tasks
   - Sorts tasks by status with separators

3. **Rename Handling**:
   - Updates links in parent files
   - Updates backlinks in children
   - Updates `parent` field in frontmatter

Hook execution flow in `MaintenanceRunner.sync()`:
```python
def sync(self, files, renamed, deleted):
    # 1. Handle deleted files
    # 2. Handle renamed files
    # 3. Update modified dates
    # 4. Validate files
    # 5. Unarchive reactivated files
    # 6. Archive completed files
    # 7. Update group status
    # 8. Update project status
    # 9. Sync task status to project checkboxes
    # 10. Sort tasks
```

### Configuration System

Config stored at `~/.config/cor/config.yaml` (respects `XDG_CONFIG_HOME`):

```yaml
vaults:                        # Named vault registry
  work: /path/to/work
  personal: /path/to/personal
default: work                  # Fallback when cwd and COR_VAULT say nothing
vault: /path/to/work           # Legacy key, mirrors the default
verbosity: 1                   # 0=silent, 1=normal, 2=verbose, 3=debug
timezone: UTC
remote_inbox: 123456:ABC...    # Telegram bot token (optional)
vault_state:                   # Per-vault state
  work:
    focus: myproject
```

Config functions in `cor/config.py`:
- `load_config()`, `save_config()` - Read/write config file
- `resolve_vault()` - **The** vault resolver; returns `(path, source)`
- `get_vault_path()` - Thin wrapper returning just the path
- `get_vaults()`, `add_vault()`, `remove_vault()`, `get_default_vault()`, `set_default_vault()`, `vault_name_for()` - Named registry
- `get_remote_inbox()` - Get Telegram token
- `get_verbosity()`, `set_verbosity()` - Logging level

#### Vault resolution (multivault)

`resolve_vault()` applies three rules in order and reports which one won:

1. `VAULT_SOURCE_CWD` - nearest ancestor of cwd containing `backlog.md`
2. `VAULT_SOURCE_ENV` - the `COR_VAULT` environment variable
3. `VAULT_SOURCE_CONFIG` - the default vault in the config file

The source matters. Rule 3 means nothing about the user's location indicated
a vault, so the answer is a *guess*. `require_init` in `cor/utils.py` turns
that into a prompt when a TTY is present and a hard error when one is not, so
a script or git hook can never silently write to the wrong vault.

Read-only commands opt out with `@require_init(write=False)` and keep the
silent fallback — a wrong read is recoverable, a wrong write is not. The
decorator defaults to strict on purpose: a read command left strict merely
prompts, while a write command left lax corrupts the wrong vault silently.

Nothing caches the resolved path; every command re-resolves at call time.
That is what lets the interactive shell switch vaults in-process.

### Interactive Shell

`cor/repl.py` — bare `cor` (or `cor shell`) opens a prompt_toolkit session
pinned to one vault, showing its name in the prompt on every line.

- **Pinning**: `CorShell._pin()` does `os.chdir(vault)` *and* sets
  `COR_VAULT`, satisfying resolution rules 1 and 2. Subprocesses a command
  spawns (`$EDITOR`, git, the tree TUI) therefore agree with the prompt.
- **Dispatch**: in-process via `group.main(standalone_mode=False)`, catching
  `SystemExit` — `_handle_cor_error` calls `sys.exit(1)`, which would
  otherwise end the session on the first typo. Verbosity is snapshotted and
  restored around each line because the group's `-v` flag persists to config.
- **Completion**: `CorCompleter` delegates to
  `click.shell_completion.ShellComplete`, the same code path zsh uses. Do not
  hand-build a `click.Context` for completion callbacks — several read state
  off the resolved context (`complete_name` branches on `ctx.params["note_type"]`).
- **Meta-commands** are `:`-prefixed to avoid colliding with real commands.

The shell is **additive**. The one-shot CLI must stay scriptable: the Neovim
plugin (`cor/assets/cortex.lua`), the pre-commit hook, `cor example-vault`,
the tree TUI, and shell completion all invoke `cor` headlessly.

## Testing Strategy

### Test Organization

Tests are organized by functionality:

| Test File | Coverage |
|-----------|----------|
| `test_cli.py` | CLI command invocation and output |
| `test_precommit.py` | Pre-commit hook behavior |
| `test_maintenance.py` | Archive/unarchive, status sync |
| `test_links.py` | Link parsing and updates |
| `test_files.py` | File iteration utilities |
| `test_rename_*.py` | Rename/move operations |
| `test_bulk_operations.py` | Glob pattern operations |
| `test_search.py` | Content search functionality |

### Writing Tests

Use the `temp_vault` fixture for integration tests:

```python
def test_something(temp_vault):
    # temp_vault is a Path to initialized vault with git
    # Create files, run commands, assert results
    result = runner.invoke(cli, ['new', 'task', 'myproject.task'])
    assert result.exit_code == 0
```

Use `project_with_tasks` or `project_with_group` for hierarchy tests:

```python
def test_group_status(project_with_group):
    # Pre-configured project with group and subtasks
    vault = project_with_group["vault"]
    group_path = project_with_group["group"]
    # Modify, commit, assert hook behavior
```

### Running Pre-commit Hook in Tests

```python
from conftest import run_precommit, stage_files

# Stage files and run hook
stage_files(vault, file1, file2)
returncode, stdout, stderr = run_precommit(vault)
```

## Security Considerations

1. **Config file permissions**: `cor/config.py` sets `0o600` on config file (owner read/write only)
2. **Google credentials**: Stored as pickle with restricted permissions
3. **Telegram tokens**: Can be set via env var `TELEGRAM_BOT_TOKEN` or config file
4. **No shell injection**: All subprocess calls use list arguments, not shell strings

## Common Development Tasks

### Adding a New CLI Command

1. Create function in appropriate module (or new module):
```python
# cor/commands/mycommand.py
import click
from ..config import get_vault_path

@click.command()
@click.argument('name')
def mycommand(name):
    """Command description for --help."""
    vault = get_vault_path()
    # Implementation
```

2. Import and register in `cor/cli/__init__.py`:
```python
from ..commands.mycommand import mycommand
cli.add_command(mycommand)
```

### Adding a New Metadata Field

1. Update `cor/assets/schema.yaml`
2. Add validation in `cor/sync/runner.py` if needed
3. Update templates in `cor/assets/*.md`
4. Add tests in appropriate test file

### Modifying Pre-commit Hook Behavior

1. Core logic is in `MaintenanceRunner` class (`cor/sync/runner.py`)
2. Hook entry point is `cor/hooks/pre-commit`
3. Add tests in `tests/test_precommit.py`
4. Test manually with `cor maintenance sync`

## External Integrations

### Google Calendar

- OAuth2 flow in `cor/commands/calendar.py`
- Credentials stored at `~/.config/cortex/google_credentials.pickle`
- Sync creates events for tasks with due dates

### Telegram Bot

- Bot token configured via `cor config inbox <token>`
- Messages pulled during `cor sync`
- Implementation in `cor/commands/inbox.py`

### Crossref/arXiv

- DOI resolution via `cor/crossref.py`
- Used by `cor ref add` command
- Stores references in `ref/` directory with BibTeX export

## Useful References

- **Click docs**: https://click.palletsprojects.com/
- **Frontmatter**: https://python-frontmatter.readthedocs.io/
- **Schema**: See `cor/assets/schema.yaml` for all valid values
- **README.md**: Comprehensive user-facing documentation

"""Utility functions for Cor CLI."""

import functools
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import click
from dateparser.search import search_dates
from dateparser import parse as parse_date

from .config import get_vault_path, get_verbosity
from .exceptions import NotInitializedError, NotFoundError
from .schema import DATE_TIME


def require_init(f):
    """Decorator that ensures vault is initialized before running command."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        notes_dir = get_vault_path()
        if not (notes_dir / "root.md").exists():
            raise NotInitializedError("Not initialized. Run 'cor init' first.")
        return f(*args, **kwargs)
    return wrapper


def get_notes_dir() -> Path:
    """Get the current notes directory."""
    return get_vault_path()


# --- Path hierarchy utilities ---

def get_parent_name(stem: str) -> str | None:
    """Get parent from hierarchy (project.group.task -> project.group).

    Returns None if no parent (single-part name like 'project').
    """
    parts = stem.split(".")
    return ".".join(parts[:-1]) if len(parts) >= 2 else None


def get_root_project(stem: str) -> str:
    """Get root project name (project.group.task -> project)."""
    return stem.split(".")[0]


def get_hierarchy_depth(stem: str) -> int:
    """Get depth in hierarchy (project=1, project.task=2, project.group.task=3)."""
    return len(stem.split("."))


def get_templates_dir() -> Path:
    """Get the templates directory."""
    return get_vault_path() / "templates"


def get_projects() -> list[str]:
    """Get list of project names (no dots in stem, ``type`` is not ``note``).

    A top-level note (``theme.md`` with ``type: note``) is structurally like a
    project but represents knowledge rather than tracked work, so it is
    excluded. Files without a parseable type are treated as projects.
    """
    from .core.files import FileIterator
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        return []
    return FileIterator(notes_dir).get_project_stems()


def get_task_groups(project: str) -> list[str]:
    """Get list of task group names for a project (project.group.md files)."""
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        return []
    groups = []
    for p in notes_dir.glob(f"{project}.*.md"):
        parts = p.stem.split(".")
        # Task groups have exactly 2 parts: project.group
        if len(parts) == 2:
            groups.append(parts[1])
    return sorted(groups)


def get_project_tasks(project: str) -> list[str]:
    """Get list of direct task names for a project (project.task.md files, not in groups)."""
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        return []
    tasks = []
    for p in notes_dir.glob(f"{project}.*.md"):
        parts = p.stem.split(".")
        # Direct tasks have exactly 2 parts: project.task
        if len(parts) == 2:
            tasks.append(parts[1])
    return sorted(tasks)


def get_all_notes() -> list[str]:
    """Get list of all note file stems (projects + notes, excluding special files)."""
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        return []
    notes = []
    for p in notes_dir.glob("*.md"):
        if p.stem not in ("root", "backlog"):
            notes.append(p.stem)
    return sorted(notes)


def get_template(template_type: str) -> str:
    """Read a template file and return its contents."""
    template_path = get_templates_dir() / f"{template_type}.md"
    if not template_path.exists():
        raise NotFoundError(f"Template not found: {template_path}")
    return template_path.read_text()


def format_time_ago(ref_time: datetime) -> str:
    """Format time difference as human-readable string.

    - Less than 1 hour: shows minutes (e.g., "45m ago")
    - Less than 1 day: shows hours (e.g., "5h ago")
    - 1 day or more: shows days (e.g., "3d ago")
    """
    now = datetime.now()
    diff = now - ref_time
    total_seconds = diff.total_seconds()

    if total_seconds < 3600:  # Less than 1 hour
        minutes = int(total_seconds / 60)
        return f"{minutes}m ago" if minutes > 0 else "just now"
    elif total_seconds < 86400:  # Less than 1 day
        hours = int(total_seconds / 3600)
        return f"{hours}h ago"
    else:
        days = diff.days
        return f"{days}d ago"


def format_due_date(due_date) -> str:
    """Format due date as human-readable string.
    
    - Today: "today"
    - Tomorrow: "tomorrow" 
    - Yesterday: "yesterday"
    - Within 7 days: "in X days" or "X days ago"
    - Further: "on YYYY-MM-DD"
    """
    from datetime import date as date_type
    
    # Convert to date if datetime
    if isinstance(due_date, datetime):
        due_date = due_date.date()
    elif not isinstance(due_date, date_type):
        return str(due_date)
    
    today = date_type.today()
    diff = (due_date - today).days
    
    if diff == 0:
        return "today"
    elif diff == 1:
        return "tomorrow"
    elif diff == -1:
        return "yesterday"
    elif 2 <= diff <= 7:
        return f"in {diff} days"
    elif -7 <= diff <= -2:
        return f"{abs(diff)} days ago"
    else:
        return f"on {due_date.strftime('%Y-%m-%d')}"


def format_title(name: str) -> str:
    """Format name as title: underscores become spaces, capitalize first letter.

    Examples: "my_cool_task" -> "My cool task", "fix-bug" -> "Fix-bug"
    """
    title = name.replace("_", " ")
    return title[0].upper() + title[1:] if title else title


def read_h1(path: Path) -> str | None:
    """Read the first H1 heading from a markdown file.

    Returns the title string (without the leading '# ') or None if not found.
    """
    try:
        content = path.read_text()
    except OSError:
        return None
    in_frontmatter = False
    frontmatter_done = False
    for line in content.splitlines():
        stripped = line.strip()
        if not frontmatter_done:
            if stripped == "---":
                in_frontmatter = not in_frontmatter
                if not in_frontmatter:
                    frontmatter_done = True
                continue
            if in_frontmatter:
                continue
        if line.startswith("# "):
            return line[2:].strip()
    return None


def title_to_stem(title: str) -> str:
    """Convert a human-readable title back to a filename stem (reverse of format_title).

    Examples: "Auth fix" -> "auth_fix", "Fix-bug" -> "fix-bug"
    """
    slug = title.lower().replace(" ", "_")
    return re.sub(r'[^\w-]', '', slug).strip("_-")


def render_template(
    template: str, name: str, parent: str | None = None, parent_title: str | None = None,
    message: str | None = None
) -> str:
    """Substitute placeholders in template."""
    now = datetime.now().strftime(DATE_TIME)
    
    # Generate parent link if parent exists
    parent_link = ""
    if parent and parent_title:
        parent_link = f"[< {parent_title}]({parent}.md)"
    
    content = template.format(
        date=now,
        name=format_title(name),
        parent=parent or "",
        parent_title=parent_title or "",
        parent_link=parent_link,
    )
    # If message provided, add it to the Description section
    if message:
        content = content.replace("## Description\n", f"## Description\n\n{message}\n")
        content = content.replace("## Goal\n", f"## Goal\n\n{message}\n")
    return content


def is_vscode_terminal() -> bool:
    """Check if running inside VSCode's integrated terminal."""
    return (
        os.environ.get("TERM_PROGRAM") == "vscode" or
        "VSCODE_GIT_IPC_HANDLE" in os.environ
    )


def open_in_editor(filepath: Path):
    """Open file in appropriate editor based on environment.

    - In VSCode terminal: opens with `code` command
    - In regular terminal: opens with $EDITOR, or tries common editors
    """
    if is_vscode_terminal():
        # Use VSCode's code command
        subprocess.call(["code", str(filepath)])
        return

    # Get editor from environment or try common editors
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    
    if editor:
        # User has specified an editor, try to use it
        try:
            subprocess.call([editor, str(filepath)])
            return
        except FileNotFoundError:
            click.echo(click.style(f"Warning: $EDITOR is set to '{editor}' but it's not found.", fg="yellow"))
    
    # Try common editors in order of preference
    for fallback in ["nvim", "vim", "nano", "vi"]:
        try:
            subprocess.call([fallback, str(filepath)])
            return
        except FileNotFoundError:
            continue
    
    # No editor found
    click.echo(click.style("No editor found!", fg="red", bold=True))
    click.echo("Tried: nvim, vim, nano, vi")
    click.echo(f"\nTo fix this, either:")
    click.echo("  1. Install an editor (e.g., apt install nano)")
    click.echo("  2. Set $EDITOR environment variable: export EDITOR=/path/to/your/editor")
    click.echo(f"\nFile location: {filepath}")


def add_task_to_project(project_path: Path, task_name: str, task_filename: str):
    """Add a task entry to the project's Tasks section."""
    if not project_path.exists():
        return

    content = project_path.read_text()
    task_entry = f"- [ ] [{format_title(task_name)}]({task_filename}.md)"

    # Find Tasks section and add entry
    if "## Tasks" in content:
        lines = content.split("\n")
        new_lines = []
        in_tasks = False
        added = False

        for line in lines:
            new_lines.append(line)
            if line.strip() == "## Tasks":
                in_tasks = True
            elif in_tasks and not added:
                # Skip comment line
                if line.strip().startswith("<!--"):
                    continue
                # Add task after section header (and any comment)
                new_lines.append(task_entry)
                added = True
                in_tasks = False

        if not added:
            # Tasks section exists but empty, append at end
            new_lines.append(task_entry)

        project_path.write_text("\n".join(new_lines)+"\n")
    else:
        # No Tasks section, add one
        content += f"\n## Tasks\n{task_entry}\n"
        project_path.write_text(content)


def parse_checklist_items(content: str) -> list[tuple[str, str, str]]:
    """Parse checklist items from markdown content.
    
    Extracts task names and their status from checklist items with any Cor status symbol.
    Uses STATUS_SYMBOLS from schema.py to recognize symbols.
    
    Args:
        content: Markdown content with checklist items
        
    Returns:
        List of tuples (task_name, status, task_text) extracted from checklist items
        Example: [('design_api', 'todo', 'Design API'), ('completed_task', 'done', 'Completed task')]
    """
    from .schema import STATUS_SYMBOLS
    
    # Build reverse mapping: symbol -> status
    symbol_to_status = {symbol: status for status, symbol in STATUS_SYMBOLS.items()}
    
    # Build regex pattern from STATUS_SYMBOLS to match any valid symbol
    # Extract the character inside brackets from each symbol
    symbol_chars = set()
    for symbol in STATUS_SYMBOLS.values():
        # Extract character between [ and ] (e.g., '[x]' -> 'x', '[ ]' -> ' ')
        char = symbol[1]
        symbol_chars.add(re.escape(char))
    
    # Build pattern: - [any_symbol_char] task text
    pattern = r'^\s*-\s+\[([' + ''.join(symbol_chars) + r'])\]\s+(.+)$'
    items = []
    
    for line in content.split('\n'):
        match = re.match(pattern, line)
        if match:
            symbol_char = match.group(1)
            task_text = match.group(2).strip()
            
            # Map symbol character back to status
            status = None
            for status_name, symbol in STATUS_SYMBOLS.items():
                if symbol[1] == symbol_char:
                    status = status_name
                    break
            
            if status is None:
                # Fallback to 'todo' if symbol not recognized
                status = 'todo'
            
            # Convert task text to slug
            task_slug = task_text.lower()
            # Replace spaces with underscores
            task_slug = re.sub(r'\s+', '_', task_slug)
            # Remove characters that are invalid in filenames or used as separators (dots)
            task_slug = re.sub(r'[/<>:"|?*\\.]+', '', task_slug)
            # Clean up multiple consecutive underscores and trim
            task_slug = re.sub(r'_+', '_', task_slug).strip('_')
            
            items.append((task_slug, status, task_text))
    
    return items


def remove_checklist_items(content: str) -> str:
    """Remove all checklist items from markdown content.
    
    Removes checklist items with any Cor status symbol.
    Uses STATUS_SYMBOLS from schema.py.
    
    Args:
        content: Markdown content with checklist items
        
    Returns:
        Content with checklist items removed
    """
    from .schema import STATUS_SYMBOLS
    
    # Build regex pattern from STATUS_SYMBOLS
    symbol_chars = set()
    for symbol in STATUS_SYMBOLS.values():
        char = symbol[1]
        symbol_chars.add(re.escape(char))
    
    pattern = r'^\s*-\s+\[([' + ''.join(symbol_chars) + r'])\]\s+.+$'
    lines = content.split('\n')
    filtered_lines = [line for line in lines if not re.match(pattern, line)]
    
    return '\n'.join(filtered_lines)


# --- Verbosity utilities ---

def log_info(message: str, min_level: int = 1) -> None:
    """Print info message if verbosity >= min_level.

    Args:
        message: Message to print
        min_level: Minimum verbosity level to show (default: 1 for normal output)
    """
    if get_verbosity() >= min_level:
        click.echo(message)


def log_verbose(message: str) -> None:
    """Print verbose message (verbosity level 2)."""
    log_info(message, min_level=2)


def log_debug(message: str) -> None:
    """Print debug message (verbosity level 3)."""
    log_info(message, min_level=3)


def log_error(message: str) -> None:
    """Print error message (always shown)."""
    click.secho(message, fg="red", err=True)


# Time keywords mapping for natural language date parsing
_TIME_KEYWORDS = {
    'morning': (9, 0),
    'noon': (12, 0),
    'afternoon': (14, 0),
    'evening': (18, 0),
    'night': (21, 0),
}

# Regex pattern to match time formats like "20pm", "20h", "20:00", "8pm", etc.
# Captures hour (1-24) with optional minute, and optional am/pm/h suffix
_TIME_PATTERN = re.compile(
    r'\b(\d{1,2})(?::(\d{2}))?(pm|am|h)?\b',
    re.IGNORECASE
)


def _extract_explicit_time(date_text: str) -> tuple[int, int] | None:
    """Extract explicit time from text like '20pm', '20h', '8pm', '14:30'.
    
    Handles edge cases where users mix 24-hour format with am/pm (e.g., '20pm')
    or use 'h' suffix (e.g., '20h'). Returns None if no valid time found.
    
    Skips relative time patterns like 'in 5h' which mean '5 hours from now'.
    
    Args:
        date_text: The date text to extract time from
        
    Returns:
        Tuple of (hour, minute) or None if no valid time found
        
    Examples:
        >>> _extract_explicit_time('friday 20pm')
        (20, 0)
        >>> _extract_explicit_time('tomorrow 8pm')
        (20, 0)
        >>> _extract_explicit_time('next week 14:30')
        (14, 30)
        >>> _extract_explicit_time('friday 20h')
        (20, 0)
        >>> _extract_explicit_time('in 5h')  # relative time, skip
        None
    """
    date_lower = date_text.lower()
    
    # Skip relative time patterns like "in 5h" (meaning "in 5 hours")
    # These should be handled by dateparser's parse() function
    if re.search(r'\bin\s+\d{1,2}h\b', date_lower):
        return None
    
    matches = _TIME_PATTERN.findall(date_text)
    
    for hour_str, minute_str, suffix in matches:
        hour = int(hour_str)
        minute = int(minute_str) if minute_str else 0
        suffix_lower = suffix.lower() if suffix else ''
        
        # Skip if hour is out of valid range
        if hour < 1 or hour > 24:
            continue
            
        # Handle am/pm suffix
        if suffix_lower in ('pm', 'am'):
            # Handle edge case: user wrote "20pm" (24h + pm suffix)
            # We interpret this as 20:00 (8pm) - the pm is redundant but clear
            if hour > 12:
                # Already 24-hour format, pm is redundant, use hour as-is
                pass
            elif suffix_lower == 'pm' and hour != 12:
                hour += 12
            elif suffix_lower == 'am' and hour == 12:
                hour = 0
        
        # 'h' suffix is just a marker (e.g., "20h" = 20:00)
        # No adjustment needed for 'h' suffix
        
        # Cap at 23:59
        if hour > 23:
            hour = 23
            
        return (hour, minute)
    
    return None


def _apply_time_keyword(date_text: str, parsed_date: datetime) -> datetime:
    """Apply time from keywords (morning, afternoon, etc.) or explicit time to a parsed date.
    
    Args:
        date_text: Original date text that was parsed
        parsed_date: The datetime returned by dateparser
        
    Returns:
        Datetime with time adjusted if a keyword or explicit time was found, otherwise original
    """
    date_lower = date_text.lower()
    
    # First check for explicit time patterns (e.g., "20pm", "8pm", "14:30")
    explicit_time = _extract_explicit_time(date_text)
    if explicit_time:
        hour, minute = explicit_time
        return parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    # Then check for time keywords
    for keyword, (hour, minute) in _TIME_KEYWORDS.items():
        # Use word boundary to avoid matching "noon" inside "afternoon"
        if re.search(r'\b' + keyword + r'\b', date_lower):
            return parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    return parsed_date


def parse_natural_language_text(text: str) -> tuple[str, datetime | None, list[str], str | None, str | None]:
    """Parse natural language text for due dates, tags, status, and priority.
    
    Detects:
    - Due dates: "due <date>" or "due: <date>" where <date> can be natural language
      Supports time precision: "due tomorrow 8pm", "due tomorrow morning", "due in 48h"
    - Tags: "tag <name>" or "tag: <name>" or multiple "tag <name1> <name2>"
    - Status: "mark <status>" or "status <status>" where status is a valid task status
    - Priority: "priority <level>" or "priority: <level>" where level is low, medium, or high
    
    Args:
        text: Input text potentially containing due dates, tags, status, and priority
        
    Returns:
        Tuple of (cleaned_text, due_date, tags, status, priority) where:
        - cleaned_text: text with due/tag/mark/priority specifications removed
        - due_date: parsed datetime object or None
        - tags: list of tag names
        - status: parsed status (todo, active, blocked, done, waiting, dropped) or None
        - priority: parsed priority (low, medium, high) or None
        
    Examples:
        >>> parse_natural_language_text("finish the pipeline due next friday")
        ('finish the pipeline', datetime(...), [], None, None)
        >>> parse_natural_language_text("fix bug tag urgent ml")
        ('fix bug', None, ['urgent', 'ml'], None, None)
        >>> parse_natural_language_text("complete task due tomorrow tag:urgent")
        ('complete task', datetime(...), ['urgent'], None, None)
        >>> parse_natural_language_text("review due tomorrow 8pm")
        ('review', datetime(...), [], None, None)  # Tomorrow at 20:00
        >>> parse_natural_language_text("submit due tomorrow morning")
        ('submit', datetime(...), [], None, None)  # Tomorrow at 09:00
        >>> parse_natural_language_text("start work mark active")
        ('start work', None, [], 'active', None)
        >>> parse_natural_language_text("important task priority high")
        ('important task', None, [], None, 'high')
    """
    from .schema import VALID_TASK_STATUS, VALID_PRIORITY
    
    if not text:
        return text, None, [], None, None
    
    due_date = None
    tags = []
    status = None
    priority = None
    cleaned_text = text
    
    # Pattern to match "due <date>" or "due: <date>"
    # Regex explanation:
    # - \bdue:?\s+ : Match "due" or "due:" followed by whitespace (word boundary before "due")
    # - (.+?) : Capture the date text (non-greedy)
    # - (?=\s+tag(?:\b|:)|\s+mark(?:\b|:)|\s+status(?:\b|:)|\s+priority(?:\b|:)|$) : Look ahead for keywords or end
    due_pattern = r'\bdue:?\s+(.+?)(?=\s+tag(?:\b|:)|\s+mark(?:\b|:)|\s+status(?:\b|:)|\s+priority(?:\b|:)|$)'
    due_match = re.search(due_pattern, cleaned_text, re.IGNORECASE)
    
    if due_match:
        due_text = due_match.group(1).strip()
        # Parse the date using dateparser's search_dates which is better at finding dates
        result = search_dates(
            due_text,
            settings={
                'PREFER_DATES_FROM': 'future',
                'RETURN_AS_TIMEZONE_AWARE': False,
            }
        )
        if result:
            # search_dates returns a list of tuples (date_string, datetime)
            # Take the first match
            due_date = result[0][1]
        else:
            # Fallback to parse for patterns search_dates misses (e.g., "in 5h")
            due_date = parse_date(
                due_text,
                settings={
                    'PREFER_DATES_FROM': 'future',
                    'RETURN_AS_TIMEZONE_AWARE': False,
                }
            )
        
        if due_date:
            # Apply time keywords (morning, afternoon, etc.) if present
            due_date = _apply_time_keyword(due_text, due_date)
            # Remove the entire due specification from text
            cleaned_text = cleaned_text[:due_match.start()] + cleaned_text[due_match.end():]
            cleaned_text = cleaned_text.strip()
    
    # Pattern to match "tag <tag1> <tag2> ..." or "tag: <tag1> <tag2> ..."
    # Regex explanation:
    # - \btag:?\s+ : Match "tag" or "tag:" followed by whitespace
    # - (.+?) : Capture the tag text (non-greedy)
    # - (?=\s+due|...|$) : Look ahead for other keywords or end of string
    tag_pattern = r'\btag:?\s+(.+?)(?=\s+due(?:\b|:)|\s+mark(?:\b|:)|\s+status(?:\b|:)|\s+priority(?:\b|:)|$)'
    tag_match = re.search(tag_pattern, cleaned_text, re.IGNORECASE)
    
    if tag_match:
        tag_text = tag_match.group(1).strip()
        # Split by spaces to get individual tags
        tags = [t.strip() for t in tag_text.split() if t.strip()]
        # Remove the entire tag specification from text
        cleaned_text = cleaned_text[:tag_match.start()] + cleaned_text[tag_match.end():]
        cleaned_text = cleaned_text.strip()
    
    # Pattern to match "mark <status>" or "status <status>" or "mark: <status>" or "status: <status>"
    # Valid statuses: todo, active, blocked, done, waiting, dropped
    status_pattern = r'\b(?:mark|status):?\s+(\w+)(?=\s+due(?:\b|:)|\s+tag(?:\b|:)|\s+priority(?:\b|:)|$)'
    status_match = re.search(status_pattern, cleaned_text, re.IGNORECASE)
    
    if status_match:
        potential_status = status_match.group(1).lower()
        if potential_status in VALID_TASK_STATUS:
            status = potential_status
            # Remove the entire mark/status specification from text
            cleaned_text = cleaned_text[:status_match.start()] + cleaned_text[status_match.end():]
            cleaned_text = cleaned_text.strip()
    
    # Pattern to match "priority <level>" or "priority: <level>"
    # Valid priorities: low, medium, high
    priority_pattern = r'\bpriority:?\s+(\w+)(?=\s+due(?:\b|:)|\s+tag(?:\b|:)|\s+mark(?:\b|:)|\s+status(?:\b|:)|$)'
    priority_match = re.search(priority_pattern, cleaned_text, re.IGNORECASE)
    
    if priority_match:
        potential_priority = priority_match.group(1).lower()
        if potential_priority in VALID_PRIORITY:
            priority = potential_priority
            # Remove the entire priority specification from text
            cleaned_text = cleaned_text[:priority_match.start()] + cleaned_text[priority_match.end():]
            cleaned_text = cleaned_text.strip()
    
    return cleaned_text, due_date, tags, status, priority


# --- Glob pattern utilities for bulk operations ---

def is_glob_pattern(pattern: str) -> bool:
    """Check if a string contains glob wildcards.
    
    Args:
        pattern: String to check
        
    Returns:
        True if pattern contains *, ?, or [] wildcards
    """
    return any(c in pattern for c in ['*', '?', '['])


def expand_glob_pattern(
    pattern: str, 
    notes_dir: Path, 
    include_archive: bool = False
) -> list[Path]:
    """Expand a glob pattern to matching file paths.
    
    Args:
        pattern: Glob pattern (e.g., "project.*.md")
        notes_dir: Path to notes directory
        include_archive: If True, also search archive directory
        
    Returns:
        List of matching Path objects (sorted)
    """
    matches = []
    
    # Ensure pattern has .md extension if not already present
    if not pattern.endswith('.md'):
        pattern = f"{pattern}.md"
    
    # Search active directory
    matches.extend(notes_dir.glob(pattern))
    
    # Search archive if requested
    if include_archive:
        archive_dir = notes_dir / "archive"
        if archive_dir.exists():
            matches.extend(archive_dir.glob(pattern))
    
    # Remove duplicates and sort
    seen = set()
    unique_matches = []
    for path in matches:
        if path not in seen:
            seen.add(path)
            unique_matches.append(path)
    
    return sorted(unique_matches)


def expand_glob_to_stems(
    pattern: str,
    notes_dir: Path,
    include_archive: bool = False
) -> list[tuple[str, bool]]:
    """Expand a glob pattern to matching file stems with archive status.
    
    Args:
        pattern: Glob pattern (e.g., "project.*.md")
        notes_dir: Path to notes directory
        include_archive: If True, also search archive directory
        
    Returns:
        List of (stem, is_archived) tuples
    """
    paths = expand_glob_pattern(pattern, notes_dir, include_archive)
    archive_dir = notes_dir / "archive"
    
    results = []
    for path in paths:
        is_archived = archive_dir in path.parents or path.parent == archive_dir
        results.append((path.stem, is_archived))
    
    return results


def compute_bulk_rename(
    source_pattern: str,
    target_pattern: str,
    notes_dir: Path,
    include_archive: bool = False
) -> list[tuple[Path, Path]]:
    """Compute bulk rename operations from source pattern to target pattern.
    
    The target pattern can contain wildcards that mirror the source wildcards.
    For example: "project.old-*.md" -> "project.new-*.md"
    
    Args:
        source_pattern: Source glob pattern (e.g., "project.old-*.md")
        target_pattern: Target pattern with wildcards (e.g., "project.new-*.md")
        notes_dir: Path to notes directory
        include_archive: If True, also search archive directory
        
    Returns:
        List of (source_path, target_path) tuples
        
    Raises:
        ValueError: If wildcards don't match between patterns
    """
    from fnmatch import fnmatch
    
    # Normalize patterns
    if not source_pattern.endswith('.md'):
        source_pattern = f"{source_pattern}.md"
    if not target_pattern.endswith('.md'):
        target_pattern = f"{target_pattern}.md"
    
    # Remove .md for wildcard analysis
    source_stem_pattern = source_pattern[:-3]
    target_stem_pattern = target_pattern[:-3]
    
    # Count wildcards in both patterns
    source_wildcards = source_stem_pattern.count('*')
    target_wildcards = target_stem_pattern.count('*')
    
    if source_wildcards != target_wildcards:
        raise ValueError(
            f"Wildcard mismatch: source has {source_wildcards} *, "
            f"target has {target_wildcards} *. "
            f"Patterns must have matching wildcards for bulk rename."
        )
    
    if source_wildcards == 0:
        raise ValueError(
            "No wildcards found in patterns. Use regular rename for single files."
        )
    
    # Find all matching source files
    source_paths = expand_glob_pattern(source_pattern, notes_dir, include_archive)
    
    if not source_paths:
        raise NotFoundError(f"No files match pattern: {source_pattern}")
    
    # Compute target paths for each source
    renames = []
    archive_dir = notes_dir / "archive"
    
    for src_path in source_paths:
        src_stem = src_path.stem
        
        # Match source stem against pattern to extract wildcard values
        # We use fnmatch to verify match, then compute the replacement
        if not fnmatch(src_stem, source_stem_pattern):
            continue
        
        # Compute target stem by replacing wildcards
        # Strategy: split patterns by * and reconstruct
        src_parts = source_stem_pattern.split('*')
        tgt_parts = target_stem_pattern.split('*')
        
        # Extract wildcard values from source stem
        values = []
        remaining = src_stem
        for i, part in enumerate(src_parts):
            if part:
                if i == 0:
                    # First part - remove prefix
                    if remaining.startswith(part):
                        remaining = remaining[len(part):]
                else:
                    # Middle/end parts - extract up to this part
                    idx = remaining.find(part)
                    if idx >= 0:
                        values.append(remaining[:idx])
                        remaining = remaining[idx + len(part):]
            elif i < len(src_parts) - 1:
                # Empty part means consecutive * or * at start
                # Find next non-empty part
                next_part = src_parts[i + 1] if i + 1 < len(src_parts) else ""
                if next_part:
                    idx = remaining.find(next_part)
                    if idx >= 0:
                        values.append(remaining[:idx])
                        remaining = remaining[idx:]
        
        # Handle trailing wildcard
        if src_parts[-1] == '' or (len(src_parts) > 1 and src_parts[-2] == ''):
            if remaining and (not values or remaining != values[-1]):
                values.append(remaining)
        
        # Build target stem
        tgt_stem = ""
        value_idx = 0
        for i, part in enumerate(tgt_parts):
            tgt_stem += part
            if i < len(tgt_parts) - 1 and value_idx < len(values):
                tgt_stem += values[value_idx]
                value_idx += 1
        
        # Determine target directory (same as source)
        tgt_dir = src_path.parent
        tgt_path = tgt_dir / f"{tgt_stem}.md"
        
        renames.append((src_path, tgt_path))
    
    return renames

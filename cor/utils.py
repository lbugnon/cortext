"""Utility functions for Cor CLI."""

import functools
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import click

# NOTE: `dateparser` is imported lazily inside parse_natural_language_text().
# It costs ~160ms to import and is only needed when parsing natural-language
# text, so keeping it out of module scope keeps `cor` startup fast.

from .config import get_vault_path, get_verbosity
from .exceptions import NotInitializedError, NotFoundError
from .schema import DATE_TIME


def require_init(f):
    """Decorator that ensures vault is initialized before running command."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        notes_dir = get_vault_path()
        if not (notes_dir / "backlog.md").exists():
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
        if p.stem != "backlog":
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

    # Idempotent: don't add a second entry if this task is already linked
    # (link may carry an archive/ or ../ prefix).
    if re.search(rf'\]\((?:archive/|\.\./)?{re.escape(task_filename)}\.md\)', content):
        return

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


_RELATIVE_TIME_PATTERN = re.compile(
    r'\bin\s+\d+\s*(h\b|hours?\b|min\b|minutes?\b|sec\b|seconds?\b)',
    re.IGNORECASE,
)


def _extract_explicit_time(date_text: str) -> tuple[int, int] | None:
    """Extract explicit time from text like '20pm', '20h', '8pm', '14:30'.

    Requires a suffix (am/pm/h) or an explicit minute (HH:MM) so that bare
    digits inside date-only strings (e.g. the '02' in '2026-02-15') are not
    mistaken for an hour. Returns None when no recognisable time is present.

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
        >>> _extract_explicit_time('2026-02-15')  # date only, no time
        None
    """
    # Skip relative time patterns like "in 5h" (handled by dateparser.parse).
    if _RELATIVE_TIME_PATTERN.search(date_text):
        return None

    for hour_str, minute_str, suffix in _TIME_PATTERN.findall(date_text):
        hour = int(hour_str)
        minute = int(minute_str) if minute_str else 0
        suffix_lower = suffix.lower() if suffix else ''

        if hour < 1 or hour > 24:
            continue

        # Require a clear time marker: an am/pm/h suffix or explicit minutes.
        # Otherwise bare digits (e.g. day numbers in '2026-02-15') would match.
        if not suffix_lower and not minute_str:
            continue

        if suffix_lower in ('pm', 'am'):
            if hour > 12:
                pass  # "20pm" → keep 20, the pm is redundant
            elif suffix_lower == 'pm' and hour != 12:
                hour += 12
            elif suffix_lower == 'am' and hour == 12:
                hour = 0

        if hour > 23:
            hour = 23

        return (hour, minute)

    return None


# Default deadline time when the user names only a day (today, tomorrow,
# friday, 2026-02-15…) without specifying a time — end of the working day in
# the user's local timezone.
DEFAULT_DEADLINE_HOUR = 19


def _apply_time_keyword(date_text: str, parsed_date: datetime) -> datetime:
    """Apply explicit time, time keyword, or default to end of workday.

    Priority order:
      1. Explicit time in the text (e.g. "8pm", "14:30", "20h").
      2. Time keyword (morning, noon, afternoon, evening, night).
      3. Relative time (e.g. "in 5h") — keep whatever dateparser computed.
      4. Otherwise the user gave a pure day reference, so set the deadline to
         DEFAULT_DEADLINE_HOUR:00 local time.
    """
    explicit_time = _extract_explicit_time(date_text)
    if explicit_time:
        hour, minute = explicit_time
        return parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    date_lower = date_text.lower()
    for keyword, (hour, minute) in _TIME_KEYWORDS.items():
        # Use word boundary to avoid matching "noon" inside "afternoon"
        if re.search(r'\b' + keyword + r'\b', date_lower):
            return parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if _RELATIVE_TIME_PATTERN.search(date_text):
        return parsed_date

    return parsed_date.replace(
        hour=DEFAULT_DEADLINE_HOUR, minute=0, second=0, microsecond=0
    )


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

    # Imported here rather than at module scope: dateparser costs ~160ms and
    # every `cor` invocation would otherwise pay it.
    from dateparser.search import search_dates
    from dateparser import parse as parse_date

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
        raw_due_text = due_match.group(1)
        due_text = raw_due_text.strip()
        matched_date_str: str | None = None
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
            matched_date_str, due_date = result[0]
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
            # Remove only "due <date>" from the text, preserving any trailing
            # description (e.g. "due tomorrow this is a text" → "this is a text").
            removal_end = due_match.end()
            if matched_date_str:
                pos = raw_due_text.lower().find(matched_date_str.lower())
                if pos >= 0:
                    removal_end = due_match.start(1) + pos + len(matched_date_str)
                    # Swallow a trailing time keyword or explicit-time suffix
                    # that search_dates didn't include in its match — otherwise
                    # "due tomorrow morning" would leave "morning" in the text.
                    tail = cleaned_text[removal_end:]
                    keyword_re = r'\s+(?:' + '|'.join(_TIME_KEYWORDS) + r')\b'
                    explicit_re = r'\s+\d{1,2}(?::\d{2}|(?:pm|am|h)\b)'
                    tail_match = re.match(
                        keyword_re + r'|' + explicit_re, tail, re.IGNORECASE
                    )
                    if tail_match:
                        removal_end += tail_match.end()
            cleaned_text = cleaned_text[:due_match.start()] + cleaned_text[removal_end:]
            cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
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

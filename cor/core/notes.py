"""Note models for Cor.

This module provides two note models:
- NoteMetadata: Lightweight metadata-only model for operations
- Note: Full model with content and computed properties for display

Use NoteMetadata for:
- File operations (rename, delete, move)
- Validation and sync operations
- Bulk operations where computed properties aren't needed

Use Note for:
- Display commands (daily, tree, weekly, status)
- Any operation needing is_overdue, is_stale, days_overdue, etc.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import frontmatter

from ..schema import DATE_TIME


@dataclass
class NoteMetadata:
    """Lightweight note metadata without computed properties.

    Use for: file operations, validation, sync operations.
    Fast to create, minimal overhead, no content parsing beyond frontmatter.
    """

    path: Path
    title: str
    note_type: str  # project, task, note
    status: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    due: Optional[date] = None
    priority: Optional[str] = None
    tags: list[str] = None
    requires: list[str] = None
    continues: list[str] = None
    related: list[str] = None

    def __post_init__(self):
        """Initialize default values for lists."""
        if self.tags is None:
            self.tags = []
        if self.requires is None:
            self.requires = []
        if self.continues is None:
            self.continues = []
        if self.related is None:
            self.related = []

    @classmethod
    def from_file(cls, path: Path) -> 'NoteMetadata':
        """Fast metadata-only parsing (no full content analysis).

        Args:
            path: Path to note file

        Returns:
            NoteMetadata instance
        """
        post = frontmatter.load(path)
        meta = post.metadata

        # Extract title from first heading (fast scan, limit to first 10 lines)
        title = path.stem
        for line in post.content.split("\n", 10):
            if line.startswith("# "):
                title = line[2:].strip()
                break

        return cls(
            path=path,
            title=title,
            note_type=meta.get("type"),
            status=meta.get("status"),
            created=_parse_date(meta.get("created")),
            modified=_parse_date(meta.get("modified")),
            due=_parse_date(meta.get("due")),
            priority=meta.get("priority"),
            tags=_as_list(meta.get("tags")),
            requires=_as_list(meta.get("requires")),
            continues=_as_list(meta.get("continues")),
            related=_as_list(meta.get("related")),
        )

    def to_dict(self) -> dict:
        """Convert to dict for frontmatter.

        Returns:
            Dictionary of metadata fields
        """
        return {
            "type": self.note_type,
            "status": self.status,
            "created": self.created.strftime(DATE_TIME) if self.created else None,
            "modified": self.modified.strftime(DATE_TIME) if self.modified else None,
            "due": self.due.strftime("%Y-%m-%d") if self.due else None,
            "priority": self.priority,
            "tags": self.tags,
            "requires": self.requires,
            "continues": self.continues,
            "related": self.related,
        }

    @property
    def parent_project(self) -> str | None:
        """Extract root project name from path.

        Examples:
            'project' from 'project.md'
            'project' from 'project.task.md'
            'project' from 'project.group.task.md'

        Returns:
            Root project name or None
        """
        from ..utils import get_root_project
        return get_root_project(self.path.stem)


@dataclass
class Note(NoteMetadata):
    """Full note model with computed properties and content.

    Use for: display commands (daily, tree, weekly), status views.
    Inherits from NoteMetadata, adds:
    - Full content
    - Computed properties (is_overdue, is_stale, days_overdue)

    This is the model to use when you need rich display information.
    """

    content: str = ""

    @property
    def is_overdue(self) -> bool:
        """Check if task/project is past its due date.

        Returns:
            True if overdue, False otherwise
        """
        if not self.due or self.status == "done":
            return False
        due_date = self.due if isinstance(self.due, date) and not isinstance(self.due, datetime) else self.due.date() if hasattr(self.due, 'date') else self.due
        return due_date < date.today()

    @property
    def is_due_this_week(self) -> bool:
        """Check if task/project is due within the next 7 days.

        Returns:
            True if due this week, False otherwise
        """
        if not self.due or self.status == "done":
            return False
        due_date = self.due if isinstance(self.due, date) and not isinstance(self.due, datetime) else self.due.date() if hasattr(self.due, 'date') else self.due
        days_until = (due_date - date.today()).days
        return 0 <= days_until <= 7

    @property
    def is_stale(self) -> bool:
        """Check if note hasn't been modified in over 14 days.

        Returns:
            True if stale, False otherwise
        """
        if not self.modified or self.status in ("done", "paused", "complete"):
            return False
        days_since = (datetime.today() - self.modified).days
        return days_since > 14

    @property
    def days_overdue(self) -> int:
        """Number of days past due.

        Returns:
            Number of days overdue, 0 if not overdue
        """
        if not self.is_overdue:
            return 0
        due_date = self.due if isinstance(self.due, date) and not isinstance(self.due, datetime) else self.due.date() if hasattr(self.due, 'date') else self.due
        return (date.today() - due_date).days

    @property
    def days_since_modified(self) -> int:
        """Days since last modification.

        Returns:
            Number of days since last modification
        """
        if not self.modified:
            return 0
        return (datetime.now() - self.modified).days

    @classmethod
    def from_file(cls, path: Path) -> 'Note':
        """Full parsing with content (slower than NoteMetadata).

        Args:
            path: Path to note file

        Returns:
            Note instance with full content
        """
        # First get metadata using parent class
        metadata = NoteMetadata.from_file(path)

        # Load content
        post = frontmatter.load(path)

        # Create Note with all metadata fields plus content
        return cls(
            path=metadata.path,
            title=metadata.title,
            note_type=metadata.note_type,
            status=metadata.status,
            created=metadata.created,
            modified=metadata.modified,
            due=metadata.due,
            priority=metadata.priority,
            tags=metadata.tags,
            requires=metadata.requires,
            continues=metadata.continues,
            related=metadata.related,
            content=post.content
        )


def _as_list(value) -> list[str]:
    """Coerce a frontmatter list field to a list of strings.

    These fields are hand-editable, so `related: myproject` (a bare string) is
    at least as likely as `related: [myproject]`. Without coercion a string
    would still support `in`, but as a substring test - `"proj" in "myproject"`
    is True - which silently corrupts membership checks.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _parse_date(value) -> Optional[datetime]:
    """Parse date from frontmatter.

    Supports datetime objects, date objects, and string formats.
    Date-only strings (YYYY-MM-DD) are parsed as datetime at midnight.
    Datetime strings (YYYY-MM-DD HH:MM) preserve the time component.

    Args:
        value: Date value from frontmatter

    Returns:
        datetime object or None
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        # Try datetime format first (YYYY-MM-DD HH:MM)
        return datetime.strptime(value, DATE_TIME)
    except (ValueError, TypeError):
        pass
    try:
        # Fall back to date-only format (YYYY-MM-DD)
        return datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _date_has_time(value: Optional[datetime]) -> bool:
    """Check if a datetime value has a specific time (not just midnight).
    
    Used to determine if calendar events should be all-day or timed events.
    
    Args:
        value: datetime value to check
        
    Returns:
        True if the value has a non-midnight time, False otherwise
    """
    if value is None:
        return False
    return value.hour != 0 or value.minute != 0


def parse_note(path: Path, metadata_only: bool = False):
    """Parse a note from file.

    Args:
        path: Path to note file
        metadata_only: If True, return NoteMetadata (fast).
                      If False, return Note (full, slower).

    Returns:
        NoteMetadata or Note instance
    """
    if metadata_only:
        return NoteMetadata.from_file(path)
    else:
        return Note.from_file(path)


def parse_metadata(path: Path) -> NoteMetadata:
    """Parse metadata only (fast) - for sync/validation operations.

    Args:
        path: Path to note file

    Returns:
        NoteMetadata instance
    """
    return NoteMetadata.from_file(path)


def find_notes(notes_dir: Path, metadata_only: bool = False) -> list:
    """Find and parse all notes.

    Args:
        notes_dir: Notes directory path
        metadata_only: If True, return list[NoteMetadata] (fast).
                      If False, return list[Note] (full, slower).

    Returns:
        List of NoteMetadata or Note instances
    """
    notes = []
    for path in notes_dir.glob("*.md"):
        # Skip hidden files and special files
        if path.name.startswith(".") or path.stem == "backlog":
            continue
        try:
            if metadata_only:
                notes.append(NoteMetadata.from_file(path))
            else:
                notes.append(Note.from_file(path))
        except Exception as e:
            print(f"Warning: Could not parse {path}: {e}")

    return notes

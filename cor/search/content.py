"""Content search using ripgrep for fast full-text search."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..exceptions import NotFoundError
from ..utils import get_notes_dir
from ..core.files import is_repo_doc
from datetime import date
import re


@dataclass
class SearchMatch:
    """A single search match result."""

    file: Path
    line: int
    content: str
    context_before: list[str]
    context_after: list[str]


def _run_ripgrep(
    query: str,
    notes_dir: Path,
    include_archived: bool = False,
    context_lines: int = 2,
) -> Iterator[dict]:
    """Run ripgrep and yield JSON result objects.

    Args:
        query: Search query string
        notes_dir: Root directory to search
        include_archived: Whether to include archive/ directory
        context_lines: Number of context lines before/after match

    Yields:
        Parsed JSON objects from ripgrep --json output
    """
    cmd = [
        "rg",
        "--json",
        "-i",  # Case-insensitive
        "-C",
        str(context_lines),
        "--type",
        "md",  # Only search markdown files
    ]

    # Build search paths and exclusions
    if not include_archived:
        # Exclude archive directory. The leading `**/` is required: notes_dir is
        # passed to rg as an absolute path, so candidates look like
        # /home/user/notes/archive/foo.md and a bare `archive/**` (which anchors
        # at the start of the path) would never match.
        cmd.extend(["--glob", "!**/archive/**"])

    # Always search notes_dir (which includes archive if include_archived=True)
    cmd.extend([query, str(notes_dir)])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise NotFoundError(
            "ripgrep (rg) not found. Install it:\n"
            "  apt-get install ripgrep  (Ubuntu/Debian)\n"
            "  brew install ripgrep     (macOS)\n"
            "  conda install -c conda-forge ripgrep"
        )

    # ripgrep returns exit code 1 when no matches found (not an error)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"ripgrep failed: {result.stderr}")

    for line in result.stdout.strip().split("\n"):
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def search_content(
    query: str,
    include_archived: bool = False,
    limit: int = 50,
    context_lines: int = 2,
) -> list[SearchMatch]:
    """Search note content using ripgrep.

    Args:
        query: Search query string
        include_archived: Whether to include archived files
        limit: Maximum number of results to return
        context_lines: Number of context lines before/after each match

    Returns:
        List of SearchMatch objects sorted by file and line number

    Raises:
        NotFoundError: If ripgrep is not installed
    """
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        return []

    matches = []
    current_match: SearchMatch | None = None
    match_count = 0

    for event in _run_ripgrep(query, notes_dir, include_archived, context_lines):
        event_type = event.get("type")

        if event_type == "match":
            # Start a new match
            if current_match and match_count < limit:
                matches.append(current_match)
                match_count += 1

            if match_count >= limit:
                break

            data = event.get("data", {})
            path_data = data.get("path", {})
            file_path = Path(path_data.get("text", ""))

            # Get the matched line content
            lines = data.get("lines", {})
            content = lines.get("text", "").rstrip("\n")

            # Get line number
            line_num = data.get("line_number", 0)

            current_match = SearchMatch(
                file=file_path,
                line=line_num,
                content=content,
                context_before=[],
                context_after=[],
            )

        elif event_type == "context" and current_match is not None:
            data = event.get("data", {})
            context_type = data.get("context_type")  # "before" or "after"
            lines = data.get("lines", {})
            content = lines.get("text", "").rstrip("\n")

            if context_type == "before":
                current_match.context_before.append(content)
            elif context_type == "after":
                current_match.context_after.append(content)

    # Don't forget the last match
    if current_match and match_count < limit:
        matches.append(current_match)

    return matches


def parse_search_query(query: str) -> tuple[str, dict]:
    """Parse a search query to extract filters.

    Supported filters:
        - status:value (e.g., status:active)
        - #tag (e.g., #urgent)
        - project:name (e.g., project:foundation_model)
        - type:value (project, task, or note)
        - priority:value (low, medium, high, or none for unset)
        - due:spec (overdue, today, week, none, YYYY-MM-DD, <=YYYY-MM-DD, >=YYYY-MM-DD)

    Args:
        query: Raw search query string

    Returns:
        Tuple of (clean_query, filters_dict)
    """
    filters = {}
    clean_parts = []

    for part in query.split():
        if part.startswith("status:"):
            filters["status"] = part[7:]
        elif part.startswith("#") and len(part) > 1:
            filters.setdefault("tags", []).append(part[1:])
        elif part.startswith("project:"):
            filters["project"] = part[8:]
        elif part.startswith("type:"):
            filters["type"] = part[5:]
        elif part.startswith("priority:"):
            filters["priority"] = part[9:]
        elif part.startswith("due:"):
            filters["due"] = part[4:]
        else:
            clean_parts.append(part)

    return " ".join(clean_parts), filters


DUE_KEYWORDS = ("overdue", "today", "week", "none")
_DUE_SPEC = re.compile(r"^(<=|>=)?(\d{4}-\d{2}-\d{2})$")


def validate_filters(filters: dict) -> None:
    """Reject filter values that can never match, with a message that lists the options."""
    from ..exceptions import ValidationError
    from ..schema import VALID_PRIORITY

    priority = filters.get("priority")
    if priority is not None and priority not in VALID_PRIORITY | {"none"}:
        options = ", ".join(sorted(VALID_PRIORITY)) + ", none"
        raise ValidationError(f"Invalid priority filter '{priority}'. Use one of: {options}")

    due = filters.get("due")
    if due is not None and due not in DUE_KEYWORDS:
        match = _DUE_SPEC.match(due)
        try:
            if not match:
                raise ValueError
            date.fromisoformat(match.group(2))
        except ValueError:
            raise ValidationError(
                f"Invalid due filter '{due}'. Use overdue, today, week, none, "
                "YYYY-MM-DD, <=YYYY-MM-DD or >=YYYY-MM-DD"
            ) from None


def _due_matches(note, spec: str, today: date) -> bool:
    """Evaluate one ``due:`` filter against a note."""
    from ..core.notes import CLOSED_STATUSES

    due = note.due_date
    if spec == "none":
        return due is None
    if due is None:
        return False
    days = (due - today).days
    if spec == "overdue":
        return days < 0 and note.status not in CLOSED_STATUSES
    if spec == "today":
        return days == 0
    if spec == "week":
        return 0 <= days <= 7
    match = _DUE_SPEC.match(spec)
    if not match:
        return False
    operator, raw = match.groups()
    bound = date.fromisoformat(raw)
    if operator == "<=":
        return due <= bound
    if operator == ">=":
        return due >= bound
    return due == bound


def note_record(note) -> dict:
    """The JSON record for one note, shared by every ``cor search --json`` path.

    Dates use the vault's own frontmatter formats so an agent can copy a value
    straight back into a ``cor batch`` manifest.
    """
    from ..core.notes import due_to_iso
    from ..schema import DATE_TIME

    stem = note.path.stem
    return {
        "stem": stem,
        "title": note.title,
        "type": note.note_type,
        "status": note.status,
        "archived": "archive" in note.path.parts,
        "priority": note.priority,
        "due": due_to_iso(note.due),
        "due_has_time": note.due_has_time,
        "days_until_due": note.days_until_due(),
        "created": note.created.strftime(DATE_TIME) if note.created else None,
        "modified": note.modified.strftime(DATE_TIME) if note.modified else None,
        "tags": list(note.tags or []),
        "requires": list(note.requires or []),
        "parent": stem.rsplit(".", 1)[0] if "." in stem else None,
        "project": note.parent_project,
    }


def note_matches_filters(note, filters: dict, *, today: date | None = None) -> bool:
    """Check one note's metadata against parsed search filters.

    ``today`` pins the reference day for ``due:`` filters (tests); it defaults
    to the current date.

    Shared by the ripgrep path (`filter_matches`) and the filter-only listing
    path in `cli/search_cmd.py`, which each had their own copy of these three
    checks. Two copies of "what does status:/#tag/project: mean" is one copy too
    many - they can answer the same query differently.
    """
    if "status" in filters and note.status != filters["status"]:
        return False

    if "tags" in filters:
        if not set(filters["tags"]).issubset(set(note.tags or [])):
            return False

    if "project" in filters:
        # Either a descendant (project.task.md) or the project file itself.
        project = filters["project"]
        stem = note.path.stem
        if not (stem == project or stem.startswith(f"{project}.")):
            return False

    if "type" in filters and note.note_type != filters["type"]:
        return False

    if "priority" in filters:
        wanted = filters["priority"]
        if wanted == "none":
            if note.priority:
                return False
        elif note.priority != wanted:
            return False

    if "due" in filters and not _due_matches(note, filters["due"], today or date.today()):
        return False

    return True


def list_notes(
    notes_dir: Path,
    filters: dict,
    *,
    include_archived: bool = False,
    today: date | None = None,
) -> list:
    """Return metadata records matching structured filters."""
    from ..core.notes import NoteMetadata

    paths = sorted(notes_dir.glob("*.md"))
    if include_archived:
        paths += sorted((notes_dir / "archive").glob("*.md"))
    results = []
    for path in paths:
        if path.name.startswith(".") or path.stem == "backlog" or is_repo_doc(path):
            continue
        try:
            note = NoteMetadata.from_file(path)
        except Exception:
            continue
        if note_matches_filters(note, filters, today=today):
            results.append(note)
    return results


def filter_matches(
    matches: list[SearchMatch],
    filters: dict,
    today: date | None = None,
) -> list[SearchMatch]:
    """Filter search matches based on metadata filters.

    This reads the YAML frontmatter of each file and filters accordingly.

    Args:
        matches: List of SearchMatch objects
        filters: Dict with keys like 'status', 'tags', 'project'

    Returns:
        Filtered list of matches
    """
    if not filters:
        return matches

    from ..core.notes import parse_metadata

    filtered = []
    checked_files = {}  # Cache parsed metadata per file

    for match in matches:
        file_path = match.file

        # Cache metadata parsing per file
        if file_path not in checked_files:
            try:
                note = parse_metadata(file_path)
                checked_files[file_path] = note
            except Exception:
                checked_files[file_path] = None
                continue

        note = checked_files[file_path]
        if note is None:
            continue

        if note_matches_filters(note, filters, today=today):
            filtered.append(match)

    return filtered

"""Content search using ripgrep for fast full-text search."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..exceptions import NotFoundError
from ..utils import get_notes_dir


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
        else:
            clean_parts.append(part)

    return " ".join(clean_parts), filters


def filter_matches(
    matches: list[SearchMatch],
    filters: dict,
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

        # Apply filters
        match_ok = True

        if "status" in filters and note.status != filters["status"]:
            match_ok = False

        if "tags" in filters and note.tags:
            note_tags = set(note.tags)
            required_tags = set(filters["tags"])
            if not required_tags.issubset(note_tags):
                match_ok = False
        elif "tags" in filters and not note.tags:
            match_ok = False

        if "project" in filters:
            # Check if file belongs to project: either a descendant
            # (project.task.md) or the project file itself (project.md).
            project = filters["project"]
            stem = match.file.stem
            if not (stem == project or stem.startswith(f"{project}.")):
                match_ok = False

        if match_ok:
            filtered.append(match)

    return filtered

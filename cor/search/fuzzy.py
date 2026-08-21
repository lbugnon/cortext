"""Fuzzy search and interactive picker for Cor CLI."""

import sys
from pathlib import Path
from typing import Optional

import click

# NOTE: `rapidfuzz` (~12ms) and `simple_term_menu` (~9ms) are imported lazily
# inside the two functions that need them. Neither is needed when a name
# resolves exactly, which is the common case, so paying for them at import
# time taxed every `cor` invocation - including each shell-completion Tab.
from ..exceptions import NotFoundError
from ..utils import get_notes_dir


def get_all_file_stems(include_archived: bool = False) -> list[tuple[str, bool]]:
    """Get all file stems with archive status.

    Returns list of (stem, is_archived) tuples.
    """
    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        return []

    results = []

    # Main directory files
    for path in notes_dir.glob("*.md"):
        if path.stem != "backlog":
            results.append((path.stem, False))

    # Archived files
    if include_archived:
        archive_dir = notes_dir / "archive"
        if archive_dir.exists():
            for path in archive_dir.glob("*.md"):
                results.append((path.stem, True))

    return results


def fuzzy_match(
    query: str,
    candidates: list[tuple[str, bool]],
    limit: int = 10,
    score_cutoff: int = 50,
    focused_project: str | None = None,
) -> list[tuple[str, bool, int]]:
    """Find fuzzy matches for query against candidates.

    Args:
        query: Search string
        candidates: List of (stem, is_archived) tuples
        limit: Maximum results to return
        score_cutoff: Minimum score (0-100) to include
        focused_project: If provided, boost scores for matches in this project

    Returns:
        List of (stem, is_archived, score) sorted by score descending, then by length ascending
    """
    if not candidates:
        return []

    # Extract just stems for matching
    stems = [c[0] for c in candidates]

    from rapidfuzz import fuzz, process

    # Use partial_ratio for good substring matching in filenames
    results = process.extract(
        query,
        stems,
        scorer=fuzz.partial_ratio,
        limit=limit,
        score_cutoff=score_cutoff,
    )

    # Map back to full tuples with scores
    stem_to_archived = {c[0]: c[1] for c in candidates}
    matches = [(match[0], stem_to_archived[match[0]], int(match[1])) for match in results]
    
    # Boost scores for focused project matches
    if focused_project:
        boosted = []
        for stem, is_archived, score in matches:
            # Check if this file belongs to the focused project
            # Projects are the first part before any dot
            parts = stem.split(".")
            if parts[0] == focused_project:
                # Boost score by 20 points, cap at 100
                score = min(100, score + 20)
            boosted.append((stem, is_archived, score))
        matches = boosted
    
    # Sort by score (descending), then by length (ascending) for ties
    # Focused project matches will now appear first due to boosted scores
    matches.sort(key=lambda x: (-x[2], len(x[0])))
    
    return matches


def show_picker(
    matches: list[tuple[str, bool, int]], query: str
) -> Optional[tuple[str, bool]]:
    """Show interactive picker for fuzzy matches.

    Args:
        matches: List of (stem, is_archived, score) tuples
        query: Original query (for display)

    Returns:
        Selected (stem, is_archived) or None if cancelled
    """
    # Build menu options with scores
    options = []
    for stem, is_archived, score in matches:
        suffix = " (archived)" if is_archived else ""
        options.append(f"{stem}{suffix}  [{score}%]")

    # Add cancel option
    options.append("[Cancel]")

    click.echo(f"\nMultiple matches for '{query}':")

    from simple_term_menu import TerminalMenu

    menu = TerminalMenu(
        options,
        title="Select file (arrows to navigate, Enter to confirm, q to cancel):",
        menu_cursor_style=("fg_cyan", "bold"),
        menu_highlight_style=("bg_cyan", "fg_black"),
    )

    choice = menu.show()

    if choice is None or choice == len(options) - 1:
        # Cancelled or selected [Cancel]
        return None

    stem, is_archived, _ = matches[choice]
    return (stem, is_archived)


def _pick_or_best_match(matches, name: str, auto_select_threshold: int):
    """Resolve ambiguity: ask a human if one is present, otherwise be strict.

    Non-interactive callers (scripts, git hooks, the nvim plugin) have nobody to
    confirm with, so a weak guess must not be acted on. This used to return
    matches[0] regardless of score, silently: with partial_ratio and
    score_cutoff=50 a short query scores ~50 against almost anything, so
    `cor rename old-a renamed-a` would rename an unrelated `via-flag` project
    and report success. Destructive and invisible. Require real confidence, or
    fail loudly.
    """
    if not sys.stdin.isatty():
        best_stem, best_archived, best_score = matches[0]
        if best_score < auto_select_threshold:
            options = ", ".join(f"{s} ({sc}%)" for s, _, sc in matches[:5])
            raise NotFoundError(
                f"No confident match for '{name}' (best: {best_stem} at {best_score}%). "
                f"Candidates: {options}. "
                f"Use the exact name, or run interactively to pick from a list."
            )
        if len(matches) > 1:
            click.echo(
                f"Warning: Multiple matches found, using best: {best_stem}",
                err=True,
            )
        return (best_stem, best_archived)

    return show_picker(matches, name)


def resolve_file_fuzzy(
    name: str,
    include_archived: bool = False,
    auto_select_threshold: int = 95,
    focused_project: str | None = None,
) -> Optional[tuple[str, bool]]:
    """Resolve a file name using fuzzy matching with interactive picker.

    Args:
        name: User-provided file name (possibly partial/fuzzy)
        include_archived: Whether to search archived files
        auto_select_threshold: Score above which to auto-select single match
        focused_project: If provided, prioritize matches in this project

    Returns:
        Tuple of (stem, is_archived) or None if cancelled/no match

    Raises:
        NotFoundError: If no matches found
    """
    notes_dir = get_notes_dir()

    # 1. Check for exact match first
    exact_path = notes_dir / f"{name}.md"
    if exact_path.exists():
        return (name, False)

    if include_archived:
        archive_path = notes_dir / "archive" / f"{name}.md"
        if archive_path.exists():
            return (name, True)

    # 2. Get candidates and run fuzzy search
    candidates = get_all_file_stems(include_archived)
    matches = fuzzy_match(name, candidates, focused_project=focused_project)

    if not matches:
        raise NotFoundError(f"No files found matching '{name}'")

    # 3. Single high-confidence match: auto-select
    if len(matches) == 1 and matches[0][2] >= auto_select_threshold:
        stem, is_archived, score = matches[0]
        click.echo(f"Auto-selected: {stem}" + (" (archived)" if is_archived else ""))
        return (stem, is_archived)

    # 4/5. Ambiguous: ask a human, or be strict when there isn't one.
    return _pick_or_best_match(matches, name, auto_select_threshold)


def get_file_path(stem: str, is_archived: bool) -> Path:
    """Convert (stem, is_archived) to full file path."""
    notes_dir = get_notes_dir()
    if is_archived:
        return notes_dir / "archive" / f"{stem}.md"
    return notes_dir / f"{stem}.md"


def get_task_file_stems(include_archived: bool = False) -> list[tuple[str, bool]]:
    """Get file stems for tasks only (type: task in frontmatter).

    Returns list of (stem, is_archived) tuples.
    """
    from ..core.notes import parse_metadata

    notes_dir = get_notes_dir()
    if not notes_dir.exists():
        return []

    results = []

    # Main directory files
    for path in notes_dir.glob("*.md"):
        if path.stem == "backlog":
            continue
        note = parse_metadata(path)
        if note and note.note_type == "task":
            results.append((path.stem, False))

    # Archived files
    if include_archived:
        archive_dir = notes_dir / "archive"
        if archive_dir.exists():
            for path in archive_dir.glob("*.md"):
                note = parse_metadata(path)
                if note and note.note_type == "task":
                    results.append((path.stem, True))

    return results


def resolve_task_fuzzy(
    name: str,
    include_archived: bool = False,
    auto_select_threshold: int = 95,
    focused_project: str | None = None,
) -> Optional[tuple[str, bool]]:
    """Resolve a task name using fuzzy matching with interactive picker.

    Only searches files with type: task in frontmatter.

    Args:
        name: User-provided task name (possibly partial/fuzzy)
        include_archived: Whether to search archived tasks
        auto_select_threshold: Score above which to auto-select single match
        focused_project: If provided, prioritize matches in this project

    Returns:
        Tuple of (stem, is_archived) or None if cancelled/no match

    Raises:
        NotFoundError: If no matches found
    """
    from ..core.notes import parse_metadata

    notes_dir = get_notes_dir()

    # 1. Check for exact match first (must be a task)
    exact_path = notes_dir / f"{name}.md"
    if exact_path.exists():
        note = parse_metadata(exact_path)
        if note and note.note_type == "task":
            return (name, False)

    if include_archived:
        archive_path = notes_dir / "archive" / f"{name}.md"
        if archive_path.exists():
            note = parse_metadata(archive_path)
            if note and note.note_type == "task":
                return (name, True)

    # 2. Get task candidates and run fuzzy search
    candidates = get_task_file_stems(include_archived)
    matches = fuzzy_match(name, candidates, focused_project=focused_project)

    if not matches:
        raise NotFoundError(f"No tasks found matching '{name}'")

    # 3. Single high-confidence match: auto-select
    if len(matches) == 1 and matches[0][2] >= auto_select_threshold:
        stem, is_archived, score = matches[0]
        click.echo(f"Auto-selected: {stem}" + (" (archived)" if is_archived else ""))
        return (stem, is_archived)

    # 4/5. Ambiguous: ask a human, or be strict when there isn't one.
    return _pick_or_best_match(matches, name, auto_select_threshold)


def resolve_files(
    name: str,
    include_archived: bool = False,
    focused_project: str | None = None,
    note_type: str | None = None,
) -> list[tuple[str, bool]]:
    """Resolve a name to one or more files for bulk-capable commands.

    If `name` is a glob pattern (contains *, ?, or []), expands it against the
    notes directory, optionally filters by note_type, prints the matches, and
    prompts the user for confirmation. Otherwise falls back to fuzzy single-file
    resolution.

    Glob patterns must be quoted by the user (e.g. cor tag "projects_*" tag1)
    to prevent shell expansion.

    Args:
        name: Pattern or fuzzy name
        include_archived: Include archive/ files
        focused_project: Boost scores for matches in this project (single-file only)
        note_type: If set ("task", "project", "note"), filter pattern matches

    Returns:
        List of (stem, is_archived). Empty list if user cancels or no matches.
    """
    from ..utils import is_glob_pattern, expand_glob_to_stems
    from ..core.notes import parse_metadata

    if not is_glob_pattern(name):
        result = resolve_file_fuzzy(
            name,
            include_archived=include_archived,
            focused_project=focused_project,
        )
        return [result] if result else []

    matches = expand_glob_to_stems(name, get_notes_dir(), include_archived)

    if note_type:
        filtered = []
        for stem, is_archived in matches:
            file_path = get_file_path(stem, is_archived)
            note = parse_metadata(file_path)
            if note and note.note_type == note_type:
                filtered.append((stem, is_archived))
        matches = filtered

    if not matches:
        raise NotFoundError(f"No files match pattern: {name}")

    click.echo(f"\n{len(matches)} file(s) match '{name}':")
    for stem, is_archived in matches:
        suffix = " (archived)" if is_archived else ""
        click.echo(f"  - {stem}{suffix}")

    if sys.stdin.isatty():
        if not click.confirm("Apply to all?", default=False):
            click.echo("Cancelled.")
            return []
    else:
        click.echo("Non-interactive mode: proceeding.")

    return matches

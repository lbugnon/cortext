"""File iteration and I/O operations for Cor.

This module consolidates file iteration patterns that were repeated 20+ times
across the codebase with inconsistent approaches.
"""

from pathlib import Path
from typing import Iterator
import frontmatter

from .storage import atomic_write_post


def load_note(filepath: str | Path) -> frontmatter.Post | None:
    """Load a note file and return its frontmatter Post, or None on failure.

    The canonical implementation. There used to be three spellings of these two
    operations: module-level copies in `sync/runner.py` and methods on a
    `NoteFileManager` class that nothing in production ever constructed.
    """
    path = Path(filepath)
    if not path.exists():
        return None
    try:
        return frontmatter.load(path)
    except Exception:
        return None


def save_note(filepath: str | Path, post: frontmatter.Post) -> None:
    """Write a frontmatter Post back to disk, preserving key order."""
    atomic_write_post(filepath, post)

def _is_top_level_note(path: Path) -> bool:
    """Return True if ``path`` is a note file (``type: note``) at the top level.

    Files without parseable frontmatter or without a type field are treated as
    projects (back-compat with files predating the explicit type marker).
    """
    try:
        post = frontmatter.load(path)
    except Exception:
        return False
    return post.metadata.get("type") == "note"


class FileIterator:
    """Consistent file iteration patterns for notes."""

    # Files to exclude from iteration
    EXCLUDED_STEMS = {"backlog"}

    def __init__(self, notes_dir: Path):
        """Initialize file iterator.

        Args:
            notes_dir: Path to notes directory
        """
        self.notes_dir = notes_dir
        self.archive_dir = notes_dir / "archive"

    def iter_all_notes(self, include_archive: bool = False,
                      exclude_special: bool = True) -> Iterator[Path]:
        """Iterate all note files.

        Args:
            include_archive: If True, include archived files
            exclude_special: If True, exclude backlog

        Yields:
            Path objects for each note file
        """
        # Active notes
        for path in self.notes_dir.glob("*.md"):
            if path.name.startswith("."):
                continue
            if exclude_special and path.stem in self.EXCLUDED_STEMS:
                continue
            yield path

        # Archive if requested
        if include_archive and self.archive_dir.exists():
            for path in self.archive_dir.glob("*.md"):
                if path.name.startswith("."):
                    continue
                yield path

    def iter_projects(self, include_archive: bool = False) -> Iterator[Path]:
        """Iterate project files (no dots in name).

        Top-level notes (stem without dots but ``type: note`` in frontmatter)
        are excluded — they look structurally like projects but represent
        knowledge, not tracked work.

        Args:
            include_archive: If True, include archived projects

        Yields:
            Path objects for each project file
        """
        for path in self.iter_all_notes(include_archive=include_archive):
            if "." in path.stem:
                continue
            if _is_top_level_note(path):
                continue
            yield path

    def iter_tasks_for_project(self, project: str,
                               include_archive: bool = False,
                               direct_only: bool = True) -> Iterator[Path]:
        """Iterate tasks belonging to a project.

        Args:
            project: Project stem name
            include_archive: If True, include archived tasks
            direct_only: If True, only direct children (project.task),
                        if False, include all descendants (project.task.subtask)

        Yields:
            Path objects for each task
        """
        pattern = f"{project}.*.md"

        for search_dir in [self.notes_dir] + ([self.archive_dir] if include_archive and self.archive_dir.exists() else []):
            for path in search_dir.glob(pattern):
                if direct_only:
                    # Only direct children: project.task (exactly 2 parts)
                    parts = path.stem.split(".")
                    if len(parts) == 2:
                        yield path
                else:
                    # All descendants
                    yield path

    def iter_children(self, parent_stem: str,
                     include_archive: bool = True) -> Iterator[Path]:
        """Iterate all children (direct + nested) of a parent.

        Args:
            parent_stem: Parent note stem
            include_archive: If True, search archive directory too

        Yields:
            Path objects for each child
        """
        pattern = f"{parent_stem}.*.md"

        # Search active directory
        yield from self.notes_dir.glob(pattern)

        # Search archive if requested
        if include_archive and self.archive_dir.exists():
            yield from self.archive_dir.glob(pattern)

    def iter_direct_children(self, parent_stem: str,
                            include_archive: bool = True) -> Iterator[Path]:
        """Iterate only direct children of a parent.

        For example, if parent is "project", yields "project.task" but not
        "project.task.subtask".

        Args:
            parent_stem: Parent note stem
            include_archive: If True, search archive directory too

        Yields:
            Path objects for each direct child
        """
        for child_path in self.iter_children(parent_stem, include_archive):
            # Check if direct child (exactly one more level)
            parts = child_path.stem.split(".")
            parent_parts = parent_stem.split(".")
            if len(parts) == len(parent_parts) + 1:
                yield child_path

    def get_project_stems(self, include_archive: bool = False) -> list[str]:
        """Get list of project stems.

        Args:
            include_archive: If True, include archived projects

        Returns:
            List of project stems (sorted)
        """
        stems = []
        for path in self.iter_projects(include_archive):
            stems.append(path.stem)
        return sorted(stems)

    def get_all_stems(self, include_archive: bool = False,
                     exclude_special: bool = True) -> list[str]:
        """Get list of all note stems.

        Args:
            include_archive: If True, include archived notes
            exclude_special: If True, exclude backlog

        Returns:
            List of note stems (sorted)
        """
        stems = []
        for path in self.iter_all_notes(include_archive, exclude_special):
            stems.append(path.stem)
        return sorted(stems)

    def count_children(self, parent_stem: str,
                      include_archive: bool = True) -> int:
        """Count number of children for a parent.

        Args:
            parent_stem: Parent note stem
            include_archive: If True, count archived children too

        Returns:
            Number of children
        """
        return sum(1 for _ in self.iter_children(parent_stem, include_archive))


def get_all_note_files(notes_dir: Path, include_archive: bool = False) -> list[Path]:
    """Get list of all note file paths.

    Args:
        notes_dir: Notes directory
        include_archive: Include archived files

    Returns:
        List of Path objects
    """
    iterator = FileIterator(notes_dir)
    return list(iterator.iter_all_notes(include_archive=include_archive))


def get_project_files(notes_dir: Path, include_archive: bool = False) -> list[Path]:
    """Get list of project file paths.

    Args:
        notes_dir: Notes directory
        include_archive: Include archived projects

    Returns:
        List of Path objects
    """
    iterator = FileIterator(notes_dir)
    return list(iterator.iter_projects(include_archive=include_archive))


def find_children(notes_dir: Path, parent_stem: str,
                 include_archive: bool = True) -> list[Path]:
    """Find all children of a parent note.

    Args:
        notes_dir: Notes directory
        parent_stem: Parent note stem
        include_archive: Include archived children

    Returns:
        List of Path objects
    """
    iterator = FileIterator(notes_dir)
    return list(iterator.iter_children(parent_stem, include_archive=include_archive))

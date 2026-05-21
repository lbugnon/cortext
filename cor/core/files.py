"""File iteration and I/O operations for Cor.

This module consolidates file iteration patterns that were repeated 20+ times
across the codebase with inconsistent approaches.
"""

from pathlib import Path
from typing import Iterator
import frontmatter


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

    def iter_by_pattern(self, pattern: str,
                       include_archive: bool = False) -> Iterator[Path]:
        """Iterate files matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g., "*.md", "project.*.md")
            include_archive: If True, search archive directory too

        Yields:
            Path objects matching pattern
        """
        # Search active directory
        yield from self.notes_dir.glob(pattern)

        # Search archive if requested
        if include_archive and self.archive_dir.exists():
            yield from self.archive_dir.glob(pattern)

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


class NoteFileManager:
    """Centralized file I/O operations for notes."""

    def __init__(self, notes_dir: Path):
        """Initialize file manager.

        Args:
            notes_dir: Path to notes directory
        """
        self.notes_dir = notes_dir
        self.iterator = FileIterator(notes_dir)

    def load_note(self, path: Path) -> frontmatter.Post | None:
        """Load note with frontmatter.

        Args:
            path: Path to note file

        Returns:
            Frontmatter Post object, or None if error
        """
        if not path.exists():
            return None

        try:
            return frontmatter.load(path)
        except Exception:
            return None

    def save_note(self, path: Path, post: frontmatter.Post) -> None:
        """Save note with frontmatter.

        Args:
            path: Path to save to
            post: Frontmatter Post object
        """
        with open(path, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)

    def extract_title(self, post: frontmatter.Post,
                     fallback_stem: str = None) -> str:
        """Extract title from note content.

        Looks for first line starting with "# ".

        Args:
            post: Frontmatter Post object
            fallback_stem: Fallback title if no heading found

        Returns:
            Note title
        """
        if not post or not post.content:
            return fallback_stem or "Untitled"

        for line in post.content.split("\n"):
            if line.startswith("# "):
                return line[2:].strip()

        return fallback_stem or "Untitled"

    def extract_metadata(self, path: Path) -> dict:
        """Extract only frontmatter metadata (fast, no content parsing).

        Args:
            path: Path to note file

        Returns:
            Metadata dictionary
        """
        post = self.load_note(path)
        return dict(post.metadata) if post else {}

    def exists(self, stem: str, include_archive: bool = True) -> bool:
        """Check if a note exists by stem.

        Args:
            stem: Note stem to check
            include_archive: If True, check archive directory too

        Returns:
            True if note exists
        """
        active_path = self.notes_dir / f"{stem}.md"
        if active_path.exists():
            return True

        if include_archive:
            archive_dir = self.notes_dir / "archive"
            archive_path = archive_dir / f"{stem}.md"
            if archive_path.exists():
                return True

        return False

    def find_note(self, stem: str,
                 include_archive: bool = True) -> tuple[Path, bool] | None:
        """Find note by stem in active or archive directory.

        Args:
            stem: Note stem to find
            include_archive: If True, check archive directory too

        Returns:
            Tuple of (path, is_archived) if found, None otherwise
        """
        active_path = self.notes_dir / f"{stem}.md"
        if active_path.exists():
            return (active_path, False)

        if include_archive:
            archive_dir = self.notes_dir / "archive"
            archive_path = archive_dir / f"{stem}.md"
            if archive_path.exists():
                return (archive_path, True)

        return None

    def read_content(self, path: Path) -> str:
        """Read full file content (including frontmatter).

        Args:
            path: Path to file

        Returns:
            File content
        """
        return path.read_text() if path.exists() else ""

    def write_content(self, path: Path, content: str) -> None:
        """Write full file content.

        Args:
            path: Path to file
            content: Content to write
        """
        path.write_text(content)


# Convenience functions for common operations

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

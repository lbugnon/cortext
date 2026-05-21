"""Tests for cor/core/files.py module."""

import pytest
from pathlib import Path
import frontmatter

from cor.core.files import (
    FileIterator,
    NoteFileManager,
    get_all_note_files,
    get_project_files,
    find_children
)


class TestFileIterator:
    """Test FileIterator functionality."""

    @pytest.fixture
    def notes_dir(self, tmp_path):
        """Create a temporary notes directory with sample files."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "archive").mkdir()

        # Create sample files
        (notes_dir / "backlog.md").write_text("backlog")
        (notes_dir / "project1.md").write_text("project")
        (notes_dir / "project1.task1.md").write_text("task")
        (notes_dir / "project1.task2.md").write_text("task")
        (notes_dir / "project1.group1.md").write_text("group")
        (notes_dir / "project1.group1.subtask.md").write_text("subtask")
        (notes_dir / "project2.md").write_text("project")

        # Create archived files
        archive_dir = notes_dir / "archive"
        (archive_dir / "old_project.md").write_text("archived project")
        (archive_dir / "old_task.md").write_text("archived task")

        return notes_dir

    @pytest.fixture
    def iterator(self, notes_dir):
        """Create a FileIterator instance."""
        return FileIterator(notes_dir)

    def test_iter_all_notes_active_only(self, iterator):
        """Test iterating active notes only."""
        notes = list(iterator.iter_all_notes(include_archive=False))

        stems = [n.stem for n in notes]

        # Should include all notes except backlog
        assert "project1" in stems
        assert "project1.task1" in stems
        assert "project2" in stems

        # Should NOT include special files
        assert "backlog" not in stems

        # Should NOT include archived
        assert "old_project" not in stems

    def test_iter_all_notes_with_archive(self, iterator):
        """Test iterating notes including archive."""
        notes = list(iterator.iter_all_notes(include_archive=True))

        stems = [n.stem for n in notes]

        # Should include active
        assert "project1" in stems

        # Should include archived
        assert "old_project" in stems
        assert "old_task" in stems

    def test_iter_all_notes_include_special(self, iterator):
        """Test iterating with special files included."""
        notes = list(iterator.iter_all_notes(exclude_special=False))

        stems = [n.stem for n in notes]

        # Should include special files
        assert "backlog" in stems

    def test_iter_projects(self, iterator):
        """Test iterating project files only."""
        projects = list(iterator.iter_projects())

        stems = [p.stem for p in projects]

        # Should include projects (no dots)
        assert "project1" in stems
        assert "project2" in stems

        # Should NOT include tasks (have dots)
        assert "project1.task1" not in stems
        assert "project1.group1" not in stems

    def test_iter_tasks_for_project_direct_only(self, iterator):
        """Test iterating direct tasks of a project."""
        tasks = list(iterator.iter_tasks_for_project("project1", direct_only=True))

        stems = [t.stem for t in tasks]

        # Should include direct children only
        assert "project1.task1" in stems
        assert "project1.task2" in stems
        assert "project1.group1" in stems

        # Should NOT include nested
        assert "project1.group1.subtask" not in stems

    def test_iter_tasks_for_project_all_descendants(self, iterator):
        """Test iterating all descendants of a project."""
        tasks = list(iterator.iter_tasks_for_project("project1", direct_only=False))

        stems = [t.stem for t in tasks]

        # Should include all descendants
        assert "project1.task1" in stems
        assert "project1.group1" in stems
        assert "project1.group1.subtask" in stems

    def test_iter_children(self, iterator):
        """Test iterating all children of a parent."""
        children = list(iterator.iter_children("project1"))

        stems = [c.stem for c in children]

        # Should include all children (direct and nested)
        assert "project1.task1" in stems
        assert "project1.group1" in stems
        assert "project1.group1.subtask" in stems

    def test_iter_direct_children(self, iterator):
        """Test iterating only direct children."""
        children = list(iterator.iter_direct_children("project1"))

        stems = [c.stem for c in children]

        # Should include direct children only
        assert "project1.task1" in stems
        assert "project1.group1" in stems

        # Should NOT include nested
        assert "project1.group1.subtask" not in stems

    def test_iter_direct_children_nested_parent(self, iterator):
        """Test iterating direct children of nested parent."""
        children = list(iterator.iter_direct_children("project1.group1"))

        stems = [c.stem for c in children]

        # Should include subtask
        assert "project1.group1.subtask" in stems

    def test_get_project_stems(self, iterator):
        """Test getting list of project stems."""
        stems = iterator.get_project_stems()

        assert "project1" in stems
        assert "project2" in stems
        assert "project1.task1" not in stems

        # Should be sorted
        assert stems == sorted(stems)

    def test_get_all_stems(self, iterator):
        """Test getting all note stems."""
        stems = iterator.get_all_stems(exclude_special=True)

        assert "project1" in stems
        assert "project1.task1" in stems

        # Should NOT include special
        assert "backlog" not in stems

    def test_count_children(self, iterator):
        """Test counting children."""
        count = iterator.count_children("project1")

        # Should count all descendants
        assert count == 4  # task1, task2, group1, group1.subtask


class TestNoteFileManager:
    """Test NoteFileManager functionality."""

    @pytest.fixture
    def notes_dir(self, tmp_path):
        """Create a temporary notes directory."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "archive").mkdir()
        return notes_dir

    @pytest.fixture
    def file_mgr(self, notes_dir):
        """Create a NoteFileManager instance."""
        return NoteFileManager(notes_dir)

    def test_load_note(self, file_mgr, notes_dir):
        """Test loading a note with frontmatter."""
        note_path = notes_dir / "test.md"
        note_path.write_text("""---
title: Test
status: todo
---
# Test Note
""")

        post = file_mgr.load_note(note_path)

        assert post is not None
        assert post["title"] == "Test"
        assert post["status"] == "todo"
        assert "# Test Note" in post.content

    def test_load_note_nonexistent(self, file_mgr, notes_dir):
        """Test loading non-existent note returns None."""
        result = file_mgr.load_note(notes_dir / "nonexistent.md")

        assert result is None

    def test_save_note(self, file_mgr, notes_dir):
        """Test saving a note with frontmatter."""
        note_path = notes_dir / "test.md"

        post = frontmatter.Post("Content here")
        post["title"] = "Test"
        post["status"] = "done"

        file_mgr.save_note(note_path, post)

        # Verify saved content
        loaded = frontmatter.load(note_path)
        assert loaded["title"] == "Test"
        assert loaded["status"] == "done"
        assert loaded.content == "Content here"

    def test_extract_title(self, file_mgr):
        """Test extracting title from note content."""
        post = frontmatter.Post("# My Title\n\nContent here")

        title = file_mgr.extract_title(post)

        assert title == "My Title"

    def test_extract_title_no_heading(self, file_mgr):
        """Test extracting title when no heading exists."""
        post = frontmatter.Post("Just content")

        title = file_mgr.extract_title(post, fallback_stem="fallback")

        assert title == "fallback"

    def test_extract_metadata(self, file_mgr, notes_dir):
        """Test extracting metadata from file."""
        note_path = notes_dir / "test.md"
        note_path.write_text("""---
type: task
status: todo
priority: high
---
# Content
""")

        metadata = file_mgr.extract_metadata(note_path)

        assert metadata["type"] == "task"
        assert metadata["status"] == "todo"
        assert metadata["priority"] == "high"

    def test_exists_in_active(self, file_mgr, notes_dir):
        """Test checking if note exists in active directory."""
        note_path = notes_dir / "test.md"
        note_path.write_text("content")

        assert file_mgr.exists("test")
        assert not file_mgr.exists("nonexistent")

    def test_exists_in_archive(self, file_mgr, notes_dir):
        """Test checking if note exists in archive."""
        archive_dir = notes_dir / "archive"
        note_path = archive_dir / "archived.md"
        note_path.write_text("content")

        assert file_mgr.exists("archived", include_archive=True)
        assert not file_mgr.exists("archived", include_archive=False)

    def test_find_note_in_active(self, file_mgr, notes_dir):
        """Test finding note in active directory."""
        note_path = notes_dir / "test.md"
        note_path.write_text("content")

        result = file_mgr.find_note("test")

        assert result is not None
        assert result[0] == note_path
        assert result[1] is False  # not archived

    def test_find_note_in_archive(self, file_mgr, notes_dir):
        """Test finding note in archive."""
        archive_dir = notes_dir / "archive"
        note_path = archive_dir / "archived.md"
        note_path.write_text("content")

        result = file_mgr.find_note("archived")

        assert result is not None
        assert result[0] == note_path
        assert result[1] is True  # archived


class TestConvenienceFunctions:
    """Test convenience functions."""

    @pytest.fixture
    def notes_dir(self, tmp_path):
        """Create a temporary notes directory."""
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()
        (notes_dir / "archive").mkdir()

        (notes_dir / "project.md").write_text("project")
        (notes_dir / "project.task.md").write_text("task")

        return notes_dir

    def test_get_all_note_files(self, notes_dir):
        """Test getting all note files."""
        files = get_all_note_files(notes_dir)

        stems = [f.stem for f in files]
        assert "project" in stems
        assert "project.task" in stems

    def test_get_project_files(self, notes_dir):
        """Test getting project files."""
        files = get_project_files(notes_dir)

        stems = [f.stem for f in files]
        assert "project" in stems
        assert "project.task" not in stems  # Not a project

    def test_find_children(self, notes_dir):
        """Test finding children."""
        children = find_children(notes_dir, "project")

        stems = [c.stem for c in children]
        assert "project.task" in stems

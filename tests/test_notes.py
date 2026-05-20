"""Tests for Phase 3: Split Note abstraction (NoteMetadata vs Note)."""

import pytest
from datetime import datetime, date
from pathlib import Path
from cor.core.notes import Note, NoteMetadata, parse_note, parse_metadata, find_notes


@pytest.fixture
def simple_note(tmp_path):
    """Create a simple note file for testing."""
    note_file = tmp_path / "test.md"
    note_file.write_text("""---
type: task
status: active
created: 2024-01-01 10:00
modified: 2024-01-15 14:30
due: 2024-12-31
priority: high
tags:
  - test
  - urgent
requires:
  - dependency1
  - dependency2
---

# Test Note

This is test content.
""")
    return note_file


@pytest.fixture
def overdue_task(tmp_path):
    """Create an overdue task for testing computed properties."""
    note_file = tmp_path / "overdue.md"
    note_file.write_text("""---
type: task
status: active
due: 2020-01-01
modified: 2020-06-01 12:00
---

# Overdue Task

This is overdue.
""")
    return note_file


@pytest.fixture
def stale_task(tmp_path):
    """Create a stale task for testing."""
    note_file = tmp_path / "stale.md"
    # Modified 30 days ago
    old_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    old_date = old_date.replace(day=1 if old_date.day > 1 else 1)
    note_file.write_text(f"""---
type: task
status: active
modified: 2020-01-01 10:00
---

# Stale Task

Not touched in a while.
""")
    return note_file


class TestNoteMetadata:
    """Test NoteMetadata (lightweight model)."""

    def test_from_file_basic_fields(self, simple_note):
        """Test that NoteMetadata loads basic fields correctly."""
        metadata = NoteMetadata.from_file(simple_note)

        assert metadata.path == simple_note
        assert metadata.title == "Test Note"
        assert metadata.note_type == "task"
        assert metadata.status == "active"
        assert metadata.priority == "high"
        assert metadata.tags == ["test", "urgent"]
        assert metadata.requires == ["dependency1", "dependency2"]

    def test_from_file_dates(self, simple_note):
        """Test that NoteMetadata parses dates correctly."""
        metadata = NoteMetadata.from_file(simple_note)

        assert isinstance(metadata.created, datetime)
        assert metadata.created.year == 2024
        assert metadata.created.month == 1
        assert metadata.created.day == 1

        assert isinstance(metadata.modified, datetime)
        assert metadata.modified.year == 2024
        assert metadata.modified.month == 1
        assert metadata.modified.day == 15

        # Due date is stored as date
        assert isinstance(metadata.due, (date, datetime))

    def test_no_computed_properties(self, simple_note):
        """Test that NoteMetadata does NOT have computed properties."""
        metadata = NoteMetadata.from_file(simple_note)

        # These properties should not exist on NoteMetadata
        assert not hasattr(metadata, 'is_overdue')
        assert not hasattr(metadata, 'is_stale')
        assert not hasattr(metadata, 'days_overdue')
        assert not hasattr(metadata, 'is_due_this_week')

    def test_to_dict(self, simple_note):
        """Test conversion back to dict for frontmatter."""
        metadata = NoteMetadata.from_file(simple_note)
        data = metadata.to_dict()

        assert data["type"] == "task"
        assert data["status"] == "active"
        assert data["priority"] == "high"
        assert data["tags"] == ["test", "urgent"]
        assert data["requires"] == ["dependency1", "dependency2"]

    def test_default_lists(self, tmp_path):
        """Test that tags and requires default to empty lists."""
        note_file = tmp_path / "minimal.md"
        note_file.write_text("""---
type: note
---

# Minimal Note
""")

        metadata = NoteMetadata.from_file(note_file)
        assert metadata.tags == []
        assert metadata.requires == []

    def test_parent_project(self, tmp_path):
        """Test parent_project property extraction."""
        # Project file
        project = tmp_path / "myproject.md"
        project.write_text("---\ntype: project\n---\n# My Project")
        metadata = NoteMetadata.from_file(project)
        assert metadata.parent_project == "myproject"

        # Task file
        task = tmp_path / "myproject.task1.md"
        task.write_text("---\ntype: task\n---\n# Task 1")
        metadata = NoteMetadata.from_file(task)
        assert metadata.parent_project == "myproject"

        # Nested task
        nested = tmp_path / "myproject.group.task2.md"
        nested.write_text("---\ntype: task\n---\n# Task 2")
        metadata = NoteMetadata.from_file(nested)
        assert metadata.parent_project == "myproject"


class TestNote:
    """Test Note (full model with computed properties)."""

    def test_inherits_from_metadata(self, simple_note):
        """Test that Note inherits all NoteMetadata fields."""
        note = Note.from_file(simple_note)

        # All metadata fields should be present
        assert note.path == simple_note
        assert note.title == "Test Note"
        assert note.note_type == "task"
        assert note.status == "active"
        assert note.priority == "high"
        assert note.tags == ["test", "urgent"]
        assert note.requires == ["dependency1", "dependency2"]

    def test_has_content(self, simple_note):
        """Test that Note includes full content."""
        note = Note.from_file(simple_note)

        assert note.content
        assert "# Test Note" in note.content
        assert "This is test content" in note.content

    def test_is_overdue_true(self, overdue_task):
        """Test is_overdue property returns True for overdue tasks."""
        note = Note.from_file(overdue_task)

        assert note.is_overdue is True
        assert note.days_overdue > 0

    def test_is_overdue_false_when_done(self, overdue_task):
        """Test is_overdue returns False for done tasks even if past due."""
        content = overdue_task.read_text()
        content = content.replace("status: active", "status: done")
        overdue_task.write_text(content)

        note = Note.from_file(overdue_task)
        assert note.is_overdue is False
        assert note.days_overdue == 0

    def test_is_stale_true(self, stale_task):
        """Test is_stale property for old tasks."""
        note = Note.from_file(stale_task)

        assert note.is_stale is True
        assert note.days_since_modified > 14

    def test_is_stale_false_when_done(self, stale_task):
        """Test is_stale returns False for done tasks."""
        content = stale_task.read_text()
        content = content.replace("status: active", "status: done")
        stale_task.write_text(content)

        note = Note.from_file(stale_task)
        assert note.is_stale is False

    def test_is_due_this_week(self, tmp_path):
        """Test is_due_this_week property."""
        # Create task due in 3 days
        from datetime import timedelta
        future_date = date.today() + timedelta(days=3)

        note_file = tmp_path / "soon.md"
        note_file.write_text(f"""---
type: task
status: active
due: {future_date.strftime('%Y-%m-%d')}
---

# Due Soon
""")

        note = Note.from_file(note_file)
        assert note.is_due_this_week is True

    def test_days_since_modified(self, stale_task):
        """Test days_since_modified calculation."""
        note = Note.from_file(stale_task)

        assert note.days_since_modified > 0

    def test_no_modified_date(self, tmp_path):
        """Test behavior when no modified date."""
        note_file = tmp_path / "no_modified.md"
        note_file.write_text("""---
type: note
---

# No Modified
""")

        note = Note.from_file(note_file)
        assert note.days_since_modified == 0
        assert note.is_stale is False


class TestParseHelpers:
    """Test parse_note and parse_metadata helper functions."""

    def test_parse_metadata_returns_metadata(self, simple_note):
        """Test parse_metadata returns NoteMetadata instance."""
        result = parse_metadata(simple_note)

        assert isinstance(result, NoteMetadata)
        assert not isinstance(result, Note)  # Not the subclass

    def test_parse_note_returns_note(self, simple_note):
        """Test parse_note returns Note instance."""
        result = parse_note(simple_note)

        assert isinstance(result, Note)
        assert isinstance(result, NoteMetadata)  # Is a subclass

    def test_find_notes_default(self, tmp_path):
        """Test find_notes without metadata_only returns Note instances."""
        (tmp_path / "task1.md").write_text("---\ntype: task\n---\n# Task 1")
        (tmp_path / "task2.md").write_text("---\ntype: task\n---\n# Task 2")

        notes = find_notes(tmp_path)

        assert len(notes) == 2
        assert all(isinstance(n, Note) for n in notes)

    def test_find_notes_metadata_only(self, tmp_path):
        """Test find_notes with metadata_only=True returns NoteMetadata."""
        (tmp_path / "task1.md").write_text("---\ntype: task\n---\n# Task 1")
        (tmp_path / "task2.md").write_text("---\ntype: task\n---\n# Task 2")

        notes = find_notes(tmp_path, metadata_only=True)

        assert len(notes) == 2
        assert all(isinstance(n, NoteMetadata) for n in notes)
        assert not any(isinstance(n, Note) for n in notes)

    def test_find_notes_excludes_backlog(self, tmp_path):
        """Test that find_notes excludes backlog.md."""
        (tmp_path / "task1.md").write_text("---\ntype: task\n---\n# Task 1")
        (tmp_path / "backlog.md").write_text("---\ntype: note\n---\n# Backlog")

        notes = find_notes(tmp_path)

        assert len(notes) == 1
        assert notes[0].path.stem == "task1"

    def test_find_notes_handles_errors(self, tmp_path, capsys):
        """Test that find_notes handles parse errors gracefully."""
        (tmp_path / "valid.md").write_text("---\ntype: task\n---\n# Valid")
        # python-frontmatter is very lenient, so we need a truly broken file
        # Create a file that will cause an exception during parsing
        import os
        broken_file = tmp_path / "broken.md"
        broken_file.write_text("---\ntype: task\n---\n# Valid")
        # Make file unreadable to trigger exception
        os.chmod(broken_file, 0o000)

        try:
            notes = find_notes(tmp_path)

            # Should still get the valid note
            assert len(notes) >= 1

            # Should have printed warning for broken file
            captured = capsys.readouterr()
            # Either we got a warning or we successfully skipped the broken file
            assert "Warning: Could not parse" in captured.out or all(n.path.stem != "broken" for n in notes)
        finally:
            # Restore permissions for cleanup
            os.chmod(broken_file, 0o644)


class TestDateParsing:
    """Test date parsing with time support."""

    def test_parse_date_only_format(self):
        """Test parsing date-only format (YYYY-MM-DD)."""
        from cor.core.notes import _parse_date, _date_has_time
        
        result = _parse_date("2024-12-31")
        
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 12
        assert result.day == 31
        assert result.hour == 0
        assert result.minute == 0
        assert _date_has_time(result) is False

    def test_parse_datetime_format(self):
        """Test parsing datetime format (YYYY-MM-DD HH:MM)."""
        from cor.core.notes import _parse_date, _date_has_time
        
        result = _parse_date("2024-12-31 14:30")
        
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 12
        assert result.day == 31
        assert result.hour == 14
        assert result.minute == 30
        assert _date_has_time(result) is True

    def test_parse_midnight_no_time(self):
        """Test that midnight is treated as date-only (no specific time)."""
        from cor.core.notes import _parse_date, _date_has_time
        
        result = _parse_date("2024-12-31 00:00")
        
        assert isinstance(result, datetime)
        assert _date_has_time(result) is False

    def test_parse_datetime_objects(self):
        """Test that datetime objects are passed through."""
        from cor.core.notes import _parse_date
        
        original = datetime(2024, 12, 31, 14, 30)
        result = _parse_date(original)
        
        assert result is original

    def test_parse_date_object(self):
        """Test that date objects are converted to datetime."""
        from cor.core.notes import _parse_date
        
        original = date(2024, 12, 31)
        result = _parse_date(original)
        
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 12
        assert result.day == 31
        assert result.hour == 0
        assert result.minute == 0

    def test_parse_none(self):
        """Test that None returns None."""
        from cor.core.notes import _parse_date
        
        result = _parse_date(None)
        
        assert result is None

    def test_parse_invalid(self):
        """Test that invalid strings return None."""
        from cor.core.notes import _parse_date
        
        result = _parse_date("not-a-date")
        
        assert result is None

    def test_due_date_with_time_in_note(self, tmp_path):
        """Test parsing a note with due date including time."""
        note_file = tmp_path / "timed_task.md"
        note_file.write_text("""---
type: task
status: active
due: 2024-12-31 14:30
---

# Timed Task

This task has a specific due time.
""")
        
        metadata = NoteMetadata.from_file(note_file)
        
        assert isinstance(metadata.due, datetime)
        assert metadata.due.hour == 14
        assert metadata.due.minute == 30


class TestTimezoneConfig:
    """Test timezone configuration."""

    def test_default_timezone_is_utc(self):
        """Test that default timezone is UTC."""
        from cor.config import get_timezone, default_timezone
        
        # When no timezone is set, should return default
        assert default_timezone == "UTC"

    def test_set_and_get_timezone(self, tmp_path, monkeypatch):
        """Test setting and getting timezone."""
        from cor.config import set_timezone, get_timezone, _config_dir
        
        # Mock config dir to use temp path
        monkeypatch.setattr("cor.config._config_dir", lambda: tmp_path)
        
        # Set a timezone
        set_timezone("America/Argentina/Buenos_Aires")
        
        # Get should return what we set
        assert get_timezone() == "America/Argentina/Buenos_Aires"

    def test_timezone_conversion_for_calendar(self):
        """Test timezone conversion logic for calendar events."""
        from datetime import datetime, timezone
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        
        # Simulate user in Buenos Aires setting due: 2026-01-26 20:00
        user_tz = ZoneInfo("America/Argentina/Buenos_Aires")
        due_datetime = datetime(2026, 1, 26, 20, 0)
        
        # Attach timezone and convert to UTC
        due_datetime_local = due_datetime.replace(tzinfo=user_tz)
        due_datetime_utc = due_datetime_local.astimezone(timezone.utc)
        
        # Buenos Aires is UTC-3, so 20:00 BA = 23:00 UTC
        assert due_datetime_utc.hour == 23
        assert due_datetime_utc.day == 26
        
        # Verify RFC3339 format
        rfc3339 = due_datetime_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert rfc3339 == "2026-01-26T23:00:00Z"


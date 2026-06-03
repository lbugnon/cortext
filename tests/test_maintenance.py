"""Tests for maintenance module without git setup."""

import pytest

from cor.sync.runner import (
    MaintenanceRunner,
    SyncResult,
    get_frontmatter,
    get_parent_name,
    should_archive,
    should_unarchive,
)
from cor.core.links import LinkPatterns


# --- Fixtures ---

@pytest.fixture
def simple_vault(tmp_path):
    """Create a simple vault without git for fast testing."""
    vault = tmp_path / "notes"
    vault.mkdir()
    (vault / "archive").mkdir()
    return vault


@pytest.fixture
def vault_with_project(simple_vault):
    """Create vault with a project and tasks."""
    project = simple_vault / "myproject.md"
    project.write_text("""---
type: project
status: active
created: 2024-01-01
---
# My Project

## Tasks
- [ ] [Task 1](myproject.task1.md)
- [ ] [Task 2](myproject.task2.md)
""")

    task1 = simple_vault / "myproject.task1.md"
    task1.write_text("""---
type: task
status: todo
created: 2024-01-01
---
# Task 1

See [parent](myproject.md).
""")

    task2 = simple_vault / "myproject.task2.md"
    task2.write_text("""---
type: task
status: todo
created: 2024-01-01
---
# Task 2

See [parent](myproject.md).
""")

    return {
        'vault': simple_vault,
        'project': project,
        'task1': task1,
        'task2': task2,
    }


# --- Helper function tests ---

class TestGetFrontmatter:
    def test_parses_yaml(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\nstatus: active\ntype: project\n---\n# Title")
        meta = get_frontmatter(str(f))
        assert meta == {'status': 'active', 'type': 'project'}

    def test_missing_file(self, tmp_path):
        meta = get_frontmatter(str(tmp_path / "missing.md"))
        assert meta is None


class TestGetParentName:
    def test_task_under_project(self):
        assert get_parent_name("project.task.md") == "project"

    def test_nested_hierarchy(self):
        assert get_parent_name("project.group.task.md") == "project.group"

    def test_no_parent(self):
        assert get_parent_name("project.md") is None


class TestShouldArchive:
    def test_done_project(self):
        assert should_archive("proj.md", {"type": "project", "status": "done"})

    def test_done_task(self):
        assert should_archive("task.md", {"type": "task", "status": "done"})

    def test_dropped_task(self):
        assert should_archive("task.md", {"type": "task", "status": "dropped"})

    def test_active_project(self):
        assert not should_archive("proj.md", {"type": "project", "status": "active"})

    def test_active_task(self):
        assert not should_archive("task.md", {"type": "task", "status": "active"})


class TestShouldUnarchive:
    def test_active_project(self):
        assert should_unarchive("proj.md", {"type": "project", "status": "active"})

    def test_todo_task(self):
        assert should_unarchive("task.md", {"type": "task", "status": "todo"})

    def test_done_project(self):
        assert not should_unarchive("proj.md", {"type": "project", "status": "done"})

    def test_no_status(self):
        assert not should_unarchive("task.md", {"type": "task"})


class TestIsExternalLink:
    def test_https(self):
        assert "https://example.com".startswith(LinkPatterns.EXTERNAL_PREFIXES)

    def test_http(self):
        assert "http://example.com".startswith(LinkPatterns.EXTERNAL_PREFIXES)

    def test_mailto(self):
        assert "mailto:test@example.com".startswith(LinkPatterns.EXTERNAL_PREFIXES)

    def test_anchor(self):
        assert "#section".startswith(LinkPatterns.EXTERNAL_PREFIXES)

    def test_local_file(self):
        assert not "myfile.md".startswith(LinkPatterns.EXTERNAL_PREFIXES)


# --- MaintenanceRunner tests ---

class TestMaintenanceRunner:
    def test_init(self, simple_vault):
        runner = MaintenanceRunner(simple_vault)
        assert runner.notes_dir == simple_vault
        assert runner.archive_dir == simple_vault / "archive"
        assert runner.dry_run is False

    def test_init_dry_run(self, simple_vault):
        runner = MaintenanceRunner(simple_vault, dry_run=True)
        assert runner.dry_run is True

    def test_find_file_in_notes(self, vault_with_project):
        runner = MaintenanceRunner(vault_with_project['vault'])
        found = runner.find_file_in_notes("myproject.md")
        assert found == vault_with_project['project']

    def test_find_file_not_exists(self, simple_vault):
        runner = MaintenanceRunner(simple_vault)
        found = runner.find_file_in_notes("nonexistent.md")
        assert found is None

    def test_find_children_files(self, vault_with_project):
        runner = MaintenanceRunner(vault_with_project['vault'])
        children = runner.find_children_files("myproject")
        assert len(children) == 2
        names = [c.name for c in children]
        assert "myproject.task1.md" in names
        assert "myproject.task2.md" in names

    def test_find_parent_file(self, vault_with_project):
        runner = MaintenanceRunner(vault_with_project['vault'])
        parent = runner.find_parent_file("myproject")
        assert parent == vault_with_project['project']


class TestDryRun:
    def test_archive_dry_run_does_not_move(self, vault_with_project):
        """Dry run should report what would happen but not modify files."""
        vault = vault_with_project['vault']
        task1 = vault_with_project['task1']

        # Mark task as done
        content = task1.read_text().replace("status: todo", "status: done")
        task1.write_text(content)

        runner = MaintenanceRunner(vault, dry_run=True)
        archived, _ = runner.archive_completed([str(task1)])

        # Should report the archive
        assert len(archived) == 1
        assert archived[0][0] == str(task1)

        # But file should still be in original location
        assert task1.exists()
        assert not (vault / "archive" / "myproject.task1.md").exists()

    def test_sync_dry_run_reports_all_changes(self, vault_with_project):
        """Dry run sync should return SyncResult with planned changes."""
        vault = vault_with_project['vault']
        task1 = vault_with_project['task1']

        # Mark task as done
        content = task1.read_text().replace("status: todo", "status: done")
        task1.write_text(content)

        runner = MaintenanceRunner(vault, dry_run=True)
        result = runner.sync([str(task1)])

        # Should report archive action
        assert len(result.archived) == 1

        # Original file should still exist
        assert task1.exists()


class TestArchiveCompleted:
    def test_archives_done_task(self, vault_with_project):
        """Done task should be moved to archive."""
        vault = vault_with_project['vault']
        task1 = vault_with_project['task1']
        archive = vault / "archive"

        # Mark task as done
        content = task1.read_text().replace("status: todo", "status: done")
        task1.write_text(content)

        runner = MaintenanceRunner(vault, dry_run=False)
        archived, link_updates = runner.archive_completed([str(task1)])

        assert len(archived) == 1
        assert not task1.exists()
        assert (archive / "myproject.task1.md").exists()

    def test_does_not_archive_active_task(self, vault_with_project):
        """Active task should not be archived."""
        vault = vault_with_project['vault']
        task1 = vault_with_project['task1']

        # Mark task as active
        content = task1.read_text().replace("status: todo", "status: active")
        task1.write_text(content)

        runner = MaintenanceRunner(vault, dry_run=False)
        archived, _ = runner.archive_completed([str(task1)])

        assert len(archived) == 0
        assert task1.exists()


class TestUnarchiveReactivated:
    def test_unarchives_reactivated_task(self, vault_with_project):
        """Reactivated task should be moved from archive."""
        vault = vault_with_project['vault']
        archive = vault / "archive"

        # Create archived task
        archived_task = archive / "myproject.task3.md"
        archived_task.write_text("""---
type: task
status: active
created: 2024-01-01
---
# Task 3
""")

        runner = MaintenanceRunner(vault, dry_run=False)
        unarchived, _ = runner.unarchive_reactivated(["archive/myproject.task3.md"])

        assert len(unarchived) == 1
        assert not archived_task.exists()
        assert (vault / "myproject.task3.md").exists()


class TestSyncTaskStatus:
    def test_updates_checkbox_in_parent(self, vault_with_project):
        """Task status change should update checkbox in parent."""
        vault = vault_with_project['vault']
        task1 = vault_with_project['task1']
        project = vault_with_project['project']

        # Mark task as active
        content = task1.read_text().replace("status: todo", "status: active")
        task1.write_text(content)

        runner = MaintenanceRunner(vault, dry_run=False)
        updated = runner.sync_task_status_to_project([str(task1)])

        assert len(updated) == 1
        project_content = project.read_text()
        assert "[.] [Task 1](myproject.task1.md)" in project_content


class TestSortTasks:
    def test_sorts_by_status(self, vault_with_project):
        """Tasks should be sorted by status order."""
        vault = vault_with_project['vault']
        project = vault_with_project['project']
        task1 = vault_with_project['task1']
        task2 = vault_with_project['task2']

        # Set different statuses
        task1.write_text(task1.read_text().replace("status: todo", "status: done"))
        task2.write_text(task2.read_text().replace("status: todo", "status: active"))

        # Update project checkboxes manually for test
        project_content = project.read_text()
        project_content = project_content.replace("- [ ] [Task 1]", "- [x] [Task 1]")
        project_content = project_content.replace("- [ ] [Task 2]", "- [.] [Task 2]")
        project.write_text(project_content)

        runner = MaintenanceRunner(vault, dry_run=False)
        sorted_result = runner.sort_tasks_in_parent(project)

        assert sorted_result is True
        content = project.read_text()
        # Active (.) should come before done (x)
        active_pos = content.find("[.] [Task 2]")
        done_pos = content.find("[x] [Task 1]")
        assert active_pos < done_pos


class TestSyncResult:
    def test_default_values(self):
        result = SyncResult()
        assert result.archived == []
        assert result.unarchived == []
        assert result.group_status_updated == []
        assert result.checkbox_synced == []
        assert result.tasks_sorted == []
        assert result.links_updated == []
        assert result.modified_dates_updated == []
        assert result.errors == {}


class TestUnarchiveParseFailure:
    """Regression: unarchiving a parent whose frontmatter can't be parsed must
    relocate the file instead of crashing (previously it unlinked then copied
    the already-deleted file)."""

    def test_unparseable_parent_is_moved_not_lost(self, simple_vault):
        # Malformed YAML frontmatter -> load_note() returns None
        archived_parent = simple_vault / "archive" / "proj.md"
        archived_parent.write_text("---\nstatus: [unclosed\n---\nbody\n")
        (simple_vault / "proj.task.md").write_text(
            "---\ntype: task\nstatus: active\nparent: proj\n---\n[< Proj](proj.md)\n"
        )

        runner = MaintenanceRunner(simple_vault)
        result = runner.unarchive_parent_if_needed("proj.task.md")

        assert result is not None
        assert (simple_vault / "proj.md").exists()
        assert not archived_parent.exists()
        assert (simple_vault / "proj.md").read_text() == "---\nstatus: [unclosed\n---\nbody\n"

"""Tests for the pre-commit hook functionality.

Tests cover:
- Status sync to parent files (checkbox updates)
- Archive/unarchive flows
- Task group status propagation
- Multi-task updates in single commit
- Separator insertion between active/done tasks
- Link updates during file moves
- Modified date updates
"""

import os
import subprocess
from pathlib import Path


# --- Pre-commit hook test helpers (used only by this module) ---

def stage_files(vault: Path, *files: Path):
    """Stage files for commit."""
    for f in files:
        subprocess.run(["git", "add", str(f)], cwd=vault, capture_output=True)


def run_precommit(vault: Path) -> tuple[int, str, str]:
    """Run the pre-commit hook and return (returncode, stdout, stderr)."""
    import sys
    hook_path = Path(__file__).parent.parent / "cor" / "hooks" / "pre-commit"

    result = subprocess.run(
        [sys.executable, str(hook_path)],
        cwd=vault,
        capture_output=True,
        text=True,
        env={**os.environ, "COR_VAULT": str(vault)}
    )
    return result.returncode, result.stdout, result.stderr


def get_frontmatter(filepath: Path) -> dict:
    """Parse frontmatter from a file."""
    import yaml
    content = filepath.read_text()
    if not content.startswith("---"):
        return {}
    end = content.find("\n---\n", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(content[3:end]) or {}
    except yaml.YAMLError:
        return {}


def set_status(filepath: Path, status: str):
    """Update the status field in a file's frontmatter."""
    content = filepath.read_text()
    import re
    new_content = re.sub(
        r"^status:.*$",
        f"status: {status}",
        content,
        flags=re.MULTILINE
    )
    filepath.write_text(new_content)


def get_task_checkbox(parent_path: Path, task_name: str) -> str | None:
    """Extract the checkbox symbol for a task in a parent file.

    Returns the checkbox character (space, x, ., o, ~) or None if not found.
    task_name should be the stem without .md extension.
    """
    import re
    content = parent_path.read_text()
    pattern = rf"- \[([x .o~])\] \[[^\]]+\]\((?:archive/)?{re.escape(task_name)}\.md\)"
    match = re.search(pattern, content)
    return match.group(1) if match else None


class TestStatusSync:
    """Test status synchronization from tasks to parent checkboxes."""

    def test_task_todo_to_active_updates_parent_checkbox(self, project_with_tasks):
        """When task status changes from todo to active, parent checkbox should update to [.]"""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]

        # Change task status to active
        set_status(task1, "active")
        stage_files(vault, task1)

        # Run pre-commit
        returncode, stdout, stderr = run_precommit(vault)
        assert returncode == 0, f"Pre-commit failed: {stderr}"

        # Check parent checkbox updated
        checkbox = get_task_checkbox(project, "myproject.task1")
        assert checkbox == ".", f"Expected [.] checkbox for active task, got [{checkbox}]"

    def test_task_todo_to_blocked_updates_parent_checkbox(self, project_with_tasks):
        """When task status changes to blocked, parent checkbox should update to [o]"""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]

        set_status(task1, "blocked")
        stage_files(vault, task1)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode == 0, f"Pre-commit failed: {stderr}"

        checkbox = get_task_checkbox(project, "myproject.task1")
        assert checkbox == "o", f"Expected [o] checkbox for blocked task, got [{checkbox}]"

    def test_task_todo_to_done_updates_parent_checkbox(self, project_with_tasks):
        """When task status changes to done, parent checkbox should update to [x]"""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]

        set_status(task1, "done")
        stage_files(vault, task1)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode == 0, f"Pre-commit failed: {stderr}"

        # Task should be archived, checkbox should be [x]
        checkbox = get_task_checkbox(project, "myproject.task1")
        assert checkbox == "x", f"Expected [x] checkbox for done task, got [{checkbox}]"

    def test_task_todo_to_dropped_updates_parent_checkbox(self, project_with_tasks):
        """When task status changes to dropped, parent checkbox should update to [~]"""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]

        set_status(task1, "dropped")
        stage_files(vault, task1)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode == 0, f"Pre-commit failed: {stderr}"

        checkbox = get_task_checkbox(project, "myproject.task1")
        assert checkbox == "~", f"Expected [~] checkbox for dropped task, got [{checkbox}]"

    def test_multiple_tasks_updated_same_commit(self, project_with_tasks):
        """When multiple tasks are updated in same commit, all checkboxes should update."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        task2 = project_with_tasks["task2"]
        task3 = project_with_tasks["task3"]
        project = project_with_tasks["project"]

        # Update all tasks to different statuses
        set_status(task1, "active")
        set_status(task2, "blocked")
        set_status(task3, "done")

        stage_files(vault, task1, task2, task3)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode == 0, f"Pre-commit failed: {stderr}"

        # Check all checkboxes updated correctly
        assert get_task_checkbox(project, "myproject.task1") == ".", "Task1 should be [.]"
        assert get_task_checkbox(project, "myproject.task2") == "o", "Task2 should be [o]"
        assert get_task_checkbox(project, "myproject.task3") == "x", "Task3 should be [x]"


class TestArchiveUnarchive:
    """Test archive and unarchive functionality."""

    def test_done_task_is_archived(self, project_with_tasks):
        """Task marked as done should be moved to archive/"""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        archive = vault / "archive"

        set_status(task1, "done")
        stage_files(vault, task1)

        run_precommit(vault)

        # Task should be in archive
        assert (archive / "myproject.task1.md").exists(), "Done task should be in archive"
        assert not task1.exists(), "Done task should not be in notes/"

    def test_dropped_task_is_archived(self, project_with_tasks):
        """Task marked as dropped should be moved to archive/"""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        archive = vault / "archive"

        set_status(task1, "dropped")
        stage_files(vault, task1)

        run_precommit(vault)

        assert (archive / "myproject.task1.md").exists(), "Dropped task should be in archive"

    def test_archived_task_link_updated_in_parent(self, project_with_tasks):
        """When task is archived, parent link should include archive/ prefix."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]

        set_status(task1, "done")
        stage_files(vault, task1)

        run_precommit(vault)

        content = project.read_text()
        assert "(archive/myproject.task1.md)" in content, "Parent should link to archive/task"

    def test_reactivated_task_is_unarchived(self, project_with_tasks):
        """Task changed from done to active should be moved back from archive/"""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        archive = vault / "archive"

        # First archive it
        set_status(task1, "done")
        stage_files(vault, task1)
        run_precommit(vault)

        # Now reactivate
        archived_task = archive / "myproject.task1.md"
        set_status(archived_task, "active")
        stage_files(vault, archived_task)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode == 0, f"Pre-commit failed: {stderr}"

        # Task should be back in notes/
        assert (vault / "myproject.task1.md").exists(), "Reactivated task should be in notes/"
        assert not archived_task.exists(), "Reactivated task should not be in archive/"

    def test_reactivated_task_checkbox_updated(self, project_with_tasks):
        """When task is reactivated, parent checkbox should update correctly."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]
        archive = vault / "archive"

        # Archive it
        set_status(task1, "done")
        stage_files(vault, task1)
        run_precommit(vault)

        # Reactivate
        archived_task = archive / "myproject.task1.md"
        set_status(archived_task, "active")
        stage_files(vault, archived_task)
        run_precommit(vault)

        # Checkbox should be [.] for active
        checkbox = get_task_checkbox(project, "myproject.task1")
        assert checkbox == ".", f"Expected [.] for reactivated task, got [{checkbox}]"

    def test_reactivated_task_link_updated_in_parent(self, project_with_tasks):
        """When task is unarchived, parent link should remove archive/ prefix."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]
        archive = vault / "archive"

        # Archive it
        set_status(task1, "done")
        stage_files(vault, task1)
        run_precommit(vault)

        # Verify archived
        content = project.read_text()
        assert "(archive/myproject.task1.md)" in content

        # Reactivate
        archived_task = archive / "myproject.task1.md"
        set_status(archived_task, "active")
        stage_files(vault, archived_task)
        run_precommit(vault)

        # Link should not have archive/ prefix
        content = project.read_text()
        assert "(myproject.task1.md)" in content, "Link should not have archive/ prefix"
        assert "(archive/myproject.task1.md)" not in content


class TestGroupStatusPropagation:
    """Test task group status calculation and propagation."""

    def test_group_becomes_blocked_when_child_blocked(self, project_with_group):
        """When any child task is blocked, group should become blocked."""
        vault = project_with_group["vault"]
        subtask1 = project_with_group["subtask1"]
        group = project_with_group["group"]

        set_status(subtask1, "blocked")
        stage_files(vault, subtask1)

        run_precommit(vault)

        # Group status should be blocked
        meta = get_frontmatter(group)
        assert meta.get("status") == "blocked", f"Group should be blocked, got {meta.get('status')}"

    def test_group_becomes_done_when_all_children_done(self, project_with_group):
        """When all children are done/dropped, group should become done."""
        vault = project_with_group["vault"]
        subtask1 = project_with_group["subtask1"]
        subtask2 = project_with_group["subtask2"]
        group = project_with_group["group"]

        set_status(subtask1, "done")
        set_status(subtask2, "done")
        stage_files(vault, subtask1, subtask2)

        run_precommit(vault)

        # Group status should be done
        meta = get_frontmatter(group)
        assert meta.get("status") == "done", f"Group should be done, got {meta.get('status')}"

    def test_group_becomes_active_when_child_active(self, project_with_group):
        """When any child is active (and none blocked), group should become active."""
        vault = project_with_group["vault"]
        subtask1 = project_with_group["subtask1"]
        group = project_with_group["group"]

        set_status(subtask1, "active")
        stage_files(vault, subtask1)

        run_precommit(vault)

        meta = get_frontmatter(group)
        assert meta.get("status") == "active", f"Group should be active, got {meta.get('status')}"

    def test_group_status_synced_to_project(self, project_with_group):
        """Group status change should sync checkbox to project."""
        vault = project_with_group["vault"]
        subtask1 = project_with_group["subtask1"]
        project = project_with_group["project"]

        set_status(subtask1, "blocked")
        stage_files(vault, subtask1)

        run_precommit(vault)

        # Project should show group as blocked [o]
        checkbox = get_task_checkbox(project, "myproject.group")
        assert checkbox == "o", f"Project should show group as [o], got [{checkbox}]"


class TestSeparator:
    """Test separator insertion between active and done tasks."""

    def test_separator_added_when_mixed_statuses(self, project_with_tasks):
        """Separator --- should be added between active and done tasks."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        task2 = project_with_tasks["task2"]
        task3 = project_with_tasks["task3"]
        project = project_with_tasks["project"]

        # Mix of active and done
        set_status(task1, "active")
        set_status(task2, "done")
        set_status(task3, "done")

        stage_files(vault, task1, task2, task3)
        run_precommit(vault)

        content = project.read_text()
        # Should have separator between active and done tasks
        assert "---" in content, "Separator should be added between active and done tasks"

    def test_separator_not_added_when_all_same_status(self, project_with_tasks):
        """No separator when all tasks have same category (all active or all done)."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        task2 = project_with_tasks["task2"]
        task3 = project_with_tasks["task3"]
        project = project_with_tasks["project"]

        # All active
        set_status(task1, "active")
        set_status(task2, "active")
        set_status(task3, "active")

        stage_files(vault, task1, task2, task3)
        run_precommit(vault)

        content = project.read_text()
        # Count --- occurrences (exclude frontmatter delimiters)
        body = content.split("---\n", 2)[-1]  # Skip frontmatter
        assert "---" not in body, "No separator needed when all tasks are active"


class TestTaskSorting:
    """Test task sorting in parent files."""

    def test_tasks_sorted_by_status(self, project_with_tasks):
        """Tasks should be sorted: blocked, active, todo, done, dropped."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        task2 = project_with_tasks["task2"]
        task3 = project_with_tasks["task3"]
        project = project_with_tasks["project"]

        # Set mixed statuses (in wrong order in file)
        set_status(task1, "done")     # Should be last
        set_status(task2, "blocked")  # Should be first
        set_status(task3, "active")   # Should be second

        stage_files(vault, task1, task2, task3)
        run_precommit(vault)

        content = project.read_text()

        # Find task lines
        task_lines = [l for l in content.split("\n") if l.startswith("- [")]

        # Verify order: blocked [o], active [.], done [x]
        assert "[o]" in task_lines[0], f"First task should be blocked: {task_lines[0]}"
        assert "[.]" in task_lines[1], f"Second task should be active: {task_lines[1]}"
        # Done task is after separator
        assert "[x]" in task_lines[-1], f"Last task should be done: {task_lines[-1]}"


class TestModifiedDate:
    """Test modified date updates."""

    def test_modified_date_updated_on_change(self, project_with_tasks):
        """Modified date should be updated when file is changed."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]

        # Get original date
        original_meta = get_frontmatter(task1)
        original_modified = original_meta.get("modified")

        # Change status
        set_status(task1, "active")
        stage_files(vault, task1)

        run_precommit(vault)

        # Check modified date changed
        new_meta = get_frontmatter(task1)
        new_modified = new_meta.get("modified")

        # Should contain time now (HH:MM format)
        assert new_modified is not None, "Modified date should be set"
        # The new format includes time
        assert ":" in str(new_modified) or len(str(new_modified)) > 10, \
            f"Modified should include time, got: {new_modified}"


class TestValidation:
    """Test frontmatter validation."""

    def test_invalid_task_status_rejected(self, project_with_tasks):
        """Invalid task status should cause pre-commit to fail."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]

        # Set invalid status
        content = task1.read_text()
        content = content.replace("status: todo", "status: invalid_status")
        task1.write_text(content)

        stage_files(vault, task1)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode != 0, "Pre-commit should fail on invalid status"
        assert "invalid" in stderr.lower() or "Invalid" in stderr, \
            f"Error should mention invalid status: {stderr}"

    def test_done_project_with_incomplete_tasks_rejected(self, project_with_tasks):
        """Project cannot be marked done if it has incomplete tasks."""
        vault = project_with_tasks["vault"]
        project = project_with_tasks["project"]

        # Try to mark project as done while tasks are incomplete
        content = project.read_text()
        content = content.replace("status: active", "status: done")
        project.write_text(content)

        stage_files(vault, project)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode != 0, "Pre-commit should fail when marking project done with incomplete tasks"

    def test_done_group_with_incomplete_tasks_rejected(self, project_with_group):
        """Task group cannot be marked done if its children are incomplete."""
        vault = project_with_group["vault"]
        group = project_with_group["group"]

        content = group.read_text().replace("status: todo", "status: done")
        group.write_text(content)

        stage_files(vault, group)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode != 0, "Pre-commit should fail when marking group done with incomplete tasks"
        assert "task-group" in stderr.lower() or "incomplete tasks" in stderr.lower()

    def test_dropped_group_with_incomplete_tasks_rejected(self, project_with_group):
        """Task group cannot be dropped if its children are incomplete."""
        vault = project_with_group["vault"]
        group = project_with_group["group"]

        content = group.read_text().replace("status: todo", "status: dropped")
        group.write_text(content)

        stage_files(vault, group)

        returncode, stdout, stderr = run_precommit(vault)
        assert returncode != 0, "Pre-commit should fail when dropping group with incomplete tasks"
        assert "task-group" in stderr.lower() or "incomplete tasks" in stderr.lower()


class TestLinkUpdates:
    """Test link updates during archive/unarchive."""

    def test_internal_links_updated_on_archive(self, project_with_tasks):
        """Links inside archived file should be updated with ../ prefix."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        archive = vault / "archive"

        set_status(task1, "done")
        stage_files(vault, task1)
        run_precommit(vault)

        # Check link in archived file
        archived = archive / "myproject.task1.md"
        content = archived.read_text()

        # Link to parent should have ../ prefix
        assert "(../myproject.md)" in content, \
            f"Archived file should link to ../parent, got: {content}"

    def test_internal_links_updated_on_unarchive(self, project_with_tasks):
        """Links inside unarchived file should have ../ prefix removed."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        archive = vault / "archive"

        # Archive
        set_status(task1, "done")
        stage_files(vault, task1)
        run_precommit(vault)

        # Unarchive
        archived = archive / "myproject.task1.md"
        set_status(archived, "active")
        stage_files(vault, archived)
        run_precommit(vault)

        # Check link in unarchived file
        unarchived = vault / "myproject.task1.md"
        content = unarchived.read_text()

        # Link should not have ../ prefix
        assert "(myproject.md)" in content, "Unarchived file should link to parent without ../"
        assert "(../myproject.md)" not in content, "Should not have ../ prefix after unarchive"


class TestProjectStatusPropagation:
    """Test project status automatic updates based on task status."""

    def test_project_becomes_active_when_task_active(self, temp_vault):
        """When any task becomes active, project should become active."""
        from datetime import date
        today = date.today().isoformat()

        # Create project with planning status
        project_path = temp_vault / "myproject.md"
        project_path.write_text(f"""\
---
created: {today}
modified: {today}
type: project
status: planning
---
# My Project

## Tasks
- [ ] [Task 1](myproject.task1)
""")

        task_path = temp_vault / "myproject.task1.md"
        task_path.write_text(f"""\
---
created: {today}
modified: {today}
type: task
status: todo
parent: myproject
---
# Task 1
""")

        # Stage initial files
        stage_files(temp_vault, project_path, task_path)
        subprocess.run(["git", "commit", "-m", "init"], cwd=temp_vault, capture_output=True)

        # Mark task as active
        set_status(task_path, "active")
        stage_files(temp_vault, task_path)

        returncode, stdout, stderr = run_precommit(temp_vault)
        assert returncode == 0, f"Pre-commit failed: {stderr}"

        # Project should now be active
        meta = get_frontmatter(project_path)
        assert meta.get("status") == "active", f"Project should be active, got {meta.get('status')}"

    def test_project_returns_to_planning_when_no_active_tasks(self, project_with_tasks):
        """When all active tasks are marked done, project should return to planning."""
        vault = project_with_tasks["vault"]
        task1 = project_with_tasks["task1"]
        project = project_with_tasks["project"]

        # First make task active (project is already active in fixture)
        set_status(task1, "active")
        stage_files(vault, task1)
        run_precommit(vault)

        # Now mark all tasks as done
        for i in range(1, 4):
            task = project_with_tasks[f"task{i}"]
            set_status(task, "done")
        stage_files(vault, project_with_tasks["task1"], project_with_tasks["task2"], project_with_tasks["task3"])

        run_precommit(vault)

        # Project should be planning now
        meta = get_frontmatter(project)
        assert meta.get("status") == "planning", f"Project should be planning, got {meta.get('status')}"

    def test_paused_project_not_auto_modified(self, temp_vault):
        """Paused project should not be automatically changed to active."""
        from datetime import date
        today = date.today().isoformat()

        # Create project with paused status
        project_path = temp_vault / "myproject.md"
        project_path.write_text(f"""\
---
created: {today}
modified: {today}
type: project
status: paused
---
# My Project

## Tasks
- [ ] [Task 1](myproject.task1)
""")

        task_path = temp_vault / "myproject.task1.md"
        task_path.write_text(f"""\
---
created: {today}
modified: {today}
type: task
status: todo
parent: myproject
---
# Task 1
""")

        # Stage initial files
        stage_files(temp_vault, project_path, task_path)
        subprocess.run(["git", "commit", "-m", "init"], cwd=temp_vault, capture_output=True)

        # Mark task as active
        set_status(task_path, "active")
        stage_files(temp_vault, task_path)
        run_precommit(temp_vault)

        # Project should still be paused
        meta = get_frontmatter(project_path)
        assert meta.get("status") == "paused", f"Paused project should stay paused, got {meta.get('status')}"

    def test_nested_task_activates_project(self, project_with_group):
        """When nested subtask becomes active, root project should become active."""
        vault = project_with_group["vault"]
        project = project_with_group["project"]
        subtask1 = project_with_group["subtask1"]

        # Set project to planning
        content = project.read_text()
        content = content.replace("status: active", "status: planning")
        project.write_text(content)

        stage_files(vault, project)
        subprocess.run(["git", "commit", "-m", "set planning"], cwd=vault, capture_output=True)

        # Mark nested subtask as active
        set_status(subtask1, "active")
        stage_files(vault, subtask1)

        run_precommit(vault)

        # Project should now be active
        meta = get_frontmatter(project)
        assert meta.get("status") == "active", f"Project should be active, got {meta.get('status')}"

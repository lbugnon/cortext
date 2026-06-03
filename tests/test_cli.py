"""Tests for CLI commands.

Tests cover:
- cor init
- cor new (project, task, note)
- cor status
- cor projects
- cor tree
- cor rename
- cor group
"""

import pytest
import frontmatter
from datetime import date
from click.testing import CliRunner

from cor.cli import cli


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def initialized_vault(temp_vault, runner):
    """Return a vault that has been initialized with cor init."""
    # temp_vault already has templates and backlog.md from conftest
    return temp_vault


class TestInit:
    """Test cor init command."""

    def test_init_creates_backlog_md(self, runner, tmp_path, monkeypatch):
        """cor init should create backlog.md"""
        monkeypatch.chdir(tmp_path)

        # Initialize git first
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

        result = runner.invoke(cli, ["init", "--yes"])
        assert result.exit_code == 0, f"Init failed: {result.output}"
        assert (tmp_path / "backlog.md").exists(), "backlog.md should be created"

    def test_init_creates_templates(self, runner, tmp_path, monkeypatch):
        """cor init should create template files."""
        monkeypatch.chdir(tmp_path)

        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

        result = runner.invoke(cli, ["init", "--yes"])
        assert result.exit_code == 0

        templates = tmp_path / "templates"
        assert templates.exists(), "templates/ directory should be created"
        assert (templates / "project.md").exists(), "project template should exist"
        assert (templates / "task.md").exists(), "task template should exist"
        assert (templates / "note.md").exists(), "note template should exist"


class TestNew:
    """Test cor new command."""

    def test_new_project_creates_file(self, runner, initialized_vault, monkeypatch):
        """cor new project should create a project file."""
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["new", "project", "testproject", "--no-edit"])
        assert result.exit_code == 0, f"New project failed: {result.output}"
        assert (initialized_vault / "testproject.md").exists()

    def test_new_project_has_frontmatter(self, runner, initialized_vault, monkeypatch):
        """New project should have proper frontmatter."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "testproject", "--no-edit"])

        content = (initialized_vault / "testproject.md").read_text()
        assert "status: planning" in content, "Project should have planning status"
        assert "created:" in content, "Project should have created date"

    def test_new_task_under_project(self, runner, initialized_vault, monkeypatch):
        """cor new task project.taskname should create task under project."""
        monkeypatch.chdir(initialized_vault)

        # Create project first
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])

        # Create task
        result = runner.invoke(cli, ["new", "task", "myproj.mytask", "some text"])
        assert result.exit_code == 0, f"New task failed: {result.output}"
        assert (initialized_vault / "myproj.mytask.md").exists()

    def test_new_task_has_parent_link(self, runner, initialized_vault, monkeypatch):
        """New task should have link back to parent project."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.mytask", "task work"])

        content = (initialized_vault / "myproj.mytask.md").read_text()
        assert "parent: myproj" in content, "Task should have parent field"
        assert "(myproj.md)" in content, "Task should have link to parent"

    def test_new_task_added_to_project(self, runner, initialized_vault, monkeypatch):
        """New task should be added to project's Tasks section."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.mytask", "task work"])

        content = (initialized_vault / "myproj.md").read_text()
        assert "(myproj.mytask.md)" in content, "Project should link to task"
        assert "[ ]" in content, "Project should have todo checkbox for task"

    def test_new_task_creates_group_if_needed(self, runner, initialized_vault, monkeypatch):
        """cor new task project.group.task should create group if it doesn't exist."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        result = runner.invoke(cli, ["new", "task", "myproj.mygroup.mytask", "task work"])

        assert result.exit_code == 0, f"Failed: {result.output}"
        assert (initialized_vault / "myproj.mygroup.md").exists(), "Group should be created"
        assert (initialized_vault / "myproj.mygroup.mytask.md").exists(), "Task should be created"

    def test_new_task_creates_deeper_hierarchy(self, runner, initialized_vault, monkeypatch):
        """cor new task project.group.smaller_group.task should create all intermediate groups."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        result = runner.invoke(cli, ["new", "task", "myproj.experiments.lr.sweep", "LR sweep task"])

        assert result.exit_code == 0, f"Failed: {result.output}"
        # Check all intermediate groups are created
        assert (initialized_vault / "myproj.experiments.md").exists(), "First group should be created"
        assert (initialized_vault / "myproj.experiments.lr.md").exists(), "Second group should be created"
        assert (initialized_vault / "myproj.experiments.lr.sweep.md").exists(), "Task should be created"
        
        # Verify parent links are correct
        task_content = (initialized_vault / "myproj.experiments.lr.sweep.md").read_text()
        assert "parent: myproj.experiments.lr" in task_content, "Task should have correct parent"
        
        # Verify task is added to immediate parent
        parent_content = (initialized_vault / "myproj.experiments.lr.md").read_text()
        assert "(myproj.experiments.lr.sweep.md)" in parent_content, "Task should be linked in parent"

    def test_new_project_rejects_dots(self, runner, initialized_vault, monkeypatch):
        """Project names cannot contain dots."""
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["new", "project", "my.project", "--no-edit"])
        assert result.exit_code != 0, "Should reject project name with dots"

    def test_new_note_under_project(self, runner, initialized_vault, monkeypatch):
        """cor new note project.notename should create note under project."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        result = runner.invoke(cli, ["new", "note", "myproj.meeting", "meeting notes"])

        assert result.exit_code == 0, f"New note failed: {result.output}"
        assert (initialized_vault / "myproj.meeting.md").exists()

        content = (initialized_vault / "myproj.meeting.md").read_text()
        assert "type: note" in content, "Note should have type: note"

    def test_new_note_auto_creates_top_level_parent(self, runner, initialized_vault, monkeypatch):
        """cor new note theme.note1 should auto-create theme.md as a note when missing."""
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["new", "note", "theme.note1", "child"])
        assert result.exit_code == 0, f"New note failed: {result.output}"

        parent_path = initialized_vault / "theme.md"
        assert parent_path.exists(), "Top-level parent theme.md should be auto-created"
        parent_content = parent_path.read_text()
        assert "type: note" in parent_content, "Auto-created parent of a note must be type: note"

    def test_new_note_auto_creates_hierarchy_of_notes(self, runner, initialized_vault, monkeypatch):
        """cor new note theme.sub.note1 should create theme and theme.sub as notes."""
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["new", "note", "theme.sub.note1", "deep"])
        assert result.exit_code == 0, f"New note failed: {result.output}"

        for stem in ("theme", "theme.sub", "theme.sub.note1"):
            path = initialized_vault / f"{stem}.md"
            assert path.exists(), f"{stem}.md should exist"
        assert "type: note" in (initialized_vault / "theme.md").read_text()
        assert "type: note" in (initialized_vault / "theme.sub.md").read_text()

    def test_new_task_auto_creates_top_level_project(self, runner, initialized_vault, monkeypatch):
        """cor new task missingproj.task1 should auto-create missingproj as a project."""
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["new", "task", "missingproj.task1", "work"])
        assert result.exit_code == 0, f"New task failed: {result.output}"

        parent_path = initialized_vault / "missingproj.md"
        assert parent_path.exists(), "Top-level project should be auto-created for a task"
        parent_content = parent_path.read_text()
        assert "type: project" in parent_content, "Auto-created parent of a task must be type: project"
        # Task should be linked in the project's Tasks section
        assert "(missingproj.task1.md)" in parent_content

    def test_top_level_note_excluded_from_projects(self, runner, initialized_vault, monkeypatch):
        """A top-level note must not show up in `cor projects` listings."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "realproj", "--no-edit"])
        runner.invoke(cli, ["new", "note", "knowledge.fact", "child"])  # creates knowledge.md as note

        from cor.utils import get_projects
        projects = get_projects()
        assert "realproj" in projects
        assert "knowledge" not in projects, "Top-level note must not be reported as a project"

    def test_expand_parses_checklist(self, runner, initialized_vault, monkeypatch):
        """cor expand should parse checklist from task file."""
        monkeypatch.chdir(initialized_vault)

        # Create a project and task with checklist
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])
        
        # Add checklist to task
        task_file = initialized_vault / "myproj.feature.md"
        post = frontmatter.load(task_file)
        post.content = """## Description

This is a feature with subtasks:

- [ ] implement-api
- [ ] write-tests
- [ ] update-docs

## Solution
"""
        with open(task_file, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)
        
        # Expand task to group
        result = runner.invoke(cli, ["expand", "myproj.feature"])
        assert result.exit_code == 0, f"Expand failed: {result.output}"
        
        # Check subtasks created
        assert (initialized_vault / "myproj.feature.implement-api.md").exists()
        assert (initialized_vault / "myproj.feature.write-tests.md").exists()
        assert (initialized_vault / "myproj.feature.update-docs.md").exists()

    def test_expand_removes_checklist_from_task(self, runner, initialized_vault, monkeypatch):
        """cor expand should remove checklist items from original task."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])
        
        task_file = initialized_vault / "myproj.feature.md"
        post = frontmatter.load(task_file)
        post.content = """## Description

- [ ] subtask1
- [ ] subtask2

## Solution
"""
        with open(task_file, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)
        
        runner.invoke(cli, ["expand", "myproj.feature"])
        
        # Check checklist removed
        updated_content = task_file.read_text()
        assert "- [ ] subtask1" not in updated_content
        assert "- [ ] subtask2" not in updated_content

    def test_expand_adds_subtask_links(self, runner, initialized_vault, monkeypatch):
        """cor expand should add links to subtasks in task file."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])
        
        task_file = initialized_vault / "myproj.feature.md"
        post = frontmatter.load(task_file)
        post.content = """## Description

- [ ] api-work
- [ ] test-work

## Solution
"""
        with open(task_file, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)
        
        runner.invoke(cli, ["expand", "myproj.feature"])
        
        # Check task file has links
        updated_content = task_file.read_text()
        assert "(myproj.feature.api-work.md)" in updated_content
        assert "(myproj.feature.test-work.md)" in updated_content

    def test_expand_subtasks_have_parent_link(self, runner, initialized_vault, monkeypatch):
        """Subtasks created by expand should link back to group."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])
        
        task_file = initialized_vault / "myproj.feature.md"
        post = frontmatter.load(task_file)
        post.content = "## Description\n\n- [ ] subtask1\n"
        with open(task_file, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)
        
        runner.invoke(cli, ["expand", "myproj.feature"])
        
        # Check subtask has parent link
        subtask = initialized_vault / "myproj.feature.subtask1.md"
        content = subtask.read_text()
        assert "parent: myproj.feature" in content
        assert "(myproj.feature.md)" in content

    def test_expand_requires_task_file(self, runner, initialized_vault, monkeypatch):
        """cor expand should fail if task file doesn't exist."""
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["expand", "nonexistent"])
        assert result.exit_code != 0
        assert "No files found" in result.output or "not found" in result.output.lower()

    def test_expand_requires_checklist(self, runner, initialized_vault, monkeypatch):
        """cor expand should fail if no checklist items found."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])
        
        # No checklist in task
        result = runner.invoke(cli, ["expand", "myproj.feature"])
        assert result.exit_code != 0
        assert "No checklist items" in result.output

    def test_expand_strips_dots_from_names(self, runner, initialized_vault, monkeypatch):
        """cor expand should strip dots from task names (dots are hierarchy separators)."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])

        task_file = initialized_vault / "myproj.feature.md"
        post = frontmatter.load(task_file)
        post.content = """## Description

- [ ] update-v1.2.3
- [ ] add-config.yaml

## Solution
"""
        with open(task_file, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)

        runner.invoke(cli, ["expand", "myproj.feature"])

        # Dots are stripped from task names (dots are hierarchy separators)
        assert (initialized_vault / "myproj.feature.update-v123.md").exists()
        assert (initialized_vault / "myproj.feature.add-configyaml.md").exists()

    def test_expand_handles_all_cortex_status_symbols(self, runner, initialized_vault, monkeypatch):
        """cor expand should parse checklist items with all Cortex status symbols."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])
        
        task_file = initialized_vault / "myproj.feature.md"
        post = frontmatter.load(task_file)
        post.content = """## Description

Mix of different statuses:

- [ ] todo-task
- [.] active-task
- [o] blocked-task
- [/] waiting-task
- [x] done-task
- [~] dropped-task

## Solution
"""
        with open(task_file, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)
        
        runner.invoke(cli, ["expand", "myproj.feature"])
        
        # Check that all tasks are created regardless of status symbol
        assert (initialized_vault / "myproj.feature.todo-task.md").exists()
        assert (initialized_vault / "myproj.feature.active-task.md").exists()
        assert (initialized_vault / "myproj.feature.blocked-task.md").exists()
        assert (initialized_vault / "myproj.feature.waiting-task.md").exists()
        assert (initialized_vault / "myproj.feature.done-task.md").exists()
        assert (initialized_vault / "myproj.feature.dropped-task.md").exists()
        
        # Check that all checklist items are removed
        updated_content = task_file.read_text()
        assert "- [ ] todo-task" not in updated_content
        assert "- [.] active-task" not in updated_content
        assert "- [o] blocked-task" not in updated_content
        assert "- [/] waiting-task" not in updated_content
        assert "- [x] done-task" not in updated_content
        assert "- [~] dropped-task" not in updated_content
        
        # Check that tasks have the correct status from their checklist symbols
        todo_task = frontmatter.load(initialized_vault / "myproj.feature.todo-task.md")
        assert todo_task['status'] == 'todo'
        
        active_task = frontmatter.load(initialized_vault / "myproj.feature.active-task.md")
        assert active_task['status'] == 'active'
        
        blocked_task = frontmatter.load(initialized_vault / "myproj.feature.blocked-task.md")
        assert blocked_task['status'] == 'blocked'
        
        waiting_task = frontmatter.load(initialized_vault / "myproj.feature.waiting-task.md")
        assert waiting_task['status'] == 'waiting'
        
        done_task = frontmatter.load(initialized_vault / "myproj.feature.done-task.md")
        assert done_task['status'] == 'done'
        
        dropped_task = frontmatter.load(initialized_vault / "myproj.feature.dropped-task.md")
        assert dropped_task['status'] == 'dropped'

    def test_expand_truncates_long_filenames(self, runner, initialized_vault, monkeypatch):
        """cor expand should truncate very long task names to avoid filesystem errors."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.feature", "--no-edit"])
        
        task_file = initialized_vault / "myproj.feature.md"
        post = frontmatter.load(task_file)
        # Create a very long task name (>300 chars) that would exceed filename limits
        long_name = "this-is-very-long-task-name-that-would-normally-exceed-the-maximum-filename-length-limit-of-most-filesystems" * 4
        post.content = f"""## Description

- [ ] {long_name}

## Solution
"""
        with open(task_file, 'wb') as f:
            frontmatter.dump(post, f, sort_keys=False)
        
        result = runner.invoke(cli, ["expand", "myproj.feature"])
        assert result.exit_code == 0, f"Expand failed: {result.output}"
        
        # Check that a subtask file was created (truncated)
        subtask_files = list(initialized_vault.glob("myproj.feature.this-is-very-long*.md"))
        assert len(subtask_files) == 1, "Should create exactly one subtask file with truncated name"
        
        # Verify the filename is not too long (should be <= 255 chars)
        filename = subtask_files[0].name
        assert len(filename) <= 255, f"Filename too long: {len(filename)} chars"

class TestLog:
    """Test cor log command."""

    def test_log_appends_to_inbox(self, runner, initialized_vault, monkeypatch):
        """cor log should append bullet to backlog inbox."""
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["inbox", "add", "Capture an idea"])
        assert result.exit_code == 0, f"inbox add failed: {result.output}"

        content = (initialized_vault / "backlog.md").read_text()
        assert "- Capture an idea" in content

    def test_log_creates_inbox_if_missing(self, runner, initialized_vault, monkeypatch):
        """log should create Inbox section if absent."""
        monkeypatch.chdir(initialized_vault)

        backlog_path = initialized_vault / "backlog.md"
        today = date.today().isoformat()
        backlog_path.write_text(f"""\
---
created: {today}
modified: {today}
---
# Backlog
""")

        result = runner.invoke(cli, ["inbox", "add", "New backlog item"])
        assert result.exit_code == 0, f"inbox add failed: {result.output}"

        content = backlog_path.read_text()
        assert "## Inbox" in content
        assert "- New backlog item" in content


class TestTag:
    """Test cor tag command."""

    def test_tag_add_and_remove(self, runner, initialized_vault, monkeypatch):
        """cor tag should add and remove tags on a file."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "tagproj", "--no-edit"])

        add = runner.invoke(cli, ["tag", "tagproj", "ml", "research"])
        assert add.exit_code == 0, f"Add tags failed: {add.output}"

        post = frontmatter.load(initialized_vault / "tagproj.md")
        assert post.get("tags") == ["ml", "research"]

        remove = runner.invoke(cli, ["tag", "tagproj", "-d", "ml"])
        assert remove.exit_code == 0, f"Remove tag failed: {remove.output}"

        post = frontmatter.load(initialized_vault / "tagproj.md")
        assert post.get("tags") == ["research"]


class TestStatus:
    """Test cor status command."""

    def test_status_shows_overdue(self, runner, initialized_vault, monkeypatch):
        """cor daily should show overdue tasks."""
        monkeypatch.chdir(initialized_vault)

        # Create a task with past due date
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.overdue", "task work"])

        task_path = initialized_vault / "myproj.overdue.md"
        content = task_path.read_text()
        content = content.replace("due:", "due: 2020-01-01")
        task_path.write_text(content)

        result = runner.invoke(cli, ["daily"])
        assert "Overdue" in result.output or "overdue" in result.output.lower()


class TestProjects:
    """Test cor projects command."""

    def test_projects_lists_projects(self, runner, initialized_vault, monkeypatch):
        """cor projects should list all projects."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "proj1", "--no-edit"])
        runner.invoke(cli, ["new", "project", "proj2", "--no-edit"])

        result = runner.invoke(cli, ["projects"])
        assert result.exit_code == 0
        assert "Proj1" in result.output or "proj1" in result.output.lower()
        assert "Proj2" in result.output or "proj2" in result.output.lower()

    def test_projects_shows_status(self, runner, initialized_vault, monkeypatch):
        """cor projects should show project status."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        
        result = runner.invoke(cli, ["projects"])
        assert "planning" in result.output.lower(), "Should show planning status" 


class TestTree:
    """Test cor tree command."""

    def test_tree_shows_tasks(self, runner, initialized_vault, monkeypatch):
        """cor tree should show project tasks."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task work"])
        runner.invoke(cli, ["new", "task", "myproj.task2", "task work"])

        result = runner.invoke(cli, ["tree", "myproj"])
        assert result.exit_code == 0, f"Tree failed: {result.output}"
        assert "Task1" in result.output or "task1" in result.output.lower()
        assert "Task2" in result.output or "task2" in result.output.lower()

    def test_tree_shows_nested_tasks(self, runner, initialized_vault, monkeypatch):
        """cor tree should show nested task groups."""
        monkeypatch.chdir(initialized_vault)
        runner.invoke(cli, ["init", "--yes"])
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group.subtask", "task work"])

        result = runner.invoke(cli, ["tree", "myproj"])
        assert result.exit_code == 0
        # Should show group and subtask
        assert "Group" in result.output or "group" in result.output.lower()
        assert "Subtask" in result.output or "subtask" in result.output.lower()

    def test_tree_shows_note_count(self, runner, initialized_vault, monkeypatch):
        """cor tree should mention attached notes."""
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "note", "myproj.brainstorm", "ideas"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task work"])
        runner.invoke(cli, ["new", "note", "myproj.task1.context", "context"])

        result = runner.invoke(cli, ["tree", "myproj"])
        assert result.exit_code == 0, f"Tree failed: {result.output}"

        output = result.output.lower()
        assert "and 1 note" in output, "Should show project-level note count"
        assert any("task1" in line and "and 1 note" in line for line in output.splitlines()), "Task should surface attached note count"

    def test_tree_depth_option(self, runner, initialized_vault, monkeypatch):
        """cor tree --depth should limit display depth."""
        monkeypatch.chdir(initialized_vault)

        # Create nested structure: project > g1 > sg1 > task1
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.g1.sg1.task1", "deep task"])
        runner.invoke(cli, ["new", "task", "myproj.g1.task2", "mid task"])

        # Without depth limit: should show all levels
        result = runner.invoke(cli, ["tree", "myproj"])
        assert result.exit_code == 0
        assert "task1" in result.output.lower(), "Should show deeply nested task without depth limit"

        # With depth=1: should not show task1 (too deep)
        result = runner.invoke(cli, ["tree", "myproj", "--depth", "1"])
        assert result.exit_code == 0
        assert "g1" in result.output.lower(), "Should show first level group"
        assert "task1" not in result.output.lower(), "Should not show deeply nested task with depth=1"
        assert "task2" in result.output.lower(), "Should show task at depth 1"

        # With depth=2: should show task1
        result = runner.invoke(cli, ["tree", "myproj", "--depth", "2"])
        assert result.exit_code == 0
        assert "task1" in result.output.lower(), "Should show deeply nested task with depth=2"

    def test_tree_focus_on_group(self, runner, initialized_vault, monkeypatch):
        """cor tree project.group should focus on a specific task group."""
        monkeypatch.chdir(initialized_vault)

        # Create project with groups
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group.task1", "task work", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group.task2", "task work", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.othertask", "other task", "--no-edit"])

        # Focus on the group - should show only group tasks
        result = runner.invoke(cli, ["tree", "myproj.group"])
        assert result.exit_code == 0, f"Tree group focus failed: {result.output}"
        
        output = result.output.lower()
        assert "task1" in output, "Should show task1 in group"
        assert "task2" in output, "Should show task2 in group"
        assert "othertask" not in output, "Should not show tasks outside the group"

    def test_tree_focus_on_subgroup(self, runner, initialized_vault, monkeypatch):
        """cor tree project.group.subgroup should focus on nested groups."""
        monkeypatch.chdir(initialized_vault)

        # Create nested structure
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group.sub.task1", "deep task", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group.task2", "group task", "--no-edit"])

        # Focus on the subgroup
        result = runner.invoke(cli, ["tree", "myproj.group.sub"])
        assert result.exit_code == 0, f"Tree subgroup focus failed: {result.output}"
        
        output = result.output.lower()
        assert "task1" in output, "Should show task1 in subgroup"
        assert "task2" not in output, "Should not show tasks in parent group"


class TestRename:
    """Test cor rename command."""

    def test_rename_project(self, runner, initialized_vault, monkeypatch):
        """cor rename should rename a project and its tasks."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        import subprocess
        subprocess.run(["git", "init"], cwd=initialized_vault, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=initialized_vault, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=initialized_vault, capture_output=True)

        runner.invoke(cli, ["new", "project", "oldname", "--no-edit"])
        runner.invoke(cli, ["new", "task", "oldname.task1", "task work"])

        result = runner.invoke(cli, ["rename", "oldname", "newname"])
        assert result.exit_code == 0, f"Rename failed: {result.output}"

        # Check files renamed
        assert (initialized_vault / "newname.md").exists(), "Project should be renamed"
        assert (initialized_vault / "newname.task1.md").exists(), "Task should be renamed"
        assert not (initialized_vault / "oldname.md").exists(), "Old project should not exist"

    def test_rename_updates_links(self, runner, initialized_vault, monkeypatch):
        """cor rename should update links in parent files."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        import subprocess
        subprocess.run(["git", "init"], cwd=initialized_vault, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=initialized_vault, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=initialized_vault, capture_output=True)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.oldtask", "task work"])

        # Rename task
        result = runner.invoke(cli, ["rename", "myproj.oldtask", "myproj.newtask"])
        assert result.exit_code == 0, f"Rename failed: {result.output}"

        # Check link updated in project
        content = (initialized_vault / "myproj.md").read_text()
        assert "(myproj.newtask.md)" in content, "Project should link to renamed task"
        assert "(myproj.oldtask.md)" not in content, "Old link should not exist"


class TestGroup:
    """Test cor group command."""

    def test_group_creates_group_file(self, runner, initialized_vault, monkeypatch):
        """cor group should create a new group file."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task work"])
        runner.invoke(cli, ["new", "task", "myproj.task2", "task work"])

        result = runner.invoke(cli, ["group", "myproj.refactor", "task1", "task2"])
        assert result.exit_code == 0, f"Group failed: {result.output}"

        # Check group file created
        assert (initialized_vault / "myproj.refactor.md").exists(), "Group file should be created"

    def test_group_moves_tasks(self, runner, initialized_vault, monkeypatch):
        """cor group should move tasks under the group."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task work"])
        runner.invoke(cli, ["new", "task", "myproj.task2", "task work"])

        result = runner.invoke(cli, ["group", "myproj.mygroup", "task1", "task2"])
        assert result.exit_code == 0, f"Group failed: {result.output}"

        # Check tasks renamed
        assert (initialized_vault / "myproj.mygroup.task1.md").exists(), "Task1 should be under group"
        assert (initialized_vault / "myproj.mygroup.task2.md").exists(), "Task2 should be under group"
        assert not (initialized_vault / "myproj.task1.md").exists(), "Old task1 should not exist"
        assert not (initialized_vault / "myproj.task2.md").exists(), "Old task2 should not exist"

    def test_group_updates_parent_links(self, runner, initialized_vault, monkeypatch):
        """cor group should update parent links in moved tasks."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task work"])

        result = runner.invoke(cli, ["group", "myproj.mygroup", "task1"])
        assert result.exit_code == 0, f"Group failed: {result.output}"

        # Check parent updated in task
        content = (initialized_vault / "myproj.mygroup.task1.md").read_text()
        assert "parent: myproj.mygroup" in content, "Parent should be updated to group"
        assert "[< Mygroup](myproj.mygroup.md)" in content, "Back link should point to group"

    def test_group_updates_project_tasks_section(self, runner, initialized_vault, monkeypatch):
        """cor group should add group to project and remove old task entries."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task work"])

        result = runner.invoke(cli, ["group", "myproj.mygroup", "task1"])
        assert result.exit_code == 0, f"Group failed: {result.output}"

        # Check project file
        content = (initialized_vault / "myproj.md").read_text()
        assert "(myproj.mygroup.md)" in content, "Project should link to group"
        assert "(myproj.task1.md)" not in content, "Old task link should be removed"

    def test_group_adds_tasks_to_group(self, runner, initialized_vault, monkeypatch):
        """cor group should add task entries to the group file."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task work"])
        runner.invoke(cli, ["new", "task", "myproj.task2", "task work"])

        result = runner.invoke(cli, ["group", "myproj.mygroup", "task1", "task2"])
        assert result.exit_code == 0, f"Group failed: {result.output}"

        # Check group file has tasks
        content = (initialized_vault / "myproj.mygroup.md").read_text()
        assert "(myproj.mygroup.task1.md)" in content, "Group should link to task1"
        assert "(myproj.mygroup.task2.md)" in content, "Group should link to task2"

    def test_group_requires_project(self, runner, initialized_vault, monkeypatch):
        """cor group should fail if project doesn't exist."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        result = runner.invoke(cli, ["group", "nonexistent.mygroup", "task1"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_group_requires_tasks(self, runner, initialized_vault, monkeypatch):
        """cor group should fail if no tasks specified."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])

        result = runner.invoke(cli, ["group", "myproj.mygroup"])
        assert result.exit_code != 0
        assert "task" in result.output.lower()

    def test_group_validates_task_exists(self, runner, initialized_vault, monkeypatch):
        """cor group should fail if task doesn't exist."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])

        result = runner.invoke(cli, ["group", "myproj.mygroup", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_group_fails_if_group_exists(self, runner, initialized_vault, monkeypatch):
        """cor group should fail if group already exists."""
        monkeypatch.setenv("CORTEX_VAULT", str(initialized_vault))
        monkeypatch.chdir(initialized_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.existinggroup", "some text"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task info"])

        result = runner.invoke(cli, ["group", "myproj.existinggroup", "task1"])
        assert result.exit_code != 0
        assert "exists" in result.output.lower()

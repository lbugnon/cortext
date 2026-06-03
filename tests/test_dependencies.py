"""Tests for dependency tracking functionality.

Tests cover:
- Core dependency resolution functions
- Circular dependency detection
- CLI commands (depend add/remove/list)
- Maintenance integration (rename/delete)
- Display integration (tree view)
"""

import pytest
import frontmatter
from click.testing import CliRunner
from datetime import date
from pathlib import Path

from cor.cli import cli
from cor.dependencies import (
    calculate_inverse_dependencies,
    check_dependencies_met,
    detect_circular_dependencies,
    validate_dependencies,
    get_dependency_info,
)
from cor.core.notes import parse_note, find_notes


@pytest.fixture
def vault_with_dependencies(temp_vault):
    """Create a vault with tasks/projects that have dependencies.

    Structure:
    - project1.md (requires: [])
    - project1.task1.md (requires: [])
    - project1.task2.md (requires: [project1.task1])
    - project2.md (requires: [project1])
    - project2.task1.md (requires: [project1.task1, project1.task2])
    """
    today = date.today().isoformat()

    # Create project1 with tasks
    (temp_vault / "project1.md").write_text(f"""\
---
type: project
status: active
created: {today}
modified: {today}
requires: []
---
# Project 1

## Tasks
- [ ] [Task 1](project1.task1.md)
- [ ] [Task 2](project1.task2.md)
""")

    (temp_vault / "project1.task1.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
parent: project1
requires: []
---
# Task 1

[< Project 1](project1.md)

## Description
First task
""")

    (temp_vault / "project1.task2.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
parent: project1
requires:
  - project1.task1
---
# Task 2

[< Project 1](project1.md)

## Description
Depends on task1
""")

    # Create project2 that depends on project1
    (temp_vault / "project2.md").write_text(f"""\
---
type: project
status: planning
created: {today}
modified: {today}
requires:
  - project1
---
# Project 2

Depends on Project 1 completion

## Tasks
- [ ] [Task 1](project2.task1.md)
""")

    # Create task with multiple requirements
    (temp_vault / "project2.task1.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
parent: project2
requires:
  - project1.task1
  - project1.task2
---
# Task 1

[< Project 2](project2.md)

## Description
Depends on multiple tasks from project1
""")

    return temp_vault


class TestCoreDependencyFunctions:
    """Test core dependency resolution functions."""

    def test_calculate_inverse_dependencies(self, vault_with_dependencies):
        """Test inverse dependency mapping calculation."""
        notes = find_notes(vault_with_dependencies)
        inverse = calculate_inverse_dependencies(notes)

        # project1 is required by project2
        assert "project1" in inverse
        assert "project2" in inverse["project1"]

        # project1.task1 is required by project1.task2 and project2.task1
        assert "project1.task1" in inverse
        assert "project1.task2" in inverse["project1.task1"]
        assert "project2.task1" in inverse["project1.task1"]

        # project1.task2 is required by project2.task1
        assert "project1.task2" in inverse
        assert "project2.task1" in inverse["project1.task2"]

        # Items with no dependents should not be in inverse map
        assert "project2.task1" not in inverse

    def test_check_dependencies_met_all_todo(self, vault_with_dependencies):
        """Test dependency checking when requirements are incomplete."""
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        # project1.task2 requires project1.task1 (which is todo)
        task2 = notes_by_stem["project1.task2"]
        all_met, unmet = check_dependencies_met(task2, notes)

        assert not all_met, "Dependencies should not be met"
        assert "project1.task1" in unmet, "task1 should be in unmet list"

    def test_check_dependencies_met_some_done(self, vault_with_dependencies):
        """Test dependency checking when some requirements are complete."""
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        # Mark project1.task1 as done
        task1_path = vault_with_dependencies / "project1.task1.md"
        post = frontmatter.load(task1_path)
        post["status"] = "done"
        with open(task1_path, "wb") as f:
            frontmatter.dump(post, f, sort_keys=False)

        # Re-parse notes
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        # project1.task2 should now have met dependencies
        task2 = notes_by_stem["project1.task2"]
        all_met, unmet = check_dependencies_met(task2, notes)

        assert all_met, "Dependencies should be met"
        assert len(unmet) == 0, "No unmet dependencies"

    def test_check_dependencies_met_dropped_counts_as_done(self, vault_with_dependencies):
        """Test that dropped tasks count as completed for dependency purposes."""
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        # Mark project1.task1 as dropped
        task1_path = vault_with_dependencies / "project1.task1.md"
        post = frontmatter.load(task1_path)
        post["status"] = "dropped"
        with open(task1_path, "wb") as f:
            frontmatter.dump(post, f, sort_keys=False)

        # Re-parse notes
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        # project1.task2 should have met dependencies (dropped counts as done)
        task2 = notes_by_stem["project1.task2"]
        all_met, unmet = check_dependencies_met(task2, notes)

        assert all_met, "Dependencies should be met (dropped counts as done)"
        assert len(unmet) == 0

    def test_detect_circular_dependencies_none(self, vault_with_dependencies):
        """Test circular dependency detection when there are none."""
        notes = find_notes(vault_with_dependencies)

        # No circular dependencies in the test fixture
        result = detect_circular_dependencies("project1.task1", notes)
        assert result is None, "Should not detect circular dependency"

        result = detect_circular_dependencies("project2", notes)
        assert result is None, "Should not detect circular dependency"

    def test_detect_circular_dependencies_simple_cycle(self, temp_vault):
        """Test detection of simple A->B->A cycle."""
        today = date.today().isoformat()

        # Create A requires B
        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taskb
---
# Task A
""")

        # Create B requires A (circular)
        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taska
---
# Task B
""")

        notes = find_notes(temp_vault)

        # Should detect circular dependency
        result = detect_circular_dependencies("taska", notes)
        assert result is not None, "Should detect circular dependency"
        assert "taska" in result
        assert "taskb" in result
        assert result[0] == result[-1], "Cycle should start and end with same node"

    def test_detect_circular_dependencies_three_node_cycle(self, temp_vault):
        """Test detection of A->B->C->A cycle."""
        today = date.today().isoformat()

        # Create A->B->C->A cycle
        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taskb
---
# Task A
""")

        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taskc
---
# Task B
""")

        (temp_vault / "taskc.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taska
---
# Task C
""")

        notes = find_notes(temp_vault)

        # Should detect circular dependency from any node
        result = detect_circular_dependencies("taska", notes)
        assert result is not None, "Should detect circular dependency"
        assert len(set(result[:-1])) == 3, "Should include all 3 nodes"

    def test_validate_dependencies_success(self, vault_with_dependencies):
        """Test validation passes for valid dependencies."""
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        task2 = notes_by_stem["project1.task2"]
        errors = validate_dependencies(task2, notes)

        assert len(errors) == 0, "Valid dependencies should have no errors"

    def test_validate_dependencies_missing_requirement(self, temp_vault):
        """Test validation detects missing requirements."""
        today = date.today().isoformat()

        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - nonexistent
---
# Task A
""")

        notes = find_notes(temp_vault)
        notes_by_stem = {n.path.stem: n for n in notes}

        taska = notes_by_stem["taska"]
        errors = validate_dependencies(taska, notes)

        assert len(errors) > 0, "Should detect missing requirement"
        assert any("does not exist" in e.lower() for e in errors)

    def test_validate_dependencies_self_reference(self, temp_vault):
        """Test validation detects self-referencing dependency."""
        today = date.today().isoformat()

        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taska
---
# Task A
""")

        notes = find_notes(temp_vault)
        notes_by_stem = {n.path.stem: n for n in notes}

        taska = notes_by_stem["taska"]
        errors = validate_dependencies(taska, notes)

        assert len(errors) > 0, "Should detect self-reference"
        assert any("cannot require itself" in e.lower() for e in errors)

    def test_validate_dependencies_circular(self, temp_vault):
        """Test validation detects circular dependencies."""
        today = date.today().isoformat()

        # Create A->B->A cycle
        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taskb
---
# Task A
""")

        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taska
---
# Task B
""")

        notes = find_notes(temp_vault)
        notes_by_stem = {n.path.stem: n for n in notes}

        taska = notes_by_stem["taska"]
        errors = validate_dependencies(taska, notes)

        assert len(errors) > 0, "Should detect circular dependency"
        assert any("circular" in e.lower() for e in errors)

    def test_get_dependency_info_complete(self, vault_with_dependencies):
        """Test getting complete dependency info."""
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        # Get info for project2.task1 (has 2 requirements, blocks nothing)
        task = notes_by_stem["project2.task1"]
        dep_info = get_dependency_info(task, notes)

        assert dep_info.note_stem == "project2.task1"
        assert dep_info.note_type == "task"
        assert len(dep_info.requires) == 2
        assert "project1.task1" in dep_info.requires
        assert "project1.task2" in dep_info.requires
        assert not dep_info.all_requirements_met
        assert len(dep_info.blocked_by) == 2
        assert len(dep_info.blocks) == 0
        assert len(dep_info.missing_requirements) == 0
        assert len(dep_info.circular_dependencies) == 0

    def test_get_dependency_info_blocks(self, vault_with_dependencies):
        """Test getting dependency info shows what this item blocks."""
        notes = find_notes(vault_with_dependencies)
        notes_by_stem = {n.path.stem: n for n in notes}

        # Get info for project1.task1 (blocks task2 and project2.task1)
        task1 = notes_by_stem["project1.task1"]
        dep_info = get_dependency_info(task1, notes)

        assert len(dep_info.blocks) == 2
        assert "project1.task2" in dep_info.blocks
        assert "project2.task1" in dep_info.blocks


class TestDependencyCLICommands:
    """Test CLI commands for dependency management."""

    def test_depend_add_basic(self, runner, temp_vault, monkeypatch):
        """Test adding a basic dependency."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        # Create two tasks
        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task A
""")

        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task B
""")

        # Add dependency: taskb requires taska
        result = runner.invoke(cli, ["depend", "add", "taskb", "taska"])
        assert result.exit_code == 0, f"Command failed: {result.output}"

        # Check frontmatter was updated
        post = frontmatter.load(temp_vault / "taskb.md")
        assert "requires" in post.metadata
        assert "taska" in post["requires"]

    def test_depend_add_multiple(self, runner, temp_vault, monkeypatch):
        """Test adding multiple dependencies to same task."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        # Create three tasks
        for name in ["taska", "taskb", "taskc"]:
            (temp_vault / f"{name}.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# {name.title()}
""")

        # Add dependencies: taskc requires taska and taskb
        runner.invoke(cli, ["depend", "add", "taskc", "taska"])
        runner.invoke(cli, ["depend", "add", "taskc", "taskb"])

        # Check both were added
        post = frontmatter.load(temp_vault / "taskc.md")
        assert len(post["requires"]) == 2
        assert "taska" in post["requires"]
        assert "taskb" in post["requires"]

    def test_depend_add_duplicate(self, runner, temp_vault, monkeypatch):
        """Test adding same dependency twice is idempotent."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task A
""")

        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task B
""")

        # Add dependency twice
        runner.invoke(cli, ["depend", "add", "taskb", "taska"])
        result = runner.invoke(cli, ["depend", "add", "taskb", "taska"])

        assert result.exit_code == 0
        assert "already exists" in result.output.lower()

        # Should still have only one entry
        post = frontmatter.load(temp_vault / "taskb.md")
        assert post["requires"].count("taska") == 1

    def test_depend_add_project_to_project(self, runner, temp_vault, monkeypatch):
        """Test adding dependency between projects."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        (temp_vault / "project1.md").write_text(f"""\
---
type: project
status: active
created: {today}
modified: {today}
requires: []
---
# Project 1
""")

        (temp_vault / "project2.md").write_text(f"""\
---
type: project
status: planning
created: {today}
modified: {today}
requires: []
---
# Project 2
""")

        # Add dependency: project2 requires project1
        result = runner.invoke(cli, ["depend", "add", "project2", "project1"])
        assert result.exit_code == 0

        post = frontmatter.load(temp_vault / "project2.md")
        assert "project1" in post["requires"]

    def test_depend_remove_basic(self, runner, temp_vault, monkeypatch):
        """Test removing a dependency."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        # Create task with dependency
        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task A
""")

        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taska
---
# Task B
""")

        # Remove dependency
        result = runner.invoke(cli, ["depend", "remove", "taskb", "taska"])
        assert result.exit_code == 0

        # Check it was removed
        post = frontmatter.load(temp_vault / "taskb.md")
        assert "taska" not in post["requires"]
        assert len(post["requires"]) == 0

    def test_depend_remove_nonexistent(self, runner, temp_vault, monkeypatch):
        """Test removing dependency that doesn't exist."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task A
""")

        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task B
""")

        # Try to remove non-existent dependency
        result = runner.invoke(cli, ["depend", "remove", "taskb", "taska"])
        assert result.exit_code == 0
        assert "does not exist" in result.output.lower()

    def test_depend_list_no_dependencies(self, runner, temp_vault, monkeypatch):
        """Test listing dependencies for item with none."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task A
""")

        result = runner.invoke(cli, ["depend", "list", "taska"])
        assert result.exit_code == 0
        assert "Task A" in result.output
        assert "No requirements" in result.output
        assert "Does not block" in result.output

    def test_depend_list_with_requirements(self, runner, vault_with_dependencies, monkeypatch):
        """Test listing dependencies shows requirements."""
        monkeypatch.chdir(vault_with_dependencies)

        result = runner.invoke(cli, ["depend", "list", "project1.task2"])
        assert result.exit_code == 0
        assert "Task 2" in result.output
        assert "Requires:" in result.output
        assert "Task 1" in result.output
        assert "Waiting on" in result.output

    def test_depend_list_shows_blocks(self, runner, vault_with_dependencies, monkeypatch):
        """Test listing dependencies shows what this item blocks."""
        monkeypatch.chdir(vault_with_dependencies)

        result = runner.invoke(cli, ["depend", "list", "project1.task1"])
        assert result.exit_code == 0
        assert "Task 1" in result.output
        assert "Blocks these items:" in result.output
        assert "Task 2" in result.output


class TestDependencyMaintenance:
    """Test dependency handling in maintenance operations."""

    def test_dependencies_updated_on_rename(self, runner, temp_vault, monkeypatch):
        """Test that dependencies are updated when a task is renamed."""
        monkeypatch.chdir(temp_vault)
        today = date.today().isoformat()

        # Create tasks with dependency
        (temp_vault / "taska.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires: []
---
# Task A
""")

        (temp_vault / "taskb.md").write_text(f"""\
---
type: task
status: todo
created: {today}
modified: {today}
requires:
  - taska
---
# Task B
""")

        # Rename taska to newname
        result = runner.invoke(cli, ["rename", "taska", "newname"])
        assert result.exit_code == 0

        # Check taskb now requires newname
        post = frontmatter.load(temp_vault / "taskb.md")
        assert "newname" in post["requires"]
        assert "taska" not in post["requires"]


class TestDependencyDisplay:
    """Test dependency display in status views."""

    def test_tree_shows_dependency_indicator(self, runner, vault_with_dependencies, monkeypatch):
        """Test that tree view shows dependency indicators."""
        monkeypatch.chdir(vault_with_dependencies)

        result = runner.invoke(cli, ["tree", "project1"])
        assert result.exit_code == 0

        # Should show indicator for task2 (has unmet requirement)
        assert "→" in result.output or "[→" in result.output

    def test_tree_shows_project_dependencies(self, runner, vault_with_dependencies, monkeypatch):
        """Test that tree view shows project-level dependencies."""
        monkeypatch.chdir(vault_with_dependencies)

        result = runner.invoke(cli, ["tree", "project2"])
        assert result.exit_code == 0

        # Should show project2 requires project1
        assert "→" in result.output or "Requires" in result.output

import subprocess
from click.testing import CliRunner
import pytest

from cor.cli import cli


@pytest.fixture
def initialized_vault(temp_vault, runner, monkeypatch):
    monkeypatch.chdir(temp_vault)
    # Init git for hooks/maintenance in rename
    subprocess.run(["git", "init"], cwd=temp_vault, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_vault, capture_output=True)
    return temp_vault


def test_move_task_within_project_target_does_not_exist(runner, initialized_vault):
    """When target doesn't exist in same project, do full rename."""
    result = runner.invoke(cli, ["new", "project", "py1", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "task", "py1.task", "--no-edit"]) 
    assert result.exit_code == 0

    result = runner.invoke(cli, ["move", "py1.task", "py1.task_new"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "py1.task.md").exists()
    assert (initialized_vault / "py1.task_new.md").exists(), "Should be renamed to task_new"
    assert not (initialized_vault / "py1.task_new.task.md").exists(), "Should NOT create task_new.task"


def test_move_task_within_project_target_exists(runner, initialized_vault):
    """When target exists in same project, move under it as a child."""
    result = runner.invoke(cli, ["new", "project", "py1", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "task", "py1.task", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "task", "py1.task_new", "--no-edit"]) 
    assert result.exit_code == 0

    result = runner.invoke(cli, ["move", "py1.task", "py1.task_new"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "py1.task.md").exists()
    assert (initialized_vault / "py1.task_new.md").exists(), "task_new should still exist"
    assert (initialized_vault / "py1.task_new.task.md").exists(), "task should be moved under task_new"


def test_move_task_to_different_project_creates_group(runner, initialized_vault):
    """When moving to different project, always apply shortcut (old behavior)."""
    result = runner.invoke(cli, ["new", "project", "p1", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "task", "p1.task1", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "project", "p2", "--no-edit"]) 
    assert result.exit_code == 0

    result = runner.invoke(cli, ["move", "p1.task1", "p2.group"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "p1.task1.md").exists()
    assert (initialized_vault / "p2.group.md").exists(), "Group should be created"
    assert (initialized_vault / "p2.group.task1.md").exists(), "task1 should be moved under group"


def test_move_task_to_existing_project(runner, initialized_vault):
    """When moving to existing project, apply shortcut to create task under project."""
    result = runner.invoke(cli, ["new", "project", "p1", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "task", "p1.task", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "project", "p2", "--no-edit"]) 
    assert result.exit_code == 0

    result = runner.invoke(cli, ["move", "p1.task", "p2"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "p1.task.md").exists()
    assert (initialized_vault / "p2.md").exists(), "p2 project should still exist"
    assert (initialized_vault / "p2.task.md").exists(), "task should be moved to p2.task"


def test_move_task_to_nonexistent_project(runner, initialized_vault):
    """When moving to non-existent project, do full rename."""
    result = runner.invoke(cli, ["new", "project", "p1", "--no-edit"]) 
    assert result.exit_code == 0
    result = runner.invoke(cli, ["new", "task", "p1.task", "--no-edit"]) 
    assert result.exit_code == 0

    result = runner.invoke(cli, ["move", "p1.task", "p2"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "p1.task.md").exists()
    assert (initialized_vault / "p2.md").exists(), "Should rename to p2.md"
    assert not (initialized_vault / "p2.task.md").exists(), "Should NOT create p2.task.md"

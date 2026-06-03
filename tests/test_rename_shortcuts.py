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


def test_rename_task_switch_project_keeps_leaf(runner, initialized_vault):
    runner.invoke(cli, ["new", "project", "p1", "--no-edit"]) 
    runner.invoke(cli, ["new", "task", "p1.task1", "--no-edit"]) 
    runner.invoke(cli, ["new", "project", "p2", "--no-edit"]) 

    result = runner.invoke(cli, ["rename", "p1.task1", "p2"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "p1.task1.md").exists()
    assert (initialized_vault / "p2.task1.md").exists()


def test_rename_task_to_group_creates_group_and_keeps_leaf(runner, initialized_vault):
    runner.invoke(cli, ["new", "project", "p1", "--no-edit"]) 
    runner.invoke(cli, ["new", "task", "p1.task1", "--no-edit"]) 
    runner.invoke(cli, ["new", "project", "p2", "--no-edit"]) 

    result = runner.invoke(cli, ["rename", "p1.task1", "p2.group"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "p1.task1.md").exists()
    assert (initialized_vault / "p2.group.md").exists(), "Group should be created"
    assert (initialized_vault / "p2.group.task1.md").exists()


def test_rename_mixed_depth_old_to_project_keeps_leaf(runner, initialized_vault):
    runner.invoke(cli, ["new", "project", "p1", "--no-edit"]) 
    runner.invoke(cli, ["new", "task", "p1.g1.task", "--no-edit"]) 
    runner.invoke(cli, ["new", "project", "p2", "--no-edit"]) 

    result = runner.invoke(cli, ["rename", "p1.g1.task", "p2"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "p1.g1.task.md").exists()
    # g1 group remains (may or may not exist depending on new task creation logic)
    # task moved to p2.task
    assert (initialized_vault / "p2.task.md").exists()

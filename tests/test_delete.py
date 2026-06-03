import subprocess
from click.testing import CliRunner
import pytest

from cor.cli import cli


@pytest.fixture
def initialized_vault(temp_vault, runner, monkeypatch):
    monkeypatch.setenv("CORTEX_VAULT", str(temp_vault))
    monkeypatch.chdir(temp_vault)
    subprocess.run(["git", "init"], cwd=temp_vault, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_vault, capture_output=True)
    return temp_vault


def test_del_alias_deletes_task(runner, initialized_vault):
    runner.invoke(cli, ["new", "project", "p1", "--no-edit"]) 
    runner.invoke(cli, ["new", "task", "p1.task2", "--no-edit"]) 

    result = runner.invoke(cli, ["del", "p1.task2"]) 
    assert result.exit_code == 0, result.output

    assert not (initialized_vault / "p1.task2.md").exists()
    content = (initialized_vault / "p1.md").read_text()
    assert "(p1.task2)" not in content

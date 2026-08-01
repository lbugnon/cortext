"""Shared test fixtures for Cor tests."""

import os
import shutil
import subprocess
from pathlib import Path
from datetime import date

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def isolate_user_config(tmp_path, monkeypatch):
    """Keep every test out of the real user's home directory.

    Without this, `cor init` writes to the developer's actual config:
    `set_vault_path()` repointed ~/.config/cor/config.yaml at a pytest tmp dir,
    and `_install_nvim_plugin()` overwrote ~/.config/nvim/lua/plugins/cortex.lua
    (it resolves Path.home() directly, so XDG_CONFIG_HOME alone is not enough).
    Running the suite would silently break the developer's own vault.

    Individual fixtures may point XDG_CONFIG_HOME somewhere more specific;
    this only guarantees it is never the real one.
    """
    fake_home = tmp_path / "home"
    (fake_home / ".config").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    # Path.home() consults these before HOME on some platforms.
    monkeypatch.delenv("USERPROFILE", raising=False)
    # A stale COR_VAULT from the developer's shell would leak in too.
    monkeypatch.delenv("COR_VAULT", raising=False)


@pytest.fixture
def runner():
    """Create a Click CLI test runner."""
    return CliRunner()


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """Create a temporary vault directory with git initialized.

    Sets up:
    - Git repository
    - Basic directory structure (templates/, archive/)
    - Environment variable COR_VAULT pointing to vault
    - Patches cli.NOTES_DIR and cli.TEMPLATES_DIR
    - Changes working directory to vault

    Returns the vault path.
    """
    vault = tmp_path / "notes"
    vault.mkdir()

    # Use XDG_CONFIG_HOME so cor.config picks up our test config directory
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # Create config that points to this vault (cor reads $XDG_CONFIG_HOME/cor/config.yaml)
    config_dir = Path(tmp_path) / "cor"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    config_file.write_text(f"vault: {vault}\n")

    # Change to vault directory
    monkeypatch.chdir(vault)

    # Initialize git
    subprocess.run(["git", "init"], cwd=vault, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=vault, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=vault, capture_output=True)

    # Create templates directory
    templates = vault / "templates"
    templates.mkdir()

    # Create archive directory
    archive = vault / "archive"
    archive.mkdir()

    # Create basic templates
    (templates / "project.md").write_text("""\
---
created: {date}
modified: {date}
type: project
status: planning
priority:
due:
---
# {name}

## Goal

## Tasks
""")

    (templates / "task.md").write_text("""\
---
created: {date}
modified: {date}
type: task
status: todo
due:
priority:
parent: {parent}
---
# {name}

{parent_link}

## Description
""")

    (templates / "note.md").write_text("""\
---
created: {date}
modified: {date}
type: note
---
# {name}
""")

    # Create backlog.md (vault marker)
    today = date.today().isoformat()
    (vault / "backlog.md").write_text(f"""\
---
created: {today}
modified: {today}
---
# Backlog

## Inbox
""")

    return vault


@pytest.fixture
def project_with_tasks(temp_vault):
    """Create a project with multiple tasks for testing.

    Creates:
    - myproject.md (project)
    - myproject.task1.md (task, status: todo)
    - myproject.task2.md (task, status: todo)
    - myproject.task3.md (task, status: todo)

    Returns dict with paths.
    """
    today = date.today().isoformat()

    # Create project
    project_path = temp_vault / "myproject.md"
    project_path.write_text(f"""\
---
created: {today}
modified: {today}
status: active
---
# My Project

## Goal
Test project

## Tasks
- [ ] [Task 1](myproject.task1.md)
- [ ] [Task 2](myproject.task2.md)
- [ ] [Task 3](myproject.task3.md)
""")

    # Create tasks
    tasks = {}
    for i in range(1, 4):
        task_path = temp_vault / f"myproject.task{i}.md"
        task_path.write_text(f"""\
---
created: {today}
modified: {today}
type: task
status: todo
parent: myproject
---
# Task {i}

[< My Project](myproject.md)

## Description
Task {i} description
""")
        tasks[f"task{i}"] = task_path

    return {
        "vault": temp_vault,
        "project": project_path,
        **tasks
    }


@pytest.fixture
def project_with_group(temp_vault):
    """Create a project with a task group containing subtasks.

    Creates:
    - myproject.md (project)
    - myproject.group.md (task group)
    - myproject.group.subtask1.md (task under group)
    - myproject.group.subtask2.md (task under group)

    Returns dict with paths.
    """
    today = date.today().isoformat()

    # Create project
    project_path = temp_vault / "myproject.md"
    project_path.write_text(f"""\
---
created: {today}
modified: {today}
status: active
---
# My Project

## Tasks
- [ ] [Group](myproject.group.md)
""")

    # Create group (task with children)
    group_path = temp_vault / "myproject.group.md"
    group_path.write_text(f"""\
---
created: {today}
modified: {today}
type: task
status: todo
parent: myproject
---
# Group

[< My Project](myproject.md)

## Tasks
- [ ] [Subtask 1](myproject.group.subtask1.md)
- [ ] [Subtask 2](myproject.group.subtask2.md)
""")

    # Create subtasks
    subtasks = {}
    for i in range(1, 3):
        subtask_path = temp_vault / f"myproject.group.subtask{i}.md"
        subtask_path.write_text(f"""\
---
created: {today}
modified: {today}
type: task
status: todo
parent: myproject.group
---
# Subtask {i}

[< Group](myproject.group.md)

## Description
Subtask {i} description
""")
        subtasks[f"subtask{i}"] = subtask_path

    return {
        "vault": temp_vault,
        "project": project_path,
        "group": group_path,
        **subtasks
    }



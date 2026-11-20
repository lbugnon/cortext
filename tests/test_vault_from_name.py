"""A write command outside any vault takes its vault from the note it names.

Tab completion offers notes without asking which vault, so a full name picked
from it has already chosen the vault. Asking again is noise. Only an exact name
held by a single registered vault counts; anything else is still a guess.
"""

import os
from pathlib import Path

import frontmatter
import pytest

from conftest import build_vault
from cor.cli import cli
from cor.config import add_vault


@pytest.fixture
def two_vaults(tmp_path, monkeypatch):
    """Two registered vaults, `work` the default, with cwd outside both."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("COR_VAULT", raising=False)
    work = build_vault(tmp_path / "work")
    personal = build_vault(tmp_path / "personal")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    add_vault("work", work)
    add_vault("personal", personal)
    return {"work": work, "personal": personal}


def _new_project(runner, vault: Path, name: str) -> None:
    result = runner.invoke(
        cli, ["new", "project", name, "--no-edit"], env={"COR_VAULT": str(vault)}
    )
    assert result.exit_code == 0, result.output


def test_exact_name_in_one_vault_picks_that_vault(runner, two_vaults, monkeypatch):
    # In the non-default vault, so the config fallback alone would miss it.
    _new_project(runner, two_vaults["personal"], "garden")

    result = runner.invoke(cli, ["tag", "garden", "spring"])

    assert result.exit_code == 0, result.output
    post = frontmatter.load(two_vaults["personal"] / "garden.md")
    assert post.get("tags") == ["spring"]
    assert os.environ["COR_VAULT"] == str(two_vaults["personal"])
    monkeypatch.delenv("COR_VAULT")


def test_name_in_several_vaults_is_still_ambiguous(runner, two_vaults):
    _new_project(runner, two_vaults["work"], "garden")
    _new_project(runner, two_vaults["personal"], "garden")

    result = runner.invoke(cli, ["tag", "garden", "spring"])

    assert result.exit_code != 0
    assert "ambiguous" in result.output
    for vault in two_vaults.values():
        assert not frontmatter.load(vault / "garden.md").get("tags")


def test_partial_name_is_still_ambiguous(runner, two_vaults):
    _new_project(runner, two_vaults["personal"], "garden")

    result = runner.invoke(cli, ["tag", "gard", "spring"])

    assert result.exit_code != 0
    assert "ambiguous" in result.output
    assert not frontmatter.load(two_vaults["personal"] / "garden.md").get("tags")


# --- tab completion outside a vault -------------------------------------------

class _Ctx:
    """Minimal click context: completers only read ``params``."""

    def __init__(self, **params):
        self.params = params


def _values(items) -> dict[str, str]:
    return {item.value: item.help or "" for item in items}


def test_edit_completion_outside_a_vault_spans_every_vault(runner, two_vaults):
    from cor.completions import complete_existing_name

    _new_project(runner, two_vaults["work"], "alpha")
    _new_project(runner, two_vaults["personal"], "garden")

    items = _values(complete_existing_name(_Ctx(), None, ""))

    assert "[work]" in items["alpha"]
    assert "[personal]" in items["garden"]


def test_completion_labels_a_name_held_by_several_vaults(runner, two_vaults):
    from cor.completions import complete_existing_name

    _new_project(runner, two_vaults["work"], "garden")
    _new_project(runner, two_vaults["personal"], "garden")

    items = _values(complete_existing_name(_Ctx(), None, "gar"))

    assert list(items) == ["garden"]
    assert "[personal, work]" in items["garden"]


def test_completion_inside_a_vault_stays_in_that_vault(runner, two_vaults, monkeypatch):
    from cor.completions import complete_existing_name

    _new_project(runner, two_vaults["work"], "alpha")
    _new_project(runner, two_vaults["personal"], "garden")
    monkeypatch.chdir(two_vaults["personal"])

    items = _values(complete_existing_name(_Ctx(), None, ""))

    assert "garden" in items and "alpha" not in items
    assert "[" not in items["garden"]


def test_task_completion_outside_a_vault_spans_every_vault(runner, two_vaults):
    from cor.completions import complete_task_name

    for vault, stem in ((two_vaults["work"], "alpha"), (two_vaults["personal"], "garden")):
        _new_project(runner, vault, stem)
        result = runner.invoke(cli, ["new", "task", f"{stem}.water", "--no-edit"],
                               env={"COR_VAULT": str(vault)})
        assert result.exit_code == 0, result.output

    items = _values(complete_task_name(_Ctx(), None, ""))

    assert set(items) == {"alpha.water", "garden.water"}
    assert "[personal]" in items["garden.water"]


def test_read_command_outside_a_vault_follows_the_named_note(runner, two_vaults, monkeypatch):
    # `cor tree` is read-only; it used to read the default vault regardless.
    _new_project(runner, two_vaults["personal"], "garden")

    result = runner.invoke(cli, ["tree", "garden"])

    assert result.exit_code == 0, result.output
    assert "Garden" in result.output
    monkeypatch.delenv("COR_VAULT")


# --- `cor new` outside a vault ---------------------------------------------------

def test_new_completion_outside_a_vault_offers_every_vaults_projects(runner, two_vaults):
    from cor.completions import complete_name

    _new_project(runner, two_vaults["work"], "alpha")
    _new_project(runner, two_vaults["personal"], "garden")

    items = _values(complete_name(_Ctx(note_type="task"), None, ""))

    assert "[work]" in items["alpha."]
    assert "[personal]" in items["garden."]


def test_new_completion_outside_a_vault_offers_groups_from_every_vault(runner, two_vaults):
    from cor.completions import complete_name

    _new_project(runner, two_vaults["personal"], "garden")
    result = runner.invoke(cli, ["new", "task", "garden.beds.weed", "--no-edit"],
                           env={"COR_VAULT": str(two_vaults["personal"])})
    assert result.exit_code == 0, result.output

    items = _values(complete_name(_Ctx(note_type="task"), None, "garden."))

    assert "garden.beds." in items
    assert "[personal]" in items["garden.beds."]


def test_new_completion_inside_a_vault_stays_in_that_vault(runner, two_vaults, monkeypatch):
    from cor.completions import complete_name

    _new_project(runner, two_vaults["work"], "alpha")
    _new_project(runner, two_vaults["personal"], "garden")
    monkeypatch.chdir(two_vaults["personal"])

    items = _values(complete_name(_Ctx(note_type="task"), None, ""))

    assert "garden." in items and "alpha." not in items
    assert "[" not in items["garden."]


def test_new_task_outside_a_vault_goes_to_its_projects_vault(runner, two_vaults, monkeypatch):
    # personal is not the default, so the config fallback alone would miss it.
    _new_project(runner, two_vaults["personal"], "garden")

    result = runner.invoke(cli, ["new", "task", "garden.water", "--no-edit"])

    assert result.exit_code == 0, result.output
    assert (two_vaults["personal"] / "garden.water.md").is_file()
    assert not (two_vaults["work"] / "garden.water.md").exists()
    monkeypatch.delenv("COR_VAULT")


def test_new_task_under_a_project_in_several_vaults_is_ambiguous(runner, two_vaults):
    _new_project(runner, two_vaults["work"], "garden")
    _new_project(runner, two_vaults["personal"], "garden")

    result = runner.invoke(cli, ["new", "task", "garden.water", "--no-edit"])

    assert result.exit_code != 0
    assert "ambiguous" in result.output
    for vault in two_vaults.values():
        assert not (vault / "garden.water.md").exists()


def test_new_project_outside_a_vault_is_still_ambiguous(runner, two_vaults):
    result = runner.invoke(cli, ["new", "project", "orchard", "--no-edit"])

    assert result.exit_code != 0
    assert "ambiguous" in result.output

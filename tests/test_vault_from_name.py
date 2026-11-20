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

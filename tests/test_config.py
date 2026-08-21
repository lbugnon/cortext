"""Tests for vault-path resolution in cor.config.

Covers the multi-vault discovery added on top of the original config-only
lookup: cwd-walk for `backlog.md`, COR_VAULT env override, and config fallback.
"""

from pathlib import Path

import pytest

from cor.config import (
    VAULT_SOURCE_CONFIG,
    VAULT_SOURCE_CWD,
    VAULT_SOURCE_ENV,
    _find_vault_from_cwd,
    add_vault,
    clear_focused_project,
    get_default_vault,
    get_focused_project,
    get_vault_path,
    get_vaults,
    remove_vault,
    resolve_vault,
    set_default_vault,
    set_focused_project,
    vault_name_for,
)
from cor.exceptions import ConfigError


def _isolate_config(monkeypatch, tmp_path: Path, vault_in_config: Path | None = None):
    """Point XDG_CONFIG_HOME at tmp_path; optionally seed vault key."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("COR_VAULT", raising=False)
    cfg_dir = tmp_path / "cor"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.yaml"
    if vault_in_config is not None:
        cfg_file.write_text(f"vault: {vault_in_config}\n")
    elif cfg_file.exists():
        cfg_file.unlink()


def _make_vault(parent: Path, name: str = "vault") -> Path:
    vault = parent / name
    vault.mkdir()
    (vault / "backlog.md").write_text("# Backlog\n")
    return vault


def test_find_vault_from_cwd_at_vault_root(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    monkeypatch.chdir(vault)
    assert _find_vault_from_cwd() == vault


def test_find_vault_from_cwd_in_subdirectory(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    sub = vault / "scripts" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert _find_vault_from_cwd() == vault


def test_find_vault_from_cwd_returns_none_when_no_backlog_md(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _find_vault_from_cwd() is None


def test_get_vault_path_prefers_cwd_over_env_and_config(tmp_path, monkeypatch):
    cwd_vault = _make_vault(tmp_path, "cwd_vault")
    env_vault = _make_vault(tmp_path, "env_vault")
    cfg_vault = tmp_path / "cfg_vault"
    cfg_vault.mkdir()
    _isolate_config(monkeypatch, tmp_path, vault_in_config=cfg_vault)
    monkeypatch.setenv("COR_VAULT", str(env_vault))
    monkeypatch.chdir(cwd_vault)

    assert get_vault_path() == cwd_vault


def test_get_vault_path_falls_back_to_env_when_cwd_misses(tmp_path, monkeypatch):
    env_vault = _make_vault(tmp_path, "env_vault")
    cfg_vault = tmp_path / "cfg_vault"
    cfg_vault.mkdir()
    _isolate_config(monkeypatch, tmp_path, vault_in_config=cfg_vault)
    monkeypatch.setenv("COR_VAULT", str(env_vault))
    # cwd is tmp_path itself — no backlog.md anywhere in chain
    monkeypatch.chdir(tmp_path)

    assert get_vault_path() == env_vault


def test_get_vault_path_falls_back_to_config_when_cwd_and_env_miss(tmp_path, monkeypatch):
    cfg_vault = tmp_path / "cfg_vault"
    cfg_vault.mkdir()
    _isolate_config(monkeypatch, tmp_path, vault_in_config=cfg_vault)
    monkeypatch.chdir(tmp_path)

    assert get_vault_path() == cfg_vault


def test_get_vault_path_raises_when_nothing_configured(tmp_path, monkeypatch):
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError):
        get_vault_path()


# --- resolve_vault reports which rule won ---
#
# The source matters as much as the path: it is what lets a write refuse to
# act on a guess. Each rule gets its own assertion.


def test_resolve_vault_reports_cwd(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path, "cwd_vault")
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    monkeypatch.chdir(vault)

    assert resolve_vault() == (vault, VAULT_SOURCE_CWD)


def test_resolve_vault_reports_env(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path, "env_vault")
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    monkeypatch.setenv("COR_VAULT", str(vault))
    monkeypatch.chdir(tmp_path)

    assert resolve_vault() == (vault, VAULT_SOURCE_ENV)


def test_resolve_vault_reports_config_fallback(tmp_path, monkeypatch):
    cfg_vault = _make_vault(tmp_path, "cfg_vault")
    _isolate_config(monkeypatch, tmp_path, vault_in_config=cfg_vault)
    monkeypatch.chdir(tmp_path)

    path, source = resolve_vault()
    assert source == VAULT_SOURCE_CONFIG
    assert path == cfg_vault


# --- named registry ---


def test_legacy_vault_key_reads_as_a_named_registry(tmp_path, monkeypatch):
    """A config predating the registry must keep working, unmigrated."""
    cfg_vault = _make_vault(tmp_path, "notes")
    _isolate_config(monkeypatch, tmp_path, vault_in_config=cfg_vault)
    monkeypatch.chdir(tmp_path)

    assert get_vaults() == {"notes": cfg_vault}
    assert get_default_vault() == "notes"
    assert resolve_vault()[0] == cfg_vault


def test_add_and_list_vaults(tmp_path, monkeypatch):
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    work = _make_vault(tmp_path, "work")
    personal = _make_vault(tmp_path, "personal")
    monkeypatch.chdir(tmp_path)

    add_vault("work", work)
    add_vault("personal", personal)

    assert get_vaults() == {"work": work, "personal": personal}
    # First one registered becomes the default.
    assert get_default_vault() == "work"


def test_set_default_vault_changes_config_fallback(tmp_path, monkeypatch):
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    work = _make_vault(tmp_path, "work")
    personal = _make_vault(tmp_path, "personal")
    monkeypatch.chdir(tmp_path)
    add_vault("work", work)
    add_vault("personal", personal)

    set_default_vault("personal")

    assert resolve_vault() == (personal, VAULT_SOURCE_CONFIG)


def test_remove_vault_promotes_a_survivor_as_default(tmp_path, monkeypatch):
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    work = _make_vault(tmp_path, "work")
    personal = _make_vault(tmp_path, "personal")
    monkeypatch.chdir(tmp_path)
    add_vault("work", work)
    add_vault("personal", personal)

    remove_vault("work")

    assert get_vaults() == {"personal": personal}
    # A dangling default would make every fallback resolution fail.
    assert get_default_vault() == "personal"


def test_remove_unknown_vault_raises(tmp_path, monkeypatch):
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    monkeypatch.chdir(tmp_path)
    add_vault("work", _make_vault(tmp_path, "work"))

    with pytest.raises(ConfigError):
        remove_vault("nope")


def test_vault_name_for_falls_back_to_directory_name(tmp_path, monkeypatch):
    """An unregistered vault found from cwd still needs a label to display."""
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    stray = _make_vault(tmp_path, "stray")
    monkeypatch.chdir(tmp_path)

    assert vault_name_for(stray) == "stray"


# --- focus is per-vault ---


def test_focus_does_not_leak_between_vaults(tmp_path, monkeypatch):
    """Focusing in one vault must not affect another, where it does not exist."""
    _isolate_config(monkeypatch, tmp_path, vault_in_config=None)
    work = _make_vault(tmp_path, "work")
    personal = _make_vault(tmp_path, "personal")
    monkeypatch.chdir(tmp_path)
    add_vault("work", work)
    add_vault("personal", personal)

    monkeypatch.chdir(work)
    set_focused_project("alpha")
    assert get_focused_project() == "alpha"

    monkeypatch.chdir(personal)
    assert get_focused_project() is None

    monkeypatch.chdir(work)
    assert get_focused_project() == "alpha"
    clear_focused_project()
    assert get_focused_project() is None


def test_legacy_global_focus_is_still_honoured(tmp_path, monkeypatch):
    """Configs written before focus was per-vault must not lose their focus."""
    vault = _make_vault(tmp_path, "notes")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("COR_VAULT", raising=False)
    cfg_dir = tmp_path / "cor"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(
        f"vault: {vault}\nfocused_project: legacyproj\n"
    )
    monkeypatch.chdir(vault)

    assert get_focused_project() == "legacyproj"

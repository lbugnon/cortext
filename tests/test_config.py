"""Tests for vault-path resolution in cor.config.

Covers the multi-vault discovery added on top of the original config-only
lookup: cwd-walk for `backlog.md`, COR_VAULT env override, and config fallback.
"""

from pathlib import Path

import pytest

from cor.config import get_vault_path, _find_vault_from_cwd
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

"""Tests for Google Calendar OAuth credential handling."""

from pathlib import Path

import pytest

import cor.commands.calendar as calendar
from cor.exceptions import ConfigError


def test_default_client_config_requires_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    with pytest.raises(ConfigError):
        calendar._default_client_config()


def test_default_client_config_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-123.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret-xyz")
    config = calendar._default_client_config()
    installed = config["installed"]
    assert installed["client_id"] == "id-123.apps.googleusercontent.com"
    assert installed["client_secret"] == "secret-xyz"
    assert installed["token_uri"] == "https://oauth2.googleapis.com/token"


def test_no_hardcoded_secret_in_source():
    """The leaked OAuth client secret must not be embedded in source anymore."""
    source = Path(calendar.__file__).read_text()
    assert "GOCSPX-" not in source
    assert "googleusercontent.com" not in source

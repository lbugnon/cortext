"""Safety and regression tests for the shared mutation foundation."""

import json
import os

import pytest

from cor.cli import cli
from cor.core.storage import atomic_write_text
from cor.core.transactions import (
    VaultTransaction,
    _ACTIVE_TRANSACTION,
    recover_transaction,
    vault_revision,
)
from cor.exceptions import ConflictError, ValidationError


def leave_interrupted_transaction(temp_vault):
    """Simulate process death after a write, without running __exit__."""
    transaction = VaultTransaction(temp_vault)
    transaction.__enter__()
    atomic_write_text(temp_vault / "backlog.md", "partial write\n")
    atomic_write_text(temp_vault / "partial.md", "partial file\n")
    _ACTIVE_TRANSACTION.reset(transaction._token)
    transaction._token = None
    transaction._release_lock()
    return transaction


def test_transaction_commits_with_revision_and_history(temp_vault):
    before = vault_revision(temp_vault)

    with VaultTransaction(temp_vault) as transaction:
        atomic_write_text(temp_vault / "created.md", "---\ntype: note\n---\n# Created\n")

    assert transaction.before_revision == before
    assert transaction.after_revision != before
    assert transaction.changed == ["created.md"]
    manifest = json.loads(
        (temp_vault / ".git" / "cortex" / "history"
         / transaction.transaction_id / "transaction.json").read_text()
    )
    assert manifest["state"] == "committed"
    assert manifest["changed"] == ["created.md"]
    assert set(manifest["before"]) >= {"backlog.md"}


def test_history_reuses_content_objects(temp_vault):
    with VaultTransaction(temp_vault):
        atomic_write_text(temp_vault / "one.md", "---\ntype: note\n---\n# One\n")
    objects = temp_vault / ".git" / "cortex" / "objects"
    first_objects = {path.name for path in objects.iterdir()}

    with VaultTransaction(temp_vault):
        atomic_write_text(temp_vault / "two.md", "---\ntype: note\n---\n# Two\n")

    assert first_objects <= {path.name for path in objects.iterdir()}
    history = temp_vault / ".git" / "cortex" / "history"
    assert not list(history.glob("*/before"))


def test_transaction_rolls_back_modified_deleted_and_created_files(temp_vault):
    original = (temp_vault / "backlog.md").read_bytes()
    deleted = temp_vault / "templates" / "note.md"
    # Templates are intentionally outside the registry transaction boundary.
    assert deleted.exists()

    with pytest.raises(RuntimeError, match="stop"):
        with VaultTransaction(temp_vault) as transaction:
            atomic_write_text(temp_vault / "backlog.md", "changed\n")
            atomic_write_text(temp_vault / "new.md", "new\n")
            raise RuntimeError("stop")

    assert (temp_vault / "backlog.md").read_bytes() == original
    assert not (temp_vault / "new.md").exists()
    manifest = json.loads(
        (temp_vault / ".git" / "cortex" / "history"
         / transaction.transaction_id / "transaction.json").read_text()
    )
    assert manifest["state"] == "rolled-back"


def test_invalid_changed_frontmatter_is_rejected_and_rolled_back(temp_vault):
    task = temp_vault / "bad.md"
    original = "---\ntype: task\nstatus: todo\n---\n# Bad\n"
    task.write_text(original)

    with pytest.raises(ValidationError, match="Invalid task status"):
        with VaultTransaction(temp_vault):
            atomic_write_text(
                task, "---\ntype: task\nstatus: impossible\n---\n# Bad\n"
            )

    assert task.read_text() == original


def test_broken_link_is_rejected_and_rolled_back(temp_vault):
    note = temp_vault / "broken.md"

    with pytest.raises(ValidationError, match="Broken link"):
        with VaultTransaction(temp_vault):
            atomic_write_text(
                note,
                "---\ntype: note\n---\n# Broken\n\n[Missing](missing.md)\n",
            )

    assert not note.exists()


def test_nested_transactions_share_one_history_record(temp_vault):
    history = temp_vault / ".git" / "cortex" / "history"
    with VaultTransaction(temp_vault):
        atomic_write_text(temp_vault / "one.md", "---\ntype: note\n---\n# One\n")
        with VaultTransaction(temp_vault):
            atomic_write_text(temp_vault / "two.md", "---\ntype: note\n---\n# Two\n")

    assert len(list(history.iterdir())) == 1


def test_atomic_write_preserves_original_if_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "note.md"
    target.write_text("original")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(target, "replacement")

    assert target.read_text() == "original"
    assert list(tmp_path.glob(".cor-write-*.tmp")) == []


def test_unfinished_transaction_blocks_writes_until_recovered(temp_vault):
    original = (temp_vault / "backlog.md").read_bytes()
    interrupted = leave_interrupted_transaction(temp_vault)

    with pytest.raises(ConflictError, match="Unfinished transaction"):
        with VaultTransaction(temp_vault):
            atomic_write_text(temp_vault / "other.md", "must not write\n")

    assert (temp_vault / "backlog.md").read_text() == "partial write\n"
    assert not (temp_vault / "other.md").exists()

    recovered = recover_transaction(temp_vault, interrupted.transaction_id)

    assert recovered["recovered"] == interrupted.transaction_id
    assert (temp_vault / "backlog.md").read_bytes() == original
    assert not (temp_vault / "partial.md").exists()
    manifest = json.loads(
        (temp_vault / ".git" / "cortex" / "history"
         / interrupted.transaction_id / "transaction.json").read_text()
    )
    assert manifest["state"] == "recovered"
    assert manifest["recovered_by"] == recovered["transaction"]


def test_recover_without_id_uses_only_unfinished_transaction(temp_vault):
    original = (temp_vault / "backlog.md").read_bytes()
    interrupted = leave_interrupted_transaction(temp_vault)

    recovered = recover_transaction(temp_vault)

    assert recovered["recovered"] == interrupted.transaction_id
    assert (temp_vault / "backlog.md").read_bytes() == original


@pytest.mark.parametrize(
    ("note_type", "stem", "text", "expected_heading"),
    [
        ("project", "research", "Build a compact model.", "## Goal"),
        ("task", "research.measure", "Measure validation F1.", "## Description"),
        ("note", "research.idea", "Try a distance proxy.", None),
    ],
)
def test_new_preserves_initial_text(
    runner, temp_vault, note_type, stem, text, expected_heading
):
    if note_type != "project" and not (temp_vault / "research.md").exists():
        runner.invoke(cli, ["new", "project", "research", "--no-edit"])

    result = runner.invoke(cli, ["new", note_type, stem, text, "--no-edit"])

    assert result.exit_code == 0, result.output
    body = (temp_vault / f"{stem}.md").read_text()
    assert text in body
    if expected_heading:
        assert body.index(expected_heading) < body.index(text)

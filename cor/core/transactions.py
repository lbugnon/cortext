"""Vault-level transaction, revision, history, and rollback primitives."""

from __future__ import annotations

import contextvars
import fcntl
import functools
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from ..exceptions import ConflictError, ValidationError
from .storage import atomic_write_bytes, atomic_write_text, content_revision


_ACTIVE_TRANSACTION: contextvars.ContextVar["VaultTransaction | None"] = (
    contextvars.ContextVar("cortex_active_transaction", default=None)
)


def _managed_files(notes_dir: Path) -> list[Path]:
    files = [
        path for path in notes_dir.glob("*.md")
        if path.name not in {"AGENTS.md", "README.md"}
    ]
    archive = notes_dir / "archive"
    if archive.exists():
        files.extend(archive.glob("*.md"))
    return sorted(path for path in files if path.is_file())


def managed_files(notes_dir: str | Path) -> list[Path]:
    """Public registry-file iterator used by validation and recovery."""
    return _managed_files(Path(notes_dir))


def vault_revision(notes_dir: Path) -> str:
    """Hash all active and archived registry documents in stable path order."""
    digest = bytearray()
    for path in _managed_files(notes_dir):
        relative = path.relative_to(notes_dir).as_posix().encode("utf-8")
        digest.extend(len(relative).to_bytes(8, "big"))
        digest.extend(relative)
        data = path.read_bytes()
        digest.extend(len(data).to_bytes(8, "big"))
        digest.extend(data)
    return content_revision(bytes(digest))


def _control_dir(notes_dir: Path) -> Path:
    """Keep journals out of the tracked vault whenever it has a Git directory."""
    git_dir = notes_dir / ".git"
    return git_dir / "cortex" if git_dir.is_dir() else notes_dir / ".cortex"


def validate_changed_documents(notes_dir: Path, paths: list[Path]) -> list[str]:
    """Validate changed registry documents before committing a transaction."""
    from ..sync.runner import validate_frontmatter, validate_links

    errors: list[str] = []
    for path in paths:
        if path.suffix != ".md" or not path.exists():
            continue
        relative = path.relative_to(notes_dir).as_posix()
        messages = validate_frontmatter(relative, notes_dir)
        messages.extend(validate_links(relative, notes_dir))
        errors.extend(f"{relative}: {message}" for message in messages)
    return errors


class VaultTransaction:
    """Protect a vault mutation with locking, a durable journal, and rollback.

    Nested uses for the same vault share the outer transaction. This lets a
    future batch command compose the exact operations used by human commands.
    """

    def __init__(
        self,
        notes_dir: str | Path,
        *,
        validators: Iterable[Callable[[Path, list[Path]], list[str]]] | None = None,
        expected_revision: str | None = None,
        dry_run: bool = False,
        request_id: str | None = None,
        request_hash: str | None = None,
        recovering_transaction: str | None = None,
    ) -> None:
        self.notes_dir = Path(notes_dir).resolve()
        self.validators = (
            tuple(validators)
            if validators is not None
            else (validate_changed_documents,)
        )
        self.expected_revision = expected_revision
        self.dry_run = dry_run
        self.request_id = request_id
        self.request_hash = request_hash
        self.recovering_transaction = recovering_transaction
        self.replayed = False
        self.transaction_id = uuid.uuid4().hex
        self.before_revision = ""
        self.after_revision = ""
        self.changed: list[str] = []
        self._nested = False
        self._lock_stream = None
        self._token = None
        self._before: dict[str, bytes] = {}
        self._before_objects: dict[str, str] = {}
        self._control_dir = _control_dir(self.notes_dir)
        self._history_dir = (
            self._control_dir / "history" / self.transaction_id
        )

    def __enter__(self) -> "VaultTransaction":
        active = _ACTIVE_TRANSACTION.get()
        if active is not None:
            if active.notes_dir != self.notes_dir:
                raise ConflictError("Cannot mutate two vaults in one transaction.")
            self._nested = True
            return active

        control_dir = _control_dir(self.notes_dir)
        control_dir.mkdir(parents=True, exist_ok=True)
        self._lock_stream = (control_dir / "write.lock").open("a+b")
        try:
            fcntl.flock(
                self._lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
            )
        except BlockingIOError as error:
            self._lock_stream.close()
            raise ConflictError("The vault is being changed by another process.") from error

        self.before_revision = vault_revision(self.notes_dir)
        unfinished = [
            record for record in _unfinished_manifests(self.notes_dir)
            if record.get("id") != self.recovering_transaction
        ]
        if unfinished:
            identifiers = ", ".join(record["id"] for record in unfinished)
            self._release_lock()
            raise ConflictError(
                "Unfinished transaction blocks writes; inspect 'cor history --json' "
                f"and recover it first: {identifiers}"
            )
        previous = self._find_committed_request()
        if previous is not None:
            if previous.get("request_hash") != self.request_hash:
                self._release_lock()
                raise ConflictError("Request id was already used for a different batch.")
            self.replayed = True
            self.transaction_id = previous["id"]
            self.before_revision = previous["before_revision"]
            self.after_revision = previous["after_revision"]
            self.changed = previous.get("changed") or []
            return self
        if (
            self.expected_revision is not None
            and self.expected_revision != self.before_revision
        ):
            self._release_lock()
            raise ConflictError("Vault changed since the operation was planned.")
        self._before = {
            path.relative_to(self.notes_dir).as_posix(): path.read_bytes()
            for path in _managed_files(self.notes_dir)
        }
        self._write_started_journal()
        self._token = _ACTIVE_TRANSACTION.set(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if self._nested:
            return False
        if self.replayed:
            self._release_lock()
            return False
        try:
            if exc_type is not None:
                self._restore_before()
                self._write_manifest("rolled-back", error=str(exc_value))
                return False

            current = {
                path.relative_to(self.notes_dir).as_posix(): path.read_bytes()
                for path in _managed_files(self.notes_dir)
            }
            changed_paths = sorted(
                relative
                for relative in set(self._before) | set(current)
                if self._before.get(relative) != current.get(relative)
            )
            changed_files = [
                self.notes_dir / relative
                for relative in changed_paths
                if relative in current
            ]
            errors = [
                error
                for validator in self.validators
                for error in validator(self.notes_dir, changed_files)
            ]
            if errors:
                self._restore_before()
                message = "; ".join(errors)
                self._write_manifest("rolled-back", error=message)
                raise ValidationError(message)

            self.changed = changed_paths
            self.after_revision = vault_revision(self.notes_dir)
            if self.dry_run:
                self._restore_before()
                self._write_manifest("dry-run")
            else:
                self._write_manifest("committed")
            return False
        finally:
            if self._token is not None:
                _ACTIVE_TRANSACTION.reset(self._token)
            if self._lock_stream is not None:
                self._release_lock()

    def _release_lock(self) -> None:
        if self._lock_stream is None:
            return
        fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_UN)
        self._lock_stream.close()
        self._lock_stream = None

    def _find_committed_request(self) -> dict | None:
        if self.request_id is None:
            return None
        for record in _history_manifests(self.notes_dir):
            if (
                record.get("state") == "committed"
                and record.get("request_id") == self.request_id
            ):
                return record
        return None

    def _write_started_journal(self) -> None:
        self._history_dir.mkdir(parents=True, exist_ok=False)
        objects_dir = self._control_dir / "objects"
        for relative, data in self._before.items():
            object_id = content_revision(data)
            object_path = objects_dir / object_id
            if not object_path.exists():
                atomic_write_bytes(object_path, data)
            self._before_objects[relative] = object_id
        self._write_manifest("started")

    def _write_manifest(self, state: str, *, error: str | None = None) -> None:
        manifest = {
            "version": 1,
            "id": self.transaction_id,
            "request_id": self.request_id,
            "request_hash": self.request_hash,
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "before_revision": self.before_revision,
            "before": self._before_objects,
            "after_revision": self.after_revision or None,
            "changed": self.changed,
            "error": error,
        }
        atomic_write_text(
            self._history_dir / "transaction.json",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )

    def _restore_before(self) -> None:
        current = {
            path.relative_to(self.notes_dir).as_posix(): path
            for path in _managed_files(self.notes_dir)
        }
        for relative in set(current) - set(self._before):
            current[relative].unlink(missing_ok=True)
        for relative, data in self._before.items():
            atomic_write_bytes(self.notes_dir / relative, data)


def transactional_command(function):
    """Run a mutating CLI command inside the shared vault transaction."""
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        from ..utils import get_notes_dir

        with VaultTransaction(get_notes_dir()):
            return function(*args, **kwargs)

    return wrapper


def transactional_operation(function):
    """Run a core operation in a transaction, nesting into an active batch."""
    @functools.wraps(function)
    def wrapper(notes_dir, *args, **kwargs):
        with VaultTransaction(notes_dir):
            return function(Path(notes_dir), *args, **kwargs)

    return wrapper


def transaction_history(notes_dir: str | Path, *, limit: int = 20) -> list[dict]:
    """Return compact newest-first transaction records."""
    records = []
    for record in _history_manifests(Path(notes_dir))[:limit]:
        records.append({
            key: record.get(key) for key in (
                "id", "request_id", "state", "timestamp", "before_revision",
                "after_revision", "changed", "error", "recovered_by",
            )
        })
    return records


def _history_manifests(notes_dir: Path) -> list[dict]:
    history_dir = _control_dir(notes_dir) / "history"
    if not history_dir.exists():
        return []
    paths = sorted(
        history_dir.glob("*/transaction.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    records = []
    for path in paths:
        try:
            records.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return records


def _unfinished_manifests(notes_dir: Path) -> list[dict]:
    return [
        record for record in _history_manifests(notes_dir)
        if record.get("state") == "started" and isinstance(record.get("id"), str)
    ]


def _recoverable_relative_path(relative: str) -> bool:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".md":
        return False
    if len(path.parts) == 1:
        return path.name not in {"AGENTS.md", "README.md"}
    return len(path.parts) == 2 and path.parts[0] == "archive"


def recover_transaction(
    notes_dir: str | Path, transaction_id: str | None = None
) -> dict:
    """Restore the durable before-state of one interrupted transaction."""
    root = Path(notes_dir).resolve()
    if transaction_id is not None and not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise ValidationError("Invalid transaction id.")
    unfinished = _unfinished_manifests(root)
    if len(unfinished) > 1:
        identifiers = ", ".join(record["id"] for record in unfinished)
        raise ConflictError(
            "Multiple unfinished transactions require manual inspection: "
            f"{identifiers}"
        )
    if transaction_id is None:
        if not unfinished:
            raise ValidationError("No unfinished transaction to recover.")
        manifest = unfinished[0]
    else:
        manifest = next(
            (record for record in unfinished if record.get("id") == transaction_id),
            None,
        )
        if manifest is None:
            raise ValidationError(f"Unfinished transaction not found: {transaction_id}")

    interrupted_id = manifest["id"]
    before = manifest.get("before")
    if not isinstance(before, dict) or not all(
        isinstance(relative, str)
        and _recoverable_relative_path(relative)
        and isinstance(object_id, str)
        and re.fullmatch(r"[0-9a-f]{64}", object_id)
        for relative, object_id in before.items()
    ):
        raise ValidationError("Interrupted transaction has an invalid before-state.")

    control_dir = _control_dir(root)
    restored: dict[str, bytes] = {}
    for relative, object_id in before.items():
        object_path = control_dir / "objects" / object_id
        if not object_path.exists():
            raise ValidationError(f"Missing history object: {object_id}")
        restored[relative] = object_path.read_bytes()

    with VaultTransaction(
        root,
        validators=(),
        recovering_transaction=interrupted_id,
    ) as recovery:
        current = {
            path.relative_to(root).as_posix(): path for path in _managed_files(root)
        }
        for relative in set(current) - set(restored):
            current[relative].unlink()
        for relative, data in restored.items():
            atomic_write_bytes(root / relative, data)

    manifest["state"] = "recovered"
    manifest["recovered_by"] = recovery.transaction_id
    manifest["recovered_at"] = datetime.now(timezone.utc).isoformat()
    manifest_path = (
        control_dir / "history" / interrupted_id / "transaction.json"
    )
    atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return {
        "recovered": interrupted_id,
        "transaction": recovery.transaction_id,
        "before_revision": recovery.before_revision,
        "after_revision": recovery.after_revision,
        "changed": recovery.changed,
    }


def undo_transaction(notes_dir: str | Path, transaction_id: str) -> dict:
    """Restore a committed transaction if no newer vault change exists."""
    root = Path(notes_dir).resolve()
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise ValidationError("Invalid transaction id.")
    control_dir = _control_dir(root)
    manifest_path = control_dir / "history" / transaction_id / "transaction.json"
    if not manifest_path.exists():
        raise ValidationError(f"Transaction not found: {transaction_id}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("state") != "committed":
        raise ValidationError("Only committed transactions can be undone.")
    current_revision = vault_revision(root)
    if current_revision != manifest.get("after_revision"):
        raise ConflictError("Vault changed after this transaction; refusing undo.")

    before = manifest.get("before")
    if not isinstance(before, dict):
        raise ValidationError("Transaction has no recoverable before state.")
    restored: dict[str, bytes] = {}
    for relative, object_id in before.items():
        object_path = control_dir / "objects" / str(object_id)
        if not object_path.exists():
            raise ValidationError(f"Missing history object: {object_id}")
        restored[relative] = object_path.read_bytes()

    with VaultTransaction(root, expected_revision=current_revision) as transaction:
        current = {
            path.relative_to(root).as_posix(): path for path in _managed_files(root)
        }
        for relative in set(current) - set(restored):
            current[relative].unlink()
        for relative, data in restored.items():
            atomic_write_bytes(root / relative, data)
    return {
        "undone": transaction_id,
        "transaction": transaction.transaction_id,
        "before_revision": transaction.before_revision,
        "after_revision": transaction.after_revision,
        "changed": transaction.changed,
    }

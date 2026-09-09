"""Compact, exact commands for agents and reproducible automation."""

from __future__ import annotations

import json
import sys
import functools
from pathlib import Path

import click

from ..exceptions import CorError, ValidationError
from ..utils import get_notes_dir, require_init
from ..core.operations import dispatch_operation, get_entry, update_entry, validate_vault
from ..core.transactions import (
    VaultTransaction,
    recover_transaction,
    transaction_history,
    undo_transaction,
)
from ..core.storage import content_revision


def _emit_json(value) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _machine_errors(*, flag: str | None = None):
    """Emit stable JSON errors for machine-mode command callbacks."""
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except CorError as error:
                if flag is not None and not kwargs.get(flag, False):
                    raise
                _emit_json({
                    "ok": False,
                    "error": {"code": error.code, "message": str(error)},
                })
                raise click.exceptions.Exit(1) from error
        return wrapper
    return decorate


@click.command(name="get")
@click.argument("stem")
@click.option("--body", is_flag=True, help="Include the complete Markdown body.")
@click.option("--json", "as_json", is_flag=True, help="Emit compact JSON.")
@_machine_errors(flag="as_json")
@require_init(write=False)
def get_cmd(stem: str, body: bool, as_json: bool) -> None:
    """Read one entry by exact stem."""
    record = get_entry(get_notes_dir(), stem, include_body=body)
    if as_json:
        _emit_json(record)
        return
    click.echo(f"{record['stem']} ({record['type']}, {record['status'] or 'no status'})")
    click.echo(f"revision: {record['revision']}")
    for heading, value in record["sections"].items():
        click.echo(f"\n## {heading}\n{value}")


@click.command(name="update")
@click.argument("stem")
@click.option("--section", required=True, help="Exact H2 section heading.")
@click.option("--append", is_flag=True, help="Append instead of replacing the section.")
@click.option("--expect-revision", help="Reject the write if the entry changed.")
@click.option("--json", "as_json", is_flag=True, help="Emit compact JSON.")
@click.argument("text", nargs=-1)
@_machine_errors(flag="as_json")
@require_init
def update_cmd(
    stem: str,
    section: str,
    append: bool,
    expect_revision: str | None,
    as_json: bool,
    text: tuple[str, ...],
) -> None:
    """Set or append one Markdown section on an exact entry."""
    value = " ".join(text).strip()
    if not value:
        raise ValidationError("Section text cannot be empty.")
    kwargs = {"append_sections" if append else "section_values": {section: value}}
    record = update_entry(
        get_notes_dir(), stem, expected_revision=expect_revision, **kwargs
    )
    if as_json:
        _emit_json(record)
    else:
        click.echo(f"Updated {stem}: {section}")


def _read_manifest(source: str) -> dict:
    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text()
        manifest = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"Invalid batch manifest: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("operations"), list):
        raise ValidationError("Batch manifest requires an 'operations' list.")
    if manifest.get("version", 1) != 1:
        raise ValidationError("Unsupported batch manifest version.")
    if set(manifest) - {"version", "request_id", "operations"}:
        unknown = ", ".join(sorted(set(manifest) - {"version", "request_id", "operations"}))
        raise ValidationError(f"Unknown batch manifest fields: {unknown}")
    if not manifest["operations"]:
        raise ValidationError("Batch operations cannot be empty.")
    return manifest


@click.command(name="batch")
@click.argument("source", default="-")
@click.option("--dry-run", is_flag=True, help="Validate and report without persisting.")
@click.option("--expect-revision", help="Reject if the vault changed since planning.")
@_machine_errors()
@require_init
def batch_cmd(source: str, dry_run: bool, expect_revision: str | None) -> None:
    """Apply a JSON operation list as one all-or-nothing transaction."""
    manifest = _read_manifest(source)
    notes_dir = get_notes_dir()
    results = []
    request_id = manifest.get("request_id")
    if request_id is not None and (
        not isinstance(request_id, str)
        or not request_id
        or len(request_id) > 128
    ):
        raise ValidationError("request_id must be a non-empty string up to 128 characters.")
    request_hash = content_revision(
        json.dumps(
            manifest["operations"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    with VaultTransaction(
        notes_dir,
        expected_revision=expect_revision,
        dry_run=dry_run,
        request_id=request_id,
        request_hash=request_hash,
    ) as transaction:
        if not transaction.replayed:
            for specification in manifest["operations"]:
                if not isinstance(specification, dict):
                    raise ValidationError("Each batch operation must be an object.")
                results.append(dispatch_operation(notes_dir, specification))
    _emit_json({
        "transaction": transaction.transaction_id,
        "request_id": request_id,
        "state": "already-committed" if transaction.replayed else (
            "dry-run" if dry_run else "committed"
        ),
        "before_revision": transaction.before_revision,
        "after_revision": transaction.after_revision,
        "changed": transaction.changed,
        "results": results,
    })


@click.command(name="validate")
@click.option("--json", "as_json", is_flag=True, help="Emit compact JSON.")
@_machine_errors(flag="as_json")
@require_init(write=False)
def validate_cmd(as_json: bool) -> None:
    """Check vault structure and references without writing."""
    result = validate_vault(get_notes_dir())
    if as_json:
        _emit_json(result)
        return
    state = "valid" if result["valid"] else "invalid"
    click.echo(f"Vault {state}: {result['files']} files")
    for error in result["errors"]:
        click.echo(f"- {error['path']}: {error['message']}")


@click.command(name="history")
@click.option("--limit", type=click.IntRange(min=1), default=20)
@click.option("--json", "as_json", is_flag=True, help="Emit compact JSON.")
@_machine_errors(flag="as_json")
@require_init(write=False)
def history_cmd(limit: int, as_json: bool) -> None:
    """List recent local Cortex transactions."""
    records = transaction_history(get_notes_dir(), limit=limit)
    if as_json:
        _emit_json(records)
        return
    for record in records:
        click.echo(
            f"{record['id']} {record['state']} "
            f"{len(record.get('changed') or [])} files {record['timestamp']}"
        )


@click.command(name="undo")
@click.argument("transaction_id")
@_machine_errors()
@require_init
def undo_cmd(transaction_id: str) -> None:
    """Undo a committed transaction when no newer vault change exists."""
    _emit_json(undo_transaction(get_notes_dir(), transaction_id))


@click.command(name="recover")
@click.argument("transaction_id", required=False)
@_machine_errors()
@require_init
def recover_cmd(transaction_id: str | None) -> None:
    """Restore an interrupted transaction's durable before-state."""
    _emit_json(recover_transaction(get_notes_dir(), transaction_id))

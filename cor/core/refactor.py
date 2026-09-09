"""Exact, transaction-safe hierarchy moves shared by CLI and batch commands."""

from __future__ import annotations

import re
from pathlib import Path

from ..exceptions import AlreadyExistsError, ConflictError, ValidationError
from ..sync.runner import MaintenanceRunner
from ..utils import format_title
from .operations import entry_revision, resolve_exact
from .storage import atomic_write_text
from .transactions import managed_files, transactional_operation


def _validate_stem(stem: str) -> None:
    if stem.endswith(".md") or "/" in stem:
        raise ValidationError("Use an exact bare stem, without a path or .md suffix.")
    if any(not part or "&" in part for part in stem.split(".")):
        raise ValidationError(f"Invalid stem hierarchy: {stem}")


def _rewrite_links(notes_dir: Path, renames: list[tuple[Path, Path]]) -> None:
    stem_map = sorted(
        ((old.stem, new.stem) for old, new in renames),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    runner = MaintenanceRunner(notes_dir)
    for path in managed_files(notes_dir):
        content = path.read_text()
        updated = content
        for old_stem, new_stem in stem_map:
            updated = re.sub(
                rf"(?P<open>\]\((?:archive/|\.\./)?){re.escape(old_stem)}\.md(?P<close>\))",
                rf"\g<open>{new_stem}.md\g<close>",
                updated,
            )
        if updated != content:
            atomic_write_text(path, updated)
        runner.normalize_link_prefixes(path)


def _update_titles(renames: list[tuple[Path, Path]]) -> None:
    for _, path in renames:
        content = path.read_text()
        title = format_title(path.stem.split(".")[-1])
        updated = re.sub(r"^# .*$", f"# {title}", content, count=1, flags=re.MULTILINE)
        if updated != content:
            atomic_write_text(path, updated)


@transactional_operation
def move_entry(
    notes_dir: Path,
    stem: str,
    destination: str,
    *,
    expected_revision: str | None = None,
) -> dict:
    """Move one exact entry and its descendants to an exact destination stem."""
    _validate_stem(stem)
    _validate_stem(destination)
    if stem == destination:
        raise ValidationError("Source and destination stems are identical.")
    if destination.startswith(f"{stem}."):
        raise ValidationError("Cannot move an entry inside its own hierarchy.")

    source, _ = resolve_exact(notes_dir, stem)
    if expected_revision is not None and entry_revision(source) != expected_revision:
        raise ConflictError(f"Entry changed since it was read: {stem}")

    import frontmatter

    note_type = frontmatter.load(source).get("type")
    if note_type == "project" and "." in destination:
        raise ValidationError("A project destination cannot contain hierarchy dots.")
    new_parent = destination.rpartition(".")[0]
    if note_type in {"task", "note"} and new_parent:
        resolve_exact(notes_dir, new_parent)

    sources = [
        path for path in managed_files(notes_dir)
        if path.stem == stem or path.stem.startswith(f"{stem}.")
    ]
    source_set = set(sources)
    renames: list[tuple[Path, Path]] = []
    for old_path in sources:
        suffix = old_path.stem[len(stem):]
        new_path = old_path.with_name(f"{destination}{suffix}.md")
        if new_path.exists() and new_path not in source_set:
            raise AlreadyExistsError(f"Target already exists: {new_path.stem}")
        renames.append((old_path, new_path))

    target_stems = {new.stem for _, new in renames}
    for path in managed_files(notes_dir):
        if path in source_set:
            continue
        if any(
            path.stem == target or path.stem.startswith(f"{target}.")
            for target in target_stems
        ):
            raise AlreadyExistsError(f"Target hierarchy already exists: {path.stem}")

    for old_path, new_path in sorted(
        renames, key=lambda pair: pair[0].stem.count("."), reverse=True
    ):
        old_path.rename(new_path)

    relative_renames = [
        (
            old.relative_to(notes_dir).as_posix(),
            new.relative_to(notes_dir).as_posix(),
        )
        for old, new in renames
    ]
    _, errors = MaintenanceRunner(notes_dir).handle_renamed_files(relative_renames)
    if errors:
        raise ValidationError("; ".join(errors))
    _rewrite_links(notes_dir, renames)
    _update_titles(renames)

    destination_path, destination_archived = resolve_exact(notes_dir, destination)
    return {
        "stem": stem,
        "destination": destination,
        "archived": destination_archived,
        "revision": entry_revision(destination_path),
        "moved": [
            {
                "from": old.relative_to(notes_dir).as_posix(),
                "to": new.relative_to(notes_dir).as_posix(),
            }
            for old, new in renames
        ],
    }

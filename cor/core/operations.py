"""Exact, reusable Cortex operations shared by human and machine commands."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import frontmatter

from ..exceptions import AlreadyExistsError, ConflictError, NotFoundError, ValidationError
from ..schema import VALID_PROJECT_STATUS, VALID_TASK_STATUS
from ..sync.runner import MaintenanceRunner, validate_frontmatter, validate_links
from ..utils import add_task_to_project, format_title, render_template
from .archive import ArchiveManager
from .content import sections as read_sections
from .content import set_section
from .continuation import apply_continuation_context
from .files import save_note
from .relations import _all_notes, add_relation, remove_relation
from .storage import content_revision
from .transactions import managed_files, transactional_operation, vault_revision


PROTECTED_METADATA = {
    "type", "created", "modified", "parent", "status",
    "requires", "continues", "related",
}


def _mapping_field(
    specification: Mapping[str, Any], key: str
) -> Mapping[str, Any] | None:
    value = specification.get(key)
    if value is None:
        return None
    if not isinstance(value, Mapping) or not all(
        isinstance(item, str) for item in value
    ):
        raise ValidationError(f"'{key}' must be an object with string keys.")
    return value


def _section_field(
    specification: Mapping[str, Any], key: str
) -> Mapping[str, str] | None:
    value = _mapping_field(specification, key)
    if value is not None and not all(isinstance(item, str) for item in value.values()):
        raise ValidationError(f"'{key}' values must be strings.")
    return value


def _optional_string(specification: Mapping[str, Any], key: str) -> str | None:
    value = specification.get(key)
    if value is not None and not isinstance(value, str):
        raise ValidationError(f"'{key}' must be a string.")
    return value


def _reject_unknown_fields(
    specification: Mapping[str, Any], allowed: set[str]
) -> None:
    unknown = set(specification) - allowed
    if unknown:
        raise ValidationError(
            f"Unknown fields for {specification.get('op')}: {', '.join(sorted(unknown))}"
        )


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def resolve_exact(notes_dir: Path, stem: str) -> tuple[Path, bool]:
    """Resolve an exact bare stem, rejecting missing or ambiguous entries."""
    if stem.startswith("archive/"):
        stem = stem.removeprefix("archive/")
    if stem.endswith(".md") or "/" in stem:
        raise ValidationError("Use an exact bare stem, without a path or .md suffix.")
    active = notes_dir / f"{stem}.md"
    archived = notes_dir / "archive" / f"{stem}.md"
    matches = [path for path in (active, archived) if path.exists()]
    if not matches:
        raise NotFoundError(f"Entry not found: {stem}")
    if len(matches) > 1:
        raise ConflictError(f"Entry exists both active and archived: {stem}")
    return matches[0], matches[0] == archived


def entry_revision(path: Path) -> str:
    return content_revision(path.read_bytes())


def get_entry(notes_dir: str | Path, stem: str, *, include_body: bool = False) -> dict:
    """Return one exact entry as a stable, JSON-serializable record."""
    root = Path(notes_dir)
    path, archived = resolve_exact(root, stem)
    post = frontmatter.load(path)
    record = {
        "stem": path.stem,
        "type": post.get("type"),
        "status": post.get("status"),
        "archived": archived,
        "revision": entry_revision(path),
        "metadata": _json_value(dict(post.metadata)),
        "sections": read_sections(post.content),
    }
    if include_body:
        record["body"] = post.content
    return record


def _check_expected_revision(path: Path, expected_revision: str | None) -> None:
    if expected_revision and entry_revision(path) != expected_revision:
        raise ConflictError(f"Entry changed since it was read: {path.stem}")


def _validate_name(note_type: str, stem: str) -> None:
    if note_type not in {"project", "task", "note"}:
        raise ValidationError(f"Invalid entry type: {note_type}")
    parts = stem.split(".")
    if any(not part or "&" in part for part in parts):
        raise ValidationError("Invalid stem hierarchy.")
    if note_type == "project" and len(parts) != 1:
        raise ValidationError("Project stems cannot contain hierarchy dots.")


def _ensure_parents(notes_dir: Path, stem: str, child_type: str) -> None:
    parts = stem.split(".")[:-1]
    for index in range(len(parts)):
        parent_stem = ".".join(parts[: index + 1])
        active = notes_dir / f"{parent_stem}.md"
        archived = notes_dir / "archive" / f"{parent_stem}.md"
        if archived.exists() and not active.exists():
            ArchiveManager(notes_dir).unarchive_file(archived)
            post = frontmatter.load(active)
            if post.get("status") in {"done", "dropped"}:
                post["status"] = "todo" if post.get("type") == "task" else "planning"
                save_note(active, post)
        if active.exists():
            continue
        parent_type = "note" if child_type == "note" else (
            "project" if index == 0 else "task"
        )
        create_entry(notes_dir, parent_type, parent_stem, create_parents=False)


@transactional_operation
def create_entry(
    notes_dir: Path,
    note_type: str,
    stem: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    section_values: Mapping[str, str] | None = None,
    create_parents: bool = True,
) -> dict:
    """Create an exact entry from its vault template."""
    _validate_name(note_type, stem)
    active = notes_dir / f"{stem}.md"
    archived = notes_dir / "archive" / f"{stem}.md"
    if active.exists() or archived.exists():
        raise AlreadyExistsError(f"Entry already exists: {stem}")
    if create_parents and note_type in {"task", "note"}:
        _ensure_parents(notes_dir, stem, note_type)

    parts = stem.split(".")
    leaf = parts[-1]
    parent = ".".join(parts[:-1]) or None
    parent_title = format_title(parts[-2]) if parent else None
    template_path = notes_dir / "templates" / f"{note_type}.md"
    if not template_path.exists():
        raise NotFoundError(f"Template not found: {template_path}")
    content = render_template(
        template_path.read_text(), leaf, parent, parent_title
    )
    post = frontmatter.loads(content)
    for key, value in (metadata or {}).items():
        if key in {"type", "created", "modified", "parent", "requires", "continues", "related"}:
            raise ValidationError(f"Metadata field is managed by Cortex: {key}")
        post[key] = value
    if post.get("status") in {"done", "dropped"}:
        raise ValidationError("Create active work first, then use a transition operation.")
    for heading, value in (section_values or {}).items():
        post.content = set_section(post.content, heading, value)
    save_note(active, post)

    if note_type == "task" and parent:
        add_task_to_project(
            notes_dir / f"{parent}.md", leaf, stem
        )
    return get_entry(notes_dir, stem)


@transactional_operation
def update_entry(
    notes_dir: Path,
    stem: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    section_values: Mapping[str, str] | None = None,
    append_sections: Mapping[str, str] | None = None,
    expected_revision: str | None = None,
) -> dict:
    """Update safe metadata and Markdown sections on one exact entry."""
    path, _ = resolve_exact(notes_dir, stem)
    _check_expected_revision(path, expected_revision)
    post = frontmatter.load(path)
    for key, value in (metadata or {}).items():
        if key in PROTECTED_METADATA:
            raise ValidationError(f"Use a dedicated operation for metadata field: {key}")
        post[key] = value
    for heading, value in (section_values or {}).items():
        post.content = set_section(post.content, heading, value)
    for heading, value in (append_sections or {}).items():
        post.content = set_section(post.content, heading, value, append=True)
    save_note(path, post)
    MaintenanceRunner(notes_dir).sync([str(path)])
    return get_entry(notes_dir, stem)


@transactional_operation
def transition_entry(
    notes_dir: Path,
    stem: str,
    status: str,
    *,
    result: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    expected_revision: str | None = None,
    sync: bool = True,
) -> dict:
    """Change status, optionally recording the result in the same operation."""
    path, _ = resolve_exact(notes_dir, stem)
    _check_expected_revision(path, expected_revision)
    post = frontmatter.load(path)
    note_type = post.get("type")
    valid = VALID_TASK_STATUS if note_type == "task" else VALID_PROJECT_STATUS
    if note_type not in {"task", "project"}:
        raise ValidationError("Only tasks and projects have lifecycle status.")
    if status not in valid:
        raise ValidationError(
            f"Invalid {note_type} status '{status}'. Valid: {', '.join(sorted(valid))}"
        )

    if status in {"done", "dropped"}:
        incomplete = MaintenanceRunner(notes_dir).get_incomplete_tasks(stem)
        if incomplete:
            raise ValidationError(
                f"Cannot mark as {status}; incomplete children: {', '.join(incomplete)}"
            )
    post["status"] = status
    for key, value in (metadata or {}).items():
        if key in PROTECTED_METADATA:
            raise ValidationError(f"Use a dedicated operation for metadata field: {key}")
        if key == "tags":
            if not isinstance(value, list) or not all(
                isinstance(tag, str) for tag in value
            ):
                raise ValidationError("'tags' must be a list of strings.")
            existing = post.get("tags", []) or []
            if isinstance(existing, str):
                existing = [existing]
            value = existing + [tag for tag in value if tag not in existing]
        post[key] = value
    if result and result.strip():
        heading = "Solution" if note_type == "task" else "Summary"
        post.content = set_section(post.content, heading, result, append=True)
    save_note(path, post)
    if sync:
        MaintenanceRunner(notes_dir).sync([str(path)])
    return get_entry(notes_dir, stem)


@transactional_operation
def relate_entries(
    notes_dir: Path,
    stem: str,
    relation: str,
    targets: list[str],
    *,
    remove: bool = False,
) -> dict:
    """Add or remove exact relations through the canonical relation backend."""
    path, _ = resolve_exact(notes_dir, stem)
    changed = (
        remove_relation(notes_dir, stem, targets, relation)
        if remove
        else add_relation(notes_dir, stem, targets, relation)
    )
    if changed and relation == "continues" and not remove:
        apply_continuation_context(notes_dir, path, changed)
    if changed:
        MaintenanceRunner(notes_dir).sync([str(path)])
    return {
        "stem": stem,
        "relation": relation,
        "targets": changed,
        "removed": remove,
    }


def dispatch_operation(notes_dir: str | Path, specification: Mapping[str, Any]) -> dict:
    """Dispatch one validated batch specification to the canonical operation."""
    op = specification.get("op")
    stem = specification.get("stem")
    if not isinstance(op, str) or not isinstance(stem, str):
        raise ValidationError("Every operation requires string fields 'op' and 'stem'.")

    if op == "create":
        _reject_unknown_fields(
            specification,
            {"op", "type", "stem", "metadata", "sections", "create_parents"},
        )
        note_type = specification.get("type")
        if not isinstance(note_type, str):
            raise ValidationError("Create requires a string 'type'.")
        create_parents = specification.get("create_parents", False)
        if not isinstance(create_parents, bool):
            raise ValidationError("'create_parents' must be a boolean.")
        return create_entry(
            notes_dir,
            note_type,
            stem,
            metadata=_mapping_field(specification, "metadata"),
            section_values=_section_field(specification, "sections"),
            create_parents=create_parents,
        )
    if op == "update":
        _reject_unknown_fields(
            specification,
            {"op", "stem", "metadata", "sections", "append_sections", "expected_revision"},
        )
        return update_entry(
            notes_dir,
            stem,
            metadata=_mapping_field(specification, "metadata"),
            section_values=_section_field(specification, "sections"),
            append_sections=_section_field(specification, "append_sections"),
            expected_revision=_optional_string(specification, "expected_revision"),
        )
    if op == "transition":
        _reject_unknown_fields(
            specification,
            {"op", "stem", "status", "result", "metadata", "expected_revision"},
        )
        status = specification.get("status")
        if not isinstance(status, str):
            raise ValidationError("Transition requires a string 'status'.")
        return transition_entry(
            notes_dir,
            stem,
            status,
            result=_optional_string(specification, "result"),
            metadata=_mapping_field(specification, "metadata"),
            expected_revision=_optional_string(specification, "expected_revision"),
        )
    if op in {"relate", "unrelate"}:
        _reject_unknown_fields(
            specification, {"op", "stem", "relation", "targets"}
        )
        relation = specification.get("relation", "related")
        targets = specification.get("targets")
        if not isinstance(relation, str) or not isinstance(targets, list) or not targets or not all(
            isinstance(target, str) for target in targets
        ):
            raise ValidationError("Relate requires a relation and a list of target stems.")
        return relate_entries(
            notes_dir, stem, relation, targets, remove=op == "unrelate"
        )
    if op == "move":
        _reject_unknown_fields(
            specification, {"op", "stem", "to", "expected_revision"}
        )
        destination = specification.get("to")
        if not isinstance(destination, str):
            raise ValidationError("Move requires a string 'to' stem.")
        from .refactor import move_entry

        return move_entry(
            notes_dir,
            stem,
            destination,
            expected_revision=_optional_string(specification, "expected_revision"),
        )
    raise ValidationError(f"Unknown operation: {op}")


def validate_vault(notes_dir: str | Path) -> dict:
    """Validate documents, links, relation targets, and directional cycles."""
    root = Path(notes_dir)
    errors: list[dict[str, str]] = []
    for path in managed_files(root):
        relative = path.relative_to(root).as_posix()
        for message in validate_frontmatter(relative, root):
            errors.append({"path": relative, "kind": "frontmatter", "message": message})
        for message in validate_links(relative, root):
            errors.append({"path": relative, "kind": "link", "message": message})

    from ..dependencies import detect_circular_dependencies

    notes = _all_notes(root)
    seen_cycles: set[tuple[str, ...]] = set()
    for note in notes:
        for field in ("requires", "continues"):
            cycle = detect_circular_dependencies(note.path.stem, notes, field=field)
            if not cycle:
                continue
            canonical = tuple(sorted(set(cycle))) + (field,)
            if canonical in seen_cycles:
                continue
            seen_cycles.add(canonical)
            errors.append({
                "path": note.path.relative_to(root).as_posix(),
                "kind": "cycle",
                "message": f"{field}: {' -> '.join(cycle)}",
            })
    return {
        "valid": not errors,
        "revision": vault_revision(root),
        "files": len(managed_files(root)),
        "errors": errors,
    }

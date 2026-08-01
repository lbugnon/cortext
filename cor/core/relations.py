"""Adding and removing relations between notes.

Cor stores only the forward edge of every relation in frontmatter; reverse
directions (`blocks`, `continued_by`, and the far side of `related`) are
computed by scanning. See :func:`cor.dependencies.get_relations`.

All relation values are bare stems, never paths. That is deliberate: it keeps
relations stable when a note moves in or out of ``archive/``, which would
otherwise require rewriting every pointer at it.
"""

from pathlib import Path

import frontmatter

from ..dependencies import RELATION_FIELDS, detect_circular_dependencies
from ..exceptions import NotFoundError, ValidationError
from .archive import ArchiveManager
from .notes import NoteMetadata, _as_list


def _load(path: Path) -> frontmatter.Post:
    return frontmatter.load(path)


def _save(path: Path, post: frontmatter.Post) -> None:
    with open(path, "wb") as f:
        frontmatter.dump(post, f, sort_keys=False)


def _all_notes(notes_dir: Path) -> list[NoteMetadata]:
    """Every note in the vault, including archived ones.

    Archived notes must be included: the predecessor of a continued project
    normally lives in ``archive/``, and omitting it would make a perfectly
    valid target look missing.
    """
    notes = []
    for directory in (notes_dir, notes_dir / "archive"):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name.startswith(".") or path.stem == "backlog":
                continue
            try:
                notes.append(NoteMetadata.from_file(path))
            except Exception:
                continue
    return notes


def resolve_note(notes_dir: Path, stem: str) -> tuple[Path, bool]:
    """Locate a note by stem, searching the archive too.

    Returns:
        (path, is_archived)

    Raises:
        NotFoundError: if no such note exists
    """
    found = ArchiveManager(notes_dir).find_file(stem)
    if found is None:
        raise NotFoundError(f"Note not found: {stem}")
    return found


def validate_relation(
    notes_dir: Path,
    note_stem: str,
    target_stem: str,
    field: str,
) -> None:
    """Check that a relation is legal before writing it.

    Raises:
        ValidationError: self-links, unknown fields, wrong note type, or a
            cycle in a directional relation.
        NotFoundError: if either end does not exist.
    """
    if field not in RELATION_FIELDS:
        valid = ", ".join(sorted(RELATION_FIELDS))
        raise ValidationError(f"Unknown relation '{field}'. Valid: {valid}")

    if note_stem == target_stem:
        raise ValidationError(f"A note cannot be {field}-linked to itself: {note_stem}")

    note_path, _ = resolve_note(notes_dir, note_stem)
    target_path, _ = resolve_note(notes_dir, target_stem)

    allowed_types = RELATION_FIELDS[field]
    note_type = _load(note_path).get("type")
    target_type = _load(target_path).get("type")

    if note_type not in allowed_types:
        raise ValidationError(
            f"'{field}' is not valid on a {note_type or 'typeless'} note "
            f"({note_stem}). Valid types: {', '.join(allowed_types)}"
        )
    if target_type not in allowed_types:
        raise ValidationError(
            f"'{field}' target must be one of {', '.join(allowed_types)}, "
            f"but {target_stem} is a {target_type or 'typeless'} note"
        )


def _would_create_cycle(
    notes_dir: Path, note_stem: str, target_stem: str, field: str
) -> list[str] | None:
    """Return the cycle a proposed directional edge would create, if any."""
    if field not in ("requires", "continues"):
        return None

    notes = _all_notes(notes_dir)
    for note in notes:
        if note.path.stem == note_stem:
            current = list(getattr(note, field, None) or [])
            if target_stem not in current:
                setattr(note, field, current + [target_stem])
            break

    return detect_circular_dependencies(note_stem, notes, field=field)


def add_relation(
    notes_dir: Path,
    note_stem: str,
    target_stems: list[str],
    field: str,
) -> list[str]:
    """Add relation edges from a note to one or more targets.

    Idempotent: targets already present are skipped rather than duplicated.

    Returns:
        The stems actually added.

    Raises:
        ValidationError / NotFoundError: see :func:`validate_relation`
    """
    added = []
    for target in target_stems:
        validate_relation(notes_dir, note_stem, target, field)

        cycle = _would_create_cycle(notes_dir, note_stem, target, field)
        if cycle:
            raise ValidationError(
                f"'{field}' would create a cycle: {' -> '.join(cycle)}"
            )

        note_path, _ = resolve_note(notes_dir, note_stem)
        post = _load(note_path)
        current = _as_list(post.get(field))
        if target in current:
            continue
        post[field] = current + [target]
        _save(note_path, post)
        added.append(target)

    return added


def remove_relation(
    notes_dir: Path,
    note_stem: str,
    target_stems: list[str],
    field: str,
) -> list[str]:
    """Remove relation edges from a note.

    For symmetric relations the edge may be stored on the *other* side, so
    ``related`` is also cleared from the target when it is not found locally.

    Returns:
        The stems actually removed.
    """
    if field not in RELATION_FIELDS:
        valid = ", ".join(sorted(RELATION_FIELDS))
        raise ValidationError(f"Unknown relation '{field}'. Valid: {valid}")

    note_path, _ = resolve_note(notes_dir, note_stem)
    removed = []

    for target in target_stems:
        post = _load(note_path)
        current = _as_list(post.get(field))

        if target in current:
            post[field] = [s for s in current if s != target]
            _save(note_path, post)
            removed.append(target)
            continue

        # Symmetric relation stored on the far side.
        if field == "related":
            try:
                target_path, _ = resolve_note(notes_dir, target)
            except NotFoundError:
                continue
            target_post = _load(target_path)
            target_current = _as_list(target_post.get(field))
            if note_stem in target_current:
                target_post[field] = [s for s in target_current if s != note_stem]
                _save(target_path, target_post)
                removed.append(target)

    return removed

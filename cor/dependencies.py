"""Dependency tracking and resolution for tasks and projects."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .core.notes import NoteMetadata


@dataclass
class DependencyInfo:
    """Information about a note's dependencies."""

    note_stem: str
    note_type: str  # "task", "project", etc.
    requires: list[str]  # Items this note requires
    blocked_by: list[str]  # Requirements that are unmet
    blocks: list[str]  # Items that require this note
    all_requirements_met: bool
    missing_requirements: list[str]  # Requirements that don't exist
    circular_dependencies: list[str]  # Circular dependency chain if detected


def calculate_inverse(
    notes: list[NoteMetadata],
    field: str = "requires",
    note_types: tuple[str, ...] = ("task", "project"),
) -> dict[str, list[str]]:
    """Calculate the inverse of a relation field (target -> notes pointing at it).

    This is how every reverse direction in Cor is derived: only the forward
    edge is ever stored in frontmatter, so the two directions cannot drift
    apart and archived targets never need rewriting.

    Args:
        notes: List of all notes
        field: Relation field to invert ("requires", "continues", "related")
        note_types: Note types that may carry the field. `related` is valid on
            notes too, so callers pass a wider tuple for it.

    Returns:
        Dict mapping a stem to the list of stems that point at it via `field`
    """
    inverse: dict[str, list[str]] = {}

    for note in notes:
        if note_types and note.note_type not in note_types:
            continue

        note_stem = note.path.stem

        for target in getattr(note, field, None) or []:
            inverse.setdefault(target, []).append(note_stem)

    return inverse


def calculate_inverse_dependencies(notes: list[NoteMetadata]) -> dict[str, list[str]]:
    """Calculate inverse dependency mapping (note -> notes that require it).

    Thin wrapper over calculate_inverse() kept for existing callers.

    Args:
        notes: List of all notes

    Returns:
        Dict mapping note stem to list of note stems that require it
    """
    return calculate_inverse(notes, field="requires")


def check_dependencies_met(note: NoteMetadata, all_notes: list[NoteMetadata]) -> tuple[bool, list[str]]:
    """Check if all requirements for a note are met.

    Args:
        note: Note to check
        all_notes: All notes for looking up requirement status

    Returns:
        (all_met, unmet_requirements)
        - all_met: True if all requirements are done
        - unmet_requirements: List of requirement stems that are not done
    """
    if not note.requires:
        return True, []

    # Build lookup map
    notes_by_stem = {n.path.stem: n for n in all_notes}

    unmet = []
    for req_stem in note.requires:
        req_note = notes_by_stem.get(req_stem)
        if not req_note:
            # Requirement doesn't exist (will be caught in validation)
            continue

        # Check if requirement is complete
        # For tasks: done or dropped
        # For projects: done
        if req_note.note_type == "task":
            if req_note.status not in ("done", "dropped"):
                unmet.append(req_stem)
        elif req_note.note_type == "project":
            if req_note.status != "done":
                unmet.append(req_stem)

    return len(unmet) == 0, unmet


def detect_circular_dependencies(
    note_stem: str,
    all_notes: list[NoteMetadata],
    field: str = "requires",
) -> Optional[list[str]]:
    """Detect if note is part of a circular chain for a directional relation.

    Args:
        note_stem: Note to check
        all_notes: All notes
        field: Directional relation field to follow ("requires" or "continues").
            Do not pass "related" - it is symmetric, so every edge is trivially
            a two-cycle and the question is meaningless.

    Returns:
        List representing the circular chain if found, None otherwise
        Example: ["a", "b", "c", "a"] means a->b->c->a
    """
    notes_by_stem = {n.path.stem: n for n in all_notes}

    def dfs(current: str, path: list[str], visited: set[str]) -> Optional[list[str]]:
        if current in path:
            # Found cycle
            cycle_start = path.index(current)
            return path[cycle_start:] + [current]

        if current in visited:
            return None

        visited.add(current)
        note = notes_by_stem.get(current)

        if not note:
            return None

        targets = getattr(note, field, None) or []
        for target in targets:
            result = dfs(target, path + [current], visited)
            if result:
                return result

        return None

    return dfs(note_stem, [], set())


def validate_dependencies(note: NoteMetadata, all_notes: list[NoteMetadata]) -> list[str]:
    """Validate note dependencies.

    Args:
        note: Note to validate
        all_notes: All notes

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if not note.requires:
        return errors

    notes_by_stem = {n.path.stem: n for n in all_notes}

    # Check 1: All requirements exist
    for req_stem in note.requires:
        if req_stem not in notes_by_stem:
            errors.append(f"Requirement does not exist: {req_stem}")

    # Check 2: No self-dependency
    note_stem = note.path.stem
    if note_stem in note.requires:
        errors.append(f"Note cannot require itself")

    # Check 3: No circular dependencies
    circular = detect_circular_dependencies(note_stem, all_notes)
    if circular:
        cycle_str = " -> ".join(circular)
        errors.append(f"Circular dependency detected: {cycle_str}")

    return errors


def get_dependency_info(note: NoteMetadata, all_notes: list[NoteMetadata]) -> DependencyInfo:
    """Get comprehensive dependency information for a note.

    Args:
        note: Note to analyze
        all_notes: All notes

    Returns:
        DependencyInfo with all dependency details
    """
    note_stem = note.path.stem

    # Get items this note requires
    requires = note.requires if note.requires else []

    # Calculate inverse dependencies
    inverse_map = calculate_inverse_dependencies(all_notes)
    blocks = inverse_map.get(note_stem, [])

    # Check which requirements are met
    all_met, unmet = check_dependencies_met(note, all_notes)

    # Find missing requirements
    notes_by_stem = {n.path.stem: n for n in all_notes}
    missing = [req for req in requires if req not in notes_by_stem]

    # Detect circular dependencies
    circular = detect_circular_dependencies(note_stem, all_notes) or []

    return DependencyInfo(
        note_stem=note_stem,
        note_type=note.note_type,
        requires=requires,
        blocked_by=unmet,
        blocks=blocks,
        all_requirements_met=all_met,
        missing_requirements=missing,
        circular_dependencies=circular,
    )


# --- General relations (requires / continues / related) ---

#: Relation fields that may appear in frontmatter, and the note types that may
#: carry them. Only the forward edge is stored; inverses are computed.
RELATION_FIELDS = {
    "requires": ("task", "project"),
    "continues": ("project",),
    "related": ("task", "project", "note"),
}

#: Symmetric relations read the same from both ends, so a query returns the
#: union of the stored edges and the reverse scan.
SYMMETRIC_RELATIONS = {"related"}


@dataclass
class RelationInfo:
    """All relations for a note, forward and computed-inverse."""

    note_stem: str
    note_type: str
    requires: list[str]       # stored: things this note depends on
    blocks: list[str]         # computed: notes that require this one
    continues: list[str]      # stored: predecessor projects
    continued_by: list[str]   # computed: successor projects
    related: list[str]        # stored + reverse scan (symmetric)
    missing: dict[str, list[str]]  # field -> targets that do not exist

    def is_empty(self) -> bool:
        """True when the note takes part in no relations at all."""
        return not any(
            (self.requires, self.blocks, self.continues, self.continued_by, self.related)
        )


def get_relations(note: NoteMetadata, all_notes: list[NoteMetadata]) -> RelationInfo:
    """Collect every relation for a note, resolving computed inverses.

    Args:
        note: Note to analyze
        all_notes: All notes, including archived ones - predecessors of a
            continued project normally live in archive/, so omitting them
            would report them as missing.

    Returns:
        RelationInfo with stored and computed relations
    """
    note_stem = note.path.stem
    known_stems = {n.path.stem for n in all_notes}

    requires = list(note.requires or [])
    continues = list(note.continues or [])
    stored_related = list(note.related or [])

    blocks = calculate_inverse(all_notes, "requires").get(note_stem, [])
    continued_by = calculate_inverse(
        all_notes, "continues", RELATION_FIELDS["continues"]
    ).get(note_stem, [])

    # `related` is symmetric: merge what this note stores with the notes that
    # store a pointer back to it, preserving order and dropping duplicates.
    reverse_related = calculate_inverse(
        all_notes, "related", RELATION_FIELDS["related"]
    ).get(note_stem, [])
    related = list(dict.fromkeys(stored_related + reverse_related))

    missing = {}
    for field, targets in (
        ("requires", requires),
        ("continues", continues),
        ("related", stored_related),
    ):
        absent = [t for t in targets if t not in known_stems]
        if absent:
            missing[field] = absent

    return RelationInfo(
        note_stem=note_stem,
        note_type=note.note_type,
        requires=requires,
        blocks=blocks,
        continues=continues,
        continued_by=continued_by,
        related=related,
        missing=missing,
    )

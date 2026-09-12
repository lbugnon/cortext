"""Relation management commands (`cor rel`).

One command surface for every relation between notes:

- ``requires``  - directional soft dependency; inverse is ``blocks``
- ``continues`` - directional; a project picking up finished work.
  Inverse is ``continued_by``.
- ``related``   - symmetric "see also"

Only the forward edge is stored in frontmatter; every inverse is computed by
scanning, so the two directions cannot drift apart and archived targets are
never rewritten.

``cor depend`` remains as a narrower alias for the ``requires`` case.
"""

import click

from ..completions import complete_relation_target
from ..config import get_focused_project
from ..core.notes import parse_note
from ..core.operations import relate_entries
from ..core.relations import _all_notes, resolve_note
from ..dependencies import RELATION_FIELDS, get_relations
from ..exceptions import NotFoundError
from ..search import resolve_file_fuzzy
from ..utils import get_notes_dir, require_init, log_info
from ..core.transactions import transactional_command

RELATION_CHOICES = click.Choice(sorted(RELATION_FIELDS))


def _resolve(name: str, include_archived: bool) -> str | None:
    """Fuzzy-resolve a note name to a stem, or None if the user cancelled."""
    result = resolve_file_fuzzy(
        name,
        include_archived=include_archived,
        focused_project=get_focused_project(),
    )
    if result is None:
        return None
    stem, _is_archived = result
    return stem


@click.command(name="add", short_help="Add a relation between notes")
@click.option(
    "--as", "field",
    type=RELATION_CHOICES,
    default="related",
    help="Relation type (default: related).",
)
@click.argument("note", shell_complete=complete_relation_target)
@click.argument("targets", nargs=-1, required=True, shell_complete=complete_relation_target)
@require_init
@transactional_command
def rel_add(field: str, note: str, targets: tuple[str, ...]):
    """Relate NOTE to one or more TARGETS.

    \b
    Examples:
      cor rel add new-thing old-thing --as continues   # picking up finished work
      cor rel add new-thing old-a old-b --as continues # more than one predecessor
      cor rel add proj-a proj-b                        # symmetric "see also"
      cor rel add proj.feature proj.setup --as requires

    Targets are searched in the archive too - a project you are continuing has
    normally been archived already.
    """
    notes_dir = get_notes_dir()

    note_stem = _resolve(note, include_archived=True)
    if note_stem is None:
        return

    target_stems = []
    for target in targets:
        stem = _resolve(target, include_archived=True)
        if stem is None:
            return
        target_stems.append(stem)

    added = relate_entries(
        notes_dir, note_stem, field, target_stems
    )["targets"]

    if not added:
        log_info(f"No new {field} links to add for {note_stem}")
        return

    for target in added:
        click.echo(
            f"{click.style(note_stem, fg='cyan')} "
            f"{field} {click.style(target, fg='green')}"
        )


@click.command(name="rm", short_help="Remove a relation between notes")
@click.option(
    "--as", "field",
    type=RELATION_CHOICES,
    default="related",
    help="Relation type (default: related).",
)
@click.argument("note", shell_complete=complete_relation_target)
@click.argument("targets", nargs=-1, required=True, shell_complete=complete_relation_target)
@require_init
@transactional_command
def rel_rm(field: str, note: str, targets: tuple[str, ...]):
    """Remove a relation from NOTE to one or more TARGETS.

    For `related`, the edge is removed wherever it is stored - the relation is
    symmetric, so it may live on either side.

    Note that the body context copied by `rel add --as continues` is left in
    place; remove it by hand if you no longer want it.
    """
    notes_dir = get_notes_dir()

    note_stem = _resolve(note, include_archived=True)
    if note_stem is None:
        return

    target_stems = []
    for target in targets:
        stem = _resolve(target, include_archived=True)
        if stem is None:
            return
        target_stems.append(stem)

    removed = relate_entries(
        notes_dir, note_stem, field, target_stems, remove=True
    )["targets"]

    if not removed:
        log_info(f"No {field} links to remove from {note_stem}")
        return

    for target in removed:
        click.echo(
            f"{click.style(note_stem, fg='cyan')} no longer "
            f"{field} {click.style(target, fg='yellow')}"
        )


def _print_group(label: str, stems: list[str], notes_by_stem: dict, color: str):
    """Print one relation group with titles and statuses."""
    if not stems:
        return
    click.echo(click.style(f"\n{label}:", fg=color))
    for stem in stems:
        note = notes_by_stem.get(stem)
        if note is None:
            click.echo(f"  {click.style('x', fg='red')} {stem} (missing)")
            continue
        archived = " [archived]" if "archive" in note.path.parts else ""
        status = f" ({note.status})" if note.status else ""
        click.echo(
            f"  - {note.title}{status}"
            f"{click.style(archived, fg='yellow')} "
            f"{click.style(stem, dim=True)}"
        )


@click.command(name="show", short_help="Show all relations for a note")
@click.argument("note", shell_complete=complete_relation_target)
@require_init(write=False)
def rel_show(note: str):
    """Show every relation for NOTE, including computed inverses.

    Example:
        cor rel show new-thing
    """
    notes_dir = get_notes_dir()

    note_stem = _resolve(note, include_archived=True)
    if note_stem is None:
        return

    note_path, _ = resolve_note(notes_dir, note_stem)
    target = parse_note(note_path)

    all_notes = _all_notes(notes_dir)
    notes_by_stem = {n.path.stem: n for n in all_notes}

    info = get_relations(target, all_notes)

    click.echo(click.style(f"\n{target.title}", bold=True, fg="cyan"))
    click.echo(click.style(f"({note_stem})", dim=True))

    if info.is_empty():
        click.echo(click.style("\nNo relations", dim=True))
        click.echo()
        return

    _print_group("Continues", info.continues, notes_by_stem, "magenta")
    _print_group("Continued by", info.continued_by, notes_by_stem, "magenta")
    _print_group("Requires", info.requires, notes_by_stem, "yellow")
    _print_group("Blocks", info.blocks, notes_by_stem, "yellow")
    _print_group("Related", info.related, notes_by_stem, "blue")

    if info.missing:
        click.echo(click.style("\nWarning: targets that do not exist", fg="red"))
        for field, stems in info.missing.items():
            for stem in stems:
                click.echo(f"  - {field}: {stem}")

    click.echo()


@click.group()
def rel():
    """Manage relations between notes.

    \b
    Relation types:
      continues  A project picking up finished work (inverse: continued by)
      related    Symmetric "see also"
      requires   Soft dependency (inverse: blocks)

    Relations are soft indicators - they never block work.
    """
    pass


rel.add_command(rel_add)
rel.add_command(rel_rm)
rel.add_command(rel_show)

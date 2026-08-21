"""Dependency management commands."""

from datetime import datetime

import click
import frontmatter

from ..exceptions import ValidationError
from ..completions import complete_task_name
from ..dependencies import get_dependency_info, validate_dependencies
from ..search import resolve_file_fuzzy, get_file_path
from ..core.notes import find_notes, parse_note
from ..schema import DATE_TIME
from ..utils import get_notes_dir, require_init, log_info
from ..config import get_focused_project


@click.command(short_help="Add requirement between notes")
@click.argument("dependent_item", shell_complete=complete_task_name)
@click.argument("required_item", shell_complete=complete_task_name)
@require_init
def depend_add(dependent_item: str, required_item: str):
    """Add a requirement: DEPENDENT_ITEM requires REQUIRED_ITEM.

    Notation: "task2 requires task1" means task1 must be done before task2.

    Examples:
        cor depend add proj.feature proj.setup
        # Feature requires setup (setup blocks feature)

        cor depend add p1.task p2.task
        # Cross-project requirement

        cor depend add project1 project2
        # Project1 requires project2
    """
    notes_dir = get_notes_dir()

    # Resolve both items using fuzzy matching with focus prioritization
    focused = get_focused_project()
    result1 = resolve_file_fuzzy(dependent_item, include_archived=False, focused_project=focused)
    if result1 is None:
        return

    dependent_stem, _ = result1
    dependent_path = get_file_path(dependent_stem, False)

    result2 = resolve_file_fuzzy(required_item, include_archived=False, focused_project=focused)
    if result2 is None:
        return

    required_stem, _ = result2
    required_path = get_file_path(required_stem, False)

    # Load dependent item
    post = frontmatter.load(dependent_path)

    # Check it's a task or project
    note_type = post.get("type")
    if note_type not in ("task", "project"):
        raise ValidationError(
            f"{dependent_stem} is not a task or project (type: {note_type})"
        )

    # Get current requirements
    current_reqs = post.get("requires", [])

    if required_stem in current_reqs:
        log_info(f"Requirement already exists: {dependent_stem} requires {required_stem}")
        return

    # Add requirement
    updated_reqs = current_reqs + [required_stem]
    post["requires"] = updated_reqs
    post["modified"] = datetime.now().strftime(DATE_TIME)

    # Validate before saving
    all_notes = find_notes(notes_dir)
    # Also check archive
    archive_dir = notes_dir / "archive"
    if archive_dir.exists():
        all_notes.extend(find_notes(archive_dir))

    temp_note = parse_note(dependent_path)
    temp_note.requires = updated_reqs

    errors = validate_dependencies(temp_note, all_notes)
    if errors:
        click.echo(click.style("Validation errors:", fg="red"))
        for error in errors:
            click.echo(f"  - {error}")
        return

    # Save
    with open(dependent_path, "wb") as f:
        frontmatter.dump(post, f, sort_keys=False)

    log_info(
        f"Added requirement: {click.style(dependent_stem, fg='cyan')} "
        f"requires {click.style(required_stem, fg='green')}"
    )


@click.command(short_help="Remove requirement between notes")
@click.argument("dependent_item", shell_complete=complete_task_name)
@click.argument("required_item", shell_complete=complete_task_name)
@require_init
def depend_remove(dependent_item: str, required_item: str):
    """Remove a requirement between notes.

    Example:
        cor depend remove proj.feature proj.setup
    """
    # Resolve items with focus prioritization
    focused = get_focused_project()
    result1 = resolve_file_fuzzy(dependent_item, include_archived=False, focused_project=focused)
    if result1 is None:
        return

    dependent_stem, _ = result1
    dependent_path = get_file_path(dependent_stem, False)

    result2 = resolve_file_fuzzy(required_item, include_archived=False, focused_project=focused)
    if result2 is None:
        return

    required_stem, _ = result2

    # Load dependent item
    post = frontmatter.load(dependent_path)

    # Get current requirements
    current_reqs = post.get("requires", [])

    if required_stem not in current_reqs:
        log_info(
            f"Requirement does not exist: {dependent_stem} requires {required_stem}"
        )
        return

    # Remove requirement
    updated_reqs = [r for r in current_reqs if r != required_stem]
    post["requires"] = updated_reqs
    post["modified"] = datetime.now().strftime(DATE_TIME)

    # Save
    with open(dependent_path, "wb") as f:
        frontmatter.dump(post, f, sort_keys=False)

    log_info(
        f"Removed requirement: {click.style(dependent_stem, fg='cyan')} "
        f"no longer requires {click.style(required_stem, fg='yellow')}"
    )


@click.command(short_help="List dependencies for a note")
@click.argument("item_name", shell_complete=complete_task_name)
@require_init(write=False)
def depend_list(item_name: str):
    """Show dependency information for a task or project.

    Displays:
    - What this item requires
    - Which requirements are met/unmet
    - What items require this one (blocked by this)

    Example:
        cor depend list proj.feature
    """
    notes_dir = get_notes_dir()

    # Resolve item with focus prioritization
    focused = get_focused_project()
    result = resolve_file_fuzzy(item_name, include_archived=False, focused_project=focused)
    if result is None:
        return

    item_stem, _ = result
    item_path = get_file_path(item_stem, False)

    # Parse note
    note = parse_note(item_path)
    if note.note_type not in ("task", "project"):
        raise ValidationError(
            f"{item_stem} is not a task or project (type: {note.note_type})"
        )

    # Get all notes for analysis
    all_notes = find_notes(notes_dir)
    archive_dir = notes_dir / "archive"
    if archive_dir.exists():
        all_notes.extend(find_notes(archive_dir))

    # Get dependency info
    dep_info = get_dependency_info(note, all_notes)

    # Display
    click.echo(click.style(f"\n{note.title}", bold=True, fg="cyan"))
    click.echo(click.style(f"({item_stem})", dim=True))

    # Requirements
    if dep_info.requires:
        click.echo(click.style("\nRequires:", fg="yellow"))
        notes_by_stem = {n.path.stem: n for n in all_notes}
        for req_stem in dep_info.requires:
            req_note = notes_by_stem.get(req_stem)
            if req_note:
                # Check if requirement is met
                is_met = False
                if req_note.note_type == "task":
                    is_met = req_note.status in ("done", "dropped")
                elif req_note.note_type == "project":
                    is_met = req_note.status == "done"

                status_icon = "✓" if is_met else "○"
                status_color = "green" if is_met else "white"
                click.echo(
                    f"  {click.style(status_icon, fg=status_color)} "
                    f"{req_note.title} ({req_note.status})"
                )
            else:
                click.echo(f"  {click.style('✗', fg='red')} {req_stem} (missing)")

        if dep_info.all_requirements_met:
            click.echo(click.style("\n✓ All requirements met", fg="green"))
        else:
            count = len(dep_info.blocked_by)
            plural = "y" if count == 1 else "ies"
            click.echo(
                click.style(
                    f"\n○ Waiting on {count} requirement{plural}",
                    fg="yellow",
                )
            )
    else:
        click.echo(click.style("\nNo requirements", dim=True))

    # Blocks
    if dep_info.blocks:
        click.echo(click.style("\nBlocks these items:", fg="magenta"))
        notes_by_stem = {n.path.stem: n for n in all_notes}
        for blocked_stem in dep_info.blocks:
            blocked_note = notes_by_stem.get(blocked_stem)
            if blocked_note:
                click.echo(f"  • {blocked_note.title} ({blocked_note.status})")
            else:
                click.echo(f"  • {blocked_stem}")
    else:
        click.echo(click.style("\nDoes not block any items", dim=True))

    # Validation warnings
    if dep_info.missing_requirements:
        click.echo(click.style("\n⚠ Warning: Missing requirements", fg="red"))
        for missing in dep_info.missing_requirements:
            click.echo(f"  - {missing}")

    if dep_info.circular_dependencies:
        click.echo(click.style("\n⚠ Warning: Circular dependency", fg="red"))
        cycle_str = " -> ".join(dep_info.circular_dependencies)
        click.echo(f"  {cycle_str}")

    click.echo()


@click.group()
def depend():
    """Manage task and project dependencies.

    Dependencies are soft indicators - they don't block work, just provide
    information about relationships between items.

    This is the `requires` half of `cor rel`, kept as a dedicated command.
    `cor rel` additionally covers `continues` and `related`.
    """
    pass


depend.add_command(depend_add, name="add")
depend.add_command(depend_remove, name="remove")
depend.add_command(depend_list, name="list")

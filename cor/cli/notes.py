"""Note and task management commands for Cortex CLI."""

import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click
import frontmatter

from . import cli
from ..exceptions import ValidationError, NotFoundError, AlreadyExistsError
from ..schema import VALID_TASK_STATUS, VALID_PROJECT_STATUS, STATUS_SYMBOLS, DATE_TIME
from ..config import get_focused_project
from ..core.notes import parse_metadata
from ..sync import MaintenanceRunner
from ..utils import (
    get_notes_dir,
    get_template,
    format_title,
    render_template,
    open_in_editor,
    add_task_to_project,
    require_init,
    log_info,
    log_verbose,
    parse_natural_language_text,
    read_h1,
    title_to_stem,
)
from ..completions import complete_name, complete_task_name, complete_task_status, complete_existing_name
from ..search import resolve_file_fuzzy, get_file_path, resolve_task_fuzzy, resolve_files


def _template_for_level(child_type: str, level_index: int) -> str:
    """Return the template type for an auto-created parent at the given level.

    Tasks live under projects, so a missing top-level (level_index == 0) parent
    for a task becomes a project; deeper levels become task groups. Notes nest
    freely under notes at every level.
    """
    if child_type == "note":
        return "note"
    return "project" if level_index == 0 else "task"


def _ensure_parents_exist(notes_dir: Path, parent_parts: list[str], child_type: str) -> None:
    """Create any missing parent files in a hierarchy.

    For ``project.group.leaf`` with child_type ``task``, ensures ``project.md``
    (as project) and ``project.group.md`` (as task group) exist. Unarchives
    parents that are present only in archive/. For task children, also links
    each newly created intermediate level into its own parent's Tasks section.
    """
    archive_dir = notes_dir / "archive"

    for i, level_name in enumerate(parent_parts):
        level_stem = ".".join(parent_parts[: i + 1])
        level_path = notes_dir / f"{level_stem}.md"
        archived_path = archive_dir / f"{level_stem}.md"

        if archived_path.exists() and not level_path.exists():
            shutil.move(str(archived_path), level_path)
            post = frontmatter.load(level_path)
            old_status = post.get("status")
            if old_status in ("done", "dropped"):
                post["status"] = "todo"
                with open(level_path, "wb") as f:
                    frontmatter.dump(post, f, sort_keys=False)
                click.echo(f"Unarchived {level_stem} ({old_status} → todo)")
            else:
                click.echo(f"Unarchived {level_stem}")

            if i > 0:
                grandparent_stem = ".".join(parent_parts[:i])
                grandparent_path = notes_dir / f"{grandparent_stem}.md"
                if grandparent_path.exists():
                    content = grandparent_path.read_text()
                    pattern = rf'(\[[^\]]+\]\()archive/{re.escape(level_stem)}\.md(\))'
                    new_content = re.sub(pattern, rf'\g<1>{level_stem}.md\g<2>', content)
                    if new_content != content:
                        grandparent_path.write_text(new_content)

        if level_path.exists():
            continue

        level_type = _template_for_level(child_type, i)
        level_template = get_template(level_type)
        if i > 0:
            level_parent = ".".join(parent_parts[:i])
            level_parent_title = format_title(parent_parts[i - 1])
        else:
            level_parent = None
            level_parent_title = None

        level_content = render_template(
            level_template, level_name, level_parent, level_parent_title
        )
        level_path.write_text(level_content)
        click.echo(f"Created {level_path}")

        # Tasks index themselves in their parent's Tasks section; notes don't.
        if i > 0 and child_type == "task":
            parent_path = notes_dir / f"{level_parent}.md"
            add_task_to_project(parent_path, level_name, level_stem)
            click.echo(f"Added to {parent_path}")


@cli.command()
@click.argument("note_type", type=click.Choice(["project", "task", "note"]))
@click.argument("name", shell_complete=complete_name)
@click.argument("text", nargs=-1)
@click.option("--no-edit", is_flag=True, help="Do not open the new file in editor")
@require_init
def new(note_type: str, name: str, text: tuple[str, ...], no_edit: bool):
    """Create a new project, task, or note.

    Use dot notation for hierarchy: project.task, project.group.task, or deeper
    Task groups auto-create if they don't exist.

    \b
    Examples:
      cor new project my-project
      cor new task my-project.implement-feature
      cor new task my-project.bugs.fix-login              # Creates bugs group
      cor new task my-project.experiments.lr.sweep        # Creates nested groups
      cor new note my-project.meeting-notes
      
    \b
    Natural language dates and tags (for tasks/notes):
      cor new task proj.task finish pipeline due tomorrow
      cor new task proj.task fix bug tag urgent ml
      cor new task proj.task code review due next friday tag review

    Note: Use hyphens in names, not dots (e.g., v0-1 not v0.1)
    """
    notes_dir = get_notes_dir()

    # Validate: dots are only for hierarchy, not within names
    parts = name.split(".")
    for part in parts:
        if not part:
            raise ValidationError(
                "Invalid name: empty segment. Use 'project.task' format."
            )
        if "&" in part:
            raise ValidationError(
                "Invalid name: '&' is not allowed in note names."
            )
    if note_type=="project" and "." in name:
        raise ValidationError(
            f"Invalid project name '{name}': dots are reserved for hierarchy. "
            "Use hyphens instead (e.g., 'v0-1' not 'v0.1')."
        )

    # Parse dot notation for task/note: "project.taskname" or "project.group.taskname" or "project.group.smaller_group.task"
    task_name = name
    parent_hierarchy, project = None, None
    
    # Apply focus if set and no project specified
    if note_type in ("task", "note") and "." not in name:
        focused = get_focused_project()
        if focused:
            # Prepend focused project to the name
            name = f"{focused}.{name}"
            parts = name.split(".")
    
    if note_type in ("task", "note") and "." in name:
        # Reuse parts from validation above
        if len(parts) == 2:
            # project.task
            project = parts[0]
            task_name = parts[1]
        elif len(parts) >= 3:
            # project.group.task or project.group.smaller_group.task (or deeper)
            project = parts[0]
            parent_hierarchy = ".".join(parts[:-1])  # Everything except the last part
            task_name = parts[-1]
        else:
            raise ValidationError(
                "Invalid name: use 'project.task', 'project.group.task', or deeper hierarchy format."
            )

    # Build filename
    if note_type == "project":
        filename = f"{name}.md"
    else:
        if parent_hierarchy:
            # Use full parent hierarchy: project.group.smaller_group.task
            filename = f"{parent_hierarchy}.{task_name}.md"
        elif project:
            filename = f"{project}.{task_name}.md"
        else:
            filename = f"{task_name}.md"

    filepath = notes_dir / filename

    if filepath.exists():
        raise AlreadyExistsError(f"File already exists: {filepath}")
    # Note: We don't check archive by default (consistent with edit/mark/move)
    # Archived files don't block creating new files with the same name

    # Read and render template
    template = get_template(note_type)

    # Determine parent for task/note files
    parent = None
    parent_title = None
    if note_type in ("task", "note"):
        if parent_hierarchy:
            # Task/note under a parent hierarchy (group or deeper)
            parent = parent_hierarchy
            # Extract the last component for the title (immediate parent)
            parent_title = format_title(parent_hierarchy.split(".")[-1])
        elif project:
            # Task/note under project: parent is the project
            parent = project
            parent_title = format_title(project)

    content = render_template(template, task_name, parent, parent_title)

    filepath.write_text(content)
    log_info(f"Created {note_type} at {filepath}")

    # Auto-create any missing parents so the parent_link in the child resolves.
    # Tasks get a project at the top and task groups in between; notes get notes
    # at every level (a top-level note like `theme.md` is valid knowledge).
    if note_type in ("task", "note"):
        if parent_hierarchy:
            parent_parts = parent_hierarchy.split(".")
        elif project:
            parent_parts = [project]
        else:
            parent_parts = []

        _ensure_parents_exist(notes_dir, parent_parts, note_type)

        # For tasks, link the new task into the immediate parent's Tasks list.
        # Notes don't maintain a child-index in markdown; the child's `parent:`
        # field and `[< Parent]` link are enough.
        if note_type == "task" and parent_parts:
            immediate_parent_stem = ".".join(parent_parts)
            immediate_parent_path = notes_dir / f"{immediate_parent_stem}.md"
            add_task_to_project(immediate_parent_path, task_name, filepath.stem)
            click.echo(f"Added to {immediate_parent_path}")
    
    if text:
        text = " ".join(text)
    
    text_was_provided = False
    if text and note_type in ("task", "note"):
        text_was_provided = True
        # Parse natural language dates, tags, status, and priority
        cleaned_text, due_date, parsed_tags, parsed_status, parsed_priority = parse_natural_language_text(text)
        
        # Update the description with cleaned text (only if there's actual text left)
        if cleaned_text:
            click.echo("Added description text.")
            with filepath.open("r+") as f:
                content = f.read()
                content = content.replace("## Description\n", f"## Description\n\n{cleaned_text}\n")
                f.seek(0)
                f.write(content)
                f.truncate()
        
        # Add due date if parsed
        if due_date:
            post = frontmatter.load(filepath)
            post['due'] = due_date.strftime(DATE_TIME)
            with open(filepath, 'wb') as f:
                frontmatter.dump(post, f, sort_keys=False)
            click.echo(f"Set due date: {due_date.strftime(DATE_TIME)}")
        
        # Add tags if parsed
        if parsed_tags:
            post = frontmatter.load(filepath)
            existing_tags = post.get("tags", [])
            new_tags = existing_tags + [t for t in parsed_tags if t not in existing_tags]
            post["tags"] = new_tags
            with open(filepath, 'wb') as f:
                frontmatter.dump(post, f, sort_keys=False)
            click.echo(f"Added tags: {', '.join(parsed_tags)}")
        
        # Set status if parsed (only for tasks)
        if parsed_status and note_type == "task":
            post = frontmatter.load(filepath)
            post['status'] = parsed_status
            with open(filepath, 'wb') as f:
                frontmatter.dump(post, f, sort_keys=False)
            click.echo(f"Set status: {parsed_status}")
        
        # Set priority if parsed (only for tasks)
        if parsed_priority and note_type == "task":
            post = frontmatter.load(filepath)
            post['priority'] = parsed_priority
            with open(filepath, 'wb') as f:
                frontmatter.dump(post, f, sort_keys=False)
            click.echo(f"Set priority: {parsed_priority}")
    
    # Open editor only if no text was provided (and --no-edit not set)
    if not text_was_provided and not no_edit:
        open_in_editor(filepath)


@cli.command()
@click.option("--archived", "-a", is_flag=True, is_eager=True, help="Include archived files in search")
@click.argument("name", shell_complete=complete_existing_name)
@require_init
def edit(archived: bool, name: str):
    """Open a file in your editor.

    Supports fuzzy matching - type partial names and select from matches.
    Use -a to include archived files in search.

    \b
    Examples:
      cor edit my-proj          # Fuzzy matches 'my-project'
      cor edit foundation       # Interactive picker if multiple matches
      cor edit -a old-project   # Include archived files
    """
    notes_dir = get_notes_dir()

    # Handle "archive/" prefix if present (from tab completion)
    if name.startswith("archive/"):
        name = name[8:]
        archived = True

    # Get focused project for prioritization
    focused = get_focused_project()
    result = resolve_file_fuzzy(name, include_archived=archived, focused_project=focused)

    if result is None:
        return  # User cancelled

    stem, is_archived = result
    file_path = get_file_path(stem, is_archived)

    h1_before = read_h1(file_path)
    open_in_editor(file_path)
    h1_after = read_h1(file_path)

    if h1_after and h1_after != h1_before:
        old_leaf = stem.split(".")[-1]
        new_leaf = title_to_stem(h1_after)
        if new_leaf and new_leaf != old_leaf and h1_after.lower() != format_title(old_leaf).lower():
            new_stem = ".".join(stem.split(".")[:-1] + [new_leaf]) if "." in stem else new_leaf
            from ..commands.refactor import rename as rename_cmd
            ctx = click.get_current_context()
            ctx.invoke(rename_cmd, old_name=stem, new_name=new_stem, archived=is_archived, dry_run=False)
            click.echo(click.style(f"Renamed \u2192 {new_stem}", fg="cyan"))


@cli.command()
@click.option("--archived", "-a", is_flag=True, help="Include archived files in search")
@click.option("--delete", "-d", "delete_tags", is_flag=True, help="Remove provided tags instead of adding")
@click.argument("name", shell_complete=complete_existing_name)
@click.argument("tags", nargs=-1)
@require_init
def tag(archived: bool, delete_tags: bool, name: str, tags: tuple[str, ...]):
    """Add or remove tags on one or more notes.

    Uses the same fuzzy search as `cor edit`. Supports quoted glob patterns
    for bulk updates.

    Examples:
      cor tag foundation_model ml research
      cor tag -d foundation_model ml
      cor tag "projects_*" research        # bulk: tag all matching files
    """
    if not tags:
        raise ValidationError("Provide at least one tag to add or remove.")

    if name.startswith("archive/"):
        name = name[8:]
        archived = True

    focused = get_focused_project()
    files = resolve_files(name, include_archived=archived, focused_project=focused)

    for stem, is_archived in files:
        _apply_tags(stem, is_archived, tags, delete_tags)


def _apply_tags(stem: str, is_archived: bool, tags: tuple[str, ...], delete_tags: bool):
    """Add or remove `tags` on a single note file."""
    file_path = get_file_path(stem, is_archived)
    post = frontmatter.load(file_path)
    existing = post.get("tags", [])

    if delete_tags:
        new_tags = [t for t in existing if t not in tags]
        if len(new_tags) == len(existing):
            log_info(f"{stem}: no matching tags to remove.")
            return
        summary = f"Removed tags from {stem}: {', '.join(sorted(set(existing) - set(new_tags)))}"
    else:
        to_add = [t for t in tags if t not in existing]
        if not to_add:
            log_info(f"{stem}: tags already up to date.")
            return
        new_tags = existing + to_add
        summary = f"Added tags to {stem}: {', '.join(to_add)}"

    post["tags"] = new_tags
    post["modified"] = datetime.now().strftime(DATE_TIME)
    with open(file_path, "wb") as f:
        frontmatter.dump(post, f, sort_keys=False)

    # Rewrite tag list in flow style: tags: [a, b]
    # Avoid matching YAML frontmatter delimiters (---) by requiring a space after '-'
    text = Path(file_path).read_text()
    pattern = re.compile(r"(^tags:\s*\n(?:\s+-\s*[^\n]+\n)+)", re.MULTILINE)

    def _inline_tags(match):
        lines = match.group(0).splitlines()
        values = [re.sub(r"^\s*-\s*", "", ln).strip() for ln in lines[1:]]
        return f"tags: [{', '.join(values)}]\n"

    new_text = pattern.sub(_inline_tags, text)
    if new_text != text:
        Path(file_path).write_text(new_text)

    log_info(summary)


@cli.command()
@click.option("--archived", "-a", is_flag=True, help="Include archived files in search")
@click.option("--delete", "-d", "delete_due", is_flag=True, help="Remove the due date instead of setting one")
@click.argument("name", shell_complete=complete_task_name)
@click.argument("text", nargs=-1, type=str)
@require_init
def due(archived: bool, delete_due: bool, name: str, text: tuple[str, ...]):
    """Set or remove due dates on one or more tasks using natural language.

    Uses the same fuzzy search as `cor edit`. Supports quoted glob patterns
    for bulk updates (tasks only).

    \b
    Examples:
      cor due task1 tomorrow
      cor due task1 next friday 9am
      cor due task1 in 3 days
      cor due -d task1                    # Remove the due date
      cor due "project1.tasks_*" friday   # Bulk: set due on matching tasks
    """
    if not delete_due and not text:
        raise ValidationError("Provide a date (e.g. 'tomorrow') or use -d to clear the due date.")

    if name.startswith("archive/"):
        name = name[8:]
        archived = True

    focused = get_focused_project()
    from ..utils import is_glob_pattern
    files = resolve_files(
        name,
        include_archived=archived,
        focused_project=focused,
        note_type="task" if is_glob_pattern(name) else None,
    )
    if not files:
        return

    due_date = None
    if not delete_due:
        text_str = " ".join(text)
        _, due_date, _, _, _ = parse_natural_language_text(f"due {text_str}")
        if due_date is None:
            raise ValidationError(f"Could not parse date: '{text_str}'")

    notes_dir = get_notes_dir()
    files_to_sync = []
    for stem, is_archived in files:
        file_path = get_file_path(stem, is_archived)
        post = frontmatter.load(file_path)

        if delete_due:
            if "due" not in post.metadata:
                log_info(f"{stem} has no due date.")
                continue
            old_due = post.metadata.pop("due")
            post["modified"] = datetime.now().strftime(DATE_TIME)
            with open(file_path, "wb") as f:
                frontmatter.dump(post, f, sort_keys=False)
            files_to_sync.append(str(file_path))
            log_info(f"Cleared due date on {stem} (was {old_due}).")
            continue

        old_due = post.metadata.get("due")
        post["due"] = due_date.strftime(DATE_TIME)
        post["modified"] = datetime.now().strftime(DATE_TIME)
        with open(file_path, "wb") as f:
            frontmatter.dump(post, f, sort_keys=False)
        files_to_sync.append(str(file_path))

        new_due = due_date.strftime(DATE_TIME)
        if old_due:
            log_info(f"{stem}: due {old_due} → {new_due}")
        else:
            log_info(f"{stem}: set due {new_due}")

    if files_to_sync:
        runner = MaintenanceRunner(notes_dir=notes_dir)
        runner.sync(files_to_sync)


@cli.command(name="delete")
@click.option("--archived", "-a", is_flag=True, help="Include archived files in search")
@click.argument("name", shell_complete=complete_existing_name)
@require_init
def delete(archived: bool, name: str):
    """Delete one or more notes and update references.

    Supports fuzzy matching, or quoted glob patterns for bulk deletion.

    \b
    Examples:
        cor delete my-proj                  # Fuzzy matches 'my-project'
        cor delete -a old-project           # Include archived files
        cor delete "project1.tasks_*"       # Bulk delete matching files
    """
    notes_dir = get_notes_dir()

    if name.startswith("archive/"):
        name = name[8:]
        archived = True

    focused = get_focused_project()
    files = resolve_files(name, include_archived=archived, focused_project=focused)

    if not files:
        return

    deleted_paths = []
    for stem, is_archived in files:
        file_path = get_file_path(stem, is_archived)
        file_path.unlink()
        deleted_paths.append(str(file_path))
        click.echo(click.style(f"Deleted {stem}.md", fg="red"))

    runner = MaintenanceRunner(notes_dir)
    runner.sync([], deleted=deleted_paths)


@cli.command()
@click.option("--archived", "-a", is_flag=True, is_eager=True, help="Include archived files in search")
@click.option("--status", "-s", "status_option", type=str, help="New status value (alternative positional arg)")
@click.argument("name", shell_complete=complete_task_name)
@click.argument("status", shell_complete=complete_task_status, required=False)
@click.argument("text", nargs=-1, type=str)
@require_init
def mark(archived: bool, status_option: str | None, name: str, status: str | None, text: tuple[str, ...]):
    """Update task or project status.

    Supports fuzzy matching for task/project names and glob patterns for bulk updates.

    You can also set due dates using natural language (e.g., "due tomorrow",
    "due next friday"). Tags can be added with "tag <name>".

    \b
    Task status values:
      todo       Ready to start
      active     Currently working on
      done       Completed
      blocked    Waiting on external dependency
      waiting    Paused, waiting for information (auto-sets due: 1 day)
      dropped    Abandoned/won't do

    \b
    Project status values:
      planning   Not started
      active     In progress
      paused     On hold
      done       Completed (prompts to drop unfinished tasks)

    \b
    Examples:
      cor mark impl active                    # Fuzzy matches 'implement-api'
      cor mark my-project.research done       # Specific task
      cor mark myproject done                 # Mark project done (drops unfinished tasks)
      cor mark -a old-task todo               # Search archived tasks too
      cor mark "project.*" done               # Bulk: mark all project tasks done
      cor mark -s done "project.*"            # Alternative: --status before pattern
      cor mark project.group.* active         # Bulk: mark group tasks active
      cor mark mytask active due tomorrow     # Set status and due date
      cor mark mytask todo due next friday tag urgent
    """
    from ..utils import is_glob_pattern

    notes_dir = get_notes_dir()

    actual_status = status_option or status
    if actual_status is None:
        raise ValidationError(
            "Status is required. Usage: cor mark <task> <status> or cor mark -s <status> <task>"
        )

    all_valid = VALID_TASK_STATUS | VALID_PROJECT_STATUS
    if actual_status not in all_valid:
        raise ValidationError(
            f"Invalid status '{actual_status}'. "
            f"Task: {', '.join(sorted(VALID_TASK_STATUS))}. "
            f"Project: {', '.join(sorted(VALID_PROJECT_STATUS))}"
        )

    if name.startswith("archive/"):
        name = name[8:]
        archived = True

    bulk = is_glob_pattern(name)
    focused = get_focused_project()
    files = resolve_files(
        name,
        include_archived=archived,
        focused_project=focused,
        note_type="task" if bulk else None,
    )
    if not files:
        return

    if bulk and actual_status not in VALID_TASK_STATUS:
        raise ValidationError(
            f"Bulk mark only supports tasks. Invalid task status '{actual_status}'. "
            f"Valid: {', '.join(sorted(VALID_TASK_STATUS))}"
        )

    updated = 0
    errors = 0
    files_to_sync: list[str] = []
    for stem, is_archived in files:
        file_path = get_file_path(stem, is_archived)
        note = parse_metadata(file_path)
        if not note:
            raise NotFoundError(f"Could not parse file: {file_path}")

        if note.note_type == "note":
            raise ValidationError(
                f"'{stem}' is a note. This command only works with tasks and projects."
            )

        if note.note_type == "project":
            _update_project_status(file_path, note, actual_status, notes_dir)
            continue

        if actual_status not in VALID_TASK_STATUS:
            raise ValidationError(
                f"Invalid task status '{actual_status}'. "
                f"Valid: {', '.join(sorted(VALID_TASK_STATUS))}"
            )
        try:
            _update_task_status(
                file_path, note, actual_status, text, notes_dir, display=not bulk
            )
            files_to_sync.append(str(file_path))
            updated += 1
        except ValidationError as e:
            if not bulk:
                raise
            click.secho(f"  Skipped {stem}: {e}", fg="yellow")
            errors += 1

    if bulk:
        if files_to_sync:
            runner = MaintenanceRunner(notes_dir)
            runner.sync(files_to_sync)
        symbol = STATUS_SYMBOLS.get(actual_status, "")
        click.echo(
            f"\n{symbol} Updated {updated} task(s) to {click.style(actual_status, bold=True)}"
        )
        if errors:
            click.echo(f"  ({errors} skipped due to errors)")


def _update_project_status(file_path: Path, note, status: str, notes_dir: Path):
    """Update project status, prompting to drop unfinished tasks when marking done."""
    if status not in VALID_PROJECT_STATUS:
        raise ValidationError(
            f"Invalid project status '{status}'. "
            f"Valid: {', '.join(sorted(VALID_PROJECT_STATUS))}"
        )

    dropped_task_paths: list[str] = []
    if status == "done":
        runner = MaintenanceRunner(notes_dir)
        incomplete = runner.get_incomplete_tasks(note.path.stem)
        if incomplete:
            click.echo(click.style(f"Project has {len(incomplete)} unfinished task(s):", fg="yellow"))
            for t in incomplete:
                click.echo(f"  - {t}")
            if not click.confirm("Drop all unfinished tasks?", default=False):
                raise click.Abort()
            # Drop all incomplete tasks
            for task_filename in incomplete:
                task_path = notes_dir / task_filename
                if not task_path.exists():
                    task_path = notes_dir / "archive" / task_filename
                if task_path.exists():
                    post = frontmatter.load(task_path)
                    post["status"] = "dropped"
                    with open(task_path, "wb") as f:
                        frontmatter.dump(post, f, sort_keys=False)
                    dropped_task_paths.append(str(task_path))

    post = frontmatter.load(file_path)
    if "status" not in post.metadata:
        raise ValidationError("Could not find status field in frontmatter")
    old_status = post.get("status", "none")
    post["status"] = status
    with open(file_path, "wb") as f:
        frontmatter.dump(post, f, sort_keys=False)

    runner = MaintenanceRunner(notes_dir)
    # Pass dropped tasks first so they archive before the project file does;
    # otherwise the project moves to archive/ while children stay in notes/
    # with broken backlinks.
    runner.sync(dropped_task_paths + [str(file_path)])

    color = {"done": "green", "active": "cyan", "paused": "yellow", "planning": "blue"}.get(status, "white")
    click.echo(
        f"{click.style(format_title(note.path.stem), bold=True)}: "
        f"{click.style(old_status, fg='white')} → {click.style(status, fg=color)}"
    )


def _update_task_status(
    file_path: Path, 
    note, 
    status: str, 
    text: tuple[str, ...], 
    notes_dir: Path,
    display: bool = True
):
    """Update the status of a single task file.
    
    Args:
        file_path: Path to the task file
        note: Parsed note object
        status: New status value
        text: Optional text to append (may contain due dates like "due tomorrow")
        notes_dir: Path to notes directory
        display: Whether to display status update
    """
    # Validate: task groups cannot be marked done/dropped if children are incomplete
    if status in ("done", "dropped"):
        runner = MaintenanceRunner(notes_dir)
        task_name = note.path.stem
        incomplete = runner.get_incomplete_tasks(task_name)
        
        if incomplete:
            raise ValidationError(
                f"Cannot mark as {status} - has incomplete subtasks: {', '.join(incomplete)}"
            )

    # Load and update frontmatter
    post = frontmatter.load(file_path)

    if 'status' not in post.metadata:
        raise ValidationError("Could not find status field in frontmatter")

    old_status = post.get('status', 'none')
    post['status'] = status

    # Parse text for due dates, tags, status, and priority
    due_date = None
    tags = []
    text_to_append = None
    priority = None
    
    if text:
        text_str = " ".join(text)
        cleaned_text, due_date, parsed_tags, parsed_status, parsed_priority = parse_natural_language_text(text_str)
        
        # Use any remaining text after parsing
        cleaned_text = cleaned_text.strip()
        if cleaned_text:
            text_to_append = cleaned_text
        
        # Store parsed tags for later use
        tags = parsed_tags
        
        # If a status was parsed from text, it overrides the command-line status
        if parsed_status:
            status = parsed_status
            post['status'] = status
        
        # Store parsed priority for later use
        priority = parsed_priority
    
    # If status is waiting and no due date was specified, add a due date of 1 day
    if status == "waiting" and due_date is None:
        due_date = datetime.now() + timedelta(days=1)
    
    # Apply due date if parsed or auto-set
    if due_date:
        post['due'] = due_date.strftime(DATE_TIME)
    
    # Apply tags if any were parsed
    if tags:
        existing_tags = post.get('tags', []) or []
        if isinstance(existing_tags, str):
            existing_tags = [existing_tags]
        # Add new tags, avoiding duplicates
        for tag in tags:
            if tag not in existing_tags:
                existing_tags.append(tag)
        post['tags'] = existing_tags
    
    # Apply priority if parsed
    if priority:
        post['priority'] = priority

    # Append remaining text if provided
    if text_to_append:
        post.content = post.content.rstrip() + f"\n{text_to_append}"

    with open(file_path, 'wb') as f:
        frontmatter.dump(post, f, sort_keys=False)

    # Run sync for immediate feedback (for single file) or batch (for bulk)
    if display:
        runner = MaintenanceRunner(notes_dir)
        runner.sync([str(file_path)])

        # Status display
        symbol = STATUS_SYMBOLS.get(status, "")
        click.echo(f"{symbol} {note.title}: {old_status} → {click.style(status, bold=True)}")
        
        if due_date:
            click.echo(f"  Set due: {due_date.strftime(DATE_TIME)}")
        if tags:
            click.echo(f"  Added tags: {', '.join(tags)}")
        if priority:
            click.echo(f"  Set priority: {priority}")
        if text_to_append:
            click.echo(f"  Added note: {text_to_append}")


@cli.command()
@click.argument("name", shell_complete=complete_existing_name)
@require_init
def expand(name: str):
    """Expand task checklist into individual subtasks.

    Parses checklist items from a task's description and creates individual
    subtask files. The original task becomes a task group with proper links.

    \b
    Examples:
      cor expand myproject.feature
      cor expand paper.experiments.md
      cor expand -a archived-task           # Include archived files

    \b
    Before (task with checklist):
      ## Description
      - [ ] design-api
      - [ ] implement-backend
      - [ ] write-tests

    \b
    After:
      Creates: myproject.feature.design-api.md
               myproject.feature.implement-backend.md
               myproject.feature.write-tests.md
      Updates: myproject.feature.md with task links
    """
    from ..utils import parse_checklist_items, remove_checklist_items

    notes_dir = get_notes_dir()

    # Remove .md extension if present
    if name.endswith('.md'):
        name = name[:-3]

    # Use fuzzy matching to resolve task name with focus prioritization
    focused = get_focused_project()
    result = resolve_file_fuzzy(name, include_archived=False, focused_project=focused)

    if result is None:
        return  # User cancelled

    stem, is_archived = result
    task_file = get_file_path(stem, is_archived)

    # Parse the task file
    post = frontmatter.load(task_file)

    # Verify it's a task
    if post.get('type') != 'task':
        raise ValidationError(f"File is not a task: {task_file}")

    # Extract checklist items from content
    checklist_items = parse_checklist_items(post.content)

    if not checklist_items:
        raise ValidationError(
            f"No checklist items found in {task_file.name}. "
            "Add unchecked items like '- [ ] subtask-name' to the Description section."
        )

    # Get the task stem (filename without .md)
    task_stem = task_file.stem

    # Determine parent info
    parent = post.get('parent')
    if not parent:
        raise ValidationError(f"Task has no parent field: {task_file}")

    log_info(click.style(f"Expanding {task_stem} into {len(checklist_items)} subtasks...", fg="cyan"))

    # Create subtask files
    template = get_template("task")
    created_files = []

    for task_name, task_status, task_text in checklist_items:
        # Shorten to max 6 words for filename and title
        words = task_name.split()[:6]
        shortened_name = '_'.join(words)
        # Replace characters that can break filenames: / { ( \ ) } , and others
        # Note: periods are preserved (e.g., v1.2.3, config.yaml)
        safe_name = re.sub(r'[,/{}()\\\[\]<>:;\'"?*|]', '_', shortened_name)
        # Truncate to avoid exceeding filesystem filename limits (max 255 chars)
        # Calculate max safe_name length: 255 - len(task_stem) - len('.') - len('.md')
        max_filename_len = 255
        max_safe_name_len = max_filename_len - len(task_stem) - 1 - 3  # -1 for '.', -3 for '.md'
        max_safe_name_len = max(20, max_safe_name_len)  # Ensure at least 20 chars for safe_name
        if len(safe_name) > max_safe_name_len:
            safe_name = safe_name[:max_safe_name_len]
        subtask_filename = f"{task_stem}.{safe_name}.md"
        subtask_path = notes_dir / subtask_filename

        if subtask_path.exists():
            click.echo(f"Warning: {subtask_filename} already exists, skipping")
            continue

        # Use shortened name (max 6 words) as the task title
        short_title = ' '.join(words)

        # Render subtask content with task as parent, using shortened title
        # and passing the full task text as message to include in body
        subtask_content = render_template(
            template,
            safe_name,
            parent=task_stem,
            parent_title=format_title(task_stem.split('.')[-1]),
            message=task_text,
        )

        # Parse the rendered content and set the status from checklist
        subtask_post = frontmatter.loads(subtask_content)
        subtask_post['status'] = task_status
        subtask_post['title'] = short_title
        
        # Write subtask with correct status
        with open(subtask_path, 'wb') as f:
            frontmatter.dump(subtask_post, f, sort_keys=False)
        
        created_files.append((safe_name, subtask_filename, task_status))
        log_verbose(f"  Created {subtask_filename} (status: {task_status})")

    # Remove checklist items from original task content
    new_content = remove_checklist_items(post.content)
    post.content = new_content

    # Write updated task file
    with open(task_file, 'wb') as f:
        frontmatter.dump(post, f, sort_keys=False)

    # Add subtask links to the task file (now acting as group)
    for safe_name, subtask_filename, _ in created_files:
        add_task_to_project(task_file, safe_name, subtask_filename.replace('.md', ''))

    log_info(click.style(f"\nSuccess! Created {len(created_files)} subtasks under {task_stem}", fg="green"))
    for safe_name, filename, status in created_files:
        log_info(f"  - {filename} (status: {status})")


@cli.command()
@click.argument("query")
@require_init
def link(query: str):
    """Print a [Title](stem.md) link for a note. Suitable for piping.

    \b
    Examples:
      cor link myproject                 # [My Project](myproject.md)
      cor link myproject.task1           # [Task 1](myproject.task1.md)
      cor link "task" | pbcopy          # Copy to clipboard
    """
    from ..core.notes import parse_note

    notes_dir = get_notes_dir()
    result = resolve_file_fuzzy(query, include_archived=True)
    if result is None:
        sys.exit(1)

    stem, in_archive = result
    note_file = notes_dir / f"{stem}.md"
    if not note_file.exists():
        note_file = notes_dir / "archive" / f"{stem}.md"

    if not note_file.exists():
        click.echo(f"No note found for '{query}'", err=True)
        sys.exit(1)

    note = parse_note(note_file)
    title = note.title if note and note.title else stem
    click.echo(f"[{title}]({stem}.md)", nl=False)

"""Note and task management commands for Cortex CLI."""

import re
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
from ..core.files import save_note
from ..core.operations import create_entry, relate_entries, resolve_exact, transition_entry
from ..core.storage import atomic_write_text
from ..core.transactions import transactional_command
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
    get_parent_name,
    parse_block,
)
from ..completions import (
    complete_name,
    complete_task_name,
    complete_task_status,
    complete_existing_name,
    complete_predecessor_project,
)
from ..search import resolve_file_fuzzy, get_file_path, resolve_task_fuzzy, resolve_files


@cli.command()
@click.argument("note_type", type=click.Choice(["project", "task", "note"]))
@click.argument("name", shell_complete=complete_name)
@click.argument("text", nargs=-1)
@click.option("--no-edit", is_flag=True, help="Do not open the new file in editor")
@click.option(
    "--continues", "-c", "continues", multiple=True,
    shell_complete=complete_predecessor_project,
    help="Project(s) this one continues. Repeatable. Projects only.",
)
@require_init
@transactional_command
def new(note_type: str, name: str, text: tuple[str, ...], no_edit: bool,
        continues: tuple[str, ...]):
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
    Continuing finished work (instead of resurrecting it from the archive):
      cor new project screening-v2 -c screening-v1
      cor new project merged -c old-a -c old-b
    The predecessor stays archived and stays done; its Goal is copied into the
    new project for context.

    \b
    Natural language dates and tags (for tasks/notes):
      cor new task proj.task finish pipeline due tomorrow
      cor new task proj.task fix bug tag urgent ml
      cor new task proj.task code review due next friday tag review

    Note: Use hyphens in names, not dots (e.g., v0-1 not v0.1)
    """
    notes_dir = get_notes_dir()

    if continues and note_type != "project":
        raise ValidationError(
            f"--continues is only valid for projects, not {note_type}s."
        )

    # Apply focus if set and no project specified
    if note_type in ("task", "note") and "." not in name:
        focused = get_focused_project()
        if focused:
            name = f"{focused}.{name}"
    text_value = " ".join(text).strip()
    text_was_provided = bool(text_value)
    metadata = {}
    section_values = {}
    if text_value:
        cleaned, due, tags, parsed_status, priority = parse_natural_language_text(
            text_value
        )
        if cleaned:
            heading = {"project": "Goal", "task": "Description"}.get(
                note_type, "Summary"
            )
            section_values[heading] = cleaned
        if due:
            metadata["due"] = due.strftime(DATE_TIME)
        if tags:
            metadata["tags"] = tags
        if parsed_status and note_type == "task":
            metadata["status"] = parsed_status
        if priority and note_type in {"task", "project"}:
            metadata["priority"] = priority

    create_entry(
        notes_dir,
        note_type,
        name,
        metadata=metadata,
        section_values=section_values,
        create_parents=True,
    )
    filepath, _ = resolve_exact(notes_dir, name)
    log_info(f"Created {note_type} at {filepath}")

    # Link predecessors and pull their Goal across. Done last so the
    # copied context sits in a file that is otherwise fully written.
    if continues:
        _link_predecessors(notes_dir, filepath, continues)

    # Open editor only if no text was provided (and --no-edit not set)
    if not text_was_provided and not no_edit:
        open_in_editor(filepath)


def _link_predecessors(notes_dir: Path, filepath: Path, continues: tuple[str, ...]):
    """Resolve and attach `continues` predecessors to a freshly created project.

    Shares the `cor rel add --as continues` implementation so both entry points
    write identical state.
    """
    from ..search import resolve_file_fuzzy

    focused = get_focused_project()
    predecessors = []
    for name in continues:
        # Predecessors are normally archived - that is the whole point.
        result = resolve_file_fuzzy(name, include_archived=True, focused_project=focused)
        if result is None:
            return
        predecessors.append(result[0])

    added = relate_entries(
        notes_dir, filepath.stem, "continues", predecessors
    )["targets"]
    if not added:
        return
    for stem in added:
        click.echo(f"Continues: {stem}")


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
    save_note(file_path, post)

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
        atomic_write_text(file_path, new_text)

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
            save_note(file_path, post)
            files_to_sync.append(str(file_path))
            log_info(f"Cleared due date on {stem} (was {old_due}).")
            continue

        old_due = post.metadata.get("due")
        post["due"] = due_date.strftime(DATE_TIME)
        post["modified"] = datetime.now().strftime(DATE_TIME)
        save_note(file_path, post)
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
@transactional_command
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
            _update_project_status(file_path, note, actual_status, text, notes_dir)
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


def _update_project_status(file_path: Path, note, status: str, text: tuple[str, ...], notes_dir: Path):
    """Update project status, prompting to drop unfinished tasks when marking done.

    Trailing text may carry natural-language due date / priority / tags
    (e.g. ``cor mark myproject active due friday``), mirroring the task path.
    """
    if status not in VALID_PROJECT_STATUS:
        raise ValidationError(
            f"Invalid project status '{status}'. "
            f"Valid: {', '.join(sorted(VALID_PROJECT_STATUS))}"
        )

    if status == "done":
        runner = MaintenanceRunner(notes_dir)
        incomplete = runner.get_incomplete_tasks(note.path.stem)
        if incomplete:
            click.echo(click.style(f"Project has {len(incomplete)} unfinished task(s):", fg="yellow"))
            for t in incomplete:
                click.echo(f"  - {t}")
            if not click.confirm("Drop all unfinished tasks?", default=False):
                raise click.Abort()
            for task_filename in sorted(
                incomplete, key=lambda item: item.count("."), reverse=True
            ):
                transition_entry(notes_dir, Path(task_filename).stem, "dropped")

    old_status = note.status or "none"
    metadata = {}

    # Trailing text may carry a due date / priority / tags (status is governed
    # by the command-line argument for projects, so it is not parsed here).
    if text:
        _, due_date, parsed_tags, _, parsed_priority = parse_natural_language_text(" ".join(text))
        if due_date:
            metadata["due"] = due_date.strftime(DATE_TIME)
        if parsed_priority:
            metadata["priority"] = parsed_priority
        if parsed_tags:
            metadata["tags"] = parsed_tags

    transition_entry(notes_dir, note.path.stem, status, metadata=metadata)

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
    old_status = note.status or "none"

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
        
        # Store parsed priority for later use
        priority = parsed_priority
    
    # If status is waiting and no due date was specified, add a due date of 1 day
    if status == "waiting" and due_date is None:
        due_date = datetime.now() + timedelta(days=1)
    
    metadata = {}
    if due_date:
        metadata["due"] = due_date.strftime(DATE_TIME)
    
    # Apply tags if any were parsed
    if tags:
        metadata["tags"] = tags
    
    # Apply priority if parsed
    if priority:
        metadata["priority"] = priority

    transition_entry(
        notes_dir,
        note.path.stem,
        status,
        result=text_to_append,
        metadata=metadata,
        sync=display,
    )

    if display:
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
        save_note(subtask_path, subtask_post)
        
        created_files.append((safe_name, subtask_filename, task_status))
        log_verbose(f"  Created {subtask_filename} (status: {task_status})")

    # Remove checklist items from original task content
    new_content = remove_checklist_items(post.content)
    post.content = new_content

    # Write updated task file
    save_note(task_file, post)

    # Add subtask links to the task file (now acting as group)
    for safe_name, subtask_filename, _ in created_files:
        add_task_to_project(task_file, safe_name, subtask_filename.replace('.md', ''))

    log_info(click.style(f"\nSuccess! Created {len(created_files)} subtasks under {task_stem}", fg="green"))
    for safe_name, filename, status in created_files:
        log_info(f"  - {filename} (status: {status})")


# Words that make a poor last word in a derived task name. Spanish connectors
# are in there too: notes are often bilingual.
_NAME_FILLER = set(
    "a an and as at by for from in into of on or the to with "
    "al con de del el en la las los para por que un una y".split()
)


def _parse_line_range(spec: str) -> tuple[int, int]:
    """Parse a 1-based inclusive ``N`` or ``N-M`` line range."""
    match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", spec)
    if not match:
        raise ValidationError(f"Invalid --lines value '{spec}'. Use 'N' or 'N-M'.")

    start = int(match.group(1))
    end = int(match.group(2) or start)
    if start < 1 or end < start:
        raise ValidationError(f"Invalid line range '{spec}'.")
    return start, end


def _find_block(lines: list[str], block: list[str], hint: int) -> int:
    """Index of ``block`` inside ``lines``, closest to ``hint``; -1 if absent.

    Creating the task can append a Tasks entry to the source note (when the note
    is the new task's parent), so line numbers taken before the creation cannot
    be trusted afterwards - the block is located by content instead.
    """
    size = len(block)
    matches = [i for i in range(len(lines) - size + 1) if lines[i:i + size] == block]
    if not matches:
        return -1
    return min(matches, key=lambda i: abs(i - hint))


def _body_start(lines: list[str]) -> int:
    """First line number (1-based) that is past the frontmatter block."""
    if not lines or lines[0].strip() != "---":
        return 1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return i + 2
    return 1


@cli.command()
@click.option("--lines", "line_range", required=True, metavar="N[-M]",
              help="Line range to extract (1-based, inclusive).")
@click.option("--name", "name", default=None,
              help="Stem for the new task. Dotted names set the parent; "
                   "a bare name becomes a sibling of SOURCE. Derived from the "
                   "text when omitted.")
@click.option("--keep", is_flag=True,
              help="Copy the lines instead of moving them.")
@click.argument("source", shell_complete=complete_existing_name)
@require_init
def extract(line_range: str, name: str | None, keep: bool, source: str):
    """Turn a block of lines in a note into a task of its own.

    The lines move into the new task's Description and are replaced in place by
    a link to it, so the note keeps its structure. Designed to be driven by an
    editor (see the nvim keymap `<leader>cx`), which is why the block is given
    as a line range; the last line printed is the path of the new task.

    \b
    Examples:
      cor extract myproject.meeting-notes --lines 14
      cor extract myproject.meeting-notes --lines 14-17
      cor extract myproject.notes --lines 14 --name fix_login
      cor extract myproject.notes --lines 14 --name myproject.bugs.fix_login
      cor extract myproject.notes --lines 14 --keep     # leave the text behind
    """
    notes_dir = get_notes_dir()

    focused = get_focused_project()
    result = resolve_file_fuzzy(source, include_archived=False, focused_project=focused)
    if result is None:
        return  # User cancelled

    source_stem, is_archived = result
    source_path = get_file_path(source_stem, is_archived)

    start, end = _parse_line_range(line_range)
    lines = source_path.read_text().split("\n")

    if end > len(lines):
        raise ValidationError(
            f"{source_stem}.md has {len(lines)} lines; asked for {start}-{end}."
        )
    if start < _body_start(lines):
        raise ValidationError(
            "That range covers the frontmatter. Select lines from the note body."
        )

    # Shrink the range to its non-blank span, so a selection that overshoots by
    # a blank line does not swallow the paragraph break around it.
    while start <= end and not lines[start - 1].strip():
        start += 1
    while end >= start and not lines[end - 1].strip():
        end -= 1
    if start > end:
        raise ValidationError("The selected lines are empty.")

    block = lines[start - 1:end]
    body, indent, marker = parse_block(block)

    if not name:
        # Same 6-word convention as `cor expand`, minus a trailing filler word
        # ("...redirect on" reads badly as both a stem and a title).
        words = body.split("\n")[0].split()[:6]
        while words and words[-1].lower().strip(",.;:") in _NAME_FILLER:
            words.pop()
        name = title_to_stem(" ".join(words))
        if not name:
            raise ValidationError(
                "Could not derive a task name from those lines; pass --name."
            )

    full_stem = name if "." in name else f"{get_parent_name(source_stem) or source_stem}.{name}"
    target_path = notes_dir / f"{full_stem}.md"
    if target_path.exists():
        raise AlreadyExistsError(f"File already exists: {target_path}")

    # Create through `cor new` so parents are auto-created and the task is
    # indexed in its parent's Tasks section exactly as usual. The stem is always
    # fully qualified here, so focus cannot rewrite it and the path is known.
    ctx = click.get_current_context()
    ctx.invoke(new, note_type="task", name=full_stem, text=(), no_edit=True,
               continues=())

    content = target_path.read_text()
    if "## Description\n" in content:
        content = content.replace("## Description\n", f"## Description\n\n{body}\n", 1)
    else:
        content = content.rstrip("\n") + f"\n\n{body}\n"
    atomic_write_text(target_path, content)

    # Keep the original bullet, but never a checkbox one: MaintenanceRunner
    # treats `- [ ] [Title](stem.md)` as a task entry and sort_tasks_in_parent
    # rewrites the whole span between the first and last such line in a parent
    # file, which would drag surrounding prose along. A plain bullet is inert.
    link_marker = re.sub(r"\[[^\]]\]\s+", "", marker)
    link_line = f"{indent}{link_marker}[{format_title(full_stem.split('.')[-1])}]({full_stem}.md)"

    lines = source_path.read_text().split("\n")
    at = _find_block(lines, block, start - 1)
    if at < 0:
        raise ValidationError(
            f"Created {full_stem}, but those lines are no longer in "
            f"{source_stem}.md; the note was left as it is."
        )

    if keep:
        lines[at + len(block):at + len(block)] = [link_line]
    else:
        lines[at:at + len(block)] = [link_line]
    atomic_write_text(source_path, "\n".join(lines))

    MaintenanceRunner(notes_dir).sync([str(source_path), str(target_path)])

    verb = "Copied" if keep else "Moved"
    log_info(f"{verb} {source_stem}.md lines {start}-{end} into {full_stem}")
    # Last line is the new file, for editors and scripts.
    click.echo(str(target_path))


@cli.command()
@click.argument("query")
@require_init(write=False)
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

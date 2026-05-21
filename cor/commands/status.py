"""Status and reporting commands for Cor CLI."""

from datetime import datetime, date, timedelta

import click

from ..exceptions import NotFoundError, ValidationError
from ..completions import complete_project, complete_existing_name
from ..core.notes import find_notes
from ..schema import STATUS_SYMBOLS
from ..utils import get_notes_dir, format_time_ago, format_due_date, require_init, format_title, get_parent_name
from ..config import get_focused_project

# Shared color mappings for all tree views
TASK_COLORS = {
    "done": "green",
    "active": "cyan",
    "blocked": "red",
    "dropped": "magenta",
    "todo": "white",
    "waiting": "yellow",
}

PROJECT_COLORS = {
    "planning": "blue",
    "active": "green",
    "paused": "yellow",
    "done": "bright_black",
}

# Status sort order for tree display
STATUS_ORDER = {"blocked": 0, "active": 1, "waiting": 2, "todo": 3, "dropped": 4, "done": 5}


def _extract_description(note) -> str | None:
    """Extract text under ## Description section from note content.

    Returns first line or up to 80 chars of description text.
    """
    if not note.content:
        return None

    lines = note.content.split("\n")
    in_description = False
    description_lines = []

    for line in lines:
        if line.startswith("## Description"):
            in_description = True
            continue
        if in_description:
            if line.startswith("##"):  # Next section
                break
            stripped = line.strip()
            if stripped:  # Non-empty line
                description_lines.append(stripped)
                break  # Take only first non-empty line

    if description_lines:
        desc = description_lines[0]
        if len(desc) > 80:
            desc = desc[:77] + "..."
        return desc
    return None


def _format_note_label(count: int) -> str:
    """Return human-friendly note count label."""
    return f"{count} note" if count == 1 else f"{count} notes"


def _build_note_counts(notes: list) -> dict[str, int]:
    """Build mapping of parent stem -> attached note count."""
    counts: dict[str, int] = {}
    for note in notes:
        if note.note_type != "note":
            continue
        parent = get_parent_name(note.path.stem)
        if not parent:
            continue
        counts[parent] = counts.get(parent, 0) + 1
    return counts


def _get_git_stats(notes_dir, weeks: int | None) -> dict | None:
    """Get git commit statistics for the specified time period.
    
    Returns dict with keys: commits, additions, deletions
    Returns None if not a git repository or git command fails.
    """
    import subprocess
    from pathlib import Path
    
    try:
        # Check if we're in a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=notes_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return None
        
        # Build git log command
        git_cmd = ["git", "log", "--numstat", "--pretty=format:COMMIT"]
        
        # Add time filter if specified
        if weeks is not None:
            git_cmd.append(f"--since={weeks} weeks ago")
        
        # Run git log
        result = subprocess.run(
            git_cmd,
            cwd=notes_dir,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            return None
        
        # Parse output
        commits = 0
        additions = 0
        deletions = 0
        
        for line in result.stdout.strip().split('\n'):
            if line == 'COMMIT':
                commits += 1
            elif line.strip() and not line.startswith('COMMIT'):
                # numstat format: additions deletions filename
                parts = line.split('\t')
                if len(parts) >= 2:
                    try:
                        add = int(parts[0]) if parts[0] != '-' else 0
                        delete = int(parts[1]) if parts[1] != '-' else 0
                        additions += add
                        deletions += delete
                    except ValueError:
                        # Skip lines that can't be parsed
                        continue
        
        return {
            'commits': commits,
            'additions': additions,
            'deletions': deletions
        }
    
    except (subprocess.SubprocessError, FileNotFoundError):
        # Git not available or command failed
        return None


def _format_dependency_indicator(note, all_notes: list, verbose: bool = False) -> str:
    """Format dependency indicator for display.

    Args:
        note: Task/project note
        all_notes: All notes for dependency resolution
        verbose: If True, show detailed info

    Returns:
        Formatted dependency string (empty if no dependencies)
    """
    if note.note_type not in ("task", "project") or not note.requires:
        return ""

    from ..dependencies import get_dependency_info

    dep_info = get_dependency_info(note, all_notes)

    if not verbose:
        # Compact mode: just show indicator for unmet requirements
        if dep_info.all_requirements_met:
            return ""  # Don't show if all requirements met
        else:
            # Show arrow with count of unmet requirements
            count = len(dep_info.blocked_by)
            return click.style(f" [→{count}]", fg="yellow", dim=True)
    else:
        # Verbose mode: show details
        lines = []

        if dep_info.requires:
            # Format requirement list with status colors
            req_parts = []
            for req_stem in dep_info.requires:
                if req_stem in dep_info.blocked_by:
                    # Unmet requirement - yellow
                    req_parts.append(click.style(req_stem, fg="yellow"))
                else:
                    # Met requirement - green
                    req_parts.append(click.style(req_stem, fg="green"))
            requires_str = ", ".join(req_parts)
            lines.append(f"→ Requires: {requires_str}")

        if dep_info.blocks:
            # Show what this blocks
            blocks_str = ", ".join([click.style(b, fg="cyan") for b in dep_info.blocks])
            lines.append(f"← Blocks: {blocks_str}")

        if dep_info.missing_requirements:
            # Warn about missing requirements
            missing_str = ", ".join([click.style(m, fg="red") for m in dep_info.missing_requirements])
            lines.append(f"⚠ Missing: {missing_str}")

        return "\n".join(lines) if lines else ""


def show_tree(
    parent_name: str,
    tasks_by_parent: dict,
    prefix: str = "",
    filter_fn=None,
    sort_fn=None,
    render_fn=None,
    show_separators: bool = False,
    separator_transitions: list = None,
    verbose: bool = False,
    all_notes: list | None = None,
    note_counts: dict[str, int] | None = None,
    max_depth: int | None = None,
    current_depth: int = 0,
):
    """
    Unified tree rendering function.

    Args:
        parent_name: The parent node to render children of
        tasks_by_parent: Dict mapping parent names to list of child tasks
        prefix: Current indentation prefix string
        filter_fn: Optional function(task) -> bool to filter which tasks to show
        sort_fn: Optional function(tasks) -> sorted_tasks
        render_fn: Function(task) -> str to render task display (symbol + title)
        show_separators: Whether to show --- separators between status groups
        separator_transitions: List of (from_statuses, to_statuses) tuples for separators
        verbose: Whether to show description text under tasks
        all_notes: List of all notes for dependency resolution (optional)
        note_counts: Optional mapping of parent stem -> number of attached notes
        max_depth: Maximum depth to display (None for unlimited)
        current_depth: Current depth in the tree (used internally for recursion)
    """
    note_counts = note_counts or {}

    if parent_name not in tasks_by_parent:
        return

    tasks = tasks_by_parent[parent_name]

    # Apply filter if provided
    if filter_fn:
        tasks = [t for t in tasks if filter_fn(t)]

    if not tasks:
        return

    # Apply sorting if provided
    if sort_fn:
        tasks = sort_fn(tasks)

    # Default separator transitions
    if separator_transitions is None:
        separator_transitions = [
            (("blocked", "active", "waiting"), ("todo",)),
            (("todo",), ("dropped", "done")),
        ]

    prev_status = None
    for i, task in enumerate(tasks):
        is_last = i == len(tasks) - 1
        branch = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        # Check for separator
        if show_separators and prev_status is not None:
            for from_statuses, to_statuses in separator_transitions:
                if prev_status in from_statuses and task.status in to_statuses:
                    sep_prefix = prefix + "│"
                    sep_line = click.style(f"{sep_prefix}   ---", dim=True)
                    click.echo(sep_line)
                    break

        # Render the task
        if render_fn:
            task_display = render_fn(task)
        else:
            symbol = STATUS_SYMBOLS.get(task.status, "[ ]")
            color = TASK_COLORS.get(task.status, "white")
            task_display = f"{click.style(symbol, fg=color)} {task.title}"

        # Add dependency indicator (compact mode)
        if all_notes:
            dep_indicator = _format_dependency_indicator(task, all_notes, verbose=False)
            task_display += dep_indicator

        # Append note count if this task has attached notes
        note_count = note_counts.get(task.path.stem, 0)
        if note_count:
            note_suffix = _format_note_label(note_count)
            task_display += click.style(f" (and {note_suffix})", dim=True)

        line = f"{prefix}{branch}{task_display}"
        click.echo(line)

        # Show description if verbose
        if verbose:
            desc = _extract_description(task)
            if desc:
                desc_line = f"{child_prefix}    {click.style(desc, dim=True)}"
                click.echo(desc_line)

            # Show due date in verbose mode
            if task.due:
                due_str = format_due_date(task.due)
                due_color = "red" if task.is_overdue else "yellow" if task.is_due_this_week else "white"
                due_line = f"{child_prefix}    {click.style(f'Due: {due_str}', dim=True, fg=due_color)}"
                click.echo(due_line)

            # Show dependency details in verbose mode
            if all_notes:
                dep_details = _format_dependency_indicator(task, all_notes, verbose=True)
                if dep_details:
                    for detail_line in dep_details.split("\n"):
                        styled_line = f"{child_prefix}    {click.style(detail_line, dim=True, fg='yellow')}"
                        click.echo(styled_line)

        # Recurse for children only if within depth limit
        if max_depth is None or current_depth < max_depth:
            show_tree(
                task.path.stem,
                tasks_by_parent,
                child_prefix,
                filter_fn=filter_fn,
                sort_fn=sort_fn,
                render_fn=render_fn,
                show_separators=show_separators,
                separator_transitions=separator_transitions,
                verbose=verbose,
                all_notes=all_notes,
                note_counts=note_counts,
                max_depth=max_depth,
                current_depth=current_depth + 1,
            )

        prev_status = task.status


def _group_by_project(tasks: list) -> dict:
    """Group tasks by their parent project."""
    from collections import OrderedDict
    groups = OrderedDict()
    for task in tasks:
        project = task.parent_project or task.path.stem
        groups.setdefault(project, []).append(task)
    return groups


def _print_section(title: str, tasks: list, color: str, formatter, limit: int, verbose: bool = False) -> bool:
    """Print section with tasks grouped by project. Returns True if printed."""
    if not tasks:
        return False

    heading = click.style(f"\n{title}", fg=color, bold=True)
    click.echo(heading)
    shown = 0
    groups = _group_by_project(tasks)

    for project, project_tasks in groups.items():
        project_display = format_title(project)
        if limit and shown >= limit:
            break
        project_line = f"  {project_display}"
        click.echo(project_line)
        for task in project_tasks:
            if limit and shown >= limit:
                break
            symbol = STATUS_SYMBOLS.get(task.status, "[ ]")
            task_color = TASK_COLORS.get(task.status, "white")
            info = formatter(task)
            styled_symbol = click.style(symbol, fg=task_color)
            line = f"  └── {styled_symbol} {task.title}{info}"
            click.echo(line)

            # Show description if verbose
            if verbose:
                desc = _extract_description(task)
                if desc:
                    desc_line = f"      {click.style(desc, dim=True)}"
                    click.echo(desc_line)

            shown += 1

    remaining = len(tasks) - shown
    if remaining > 0:
        click.echo(click.style(f"  ... and {remaining} more", dim=True))
    return True



def _matches_tag(note, tag, project_tags) -> bool:
    """Return True if note matches provided tag filter.

    Tag matches if:
    - tag is None (no filtering)
    - tag equals parent project name
    - tag exists in note.tags
    - tag exists in parent project's tags (propagated)
    """
    if not tag:
        return True
    parent = note.parent_project
    if parent and tag == parent:
        return True
    if tag in (note.tags or []):
        return True
    if parent and tag in project_tags.get(parent, set()):
        return True
    return False

@click.command(short_help="Prioritized daily view of tasks")
@click.option("--limit", "-l", default=3, help="Max items per section (default: 3)")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all items (no limit)")
@click.option("--verbose", "-v", is_flag=True, help="Show task descriptions")
@click.argument("tag", required=False, shell_complete=complete_project)
@require_init
def daily(limit: int, show_all: bool, verbose: bool, tag: str | None):
    """Show what needs attention today.

    \b
    Prioritized daily view:
    - Overdue items (fix first)
    - Stale waiting items (follow up)
    - Due today
    - In progress (continue)
    - High priority ready
    - Suggested next (from active projects)
    
    Use -v/--verbose to show task descriptions.

    If a tag is provided (e.g., `cor daily foundation_model`), only tasks
    matching the tag are shown. A task matches when:
    - The task's parent project name equals the tag, or
    - The task has the tag in its metadata, or
    - Its parent project has the tag (project tags propagate to children).
    
    If a project is focused (via `cor focus`), automatically filters to that project.
    """
    notes_dir = get_notes_dir()

    # Apply focus if set and no explicit tag provided
    focused = get_focused_project()
    if tag is None and focused:
        tag = focused

    notes = find_notes(notes_dir)
    now = datetime.now()
    today = now.date()

    if show_all:
        limit = 0  # 0 means no limit

    # Build set of active project names
    active_projects = {
        n.path.stem for n in notes if n.note_type == "project" and n.status == "active"
    }

    # Build project -> tags mapping for propagation
    project_tags: dict[str, set[str]] = {
        n.path.stem: set(n.tags or []) for n in notes if n.note_type == "project"
    }

    # Show focus indicator if filtering by focused project
    if focused and tag == focused:
        click.echo(click.style(f"[Focusing on: {focused}]\n", fg="cyan", bold=True))

    # Pre-filter tasks once (respects tag propagation)
    tasks = [n for n in notes if n.note_type == "task" and _matches_tag(n, tag, project_tags)]

    # Track shown items to avoid duplicates
    shown_paths = set()
    sections_printed = False

    # Helper to format overdue time
    def format_overdue(n):
        days = n.days_overdue
        if days == 0:
            return " (due today)"
        elif days == 1:
            return " (1d overdue)"
        else:
            return f" ({days}d overdue)"

    # 1. Overdue (tasks only, sorted by due date)
    overdue = [n for n in tasks if n.is_overdue]
    overdue.sort(key=lambda n: n.due)
    if _print_section(
        "Overdue",
        overdue,
        "red",
        format_overdue,
        limit,
        verbose=verbose,
    ):
        sections_printed = True
        shown_paths.update(n.path for n in overdue[:limit or len(overdue)])

    # 2. Waiting stale (waiting status + stale)
    waiting_stale = [n for n in tasks if n.status == "waiting" and n.is_stale and n.path not in shown_paths]
    waiting_stale.sort(key=lambda n: n.modified or datetime.min)
    if _print_section(
        "Waiting (stale)",
        waiting_stale,
        "yellow",
        lambda n: f" ({format_time_ago(n.modified)} since update)" if n.modified else "",
        limit,
        verbose=verbose,
    ):
        sections_printed = True
        shown_paths.update(n.path for n in waiting_stale[:limit or len(waiting_stale)])

    # 3. Due today
    due_today = [
        n
        for n in tasks
        if n.due and n.due == today and n.status not in ("done", "dropped") and n.path not in shown_paths
    ]
    priority_order = {"high": 0, "medium": 1, "low": 2}
    due_today.sort(key=lambda n: priority_order.get(n.priority, 3))
    if _print_section(
        "Due Today",
        due_today,
        "cyan",
        lambda n: f" [{n.priority}]" if n.priority else "",
        limit,
        verbose=verbose,
    ):
        sections_printed = True
        shown_paths.update(n.path for n in due_today[:limit or len(due_today)])

    # 4. In Progress (active tasks)
    in_progress = [n for n in tasks if n.status == "active" and n.path not in shown_paths]
    in_progress.sort(key=lambda n: n.modified or datetime.min)
    if _print_section(
        "In Progress",
        in_progress,
        "blue",
        lambda n: f" ({format_time_ago(n.modified)})" if n.modified else "",
        limit,
        verbose=verbose,
    ):
        sections_printed = True
        shown_paths.update(n.path for n in in_progress[:limit or len(in_progress)])

    # 5. High Priority Ready (high priority + todo)
    high_priority = [
        n
        for n in tasks
        if n.priority == "high" and n.status == "todo" and n.path not in shown_paths
    ]
    high_priority.sort(key=lambda n: n.created or datetime.min)
    if _print_section(
        "High Priority",
        high_priority,
        "magenta",
        lambda n: "",
        limit,
        verbose=verbose,
    ):
        sections_printed = True
        shown_paths.update(n.path for n in high_priority[:limit or len(high_priority)])

    # 6. Suggested Next (todo tasks in active projects)
    suggested = [
        n
        for n in tasks
        if n.status == "todo" and n.path not in shown_paths and n.parent_project in active_projects
    ]
    suggested.sort(key=lambda n: n.modified or datetime.min)
    if _print_section(
        "Suggested Next",
        suggested,
        "white",
        lambda n: "",
        limit,
        verbose=verbose,
    ):
        sections_printed = True

    if not sections_printed:
        click.echo(click.style("\nAll clear! Nothing urgent for today.", fg="green"))

    click.echo()


def _get_project_last_activity(project_name: str, all_notes: list) -> datetime | None:
    """Get the most recent modification date from a project's tasks/notes."""
    most_recent = None
    
    for note in all_notes:
        # Check if this note belongs to the project
        if note.parent_project == project_name and note.note_type in ("task", "note"):
            if note.modified:
                if most_recent is None or note.modified > most_recent:
                    most_recent = note.modified
    
    return most_recent


@click.command(short_help="List projects with status and activity")
@click.option("--all", "-a", "show_all", is_flag=True, help="Include archived/done projects")
@require_init
def projects(show_all: bool):
    """List active projects with status and age.

    \b
    Details:
    - Sorted by last activity (most recent task/note update)
    - Color-coded: planning (blue), active (green), paused (yellow)
    - Stale projects (>14 days) highlighted in red
    - Use -a to include done/archived projects
    """
    notes_dir = get_notes_dir()

    notes = find_notes(notes_dir)

    # Include archived projects if -a flag is set
    if show_all:
        archive_dir = notes_dir / "archive"
        if archive_dir.exists():
            notes.extend(find_notes(archive_dir))

    # Filter to projects only (exclude done unless show_all)
    if show_all:
        projects_list = [n for n in notes if n.note_type == "project"]
    else:
        projects_list = [
            n for n in notes
            if n.note_type == "project" and n.status != "done"
        ]

    if not projects_list:
        if show_all:
            click.echo("No projects found.")
        else:
            click.echo("No active projects found.")
        return

    # Sort by last activity from children (oldest first = needs attention)
    # Fall back to project's own modified date if no children
    # Handle None dates by placing them at the beginning (needs attention)
    def get_sort_key(proj):
        project_name = proj.path.stem
        last_activity = _get_project_last_activity(project_name, notes)
        return last_activity or proj.modified or datetime.min
    
    projects_list.sort(key=get_sort_key)

    header = click.style("\nProjects:", bold=True)
    click.echo(header)
    click.echo()

    for p in projects_list:
        # Use last activity from children, fall back to project modified date
        project_name = p.path.stem
        last_activity = _get_project_last_activity(project_name, notes)
        ref_date = last_activity or p.modified or p.created
        
        # Calculate time since modified
        age_str = format_time_ago(ref_date) if ref_date else "unknown"
        days_ago = (datetime.now() - ref_date).days if ref_date else 0

        # Determine color (stale overrides status color)
        is_stale = (days_ago > 7) and (p.status == "active")
        if is_stale:
            color = "red"
            status_display = f"{p.status or 'no status'} (stale)"
        else:
            color = PROJECT_COLORS.get(p.status, "white")
            status_display = p.status or "no status"

        # Format output
        status_styled = click.style(f"[{status_display}]", fg=color)
        age_styled = click.style(age_str, fg="red" if is_stale else "cyan")

        display_title = format_title(p.title)
        line = f"  {status_styled} {display_title} - {age_styled}"
        click.echo(line)

    click.echo()


@click.command()
@click.option("--weeks", "-w", default=1, help="Number of weeks to look back (default: 1)")
@click.option("--verbose", "-v", is_flag=True, help="Show task descriptions")
@click.argument("tag", required=False, shell_complete=complete_project)
@require_init
def weekly(weeks: int, verbose: bool, tag: str | None):
    """Show weekly summary in tree format.

    Without tag filter: shows completed tasks grouped by project.
    With tag filter: shows full project tree (except TODO), ordered by status.

    Optionally filter by tag (project name or metadata tag):
      cor weekly foundation_model
    
    A task matches when:
    - The task's parent project name equals the tag, or
    - The task has the tag in its metadata, or
    - Its parent project has the tag (project tags propagate to children).

    Use -v/--verbose to show task descriptions.
    
    If a project is focused (via `cor focus`), automatically filters to that project.
    """
    notes_dir = get_notes_dir()

    # Apply focus if set and no explicit tag provided
    focused = get_focused_project()
    if tag is None and focused:
        tag = focused

    # Calculate date range
    now = datetime.now()
    start_date = now - timedelta(days=7 * weeks)

    # Week display range
    week_start = start_date.date()
    week_end = now.date()

    # Get all notes including archived
    notes = find_notes(notes_dir)
    archive_dir = notes_dir / "archive"
    if archive_dir.exists():
        notes.extend(find_notes(archive_dir))

    note_counts = _build_note_counts(notes)

    # Build project -> tags mapping for propagation
    project_tags: dict[str, set[str]] = {
        n.path.stem: set(n.tags or []) for n in notes if n.note_type == "project"
    }

    # Filter by tags if specified
    project_filter = {tag} if tag else None

    # Build project data and task hierarchy
    # project_name -> {done: int, active: int, total: int, high_priority: [tasks]}
    project_data = {}
    completed_this_week = set()  # paths of tasks completed this week
    tasks_by_parent = {}  # parent_name -> [tasks]
    all_tasks = {}  # path.stem -> note

    for n in notes:
        if n.note_type != "task":
            continue

        # Skip tasks that don't match the tag filter
        if not _matches_tag(n, tag, project_tags):
            continue

        project = n.parent_project or n.path.stem
        all_tasks[n.path.stem] = n

        # Track project stats
        if project not in project_data:
            project_data[project] = {"done": 0, "active": 0, "total": 0, "high_priority": []}

        project_data[project]["total"] += 1

        if n.status in ("done", "dropped"):
            project_data[project]["done"] += 1
            # Check if completed this period
            if n.modified and n.modified >= start_date:
                completed_this_week.add(n.path.stem)
        elif n.status in ("active", "waiting"):
            project_data[project]["active"] += 1

        # Track high priority tasks (not done/dropped)
        if n.priority == "high" and n.status not in ("done", "dropped"):
            project_data[project]["high_priority"].append(n)

        # Build parent -> children mapping
        parts = n.path.stem.split(".")
        if len(parts) >= 2:
            parent = ".".join(parts[:-1])
            tasks_by_parent.setdefault(parent, []).append(n)

    # Find projects with completed tasks this week
    projects_with_completed = set()
    for stem in completed_this_week:
        parts = stem.split(".")
        if parts:
            projects_with_completed.add(parts[0])

    # Filter projects_with_completed by tag if specified
    if tag:
        # Keep only projects that match the tag filter
        filtered_projects = set()
        for project_name in projects_with_completed:
            # Check if any task in this project matches the tag filter
            for task_stem in completed_this_week:
                if task_stem.startswith(project_name + ".") or task_stem == project_name:
                    task = all_tasks.get(task_stem)
                    if task and _matches_tag(task, tag, project_tags):
                        filtered_projects.add(project_name)
                        break
        projects_with_completed = filtered_projects

    # Print week header
    if weeks == 1:
        month_name = week_start.strftime("%b")
        header = f"Week of {month_name} {week_start.day}-{week_end.day}"
    else:
        header = f"Past {weeks} weeks ({week_start} to {week_end})"

    if tag:
        header += f" [{format_title(tag)}]"

    def emit(line: str = ""):
        click.echo(line)

    # Show focus indicator if filtering by focused project
    if focused and tag == focused:
        emit(click.style(f"[Focusing on: {focused}]", fg="cyan", bold=True))

    emit(click.style(f"\n═══ {header} ═══", bold=True))

    # === Project-specific view: show full tree (except TODO) ===
    if project_filter:
        # Find all tasks that match the tag and their parent projects
        matching_tasks = [t for t in all_tasks.values() if _matches_tag(t, tag, project_tags)]
        matching_projects = set()
        for t in matching_tasks:
            if t.parent_project:
                matching_projects.add(t.parent_project)
        # Also include projects that match the tag directly (by name or by having the tag)
        for proj_name, proj_tags in project_tags.items():
            if tag == proj_name or tag in proj_tags:
                matching_projects.add(proj_name)

        for project_name in sorted(matching_projects):
            display_project = format_title(project_name)

            # Only count matching tasks for this project/tag
            matching_for_project = [t for t in matching_tasks if t.parent_project == project_name or (not t.parent_project and t.path.stem.startswith(project_name))]
            done_count = sum(1 for t in matching_for_project if t.status in ("done", "dropped"))
            active_count = sum(1 for t in matching_for_project if t.status in ("active", "waiting"))
            total_count = len(matching_for_project)

            # Project header with counters
            is_project_done = done_count == total_count and total_count > 0
            project_color = "green" if is_project_done else "cyan"

            counter_str = f"({done_count} done, {active_count} active, {total_count} total)"
            emit(click.style(display_project, fg=project_color, bold=True) + " " + click.style(counter_str, dim=True))

            # Sort: done/dropped first, then active/waiting, then blocked
            # Filter out TODO tasks
            weekly_status_order = {"done": 0, "dropped": 1, "active": 2, "waiting": 3, "blocked": 4, "todo": 99}

            def weekly_sort(tasks):
                return sorted(tasks, key=lambda t: (weekly_status_order.get(t.status, 5), t.path.stem))

            def weekly_filter(task):
                return task.status != "todo"

            def weekly_render(task):
                symbol = STATUS_SYMBOLS.get(task.status, "[ ]")
                color = TASK_COLORS.get(task.status, "white")
                priority_mark = click.style(" [HIGH]", fg="magenta", bold=True) if task.priority == "high" else ""
                return f"{click.style(symbol, fg=color)} {task.title}{priority_mark}"

            show_tree(
                project_name,
                tasks_by_parent,
                filter_fn=weekly_filter,
                sort_fn=weekly_sort,
                render_fn=weekly_render,
                verbose=verbose,
                all_notes=notes,
                note_counts=note_counts,
            )

            # List high priority tasks separately if any
            high_priority = [t for t in matching_for_project if t.priority == "high" and t.status not in ("done", "dropped")]
            if high_priority:
                emit()
                emit(click.style("  ⚡ High Priority:", fg="magenta", bold=True))
                for hp in high_priority:
                    status_str = click.style(f"[{hp.status}]", fg=TASK_COLORS.get(hp.status, "white"))
                    emit(f"    • {hp.title} {status_str}")

        emit()
        return

    # === Default view: show completed tasks this week ===
    if not projects_with_completed:
        emit(click.style("\nNo completed tasks this week.", dim=True))
        emit()
        return

    emit(click.style(f"\nCompleted: {len(completed_this_week)} tasks\n", fg="green", bold=True))

    def has_completed_descendant(parent_name: str) -> bool:
        """Check if parent has any completed descendants this week."""
        if parent_name in completed_this_week:
            return True
        for child in tasks_by_parent.get(parent_name, []):
            if has_completed_descendant(child.path.stem):
                return True
        return False

    def weekly_filter_completed(task):
        return has_completed_descendant(task.path.stem)

    def weekly_render_completed(task):
        if task.path.stem in completed_this_week:
            symbol = "[x]" if task.status == "done" else "[~]"
            color = TASK_COLORS.get(task.status, "white")
            return f"{click.style(symbol, fg=color)} {task.title}"
        else:
            # Group header (not completed, but has completed children)
            return click.style(task.title, dim=True)

    # Print each project
    for project_name in sorted(projects_with_completed):
        stats = project_data.get(project_name, {"done": 0, "active": 0, "total": 0, "high_priority": []})
        done_count = stats["done"]
        active_count = stats["active"]
        total_count = stats["total"]

        # Project is "done" if all tasks are done
        is_project_done = done_count == total_count and total_count > 0
        project_color = "green" if is_project_done else "cyan"

        # Project header with counters
        display_project = format_title(project_name)
        if is_project_done:
            suffix = click.style(" (done)", fg="green")
        else:
            counter_str = f"({done_count} done, {active_count} active, {total_count} total)"
            suffix = " " + click.style(counter_str, dim=True)

        emit(click.style(display_project, fg=project_color, bold=True) + suffix)

        show_tree(
            project_name,
            tasks_by_parent,
            filter_fn=weekly_filter_completed,
            render_fn=weekly_render_completed,
            verbose=verbose,
            all_notes=notes,
            note_counts=note_counts,
        )
        emit()


@click.command(short_help="Show a project's or group's task tree")
@click.option("--verbose", "-v", is_flag=True, help="Show task descriptions")
@click.option("--depth", "-d", type=int, default=None, help="Maximum depth to display (default: unlimited)")
@click.option("--sort", "-s", type=click.Choice(["status", "alphabetical"]), default="status",
              help="Sort tasks by status (default) or alphabetically")
@click.option("--interactive", "-i", is_flag=True, help="Interactive mode (vim keys to navigate and edit)")
@click.argument("focus", shell_complete=complete_existing_name)
@require_init
def tree(verbose: bool, depth: int | None, sort: str, interactive: bool, focus: str):
    """Show task tree for a project or task group.

    Displays tasks in a tree view with [x] or [ ] indicating status.
    Can focus on a project or a specific task group for detailed views.

    \b
    Interactive mode (-i/--interactive):
      j/k        Navigate up/down
      x          Mark as done
      o          Mark as blocked  
      .          Mark as active
      /          Mark as waiting
      ~          Mark as dropped
      backspace  Mark as todo
      e          Edit in $EDITOR
      q          Quit
      ?          Toggle help

    \b
    Examples:
      cor tree myproject                   # Show full project tree
      cor tree myproject.group             # Focus on a specific group
      cor tree myproject -v                # Show descriptions
      cor tree myproject --depth 2         # Limit to 2 levels
      cor tree myproject -s alphabetical   # Sort tasks alphabetically
      cor tree myproject -i                # Interactive mode
    """
    notes_dir = get_notes_dir()
    
    # Launch interactive mode if requested
    if interactive:
        from ..tui.tree_app import ProjectTreeApp
        app = ProjectTreeApp(focus, notes_dir, verbose=verbose, sort=sort)
        app.run()
        return

    # Try to find the focus file (project, task group, or task)
    focus_path = notes_dir / f"{focus}.md"
    archive_path = notes_dir / "archive" / f"{focus}.md"
    
    is_archived = False
    if focus_path.exists():
        pass  # Found in main notes dir
    elif archive_path.exists():
        focus_path = archive_path
        is_archived = True
    else:
        raise NotFoundError(f"Not found: {focus}")

    # Include both active notes and archived ones
    notes = find_notes(notes_dir)
    archive_dir = notes_dir / "archive"
    if archive_dir.exists():
        notes.extend(find_notes(archive_dir))

    note_counts = _build_note_counts(notes)

    # Find the focus note
    focus_note = None
    for note in notes:
        if note.path.stem == focus:
            focus_note = note
            break

    if not focus_note:
        raise ValidationError(f"Could not parse: {focus}")

    # Determine if we're focusing on a project or a task/group
    is_project = focus_note.note_type == "project"
    
    # Get the base project name (first part of the path)
    base_project = focus.split(".")[0]

    # Build parent -> tasks mapping (include all descendants of the focus node)
    tasks_by_parent = {}
    for note in notes:
        if note.note_type == "task":
            parts = note.path.stem.split(".")
            if len(parts) >= 2:
                # For project focus: include all tasks under this project
                # For group focus: include tasks that are descendants of this focus path
                if is_project:
                    if parts[0] == focus:
                        parent = ".".join(parts[:-1])
                        tasks_by_parent.setdefault(parent, []).append(note)
                else:
                    # Group focus: include tasks where the focus is a prefix
                    note_prefix = ".".join(parts[:-1])
                    if note.path.stem.startswith(focus + ".") or note.path.stem == focus:
                        parent = ".".join(parts[:-1])
                        tasks_by_parent.setdefault(parent, []).append(note)

    # Print header
    if is_project:
        color = PROJECT_COLORS.get(focus_note.status)
        click.echo(click.style(f"\n{focus_note.title}", fg=color, bold=True))
        status_line = f"({focus_note.status or 'no status'})"
        note_count = note_counts.get(focus, 0)
        if note_count:
            status_line += f" (and {_format_note_label(note_count)})"
        click.echo(click.style(status_line, dim=True))
    else:
        # Task or group header
        color = TASK_COLORS.get(focus_note.status, "white")
        symbol = STATUS_SYMBOLS.get(focus_note.status, "[ ]")
        click.echo(click.style(f"\n{symbol} {focus_note.title}", fg=color, bold=True))
        status_line = f"({focus_note.status or 'no status'})"
        note_count = note_counts.get(focus, 0)
        if note_count:
            status_line += f" (and {_format_note_label(note_count)})"
        click.echo(click.style(status_line, dim=True))

    # Show dependencies if any
    if focus_note.requires:
        dep_details = _format_dependency_indicator(focus_note, notes, verbose=True)
        if dep_details:
            for detail_line in dep_details.split("\n"):
                click.echo(click.style(f"  {detail_line}", dim=True, fg='yellow'))

    if focus not in tasks_by_parent:
        suffix = f" (but {_format_note_label(note_count)})" if note_count else ""
        click.echo(f"  No subtasks found{suffix}.")
    else:
        # Sort function based on user preference
        def sort_tasks(tasks):
            if sort == "alphabetical":
                # Sort alphabetically by task name (path stem)
                return sorted(tasks, key=lambda t: t.path.stem.lower())
            else:
                # Default: sort by status order, then by due date, then by name
                def sort_key(t):
                    status_order = STATUS_ORDER.get(t.status, 3)
                    # For todo and active tasks, prioritize those with due dates
                    if t.status in ('todo', 'active'):
                        # Tasks with due dates come first, sorted by due date
                        # Tasks without due dates come after, sorted by name
                        has_due = t.due is not None
                        due_value = t.due if has_due else date.max
                        return (status_order, not has_due, due_value, t.path.stem)
                    else:
                        # For other statuses, use original sorting
                        return (status_order, True, date.max, t.path.stem)
                return sorted(tasks, key=sort_key)

        show_tree(
            focus,
            tasks_by_parent,
            sort_fn=sort_tasks,
            show_separators=True,
            verbose=verbose,
            all_notes=notes,
            note_counts=note_counts,
            max_depth=depth,
        )


@click.command(short_help="Vault statistics and overview")
@click.option("--weeks", "-w", default=None, type=int, help="Number of weeks to look back (default: all time)")
@require_init
def status(weeks: int | None):
    """Vault status report.

    \b
    Shows:
    - Number of projects and tasks by status
    - Total lines of content
    - Activity over specified time period
    - Git commit activity and line changes

    \b
    Examples:
      cor status              # All time statistics
      cor status -w 1         # Last week
      cor status -w 4         # Last 4 weeks
    """
    import subprocess
    
    notes_dir = get_notes_dir()

    now = datetime.now()
    start_date = datetime.min if weeks is None else now - timedelta(days=7 * weeks)

    # Get all notes including archived
    notes = find_notes(notes_dir)
    archive_dir = notes_dir / "archive"
    if archive_dir.exists():
        notes.extend(find_notes(archive_dir))

    # Categorize by type and status
    projects = []
    tasks_by_status = {
        "todo": [],
        "active": [],
        "done": [],
        "blocked": [],
        "waiting": [],
        "dropped": [],
    }
    notes_count = 0
    total_lines = 0
    modified_count = 0

    for n in notes:
        # Skip special files
        if n.note_type == "backlog":
            continue

        # Count content lines (non-empty lines in content)
        if n.content:
            content_lines = [line for line in n.content.split("\n") if line.strip()]
            total_lines += len(content_lines)

        # Count modifications in period
        if n.modified and n.modified >= start_date:
            modified_count += 1

        # Categorize by type
        if n.note_type == "project":
            projects.append(n)
        elif n.note_type == "task":
            status_key = n.status or "todo"
            if status_key in tasks_by_status:
                tasks_by_status[status_key].append(n)
        elif n.note_type == "note":
            notes_count += 1

    # Print status report
    period_str = "All Time" if weeks is None else (f"Last Week" if weeks == 1 else f"Last {weeks} Weeks")
    click.echo(click.style(f"\n═══ Vault Status ({period_str}) ═══\n", bold=True))

    # Projects
    click.echo(click.style("Projects: ", bold=True) + click.style(str(len(projects)), fg='cyan', bold=True))
    
    # Break down projects by status
    project_by_status = {}
    for p in projects:
        status_key = p.status or "planning"
        project_by_status[status_key] = project_by_status.get(status_key, 0) + 1
    
    for status_key in ["planning", "active", "paused", "done"]:
        count = project_by_status.get(status_key, 0)
        if count > 0:
            color = PROJECT_COLORS.get(status_key, "white")
            click.echo(f"    {status_key}: {click.style(str(count), fg=color)}")
    click.echo()

    # Tasks
    total_tasks = sum(len(tasks) for tasks in tasks_by_status.values())
    click.echo(click.style("Tasks: ", bold=True) + click.style(str(total_tasks), fg='cyan', bold=True))
    
    for status_key in ["todo", "active", "waiting", "blocked", "done", "dropped"]:
        count = len(tasks_by_status[status_key])
        if count > 0:
            color = TASK_COLORS.get(status_key, "white")
            symbol = STATUS_SYMBOLS.get(status_key, "")
            click.echo(f"    {symbol} {status_key}: {click.style(str(count), fg=color)}")
    click.echo()

    # Notes
    click.echo(click.style("Notes", bold=True))
    click.echo(f"  Total: {click.style(str(notes_count), fg='cyan', bold=True)}")
    click.echo()

    # Content statistics
    click.echo(click.style("Total lines: ", bold=True) + f"{click.style(str(total_lines), fg='cyan', bold=True)}")
    if weeks is not None:
        click.echo(f"  Modified in period: {click.style(str(modified_count), fg='yellow', bold=True)}")
    click.echo()

    # Git activity statistics
    git_stats = _get_git_stats(notes_dir, weeks)
    if git_stats:
        click.echo(click.style("Git Activity", bold=True))
        click.echo(f"  Commits: {click.style(str(git_stats['commits']), fg='cyan', bold=True)}")
        if git_stats['commits'] > 0:
            click.echo(f"  Lines added: {click.style(f'+{git_stats["additions"]}', fg='green')}")
            click.echo(f"  Lines deleted: {click.style(f'-{git_stats["deletions"]}', fg='red')}")
            net_change = git_stats['additions'] - git_stats['deletions']
            net_color = 'green' if net_change > 0 else ('red' if net_change < 0 else 'white')
            net_sign = '+' if net_change > 0 else ''
            click.echo(f"  Net change: {click.style(f'{net_sign}{net_change}', fg=net_color)}")
            
            # Activity rate
            if weeks is not None and weeks > 0:
                commits_per_day = git_stats['commits'] / (7 * weeks)
                click.echo(f"  Activity: {click.style(f'{commits_per_day:.1f}', fg='cyan')} commits/day")
        click.echo()

    click.echo()

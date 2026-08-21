"""Interactive processing commands for Cor CLI (process).
"""

import re
import shutil

import click

from ..exceptions import NotFoundError
from ..utils import (
    get_notes_dir,
    get_projects,
    get_template,
    render_template,
    format_title,
    add_task_to_project,
    require_init,
    log_info,
    log_verbose,
    log_debug,
)

from ..completions import complete_project
from ..search.fuzzy import fuzzy_match

@click.command(short_help="Interactive backlog processing")
@require_init
def process():
    """Interactive prompt to file backlog items into projects.

    \b
    Reads backlog.md, shows each item, and prompts to:
    - Move to a project as a task
    - Keep in backlog
    - Delete

    \b
    Items are processed one by one with keyboard shortcuts.
    """
    notes_dir = get_notes_dir()

    backlog_path = notes_dir / "backlog.md"
    if not backlog_path.exists():
        raise NotFoundError("No backlog.md found. Run 'cor init' first.")

    # Parse backlog items (lines starting with - in ## Inbox section)
    content = backlog_path.read_text()
    lines = content.split("\n")

    inbox_items = []
    in_inbox = False

    for i, line in enumerate(lines):
        if line.strip() == "## Inbox":
            in_inbox = True
            continue
        if in_inbox:
            # Stop at next section
            if line.startswith("## "):
                break
            # Capture non-empty list items
            if line.strip().startswith("- ") and len(line.strip()) > 2:
                item_text = line.strip()[2:].strip()
                if item_text:
                    inbox_items.append((i, item_text))

    if not inbox_items:
        log_info(click.style("Backlog is empty. Nothing to process!", fg="green"))
        return

    # Get available projects
    projects = get_projects()

    log_info(click.style(f"\nProcessing {len(inbox_items)} backlog items...\n", bold=True))
    log_verbose("Commands: [m]ove, [c]reate project, [k]eep, [d]elete, [q]uit\n")

    items_to_remove = []  # Line indices to remove
    items_to_keep = []    # Items to keep

    for line_idx, item_text in inbox_items:
        click.echo(click.style(f"  → {item_text}", fg="cyan"))

        while True:
            choice = click.prompt(
                "  Action",
                type=click.Choice(["m", "c", "k", "d", "q"]),
                show_choices=True,
                default="k"
            )

            if choice == "q":
                click.echo("\nQuitting. Keeping remaining items in backlog.")
                # Keep all unprocessed items
                remaining_indices = [idx for idx, _ in inbox_items if idx >= line_idx]
                for idx in remaining_indices:
                    if idx not in items_to_remove:
                        original_item = next((t for i, t in inbox_items if i == idx), None)
                        if original_item:
                            items_to_keep.append(original_item)
                break

            elif choice == "k":
                log_verbose(click.style("  Keeping in backlog.", dim=True))
                items_to_keep.append(item_text)
                items_to_remove.append(line_idx)
                break

            elif choice == "d":
                log_verbose(click.style("  Deleted.", fg="red", dim=True))
                items_to_remove.append(line_idx)
                break

            elif choice == "m":
                # Select a parent (project or project.group), creating groups if needed, then create task
                # Query parent
                parent_query = click.prompt("  Parent (project or project.group)", default="").strip()
                parent_stem = None

                if parent_query:
                    # Try exact parent
                    candidate_path = notes_dir / f"{parent_query}.md"
                    if candidate_path.exists():
                        parent_stem = parent_query
                    else:
                        # Fuzzy select among existing projects and direct groups
                        all_stems = [s for s in get_projects()]
                        for p in get_projects():
                            for gp in (notes_dir.glob(f"{p}.*.md")):
                                parts = gp.stem.split(".")
                                if len(parts) == 2:
                                    all_stems.append(gp.stem)

                        candidates = [(s, False) for s in sorted(set(all_stems))]
                        matches = fuzzy_match(parent_query, candidates, limit=10, score_cutoff=40)
                        options = [f"{stem}  [{score}%]" for stem, _, score in matches]
                        options.append("[Cancel]")
                        if matches:
                            from simple_term_menu import TerminalMenu

                            menu = TerminalMenu(
                                options,
                                title="  Select parent (arrows, Enter)",
                                menu_cursor_style=("fg_cyan", "bold"),
                                menu_highlight_style=("bg_cyan", "fg_black"),
                            )
                            sel = menu.show()
                            if sel is not None and sel != len(options) - 1:
                                parent_stem = matches[sel][0]
                else:
                    # No query: list top projects
                    if not projects:
                        click.echo(click.style("  No projects found.", fg="red"))
                        continue
                    options = [p for p in projects[:10]] + ["[Cancel]"]
                    from simple_term_menu import TerminalMenu

                    menu = TerminalMenu(
                        options,
                        title="  Select project (arrows, Enter)",
                        menu_cursor_style=("fg_cyan", "bold"),
                        menu_highlight_style=("bg_cyan", "fg_black"),
                    )
                    sel = menu.show()
                    if sel is not None and sel != len(options) - 1:
                        parent_stem = options[sel]

                if not parent_stem:
                    click.echo(click.style("  No parent selected.", fg="yellow"))
                    continue

                # Ensure parent hierarchy exists (create any missing groups)
                parent_parts = parent_stem.split(".")
                for i in range(1, len(parent_parts)):
                    group_stem = ".".join(parent_parts[:i+1])
                    group_path = notes_dir / f"{group_stem}.md"
                    archive_dir = notes_dir / "archive"
                    archived_group_path = archive_dir / f"{group_stem}.md"

                    if archived_group_path.exists() and not group_path.exists():
                        shutil.move(str(archived_group_path), group_path)

                    if not group_path.exists():
                        group_template = get_template("task")
                        group_parent = ".".join(parent_parts[:i]) if i > 1 else parent_parts[0]
                        group_name = parent_parts[i]
                        group_content = render_template(group_template, group_name, group_parent, format_title(group_parent.split(".")[-1]))
                        group_path.write_text(group_content)
                        add_task_to_project(notes_dir / f"{group_parent}.md", group_name, group_stem)

                # Create the task under immediate parent
                task_name = re.sub(r'[^a-zA-Z0-9_-]', '_', item_text.lower())
                task_name = re.sub(r'_+', '_', task_name).strip('_')[:50]
                task_name = click.prompt("  Task name", default=task_name)

                task_filename = f"{parent_stem}.{task_name}"
                filepath = notes_dir / f"{task_filename}.md"
                if filepath.exists():
                    click.echo(click.style(f"  Task already exists: {filepath}", fg="red"))
                    continue

                template = get_template("task")
                parent_title = format_title(parent_stem.split(".")[-1])
                task_content = render_template(template, task_name, parent_stem, parent_title)
                task_content = task_content.replace("## Description\n", f"## Description\n{item_text}\n")
                filepath.write_text(task_content)
                add_task_to_project(notes_dir / f"{parent_stem}.md", task_name, task_filename)
                log_info(click.style(f"  Created {filepath} and added to {parent_stem}.md", fg="green"))

                items_to_remove.append(line_idx)
                break

            elif choice == "c":
                # Create a new project and file this item as its first task
                new_proj_raw = click.prompt("  New project name", default=re.sub(r'[^a-zA-Z0-9_-]', '_', item_text.lower()))
                new_project = re.sub(r'_+', '_', new_proj_raw).strip('_')[:50]

                project_path = notes_dir / f"{new_project}.md"
                if project_path.exists():
                    click.echo(click.style("  Project already exists.", fg="yellow"))
                else:
                    proj_template = get_template("project")
                    proj_content = render_template(proj_template, new_project)
                    project_path.write_text(proj_content)
                    log_info(click.style(f"  Created project {project_path}", fg="green"))

                # Create task under the new project
                task_name = re.sub(r'[^a-zA-Z0-9_-]', '_', item_text.lower())
                task_name = re.sub(r'_+', '_', task_name).strip('_')[:50]
                task_name = click.prompt("  Task name", default=task_name)

                task_filename = f"{new_project}.{task_name}"
                filepath = notes_dir / f"{task_filename}.md"
                if filepath.exists():
                    click.echo(click.style(f"  Task already exists: {filepath}", fg="red"))
                    continue

                template = get_template("task")
                task_content = render_template(template, task_name, new_project, format_title(new_project))
                task_content = task_content.replace("## Description\n", f"## Description\n{item_text}\n")
                filepath.write_text(task_content)
                add_task_to_project(project_path, task_name, task_filename)
                log_info(click.style(f"  Created {filepath} and added to {project_path}", fg="green"))

                # Update local projects list
                projects = get_projects()
                items_to_remove.append(line_idx)
                break


        if choice == "q":
            break

    # Rebuild backlog with remaining items
    new_lines = []
    for i, line in enumerate(lines):
        if i in items_to_remove:
            continue
        new_lines.append(line)

    # Find inbox section and add kept items back
    new_content = "\n".join(new_lines)

    # Ensure inbox section has kept items
    if items_to_keep:
        # Re-parse to find inbox section
        new_lines = new_content.split("\n")
        final_lines = []
        inbox_found = False

        for i, line in enumerate(new_lines):
            final_lines.append(line)
            if line.strip() == "## Inbox":
                inbox_found = True
                # Add kept items after section header
                for item in items_to_keep:
                    final_lines.append(f"- {item}")

        if not inbox_found:
            # Add inbox section at end
            final_lines.append("## Inbox")
            for item in items_to_keep:
                final_lines.append(f"- {item}")

        new_content = "\n".join(final_lines)

    # Ensure file ends properly
    if not new_content.endswith("\n"):
        new_content += "\n"

    backlog_path.write_text(new_content)
    log_info(click.style("\nBacklog updated.", fg="green"))

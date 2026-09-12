"""File refactoring commands for Cor CLI (rename, group)."""

import re
import shutil
from pathlib import Path

import click

from ..exceptions import ValidationError, NotFoundError, AlreadyExistsError
from ..completions import complete_existing_name, complete_group_project, complete_project_tasks, complete_new_parent
from ..core.notes import parse_note
from ..utils import (
    get_notes_dir,
    get_template,
    render_template,
    format_title,
    add_task_to_project,
    require_init,
    log_info,
    log_verbose,
    is_glob_pattern,
    expand_glob_pattern,
)
from ..config import get_focused_project
from ..core.refactor import move_entry
from ..core.operations import create_entry, resolve_exact
from ..core.transactions import VaultTransaction


@click.command(short_help="Rename projects/tasks; supports parent shortcuts")
@click.option("--archived", "-a", is_flag=True, is_eager=True, help="Include archived files in search")
@click.option("--dry-run", "-n", is_flag=True, help="Preview changes without applying them")
@click.argument("old_name", shell_complete=complete_existing_name)
@click.argument("new_name", shell_complete=complete_new_parent)
@require_init
def rename(archived: bool, dry_run: bool, old_name: str, new_name: str):
    """Rename projects/tasks and update all related links.

    Supports fuzzy matching for old_name and glob patterns for bulk operations.

    \b
    Single file (fuzzy matching):
      cor rename old-task new-task
      cor rename p1.task1 p2           -> p2.task1 (shortcut)
      cor rename p1.task1 p2.group     -> p2.group.task1 (shortcut)

    \b
    Bulk operations (glob patterns):
      cor rename "project.old-*.md" "project.new-*.md"
      cor rename "p1.*" "p2.*"         # Move all p1 tasks to p2

    \b
    Notes:
    - Creates the target group if it does not exist.
    - Updates parent/backlinks and all file references.
    - Use -a to include archived files.
    - Use -n/--dry-run to preview bulk changes.
    """
    from ..search import resolve_file_fuzzy, get_file_path

    notes_dir = get_notes_dir()
    archive_dir = notes_dir / "archive"

    # Handle "archive/" prefix if present (from tab completion)
    if old_name.startswith("archive/"):
        old_name = old_name[8:]
        archived = True

    # Check if this is a bulk operation (glob pattern in old_name)
    if is_glob_pattern(old_name):
        _rename_bulk(old_name, new_name, notes_dir, archived, dry_run)
        return

    # Use fuzzy matching to resolve old_name with focus prioritization
    focused = get_focused_project()
    result = resolve_file_fuzzy(old_name, include_archived=archived, focused_project=focused)

    if result is None:
        return  # User cancelled

    old_name, in_archive = result
    main_file = get_file_path(old_name, in_archive)

    # Determine types and resolve shortcut behavior
    old_parts = old_name.split(".")
    new_parts = new_name.split(".")

    # Parse note to know if we're renaming a project or a task (group included)
    note = parse_note(main_file)

    if "&" in new_name:
        raise ValidationError(
            "Invalid name: '&' is not allowed in note names."
        )

    # Project rename keeps validation: new_name must not contain dots
    if note.note_type == "project" and "." in new_name:
        raise ValidationError(
            f"Invalid project name '{new_name}': dots are reserved for hierarchy. "
            "Use hyphens instead (e.g., 'v0-1' not 'v0.1')."
        )

    # Determine target directory early (needed for checking if target exists)
    target_dir = archive_dir if in_archive else notes_dir
    
    # Resolve shortcut for tasks and notes: keep leaf name, change parent hierarchy
    # Special case: when moving within same project, only apply shortcut if target exists
    resolved_new_name = new_name
    if note.note_type in ("task", "note") and len(old_parts) >= 2:
        leaf = old_parts[-1]
        old_project = old_parts[0]
        
        if len(new_parts) == 1:
            # cor rename p1.task -> p2  => p2.task.md if p2 exists, otherwise p2.md
            # Check if target project exists before applying shortcut
            target_check = target_dir / f"{new_parts[0]}.md"
            if target_check.exists():
                resolved_new_name = f"{new_parts[0]}.{leaf}"
            else:
                # Target project doesn't exist, use new_name as-is for full rename
                resolved_new_name = new_name
        elif len(new_parts) >= 2:
            # cor rename p1.task1 -> p2.group  => p2.group.task1
            # cor rename p1.task1 -> p2.group.subgroup  => p2.group.subgroup.task1
            # Works for any depth hierarchy
            new_project = new_parts[0]
            new_parent = ".".join(new_parts)  # Full parent hierarchy
            
            if old_project == new_project:
                # Moving within same project: only apply shortcut if target exists
                target_check = target_dir / f"{new_parent}.md"
                if target_check.exists():
                    resolved_new_name = f"{new_parent}.{leaf}"
                else:
                    # Target doesn't exist, use new_name as-is for full rename
                    resolved_new_name = new_name
            else:
                # Moving to different project: always apply shortcut (create group if needed)
                resolved_new_name = f"{new_parent}.{leaf}"


    if dry_run:
        click.echo(f"Would move {old_name} → {resolved_new_name}")
        return

    with VaultTransaction(notes_dir):
        destination_parent = resolved_new_name.rpartition(".")[0]
        if note.note_type in {"task", "note"} and destination_parent:
            try:
                resolve_exact(notes_dir, destination_parent)
            except NotFoundError:
                create_entry(notes_dir, "task", destination_parent)
        outcome = move_entry(notes_dir, old_name, resolved_new_name)
    click.echo(f"Moved {len(outcome['moved'])} file(s): {old_name} → {resolved_new_name}")


@click.command(short_help="Create a group and move tasks under it")
@click.argument("group", shell_complete=complete_group_project)
@click.argument("tasks", nargs=-1, shell_complete=complete_project_tasks)
@require_init
def group(group: str, tasks: tuple):
    """Group existing tasks under a new or existing group.

    \b
    Examples:
      cor group myproj.refactor task1 task2
      cor group myproj.v2 feature1 feature2

    \b
    Notes:
    - Creates the group file if it does not exist.
    - Updates parent/backlinks and links accordingly.
    """
    notes_dir = get_notes_dir()

    # Parse group argument
    if "." not in group:
        raise ValidationError(
            "Group must be in format 'project.groupname'. Example: cor group myproject.refactor task1 task2"
        )

    parts = group.split(".")
    if len(parts) != 2:
        raise ValidationError(
            "Group must have exactly one dot: 'project.groupname'"
        )

    project, group_name = parts

    # Validate project exists
    project_path = notes_dir / f"{project}.md"
    if not project_path.exists():
        raise NotFoundError(f"Project not found: {project}.md")

    # Validate at least one task provided
    if not tasks:
        raise ValidationError("At least one task must be specified")

    # Validate all tasks exist
    files_to_rename: list[tuple] = []
    for task in tasks:
        task_path = notes_dir / f"{project}.{task}.md"
        if not task_path.exists():
            raise NotFoundError(f"Task not found: {project}.{task}.md")

        new_task_path = notes_dir / f"{project}.{group_name}.{task}.md"
        if new_task_path.exists():
            raise AlreadyExistsError(f"Target already exists: {new_task_path}")

        files_to_rename.append((task_path, new_task_path))

    # Check group file doesn't exist
    group_path = notes_dir / f"{group}.md"
    if group_path.exists():
        raise AlreadyExistsError(f"Group already exists: {group}.md")

    # Collect all files that need link updates
    files_to_update_links: list = []
    archive_dir = notes_dir / "archive"
    for search_dir in [notes_dir, archive_dir] if archive_dir.exists() else [notes_dir]:
        for md_file in search_dir.glob("*.md"):
            files_to_update_links.append(md_file)

    # Create group file from task template
    log_info(click.style("Creating group:", fg="cyan"))

    project_note = parse_note(project_path)
    project_title = project_note.title if project_note else format_title(project)

    template = get_template("task")
    content = render_template(template, group_name, parent=project, parent_title=project_title)
    group_path.write_text(content)
    log_info(f"  Created {group_path}")

    # Add group to project's Tasks section
    add_task_to_project(project_path, group_name, group)

    # Rename task files
    log_info(click.style("\nMoving tasks:", fg="cyan"))
    for old_path, new_path in files_to_rename:
        shutil.move(str(old_path), str(new_path))
        log_info(f"  {old_path.name} → {new_path.name}")

    # Update links in all files
    log_info(click.style("\nUpdating links:", fg="cyan"))
    for file_path in files_to_update_links:
        # Skip if file was renamed
        if not file_path.exists():
            for old_path, new_path in files_to_rename:
                if old_path == file_path:
                    file_path = new_path
                    break

        if not file_path.exists():
            continue

        content = file_path.read_text()
        original_content = content
        updates = []

        for old_path, new_path in files_to_rename:
            old_stem = old_path.stem
            new_stem = new_path.stem

            # Update links: [Title](filename.md) → [Title](new_filename.md)
            pattern = rf'\[([^\]]+)\]\({re.escape(old_stem)}\.md\)'
            if re.search(pattern, content):
                content = re.sub(pattern, rf'[\1]({new_stem}.md)', content)
                updates.append(f"({old_stem}.md) → ({new_stem}.md)")

            # Update archive links
            pattern = rf'\[([^\]]+)\]\(archive/{re.escape(old_stem)}\.md\)'
            if re.search(pattern, content):
                content = re.sub(pattern, rf'[\1](archive/{new_stem}.md)', content)
                updates.append(f"(archive/{old_stem}.md) → (archive/{new_stem}.md)")

        if content != original_content:
            file_path.write_text(content)
            log_info(f"  {file_path}: {', '.join(set(updates))}")

    # Update parent links in renamed files to point to group instead of project
    log_info(click.style("\nUpdating parent links:", fg="cyan"))

    group_note = parse_note(group_path)
    group_title = group_note.title if group_note else format_title(group_name)

    for old_path, new_path in files_to_rename:
        content = new_path.read_text()
        original_content = content

        # Update parent in frontmatter
        content = re.sub(
            rf'^parent: {re.escape(project)}$',
            f'parent: {group}',
            content,
            flags=re.MULTILINE
        )

        # Update reverse link: [< Project Title](project.md) → [< Group Title](group.md)
        content = re.sub(
            rf'\[< [^\]]+\]\({re.escape(project)}\.md\)',
            f'[< {group_title}]({group}.md)',
            content
        )

        if content != original_content:
            new_path.write_text(content)
            log_info(f"  Updated parent in {new_path.name}")

    # Add tasks to group's Tasks section
    log_info(click.style("\nAdding tasks to group:", fg="cyan"))
    for _, new_path in files_to_rename:
        task_name = new_path.stem.split(".")[-1]  # Get last part
        add_task_to_project(group_path, task_name, new_path.stem)
    log_info(f"  Added {len(files_to_rename)} task(s) to {group}.md")
    # Remove old task entries from project (they're now under the group)
    # Note: links were already updated to new paths, so we match on new_stem
    log_info(click.style("\nCleaning up project:", fg="cyan"))
    project_content = project_path.read_text()
    original_project_content = project_content

    for _, new_path in files_to_rename:
        new_stem = new_path.stem
        # Remove task entries (links already point to new paths after update step)
        project_content = re.sub(
            rf'^- \[[^\]]*\] \[[^\]]+\]\({re.escape(new_stem)}\.md\)\n',
            '',
            project_content,
            flags=re.MULTILINE
        )

    if project_content != original_project_content:
        project_path.write_text(project_content)
        log_info(f"  Removed old task entries from {project}.md")

    log_info(click.style("\nDone! Changes are staged for commit.", fg="green"))

# --- Bulk rename utilities ---

def _rename_bulk(
    source_pattern: str,
    target_pattern: str,
    notes_dir: Path,
    include_archive: bool,
    dry_run: bool
):
    """Perform bulk rename operation using glob patterns.
    
    Args:
        source_pattern: Glob pattern to match source files
        target_pattern: Target pattern with matching wildcards
        notes_dir: Path to notes directory
        include_archive: Whether to include archived files
        dry_run: If True, only preview changes
    """
    from fnmatch import fnmatch
    
    # Normalize patterns
    src_pattern = source_pattern
    tgt_pattern = target_pattern
    
    if not src_pattern.endswith('.md'):
        src_pattern = f"{src_pattern}.md"
    if not tgt_pattern.endswith('.md'):
        tgt_pattern = f"{tgt_pattern}.md"
    
    # Remove .md for wildcard analysis
    src_stem_pattern = src_pattern[:-3]
    tgt_stem_pattern = tgt_pattern[:-3]
    
    # Count wildcards in both patterns
    src_wildcards = src_stem_pattern.count('*')
    tgt_wildcards = tgt_stem_pattern.count('*')
    
    if src_wildcards == 0:
        raise ValidationError(
            f"Source pattern '{source_pattern}' contains no wildcards. "
            "Use glob patterns (*, ?, []) for bulk rename."
        )
    
    if src_wildcards != tgt_wildcards:
        raise ValidationError(
            f"Wildcard mismatch: source has {src_wildcards} *, "
            f"target has {tgt_wildcards} *. "
            f"Patterns must have matching wildcards for bulk rename."
        )
    
    # Find all matching source files
    archive_dir = notes_dir / "archive"
    source_paths = expand_glob_pattern(source_pattern, notes_dir, include_archive)
    
    if not source_paths:
        raise NotFoundError(f"No files match pattern: {source_pattern}")
    
    # Compute renames
    renames: list[tuple[Path, Path]] = []
    errors: list[str] = []
    
    for src_path in source_paths:
        src_stem = src_path.stem
        
        # Verify match against pattern
        if not fnmatch(src_stem, src_stem_pattern):
            continue
        
        # Compute target stem by extracting wildcard values and reconstructing
        tgt_stem = _compute_target_stem(src_stem, src_stem_pattern, tgt_stem_pattern)
        
        if tgt_stem is None:
            errors.append(f"Could not compute target for: {src_stem}")
            continue
        
        tgt_path = src_path.parent / f"{tgt_stem}.md"
        
        # Check for conflicts
        if tgt_path.exists() and tgt_path != src_path:
            errors.append(f"Target exists: {tgt_path.name}")
            continue
        
        renames.append((src_path, tgt_path))
    
    if not renames:
        raise ValidationError("No valid renames could be computed.")
    
    # Display preview
    click.echo(f"Bulk rename: {src_pattern} → {tgt_pattern}")
    click.echo()
    
    for src_path, tgt_path in renames:
        src_display = src_path.name
        if src_path.parent == archive_dir:
            src_display = f"archive/{src_display}"
        tgt_display = tgt_path.name
        if tgt_path.parent == archive_dir:
            tgt_display = f"archive/{tgt_display}"
        click.echo(f"  {src_display} → {tgt_display}")
    
    if errors:
        click.echo(f"\nWarnings ({len(errors)}):")
        for error in errors[:5]:
            click.echo(f"  - {error}")
        if len(errors) > 5:
            click.echo(f"  ... and {len(errors) - 5} more")
    
    if dry_run:
        click.echo("\nDry run - no changes made.")
        return
    
    # Confirm if more than 3 files (auto-continue in non-interactive mode)
    if len(renames) > 3:
        import sys
        if sys.stdin.isatty():
            if not click.confirm("\nApply these changes?"):
                click.echo("Cancelled.")
                return
        else:
            click.echo("\nNon-interactive mode: proceeding with rename.")
    
    # Execute renames
    click.echo("\nRenaming...")
    
    # Perform the actual file moves
    files_to_rename: list[tuple[Path, Path]] = []
    for src_path, tgt_path in renames:
        if src_path != tgt_path:
            shutil.move(str(src_path), str(tgt_path))
            files_to_rename.append((src_path, tgt_path))
            log_verbose(f"  {src_path.name} → {tgt_path.name}")
    
    if not files_to_rename:
        click.echo("No files were renamed.")
        return
    
    click.echo(f"Renamed {len(files_to_rename)} file(s).")
    
    # Update links using maintenance infrastructure
    from ..sync import MaintenanceRunner
    runner = MaintenanceRunner(notes_dir)
    
    # Prepare list of renames for handle_renamed_files (relative paths)
    renamed_list = []
    for old_path, new_path in files_to_rename:
        try:
            old_rel = old_path.relative_to(notes_dir)
        except ValueError:
            old_rel = old_path.relative_to(notes_dir.parent)
        
        try:
            new_rel = new_path.relative_to(notes_dir)
        except ValueError:
            new_rel = new_path.relative_to(notes_dir.parent)
        
        renamed_list.append((str(old_rel), str(new_rel)))
    
    if renamed_list:
        click.echo("\nUpdating links...")
        updated, link_errors = runner.handle_renamed_files(renamed_list)
        
        if link_errors:
            for error in link_errors:
                click.secho(f"  Warning: {error}", fg="yellow")
        
        if updated:
            click.echo(f"Updated {len(updated)} file(s).")
    
    # Update links in all files for additional references
    _update_links_for_renames(files_to_rename, notes_dir)
    
    # Update titles in renamed files
    _update_titles_for_renames(files_to_rename)
    
    click.echo(click.style("\nDone!", fg="green"))


def _compute_target_stem(src_stem: str, src_pattern: str, tgt_pattern: str) -> str | None:
    """Compute target stem from source stem and patterns.
    
    Args:
        src_stem: Source file stem (e.g., "project.old-task1")
        src_pattern: Source pattern with wildcards (e.g., "project.old-*")
        tgt_pattern: Target pattern with wildcards (e.g., "project.new-*")
        
    Returns:
        Target stem or None if computation fails
    """
    # Split patterns by wildcards
    src_parts = src_pattern.split('*')
    tgt_parts = tgt_pattern.split('*')
    
    # Extract wildcard values from source stem
    values = []
    remaining = src_stem
    
    for i, part in enumerate(src_parts):
        if part:
            if i == 0:
                # First part - must match prefix
                if not remaining.startswith(part):
                    return None
                remaining = remaining[len(part):]
            else:
                # Find this part in remaining string
                idx = remaining.find(part)
                if idx < 0:
                    return None
                # Everything before this part is a wildcard value
                values.append(remaining[:idx])
                remaining = remaining[idx + len(part):]
        elif i > 0:
            # Empty part with previous part means consecutive * or * at position
            # We'll handle this by capturing up to next non-empty part
            pass
    
    # Handle trailing wildcard - remaining is the last value
    if src_parts[-1] == '' or len(values) < src_pattern.count('*'):
        if remaining:
            values.append(remaining)
    
    # Ensure we have the right number of values
    expected = src_pattern.count('*')
    if len(values) != expected:
        # Try alternative extraction for edge cases
        values = _extract_wildcard_values(src_stem, src_pattern)
        if len(values) != expected:
            return None
    
    # Build target stem
    tgt_stem = ""
    value_idx = 0
    for i, part in enumerate(tgt_parts):
        tgt_stem += part
        if i < len(tgt_parts) - 1 and value_idx < len(values):
            tgt_stem += values[value_idx]
            value_idx += 1
    
    return tgt_stem


def _extract_wildcard_values(src_stem: str, src_pattern: str) -> list[str]:
    """Extract wildcard values from source stem matching pattern.
    
    Uses a more robust algorithm that handles edge cases.
    """
    values = []
    pattern_parts = src_pattern.split('*')
    
    # Remove empty parts for processing
    non_empty_parts = [p for p in pattern_parts if p]
    
    if not non_empty_parts:
        # Pattern is just "*" - entire stem is the value
        return [src_stem]
    
    remaining = src_stem
    
    for i, part in enumerate(pattern_parts):
        if not part:
            continue
            
        idx = remaining.find(part)
        if idx < 0:
            break
            
        # If this isn't the first non-empty part, capture what came before
        if i > 0 and pattern_parts[i-1] == '':
            values.append(remaining[:idx])
        
        remaining = remaining[idx + len(part):]
    
    # Handle trailing wildcard
    if pattern_parts[-1] == '' and remaining:
        values.append(remaining)
    
    return values


def _update_links_for_renames(renames: list[tuple[Path, Path]], notes_dir: Path):
    """Update links in all files that reference renamed files."""
    archive_dir = notes_dir / "archive"
    
    # Collect all files that might have links
    files_to_check: list[Path] = []
    for search_dir in [notes_dir, archive_dir] if archive_dir.exists() else [notes_dir]:
        for md_file in search_dir.glob("*.md"):
            files_to_check.append(md_file)
    
    # Update each file
    for file_path in files_to_check:
        content = file_path.read_text()
        original_content = content
        has_changes = False
        
        for old_path, new_path in renames:
            old_stem = old_path.stem
            new_stem = new_path.stem
            
            # Get new title for backlinks
            new_parts = new_stem.split(".")
            new_base = new_parts[-1] if new_parts else new_stem
            new_title = format_title(new_base)
            
            # Update backlinks
            backlink_pattern = rf'\[<\s+[^\]]*\]\({re.escape(old_stem)}\.md\)'
            if re.search(backlink_pattern, content):
                content = re.sub(backlink_pattern, rf'[< {new_title}]({new_stem}.md)', content)
                has_changes = True

            # Update archive backlinks
            archive_backlink_pattern = rf'\[<\s+[^\]]*\]\(\.\./{re.escape(old_stem)}\.md\)'
            if re.search(archive_backlink_pattern, content):
                content = re.sub(archive_backlink_pattern, rf'[< {new_title}](../{new_stem}.md)', content)
                has_changes = True

            # Update regular links (not backlinks)
            link_pattern = rf'\[(?<!\<\s)([^\]]+)\]\({re.escape(old_stem)}\.md\)'
            if re.search(link_pattern, content):
                content = re.sub(link_pattern, rf'[\1]({new_stem}.md)', content)
                has_changes = True

            # Update archive links
            archive_link_pattern = rf'\[(?<!\<\s)([^\]]+)\]\(archive/{re.escape(old_stem)}\.md\)'
            if re.search(archive_link_pattern, content):
                content = re.sub(archive_link_pattern, rf'[\1](archive/{new_stem}.md)', content)
                has_changes = True
        
        if has_changes:
            file_path.write_text(content)


def _update_titles_for_renames(renames: list[tuple[Path, Path]]):
    """Update H1 titles in renamed files."""
    for old_path, new_path in renames:
        if not new_path.exists():
            continue
        
        # Get base name for title
        is_project = "." not in new_path.stem
        if is_project:
            base_name = new_path.stem
        else:
            parts = new_path.stem.split(".")
            base_name = parts[-1]
        
        new_title = format_title(base_name)
        
        # Update title in file
        content = new_path.read_text()
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            if line.startswith('# ') and i > 0:
                # Check if we're past frontmatter
                in_frontmatter = False
                for j in range(i):
                    if lines[j].strip() == '---':
                        in_frontmatter = not in_frontmatter
                
                if not in_frontmatter:
                    lines[i] = f"# {new_title}"
                    break
        
        new_content = '\n'.join(lines)
        if new_content != content:
            new_path.write_text(new_content)

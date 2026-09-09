"""Maintenance operations for Cor notes.

This module contains file manipulation operations that can be run:
- Automatically via pre-commit hook
- Manually via `cor maintenance sync` command
"""

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter

from ..schema import VALID_PRIORITY, VALID_PROJECT_STATUS, VALID_TASK_STATUS, STATUS_SYMBOLS
from ..core.links import LinkPatterns
from ..core.archive import ArchiveManager
from ..core.files import FileIterator, load_note, save_note
from ..core.storage import atomic_write_text
from ..dependencies import RELATION_FIELDS


@dataclass
class SyncResult:
    """Result of a sync operation for dry-run and reporting."""
    archived: list[tuple[str, str]] = field(default_factory=list)  # (old_path, new_path)
    unarchived: list[tuple[str, str]] = field(default_factory=list)
    group_status_updated: list[str] = field(default_factory=list)
    project_status_updated: list[str] = field(default_factory=list)
    checkbox_synced: list[str] = field(default_factory=list)
    tasks_sorted: list[str] = field(default_factory=list)
    links_updated: list[str] = field(default_factory=list)
    modified_dates_updated: list[str] = field(default_factory=list)
    deleted_links_removed: list[str] = field(default_factory=list)
    dependencies_updated: list[str] = field(default_factory=list)
    errors: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --- Static helper functions ---



def get_frontmatter(filepath: str) -> dict | None:
    """Parse and return frontmatter as dict."""
    post = load_note(filepath)
    if post is None:
        return None
    return dict(post.metadata)


def update_field(filepath: str | Path, field: str, value: Any, dry_run: bool = False) -> bool:
    """Update a single field in frontmatter. Returns True if changed."""
    post = load_note(filepath)
    if post is None:
        return False

    if post.get(field) == value:
        return False

    post[field] = value
    if not dry_run:
        save_note(filepath, post)
    return True


def get_parent_name(filepath: str) -> str | None:
    """Extract parent name from filename (project.group.task -> project.group)."""
    from ..utils import get_parent_name as _get_parent_name
    return _get_parent_name(Path(filepath).stem)


def infer_type(filepath: str, meta: dict | None) -> str:
    """Infer note type from filename when not specified in frontmatter.

    Priority:
    1. Explicit type in frontmatter
    2. Special files (backlog)
    3. Contains .task in filename -> task
    4. Contains .note in filename -> note
    5. Otherwise -> project
    """
    if meta and meta.get("type"):
        return meta.get("type")

    stem = Path(filepath).stem
    if stem == "backlog":
        return "special"
    if ".task" in stem:
        return "task"
    if ".note" in stem:
        return "note"
    return "project"


def should_archive(filepath: str, meta: dict) -> bool:
    """Check if file should be archived based on type and status."""
    note_type = meta.get("type")
    status = meta.get("status")

    # Use ArchiveManager logic
    # The instance methods in MaintenanceRunner will use ArchiveManager directly
    if note_type == "project" and status == "done":
        return True
    if note_type == "task" and (status == "done" or status == "dropped"):
        return True
    return False


def should_unarchive(filepath: str, meta: dict) -> bool:
    """Check if archived file should be moved back to notes (no longer done)."""
    note_type = meta.get("type")
    status = meta.get("status")

    # Only unarchive if status is explicitly set and not done
    if not status:
        return False
    if note_type == "project" and status != "done":
        return True
    if note_type == "task" and (status != "done" and status != "dropped"):
        return True
    return False


def get_title_from_file(filepath: str | Path) -> str:
    """Extract title from markdown file (from # heading or filename)."""
    path = Path(filepath)
    try:
        post = load_note(path)
        if post is None:
            return format_title(path.stem)
        
        # Try to get from first heading
        for line in post.content.split("\n"):
            if line.startswith("# "):
                return line[2:].strip()
        
        # Fall back to formatted filename
        from ..utils import format_title
        return format_title(path.stem)
    except Exception:
        from ..utils import format_title
        return format_title(path.stem)


# --- Validation functions ---

def validate_frontmatter(filepath: str, notes_dir: Path) -> list[str]:
    """Validate frontmatter values. Returns list of errors."""
    path = Path(filepath)
    if not path.exists():
        # Try resolving relative to notes_dir
        path = notes_dir / filepath
        if not path.exists():
            return []

    try:
        post = frontmatter.load(path)
    except Exception:
        return ["Invalid YAML in frontmatter"]

    meta = post.metadata
    if not meta:
        return []

    errors = []
    note_type = meta.get("type")

    # Validate status
    status = meta.get("status")
    if status:
        if note_type == "task":
            if status not in VALID_TASK_STATUS:
                errors.append(
                    f"Invalid task status '{status}'. "
                    f"Valid: {', '.join(sorted(VALID_TASK_STATUS))}"
                )
        elif note_type == "project":
            if status not in VALID_PROJECT_STATUS:
                errors.append(
                    f"Invalid project status '{status}'. "
                    f"Valid: {', '.join(sorted(VALID_PROJECT_STATUS))}"
                )

    # Validate priority
    priority = meta.get("priority")
    if priority and priority not in VALID_PRIORITY:
        errors.append(
            f"Invalid priority '{priority}'. "
            f"Valid: {', '.join(sorted(VALID_PRIORITY))}"
        )

    # Validate relation targets. These hold bare stems and are hand-editable,
    # so a typo would otherwise sit there silently. Archived targets are legal
    # (a continued project's predecessor normally lives in archive/).
    stem = path.stem
    for field, allowed_types in RELATION_FIELDS.items():
        targets = meta.get(field)
        if isinstance(targets, str):
            targets = [targets]
        if not targets:
            continue

        if note_type and note_type not in allowed_types:
            errors.append(
                f"'{field}' is not valid on a {note_type} note. "
                f"Valid types: {', '.join(allowed_types)}"
            )
            continue

        for target in targets:
            if target == stem:
                errors.append(f"'{field}' points at itself: {stem}")
                continue
            if "/" in str(target) or str(target).endswith(".md"):
                errors.append(
                    f"'{field}' entry '{target}' must be a bare stem, not a path"
                )
                continue
            if not (
                (notes_dir / f"{target}.md").exists()
                or (notes_dir / "archive" / f"{target}.md").exists()
            ):
                errors.append(f"'{field}' target does not exist: {target}")

    return errors


def validate_links(filepath: str, notes_dir: Path) -> list[str]:
    """Validate that markdown links point to existing files. Returns list of errors. Md links are in the form [text](some_path/some_target), this link point to file some_path/some_target.md, relative path to the file containing the link. Input filepath is relative to vault root."""
    
    path = notes_dir / filepath
    if not path.exists() or "templates/" in str(filepath):
        return []

    content = path.read_text()
    errors = []

    base_dir = notes_dir
    if (notes_dir / "archive") in path.parents:
        base_dir = notes_dir / "archive"

    for match in LinkPatterns.LINK.finditer(content):
        link_text, target = match.groups()

        if target.startswith(LinkPatterns.EXTERNAL_PREFIXES):
            continue
        # Build target absolute path - handle relative paths correctly
        # Link targets already contain .md extension
        if "../" in target:
            target_path = (path.parent / target)
        else:
            target_path = (base_dir / target)
        
        if not target_path.exists():
            errors.append(f"Broken link: [{link_text}]({target}) -> file {target_path} not found")

    return errors

class MaintenanceRunner:
    """Runs maintenance operations on a set of files.

    Args:
        notes_dir: Path to the notes directory
        dry_run: If True, compute changes but don't apply them
    """

    def __init__(self, notes_dir: Path, dry_run: bool = False):
        self.notes_dir = notes_dir
        self.archive_dir = notes_dir / "archive"
        self.dry_run = dry_run

        # Initialize new managers
        self.archive_mgr = ArchiveManager(notes_dir)
        self.file_iter = FileIterator(notes_dir)

    def find_file_in_notes(self, filename: str) -> Path | None:
        """Find a file in notes/ or notes/archive/."""
        path = self.notes_dir / filename
        if path.exists():
            return path
        path = self.archive_dir / filename
        if path.exists():
            return path
        return None

    def find_parent_file(self, parent_name: str) -> Path | None:
        """Find parent file in notes/ or notes/archive/."""
        return self.find_file_in_notes(f"{parent_name}.md")

    def find_children_files(self, parent_name: str) -> list[Path]:
        """Find all children files of a parent (project or task group)."""
        return list(self.file_iter.iter_children(parent_name, include_archive=True))

    def get_incomplete_tasks(self, project_name: str) -> list[str]:
        """Find all tasks of a project that are not done or dropped."""
        incomplete = []
        children = self.find_children_files(project_name)

        for child_path in children:
            meta = get_frontmatter(str(child_path))
            if not meta:
                continue

            if meta.get("type") != "task":
                continue

            status = meta.get("status", "todo")
            if status not in ("done", "dropped"):
                incomplete.append(child_path.name)

        return incomplete

    def sync(self, files: list[str], renamed: list[tuple[str, str]] = None,
             deleted: list[str] = None) -> SyncResult:
        """Run full sync on the given files.

        This is the main entry point that runs all maintenance operations
        in the correct order: sync first (fix links), then validate.

        Args:
            files: List of modified/added files to sync
            renamed: List of (old_path, new_path) tuples for renamed files
            deleted: List of deleted files to clean up references for
        """
        result = SyncResult()
        staged_files = list(files)  # Make a copy to modify

        # === SYNC OPERATIONS FIRST (fix links before validation) ===

        # Handle deleted files - remove references from parents
        if deleted:
            deleted_updates = self.handle_deleted_files(deleted)
            result.deleted_links_removed.extend(deleted_updates)
            # Add parent files to staged_files for further processing
            for parent_path in deleted_updates:
                if parent_path not in staged_files:
                    staged_files.append(parent_path)

        # Handle renamed files - update links in parents
        if renamed:
            rename_updates, rename_errors = self.handle_renamed_files(renamed)
            if rename_errors:
                result.errors["renames"] = rename_errors
                return result  # Stop on rename errors (structural issue)
            result.links_updated.extend(rename_updates)
            # Add renamed files to staged_files for further processing
            for _, new_path in renamed:
                if new_path not in staged_files:
                    staged_files.append(new_path)
    
        # Update modified dates
        for filepath in staged_files:
            if self.update_modified_date(filepath):
                result.modified_dates_updated.append(filepath)

        # Self-heal stale link prefixes (archive/, ../) so that links resolve
        # to where their targets actually live, before validation rejects them.
        for filepath in staged_files:
            path = Path(filepath)
            if not path.is_absolute():
                path = self.find_file_in_notes(Path(filepath).name) or (self.notes_dir / filepath)
            if path.exists() and self.normalize_link_prefixes(path):
                result.links_updated.append(str(path))

        # Surface stems that exist both active and archived. Everything keyed
        # by stem then becomes ambiguous: checkbox sync picks whichever copy it
        # finds first, so a parent can show the status of the wrong file.
        result.warnings.extend(self.check_duplicate_stems())

        # === VALIDATE BEFORE ARCHIVE/UNARCHIVE ===
        # Validate early to prevent archiving invalid state changes
        # (e.g., marking a group as done with incomplete children)
        for filepath in staged_files:
            errors = self.validate_file(filepath)
            if errors:
                result.errors[filepath] = errors

        # If validation errors found, stop here
        if result.errors:
            return result

        # IMPORTANT: Archive/unarchive MUST happen before sync_task_status_to_project
        # so that file locations are correct when updating parent checkboxes

        # Unarchive reactivated projects and tasks FIRST
        unarchived, unarchived_link_updates = self.unarchive_reactivated(staged_files)
        result.unarchived.extend(unarchived)
        result.links_updated.extend(unarchived_link_updates)

        # Update staged_files list with new paths
        for old, new in unarchived:
            if old in staged_files:
                staged_files.remove(old)
            if new not in staged_files:
                staged_files.append(new)

        # Archive completed projects and tasks
        archived, archived_link_updates = self.archive_completed(staged_files)
        result.archived.extend(archived)
        result.links_updated.extend(archived_link_updates)

        # Update staged_files list with new paths
        for old, new in archived:
            if old in staged_files:
                staged_files.remove(old)
            if new not in staged_files:
                staged_files.append(new)

        # Update task group status based on children
        group_updates = self.update_task_group_status(staged_files)
        result.group_status_updated.extend(group_updates)
        staged_files.extend([f for f in group_updates if f not in staged_files])

        # Update project status based on children (active task -> active project)
        project_updates, project_unarchived = self.update_project_status(staged_files)
        result.project_status_updated.extend(project_updates)
        result.unarchived.extend(project_unarchived)
        staged_files.extend([f for f in project_updates if f not in staged_files])

        # Sync task status to project files
        synced = self.sync_task_status_to_project(staged_files)
        result.checkbox_synced.extend(synced)

        # Sort tasks in parent files by status
        sorted_parents = self.sort_all_parents(staged_files)
        result.tasks_sorted.extend(sorted_parents)

        return result

    def validate_file(self, filepath: str) -> list[str]:
        """Run all validation checks on a file."""
        errors = validate_frontmatter(filepath, self.notes_dir)
        errors.extend(validate_links(filepath, self.notes_dir))

        # Additional check: project/task-group cannot be marked done if it has incomplete tasks
        path = Path(filepath)
        if not path.exists():
            path = self.notes_dir / filepath
        if path.exists():
            meta = get_frontmatter(str(path))
            note_type = infer_type(str(path), meta)
            status = meta.get("status") if meta else None

            # Check projects
            if note_type == "project" and status == "done":
                project_name = path.stem
                incomplete = self.get_incomplete_tasks(project_name)
                if incomplete:
                    errors.append(
                        f"Cannot mark project as done. Incomplete tasks: {', '.join(incomplete)}"
                    )

            # Check task-groups (tasks with children)
            if note_type == "task" and status in ("done", "dropped"):
                group_name = path.stem
                children = self.find_children_files(group_name)
                if children:  # This is a task-group
                    incomplete = self.get_incomplete_tasks(group_name)
                    if incomplete:
                        errors.append(
                            f"Cannot mark task-group as {status}. Incomplete tasks: {', '.join(incomplete)}"
                        )

            # Validate dependencies (for tasks and projects)
            if note_type in ("task", "project") and meta and meta.get("requires"):
                from ..core.notes import parse_metadata, find_notes
                from ..dependencies import validate_dependencies

                note = parse_metadata(path)
                if note:
                    # Get all notes for validation (metadata only - faster)
                    all_notes = find_notes(self.notes_dir, metadata_only=True)
                    if self.archive_dir.exists():
                        all_notes.extend(find_notes(self.archive_dir, metadata_only=True))

                    dep_errors = validate_dependencies(note, all_notes)
                    errors.extend(dep_errors)

        return errors

    def update_modified_date(self, filepath: str) -> bool:
        """Update the modified date in frontmatter. Returns True if changed."""
        path = Path(filepath)
        if not path.exists():
            path = self.notes_dir / filepath
        if not path.exists():
            return False

        post = load_note(path)
        if post is None:
            return False

        today = datetime.now().strftime('%Y-%m-%d %H:%M')

        if 'modified' in post.metadata:
            if post['modified'] == today:
                return False
            post['modified'] = today
        else:
            # Add modified after created, or at end
            if 'created' in post.metadata:
                new_metadata = {}
                for key, val in post.metadata.items():
                    new_metadata[key] = val
                    if key == 'created':
                        new_metadata['modified'] = today
                post.metadata = new_metadata
            else:
                post['modified'] = today

        if not self.dry_run:
            save_note(path, post)
        return True

    def unarchive_reactivated(self, staged_files: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
        """Move reactivated tasks/projects from archive back to notes."""
        unarchived = []
        updated_parents = []

        for filepath in staged_files:
            path = Path(filepath)

            # Only process files in archive directory
            if not self.archive_mgr.is_in_archive(filepath):
                continue

            # Resolve the actual file path
            actual_path = path
            if not path.is_absolute():
                actual_path = self.archive_dir / path.name

            meta = get_frontmatter(str(actual_path))
            if not meta:
                continue

            if should_unarchive(str(actual_path), meta):
                new_path = self.notes_dir / path.name

                if not self.dry_run:
                    # Move file
                    shutil.move(str(actual_path), new_path)

                    # Update links inside the unarchived file (remove ../ prefix)
                    self.update_links_in_file(new_path, to_archive=False)

                # Return original filepath for consistency
                unarchived.append((filepath, str(new_path)))

                if not self.dry_run:
                    # Update links in parent file
                    task_filename = path.stem
                    updated_parents.extend(self.update_links_in_parent(task_filename, to_archive=False))

                    # Update links in children files
                    updated_parents.extend(self.update_links_in_children(task_filename, to_archive=False))

                    # Unarchive parent project if it's in archive (cascade up)
                    parent_result = self.unarchive_parent_if_needed(str(new_path))
                    if parent_result:
                        unarchived.append(parent_result)
                        parent_name = get_parent_name(str(new_path))
                        if parent_name:
                            updated_parents.extend(self.update_links_in_parent(parent_name, to_archive=False))

        return unarchived, updated_parents

    def archive_completed(self, staged_files: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
        """Move completed projects/tasks to archive.
        """
        archived = []
        updated_parents = []

        # Build the work set: original staged files + cascaded descendants.
        # Cascaded files are force-archived even if `should_archive` says no
        # (notes never satisfy it).
        to_archive: list[str] = []
        cascaded: set[str] = set()
        seen: set[str] = set()

        def _queue(fp: str):
            if fp not in seen:
                seen.add(fp)
                to_archive.append(fp)

        for filepath in staged_files:
            _queue(filepath)

            path = Path(filepath)
            if self.archive_mgr.is_in_archive(filepath) or "templates" in filepath:
                continue
            if path.name == "backlog.md":
                continue
            actual_path = path if path.is_absolute() else self.notes_dir / path.name
            if not actual_path.exists():
                continue
            meta = get_frontmatter(str(actual_path))
            if not meta or not should_archive(str(actual_path), meta):
                continue
            if meta.get("type") not in ("project", "task"):
                continue

            # Glob matches descendants at any depth (parent.*.md → parent.x.md,
            # parent.x.y.md, ...). One pass catches the whole subtree.
            for child_path in self.notes_dir.glob(f"{path.stem}.*.md"):
                child_str = str(child_path)
                if child_str in seen:
                    continue
                child_meta = get_frontmatter(child_str)
                if not child_meta:
                    continue
                ctype = child_meta.get("type")
                cstatus = child_meta.get("status")
                if ctype == "note" or (ctype == "task" and cstatus in ("done", "dropped")):
                    _queue(child_str)
                    cascaded.add(child_str)

        # Deepest-first: children move before their parent, so update_links_in_file
        # sees the parent still in notes/ and adds `../` prefix — which the parent's
        # later update_links_in_children call rewrites back to a sibling reference.
        to_archive.sort(key=lambda fp: -Path(fp).stem.count("."))

        for filepath in to_archive:
            path = Path(filepath)

            if self.archive_mgr.is_in_archive(filepath) or "templates" in filepath:
                continue
            if path.name == "backlog.md":
                continue

            actual_path = path if path.is_absolute() else self.notes_dir / path.name
            if not actual_path.exists():
                continue

            meta = get_frontmatter(str(actual_path))
            if not meta:
                continue

            if not (filepath in cascaded or should_archive(str(actual_path), meta)):
                continue

            new_path = self.archive_dir / path.name

            if not self.dry_run:
                self.archive_dir.mkdir(exist_ok=True)
                shutil.move(str(actual_path), new_path)
                self.update_links_in_file(new_path, to_archive=True)

            archived.append((filepath, str(new_path)))

            if not self.dry_run:
                task_filename = path.stem
                updated_parents.extend(self.update_links_in_parent(task_filename, to_archive=True))
                updated_parents.extend(self.update_links_in_children(task_filename, to_archive=True))

        return archived, updated_parents

    def update_links_in_file(self, filepath: Path, to_archive: bool) -> bool:
        """Update links inside the file itself when it's archived or unarchived."""
        content = filepath.read_text()
        new_content = content

        if to_archive:
            # First: convert (archive/foo.md) to (foo.md) - now siblings in archive
            pattern1 = r'(\[[^\]]+\]\()archive/([^)]+)(\))'
            replacement1 = r'\g<1>\g<2>\g<3>'
            new_content = re.sub(pattern1, replacement1, new_content)

            # Second: add ../ prefix only to links pointing to files in notes/
            def add_prefix_if_needed(match):
                prefix = match.group(1)
                target = match.group(2)
                suffix = match.group(3)

                if target.startswith(LinkPatterns.EXTERNAL_PREFIXES) or target.startswith('../'):
                    return match.group(0)

                # target is e.g. "stem.md" — check if it exists in archive (sibling)
                target_stem = target.removesuffix('.md')
                target_file = f"{target_stem}.md"
                archive_path = self.archive_dir / target_file
                if archive_path.exists():
                    return match.group(0)

                return f"{prefix}../{target}{suffix}"

            pattern2 = r'(\[[^\]]+\]\()([^)]+)(\))'
            new_content = re.sub(pattern2, add_prefix_if_needed, new_content)
        else:
            # Remove ../ prefix from relative links
            pattern = r'(\[[^\]]+\]\()\.\.\/([^)]+)(\))'
            replacement = r'\g<1>\g<2>\g<3>'
            new_content = re.sub(pattern, replacement, new_content)

        if new_content != content:
            if not self.dry_run:
                atomic_write_text(filepath, new_content)
            return True

        return False

    def normalize_link_prefixes(self, filepath: Path) -> bool:
        """Rewrite internal link prefixes to match where targets actually live.

        The correct prefix for an internal link depends only on whether the
        source file and the target file are in archive/:

            source active,  target active   -> bare      (target.md)
            source active,  target archived -> archive/   (archive/target.md)
            source archived, target active  -> ../        (../target.md)
            source archived, target archived -> bare      (target.md)

        Incremental archive/unarchive can leave stale prefixes (e.g. a child
        archived before its parent keeps a ../ link that later resolves to a
        missing active file, or sibling links keep an archive/ prefix that
        resolves to archive/archive/...). This self-heals all of them. Links
        whose target does not exist in either location are left untouched so
        validation can still report genuinely broken links.
        """
        content = filepath.read_text()
        source_in_archive = self.archive_dir in filepath.parents

        def fix(match):
            prefix, target, suffix = match.group(1), match.group(2), match.group(3)
            if target.startswith(LinkPatterns.EXTERNAL_PREFIXES):
                return match.group(0)
            # Only normalize note links; never touch image/asset/other paths.
            if not target.endswith('.md'):
                return match.group(0)

            if target.startswith('../'):
                bare = target[3:]
            elif target.startswith('archive/'):
                bare = target[len('archive/'):]
            else:
                bare = target

            target_active = (self.notes_dir / bare).exists()
            target_archived = (self.archive_dir / bare).exists()
            if not target_active and not target_archived:
                return match.group(0)  # genuinely missing — leave for validation

            if source_in_archive:
                correct = bare if target_archived else f"../{bare}"
            else:
                correct = f"archive/{bare}" if target_archived else bare

            return f"{prefix}{correct}{suffix}"

        new_content = LinkPatterns.LINK_CAPTURE.sub(fix, content)

        if new_content != content:
            if not self.dry_run:
                atomic_write_text(filepath, new_content)
            return True
        return False

    def update_links_in_parent(self, task_filename: str, to_archive: bool) -> list[str]:
        """Update links in parent file when task is archived or unarchived."""
        updated = []
        parent_name = get_parent_name(f"{task_filename}.md")
        if not parent_name:
            return updated

        parent_path = self.find_parent_file(parent_name)
        if not parent_path:
            return updated

        parent_content = parent_path.read_text()

        # If the parent is itself in archive/, parent and task are siblings in
        # the same directory: links must stay bare (no archive/ prefix), else
        # they resolve to archive/archive/... relative to the parent file.
        parent_in_archive = self.archive_dir in parent_path.parents

        if to_archive:
            if parent_in_archive:
                pattern = rf"(\[[^\]]+\]\()(?:archive/)?({re.escape(task_filename)}\.md)(\))"
                replacement = rf"\g<1>{task_filename}.md\g<3>"
            else:
                pattern = rf"(\[[^\]]+\]\()({re.escape(task_filename)}\.md)(\))"
                replacement = rf"\g<1>archive/{task_filename}.md\g<3>"
        else:
            pattern = rf"(\[[^\]]+\]\()archive/({re.escape(task_filename)}\.md)(\))"
            replacement = rf"\g<1>{task_filename}.md\g<3>"

        new_content = re.sub(pattern, replacement, parent_content)

        if new_content != parent_content:
            if not self.dry_run:
                atomic_write_text(parent_path, new_content)
            updated.append(str(parent_path))

        return updated

    def update_links_in_children(self, parent_name: str, to_archive: bool) -> list[str]:
        """Update links to parent in all children files when parent is archived/unarchived."""
        updated = []
        children = self.find_children_files(parent_name)

        for child_path in children:
            # Only process children in archive (fix: use .parents check, not string match)
            if self.archive_dir not in child_path.parents:
                continue

            content = child_path.read_text()

            if to_archive:
                # Parent moved to archive: child links go from (../parent.md) to (parent.md)
                pattern = rf'(\[[^\]]+\]\()\.\./{re.escape(parent_name)}\.md(\))'
                replacement = rf'\g<1>{parent_name}.md\g<2>'
            else:
                # Parent moved from archive: child links go from (parent.md) to (../parent.md)
                pattern = rf'(\[[^\]]+\]\()(?!\.\./){re.escape(parent_name)}\.md(\))'
                replacement = rf'\g<1>../{parent_name}.md\g<2>'

            new_content = re.sub(pattern, replacement, content)

            if new_content != content:
                if not self.dry_run:
                    atomic_write_text(child_path, new_content)
                updated.append(str(child_path))

        return updated

    def update_links_to_archived_children(self, parent_path: Path, parent_name: str) -> bool:
        """Update links in parent file to add archive/ prefix for children still in archive."""
        content = parent_path.read_text()
        new_content = content

        children_in_archive = []
        for child_path in self.archive_dir.glob(f"{parent_name}.*.md"):
            children_in_archive.append(child_path.stem)

        for child_name in children_in_archive:
            pattern = rf'(\[[^\]]+\]\()(?!archive/)({re.escape(child_name)}\.md)(\))'
            replacement = rf'\g<1>archive/{child_name}.md\g<3>'
            new_content = re.sub(pattern, replacement, new_content)

        if new_content != content:
            if not self.dry_run:
                atomic_write_text(parent_path, new_content)
            return True

        return False

    def unarchive_parent_if_needed(self, child_filepath: str) -> tuple[str, str] | None:
        """Unarchive and reactivate parent project if a child task is unarchived."""
        parent_name = get_parent_name(child_filepath)
        if not parent_name:
            return None

        parent_path = self.archive_dir / f"{parent_name}.md"
        if not parent_path.exists():
            return None

        new_path = self.notes_dir / f"{parent_name}.md"

        # Load and update status to active
        post = load_note(parent_path)
        if post and post.get('status') == 'done':
            post['status'] = 'active'

        if not self.dry_run:
            # Move file to the active location, writing updated content if it
            # parsed. Write-then-unlink (or move) so the original is never lost
            # when the parent can't be parsed.
            if post:
                save_note(new_path, post)
                parent_path.unlink()
            else:
                shutil.move(str(parent_path), str(new_path))

            # Update links inside the parent file
            self.update_links_in_file(new_path, to_archive=False)

            # Update links to children that remain in archive
            self.update_links_to_archived_children(new_path, parent_name)

            # Update links in archived children
            self.update_links_in_children(parent_name, to_archive=False)

            # Recursively unarchive grandparent if needed
            self.unarchive_parent_if_needed(f"{parent_name}.md")

        return (str(parent_path), str(new_path))

    def update_dependencies_on_rename(self, old_stem: str, new_stem: str) -> list[str]:
        """Update relation fields when a task/project is renamed.

        Covers every stored relation (`requires`, `continues`, `related`), not
        just dependencies - they all hold bare stems, so a rename invalidates
        them identically. Reverse directions are computed rather than stored,
        so there is nothing else to fix up.

        Args:
            old_stem: Old task/project stem
            new_stem: New task/project stem

        Returns:
            List of files that were updated
        """
        return self._rewrite_relation_stems(
            lambda stems: [new_stem if s == old_stem else s for s in stems],
            old_stem,
        )

    def remove_dependencies_on_delete(self, deleted_stem: str) -> list[str]:
        """Remove relation references when a task/project is deleted.

        Args:
            deleted_stem: Stem of deleted task/project

        Returns:
            List of files that were updated
        """
        return self._rewrite_relation_stems(
            lambda stems: [s for s in stems if s != deleted_stem],
            deleted_stem,
        )

    def check_duplicate_stems(self) -> list[str]:
        """Report stems that exist both in the vault root and in archive/.

        Cor identifies notes by stem, so a duplicate makes every stem lookup
        ambiguous. In practice the parent's checkbox ends up reflecting
        whichever copy was found first: a project showed `[/]` (waiting) for a
        link pointing at `archive/<stem>.md`, because an active file with the
        same stem had `status: waiting` while the archived one was `done`.

        Reported rather than auto-resolved - which copy is authoritative is a
        judgement call about the user's content.

        Returns:
            Human-readable warning strings, one per duplicated stem
        """
        if not self.archive_dir.exists():
            return []

        active = {
            p.stem for p in self.notes_dir.glob("*.md")
            if p.stem != "backlog" and not p.name.startswith(".")
        }
        warnings = []
        for path in sorted(self.archive_dir.glob("*.md")):
            if path.stem in active:
                warnings.append(
                    f"Duplicate stem '{path.stem}': exists both as "
                    f"{path.stem}.md and archive/{path.stem}.md. "
                    f"Stem lookups are ambiguous until one is removed or renamed."
                )
        return warnings

    def _rewrite_relation_stems(self, transform, target_stem: str) -> list[str]:
        """Apply ``transform`` to every relation list mentioning ``target_stem``.

        Args:
            transform: Callable taking the current stem list and returning the
                replacement list
            target_stem: Stem to look for; files not mentioning it are untouched

        Returns:
            List of files that were updated
        """
        updated = []

        for search_dir in [self.notes_dir, self.archive_dir]:
            if not search_dir.exists():
                continue

            for note_path in search_dir.glob("*.md"):
                post = load_note(note_path)
                if not post:
                    continue

                changed = False
                for field in RELATION_FIELDS:
                    stems = post.get(field)
                    # Tolerate a hand-written scalar (`continues: foo`).
                    if isinstance(stems, str):
                        stems = [stems]
                    if not stems or target_stem not in stems:
                        continue

                    post[field] = transform(list(stems))
                    changed = True

                if changed:
                    if not self.dry_run:
                        save_note(note_path, post)
                    updated.append(str(note_path))

        return updated

    def handle_renamed_files(self, renamed_files: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
        """Handle file renames by updating links in parent and children.
        
        When parent changes (e.g., p1.task.md -> p2.task.md):
        1. Remove link from old parent (p1.md)
        2. Add link to new parent (p2.md)
        3. Update backlink in renamed file
        4. Update parent field in frontmatter
        """
        updated = []
        errors = []

        for old_path, new_path in renamed_files:
            old_name = Path(old_path).stem
            new_name = Path(new_path).stem

            # Check if parent/group name changed
            old_parts = old_name.split(".")
            new_parts = new_name.split(".")

            old_parent = None
            new_parent = None
            parent_changed = False

            if len(old_parts) >= 2:
                old_parent = ".".join(old_parts[:-1])
            if len(new_parts) >= 2:
                new_parent = ".".join(new_parts[:-1])

            if old_parent != new_parent:
                parent_changed = True
                # Check if new parent file exists
                if new_parent:
                    new_parent_file = self.find_file_in_notes(f"{new_parent}.md")
                    if not new_parent_file:
                        errors.append(
                            f"Cannot rename '{old_name}' to '{new_name}': "
                            f"parent/group '{new_parent}' does not exist. "
                            f"Create the parent file first."
                        )
                        continue

            # Handle parent change
            if parent_changed and old_parent and new_parent:
                # 1. Remove link from old parent
                old_parent_path = self.find_parent_file(old_parent)
                if old_parent_path:
                    content = old_parent_path.read_text()
                    # Match: - [x] [Title](old_name.md) or - [x] [Title](archive/old_name.md)
                    pattern = rf'^- \[[^\]]*\] \[[^\]]+\]\((?:archive/)?{re.escape(old_name)}\.md\)\n?'
                    new_content = re.sub(pattern, '', content, flags=re.MULTILINE)
                    if new_content != content:
                        if not self.dry_run:
                            atomic_write_text(old_parent_path, new_content)
                        if str(old_parent_path) not in updated:
                            updated.append(str(old_parent_path))

                # 2. Add link to new parent
                new_parent_path = self.find_parent_file(new_parent)
                if new_parent_path:
                    # Get task info to create proper link
                    renamed_file_path = self.notes_dir / new_path
                    if not renamed_file_path.exists():
                        renamed_file_path = self.archive_dir / Path(new_path).name
                    
                    if renamed_file_path.exists():
                        meta = get_frontmatter(str(renamed_file_path))
                        if meta and meta.get("type") == "task":
                            from ..schema import get_status_symbol
                            
                            task_status = meta.get('status', 'todo')
                            # Get actual title from file
                            task_title = get_title_from_file(renamed_file_path)
                            checkbox = get_status_symbol(task_status)
                            
                            # Determine if file is in archive. Only add the
                            # archive/ prefix when the parent receiving the link
                            # is active; if the parent is itself archived they
                            # are siblings and the link must stay bare.
                            is_archived = 'archive/' in new_path or str(self.archive_dir) in str(renamed_file_path)
                            parent_in_archive = self.archive_dir in new_parent_path.parents
                            if is_archived and not parent_in_archive:
                                link_target = f"archive/{new_name}.md"
                            else:
                                link_target = f"{new_name}.md"
                            task_entry = f"- {checkbox} [{task_title}]({link_target})"

                            content = new_parent_path.read_text()
                            # Idempotent: skip if this task is already linked
                            # in the parent (avoids duplicate task entries).
                            already_linked = re.search(
                                rf'\]\((?:archive/|\.\./)?{re.escape(new_name)}\.md\)',
                                content,
                            )
                            # Add to Tasks section
                            if already_linked:
                                pass
                            elif "## Tasks" in content:
                                lines = content.split("\n")
                                new_lines = []
                                in_tasks = False
                                added = False
                                
                                for line in lines:
                                    new_lines.append(line)
                                    if line.strip() == "## Tasks":
                                        in_tasks = True
                                    elif in_tasks and not added and not line.strip().startswith("<!--"):
                                        # Add task after section header
                                        new_lines.append(task_entry)
                                        added = True
                                        in_tasks = False
                                
                                if not added:
                                    # Tasks section exists but empty
                                    new_lines.append(task_entry)
                                
                                new_content = "\n".join(new_lines)
                                if not self.dry_run:
                                    atomic_write_text(new_parent_path, new_content)
                                if str(new_parent_path) not in updated:
                                    updated.append(str(new_parent_path))

                # 3. Update backlink in renamed file
                renamed_file_path = self.notes_dir / new_path
                if not renamed_file_path.exists():
                    renamed_file_path = self.archive_dir / Path(new_path).name
                
                if renamed_file_path.exists():
                    content = renamed_file_path.read_text()
                    
                    # Get new parent title
                    new_parent_path = self.find_parent_file(new_parent)
                    if new_parent_path:
                        new_parent_title = get_title_from_file(new_parent_path)
                        
                        # Update backlink: [< Old Parent](old_parent.md) -> [< New Parent](new_parent.md)
                        # Handle both regular and archive paths
                        old_backlink_pattern = rf'\[<[^\]]+\]\({re.escape(old_parent)}\.md\)'
                        old_backlink_archive_pattern = rf'\[<[^\]]+\]\(\.\.\/({re.escape(old_parent)})\.md\)'

                        is_in_archive = str(self.archive_dir) in str(renamed_file_path)
                        if is_in_archive:
                            new_backlink = f"[< {new_parent_title}](../{new_parent}.md)"
                        else:
                            new_backlink = f"[< {new_parent_title}]({new_parent}.md)"
                        
                        new_content = re.sub(old_backlink_pattern, new_backlink, content)
                        new_content = re.sub(old_backlink_archive_pattern, new_backlink, new_content)
                        
                        if new_content != content:
                            if not self.dry_run:
                                atomic_write_text(renamed_file_path, new_content)
                            if str(renamed_file_path) not in updated:
                                updated.append(str(renamed_file_path))
                
                # 4. Update parent field in frontmatter
                if not self.dry_run:
                    if update_field(renamed_file_path, 'parent', new_parent, dry_run=False):
                        if str(renamed_file_path) not in updated:
                            updated.append(str(renamed_file_path))

            # Handle simple rename (no parent change) - update links in parent file
            elif old_name != new_name and new_parent:
                parent_path = self.find_parent_file(new_parent)
                if parent_path:
                    content = parent_path.read_text()
                    pattern = rf'(\[[^\]]+\]\()(archive/)?{re.escape(old_name)}\.md(\))'
                    replacement = rf'\g<1>\g<2>{new_name}.md\g<3>'
                    new_content = re.sub(pattern, replacement, content)

                    if new_content != content:
                        if not self.dry_run:
                            atomic_write_text(parent_path, new_content)
                        if str(parent_path) not in updated:
                            updated.append(str(parent_path))

            # Update links in children files (for both parent change and simple rename)
            if old_name != new_name:
                old_children = self.find_children_files(old_name)
                for child_path in old_children:
                    content = child_path.read_text()
                    pattern = rf'(\[[^\]]+\]\()(\.\.\/)?{re.escape(old_name)}\.md(\))'
                    replacement = rf'\g<1>\g<2>{new_name}.md\g<3>'
                    new_content = re.sub(pattern, replacement, content)

                    if new_content != content:
                        if not self.dry_run:
                            atomic_write_text(child_path, new_content)
                        if str(child_path) not in updated:
                            updated.append(str(child_path))
                    
                    # Also update the parent field in frontmatter
                    if not self.dry_run:
                        if update_field(child_path, 'parent', new_name, dry_run=False):
                            if str(child_path) not in updated:
                                updated.append(str(child_path))

            # Update dependencies
            if old_name != new_name:
                updated_deps = self.update_dependencies_on_rename(old_name, new_name)
                for dep_file in updated_deps:
                    if dep_file not in updated:
                        updated.append(dep_file)

        return updated, errors

    def handle_deleted_files(self, deleted_files: list[str]) -> list[str]:
        """Remove references to deleted files from their parent files.

        Returns list of parent files that were updated.
        """
        updated = []

        for filepath in deleted_files:
            deleted_name = Path(filepath).stem

            # Prune relations first. This must not be gated on having a parent:
            # a top-level project has none, and skipping it left dangling
            # requires/continues/related entries pointing at a deleted note.
            updated_deps = self.remove_dependencies_on_delete(deleted_name)
            for dep_file in updated_deps:
                if dep_file not in updated:
                    updated.append(dep_file)

            parent_name = get_parent_name(filepath)
            if not parent_name:
                continue

            parent_path = self.find_parent_file(parent_name)
            if not parent_path:
                continue

            content = parent_path.read_text()

            # Remove task entry line: - [x] [Title](deleted_name.md) or - [x] [Title](archive/deleted_name.md)
            pattern = rf'^- \[[^\]]*\] \[[^\]]+\]\((?:archive/)?{re.escape(deleted_name)}\.md\)\n?'
            new_content = re.sub(pattern, '', content, flags=re.MULTILINE)

            if new_content != content:
                if not self.dry_run:
                    atomic_write_text(parent_path, new_content)
                if str(parent_path) not in updated:
                    updated.append(str(parent_path))

        return updated

    def get_child_task_statuses(self, parent_name: str) -> list[str]:
        """Get all child task statuses for a parent."""
        statuses = []
        for child_path in self.find_children_files(parent_name):
            meta = get_frontmatter(str(child_path))
            if meta and meta.get("type") == "task":
                statuses.append(meta.get("status", "todo"))
        return statuses

    def set_status_in_file(self, filepath: Path, new_status: str) -> bool:
        """Update status in frontmatter. Returns True if changed."""
        post = load_note(filepath)
        if post is None:
            return False

        current_status = post.get('status')
        if current_status == new_status:
            return False

        if 'status' in post.metadata:
            post['status'] = new_status
        else:
            # Add status after type or created
            new_metadata = {}
            added = False
            for key, val in post.metadata.items():
                new_metadata[key] = val
                if key in ('type', 'created') and not added:
                    new_metadata['status'] = new_status
                    added = True
            if not added:
                new_metadata['status'] = new_status
            post.metadata = new_metadata

        if not self.dry_run:
            save_note(filepath, post)
        return True

    def update_task_group_status(self, staged_files: list[str]) -> list[str]:
        """Update task group status based on children status."""
        updated = []
        checked_groups = set()

        for filepath in staged_files:
            parent_name = get_parent_name(filepath)
            if not parent_name or parent_name in checked_groups:
                continue
            checked_groups.add(parent_name)

            parent_path = self.find_parent_file(parent_name)
            if not parent_path:
                continue

            meta = get_frontmatter(str(parent_path))
            if not meta or meta.get("type") != "task":
                continue

            child_statuses = self.get_child_task_statuses(parent_name)
            if not child_statuses:
                continue

            current_status = meta.get("status", "todo")

            # Derive status from children: blocked > done > active > todo
            if "blocked" in child_statuses:
                new_status = "blocked"
            elif all(s in ("done", "dropped") for s in child_statuses):
                new_status = "done"
            elif "active" in child_statuses:
                new_status = "active"
            else:
                new_status = "todo"

            if new_status != current_status and self.set_status_in_file(parent_path, new_status):
                updated.append(str(parent_path))

        return updated

    def update_project_status(self, staged_files: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
        """Update project status based on children task status.

        Rules:
        - Any child task active → project becomes active (unarchive if needed)
        - No active tasks AND project is active → project becomes planning
        - done and paused are user-controlled (never auto-set)
        """
        updated = []
        unarchived = []
        checked_projects = set()

        for filepath in staged_files:
            # Get root project name (first part of dot notation)
            parts = Path(filepath).stem.split(".")
            if len(parts) < 2:
                continue

            project_name = parts[0]
            if project_name in checked_projects:
                continue
            checked_projects.add(project_name)

            project_path = self.find_file_in_notes(f"{project_name}.md")
            if not project_path:
                continue

            meta = get_frontmatter(str(project_path))
            if not meta or infer_type(str(project_path), meta) != "project":
                continue

            current_status = meta.get("status", "planning")

            # Never auto-modify done or paused
            if current_status in ("done", "paused"):
                continue

            child_statuses = self.get_child_task_statuses(project_name)
            has_active = "active" in child_statuses

            if has_active and current_status != "active":
                new_status = "active"
            elif not has_active and current_status == "active":
                new_status = "planning"
            else:
                continue

            # Handle unarchiving if needed
            is_in_archive = self.archive_dir in project_path.parents
            if is_in_archive and new_status == "active":
                new_path = self.notes_dir / f"{project_name}.md"
                if not self.dry_run:
                    self.set_status_in_file(project_path, new_status)
                    shutil.move(str(project_path), new_path)
                    self.update_links_in_file(new_path, to_archive=False)
                    self.update_links_to_archived_children(new_path, project_name)
                unarchived.append((str(project_path), str(new_path)))
                updated.append(str(new_path))
            elif self.set_status_in_file(project_path, new_status):
                updated.append(str(project_path))

        return updated, unarchived

    def sync_task_status_to_project(self, staged_files: list[str]) -> list[str]:
        """Update task checkboxes in parent files based on task status.

        Batches updates by parent to avoid reading the same file multiple times.
        """
        updated_projects = []

        # Group tasks by parent for batched processing
        tasks_by_parent: dict[str, list[tuple[str, str]]] = {}  # parent_name -> [(task_stem, checkbox)]

        for filepath in staged_files:
            parent_name = get_parent_name(filepath)
            if not parent_name:
                continue

            # Resolve filepath
            path = Path(filepath)
            if not path.exists():
                path = self.notes_dir / filepath
                if not path.exists():
                    path = self.archive_dir / Path(filepath).name

            meta = get_frontmatter(str(path))
            if not meta or meta.get("type") != "task":
                continue

            task_status = meta.get("status", "todo")
            checkbox = STATUS_SYMBOLS.get(task_status, "[ ]")
            task_stem = Path(filepath).stem

            tasks_by_parent.setdefault(parent_name, []).append((task_stem, checkbox))

        # Process each parent file once
        for parent_name, tasks in tasks_by_parent.items():
            parent_path = self.find_parent_file(parent_name)
            if not parent_path:
                continue

            content = parent_path.read_text()
            new_content = content

            # Apply all task updates to this parent
            for task_stem, checkbox in tasks:
                pattern = rf"(- )\[[x .o~/]\]( \[[^\]]+\]\()(archive/)?{re.escape(task_stem)}\.md(\))"
                new_content = re.sub(
                    pattern,
                    rf"\g<1>{checkbox}\g<2>\g<3>{task_stem}.md\g<4>",
                    new_content,
                )

            if new_content != content:
                if not self.dry_run:
                    atomic_write_text(parent_path, new_content)
                updated_projects.append(str(parent_path))

        return updated_projects

    def sort_tasks_in_parent(self, parent_path: Path) -> bool:
        """Sort task entries in a parent file by status."""
        content = parent_path.read_text()

        task_pattern = re.compile(
            r'^(- \[([x .o~/])\] \[[^\]]+\]\((?:archive/)?([^\)]+)\))$',
            re.MULTILINE
        )

        matches = list(task_pattern.finditer(content))
        if len(matches) < 2:
            return False

        status_order = {'o': 0, '.': 1, '/': 1, ' ': 2, 'x': 3, '~': 4}

        tasks = []
        seen_targets = set()
        had_duplicates = False
        for match in matches:
            line = match.group(1)
            checkbox = match.group(2)
            task_name = match.group(3)
            # Dedup by normalized target (ignore archive/ and ../ prefixes):
            # the same task may be listed multiple times after repeated
            # rename/sync runs. Keep the first occurrence only.
            target_key = task_name.replace('archive/', '').replace('../', '')
            if target_key in seen_targets:
                had_duplicates = True
                continue
            seen_targets.add(target_key)
            tasks.append({
                'line': line,
                'checkbox': checkbox,
                'name': task_name,
                'start': match.start(),
                'end': match.end(),
                'order': status_order.get(checkbox, 2)
            })

        separator = "---"
        has_active = any(t['order'] < 3 for t in tasks)
        has_done = any(t['order'] >= 3 for t in tasks)

        # Span over ALL original matches (not just the deduped list) so that
        # trailing duplicate lines are absorbed into the rewritten block.
        first_task_start = matches[0].start()
        last_task_end = matches[-1].end()

        task_block = content[first_task_start:last_task_end]
        has_separator = separator in task_block

        is_sorted = all(
            tasks[i]['order'] <= tasks[i + 1]['order']
            for i in range(len(tasks) - 1)
        )

        needs_separator = has_active and has_done and not has_separator

        if is_sorted and not needs_separator and not had_duplicates:
            return False

        sorted_tasks = sorted(tasks, key=lambda t: (t['order'], t['name']))

        new_lines = []
        for i, task in enumerate(sorted_tasks):
            if (needs_separator and
                i > 0 and sorted_tasks[i - 1]['order'] < 3 and task['order'] >= 3):
                new_lines.append(separator)
            new_lines.append(task['line'])

        new_content = content[:first_task_start] + "\n".join(new_lines) + content[last_task_end:]

        if new_content != content:
            if not self.dry_run:
                atomic_write_text(parent_path, new_content)
            return True

        return False

    def sort_all_parents(self, staged_files: list[str]) -> list[str]:
        """Sort tasks in all parent files of staged files."""
        sorted_parents = []
        checked = set()

        for filepath in staged_files:
            parent_name = get_parent_name(filepath)
            if parent_name and parent_name not in checked:
                checked.add(parent_name)
                parent_path = self.find_parent_file(parent_name)
                if parent_path:
                    if self.sort_tasks_in_parent(parent_path):
                        sorted_parents.append(str(parent_path))

        return sorted_parents

"""Link extraction, validation, and manipulation for Cor.

This module consolidates all regex patterns and link operations that were
previously scattered across maintenance.py and other modules.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Link:
    """Represents a markdown link found in content."""
    text: str
    target: str
    span: tuple[int, int]  # character positions (start, end)
    is_external: bool
    is_archive: bool
    has_parent_prefix: bool  # True if target starts with ../


class LinkPatterns:
    """Centralized regex patterns for link manipulation.

    All 35+ patterns from maintenance.py consolidated here.
    """

    # Basic link pattern: [text](target)
    LINK = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

    # Generic capture group for link manipulation: ([text](), target, ())
    LINK_CAPTURE = re.compile(r'(\[[^\]]+\]\()([^)]+)(\))')

    # Archive link patterns
    ARCHIVE_LINK = re.compile(r'(\[[^\]]+\]\()archive/([^)]+)(\))')
    ARCHIVE_LINK_WITH_TEXT = re.compile(r'\[([^\]]+)\]\(archive/([^)]+)\)')

    # Parent prefix patterns (../)
    PARENT_PREFIX_LINK = re.compile(r'(\[[^\]]+\]\()\.\.\/([^)]+)(\))')

    # Task entry patterns (for task lists in parents)
    # Matches: - [x] [Title](target) or - [x] [Title](archive/target)
    TASK_ENTRY = re.compile(
        r'^(- \[([x .o~/])\] \[[^\]]+\]\((?:archive/)?([^\)]+)\))$',
        re.MULTILINE
    )

    # Task entry with capture groups for modification
    TASK_ENTRY_DETAILED = re.compile(
        r"(- )\[[x .o~/]\]( \[[^\]]+\]\()(archive/)?([^\)]+)(\))",
        re.MULTILINE
    )

    # Backlink patterns (parent references)
    BACKLINK = re.compile(r'\[<([^\]]+)\]\(([^)]+)\)')
    BACKLINK_WITH_ARCHIVE = re.compile(r'\[<([^\]]+)\]\((?:archive/)?([^)]+)\)')

    # External link prefixes
    EXTERNAL_PREFIXES = ('http://', 'https://', '#', 'mailto:')


class LinkManager:
    """Centralized link operations for notes."""

    def __init__(self, notes_dir: Path):
        """Initialize link manager.

        Args:
            notes_dir: Path to notes directory
        """
        self.notes_dir = notes_dir
        self.archive_dir = notes_dir / "archive"

    def extract_links(self, content: str) -> list[Link]:
        """Extract all links from content.

        Args:
            content: Markdown content to parse

        Returns:
            List of Link objects
        """
        links = []
        for match in LinkPatterns.LINK.finditer(content):
            text, target = match.groups()
            links.append(Link(
                text=text,
                target=target,
                span=match.span(),
                is_external=self.is_external(target),
                is_archive=target.startswith("archive/"),
                has_parent_prefix=target.startswith("../")
            ))
        return links

    def is_external(self, target: str) -> bool:
        """Check if link target is external.

        Args:
            target: Link target string

        Returns:
            True if external link
        """
        return target.startswith(LinkPatterns.EXTERNAL_PREFIXES)

    def update_archive_links(self, content: str, to_archive: bool) -> str:
        """Update links when moving between archive and active.

        Args:
            content: File content
            to_archive: True if moving to archive, False if unarchiving

        Returns:
            Updated content with modified links
        """
        if to_archive:
            # Add archive/ prefix to internal links
            # Pattern: [text](target) -> [text](archive/target)
            # But don't add if already has archive/ or is external
            def add_archive_prefix(match):
                prefix = match.group(1)  # [text](
                target = match.group(2)  # target
                suffix = match.group(3)  # )

                # Skip if already archive/ or external
                if target.startswith('archive/') or self.is_external(target):
                    return match.group(0)

                return f"{prefix}archive/{target}{suffix}"

            pattern = r'(\[[^\]]+\]\()([^)]+)(\))'
            return re.sub(pattern, add_archive_prefix, content)
        else:
            # Remove archive/ prefix from links
            # Pattern: [text](archive/target) -> [text](target)
            return re.sub(
                LinkPatterns.ARCHIVE_LINK,
                r'\g<1>\g<2>\g<3>',
                content
            )

    def update_parent_prefix(self, content: str, add_prefix: bool) -> str:
        """Update ../ prefix in links (for archived children).

        When a file is in archive/, links to parent need ../ prefix.
        When unarchiving, remove the prefix.

        Args:
            content: File content
            add_prefix: True to add ../, False to remove

        Returns:
            Updated content
        """
        if add_prefix:
            # Add ../ prefix to parent links that don't have it
            def add_prefix_if_needed(match):
                prefix = match.group(1)  # [text](
                target = match.group(2)  # target
                suffix = match.group(3)  # )

                # Skip if already has ../ or is external or is archive/
                if target.startswith('../') or target.startswith('archive/') or self.is_external(target):
                    return match.group(0)

                return f"{prefix}../{target}{suffix}"

            pattern = r'(\[[^\]]+\]\()([^)]+)(\))'
            return re.sub(pattern, add_prefix_if_needed, content)
        else:
            # Remove ../ prefix
            return re.sub(
                LinkPatterns.PARENT_PREFIX_LINK,
                r'\g<1>\g<2>\g<3>',
                content
            )

    def update_link_targets(self, content: str, old_target: str, new_target: str,
                           preserve_archive_prefix: bool = True) -> str:
        """Update all links pointing to old_target to point to new_target.

        Handles both regular and archive/ prefixed links.

        Args:
            content: File content
            old_target: Old link target (stem.md, e.g. "task.md")
            new_target: New link target (stem.md, e.g. "new_task.md")
            preserve_archive_prefix: If True, keep archive/ prefix if present

        Returns:
            Updated content
        """
        if preserve_archive_prefix:
            # Pattern: [text](archive/)?old_target -> [text](archive/)?new_target
            # Preserves archive/ if present
            pattern = rf'(\[[^\]]+\]\()(archive/)?{re.escape(old_target)}(\))'
            replacement = rf'\g<1>\g<2>{new_target}\g<3>'
        else:
            # Simple replacement
            pattern = rf'(\[[^\]]+\]\(){re.escape(old_target)}(\))'
            replacement = rf'\g<1>{new_target}\g<2>'

        return re.sub(pattern, replacement, content)

    def update_backlink(self, content: str, old_parent: str, new_parent: str,
                       new_parent_title: str) -> str:
        """Update backlink (parent reference) in content.

        Backlinks have format: [< Parent Title](parent_stem.md)

        Args:
            content: File content
            old_parent: Old parent stem
            new_parent: New parent stem
            new_parent_title: New parent title for link text

        Returns:
            Updated content
        """
        # Match regular, archive/, and ../ versions
        old_pattern = rf'\[<[^\]]+\]\((?:(?:archive/|\.\./)?){re.escape(old_parent)}\.md\)'
        new_backlink = f"[< {new_parent_title}]({new_parent}.md)"

        return re.sub(old_pattern, new_backlink, content)

    def add_archive_prefix_to_children(self, content: str, child_stems: list[str]) -> str:
        """Add archive/ prefix to links targeting specific children.

        Used when children are archived but parent remains active.

        Args:
            content: Parent file content
            child_stems: List of child stems to update

        Returns:
            Updated content
        """
        new_content = content

        for child_stem in child_stems:
            # Only add archive/ if not already present
            pattern = rf'(\[[^\]]+\]\()(?!archive/)({re.escape(child_stem)}\.md)(\))'
            replacement = rf'\g<1>archive/{child_stem}.md\g<3>'
            new_content = re.sub(pattern, replacement, new_content)

        return new_content

    def remove_task_entry(self, content: str, task_stem: str) -> str:
        """Remove task entry line from parent content.

        Removes lines like: - [x] [Title](task_stem.md)
        Handles both regular and archive/ prefixed links.

        Args:
            content: Parent file content
            task_stem: Task stem to remove (without .md extension)

        Returns:
            Content with task entry removed
        """
        # Pattern matches: - [x] [Title](task.md) or - [x] [Title](archive/task.md)
        pattern = rf'^- \[[^\]]*\] \[[^\]]+\]\((?:archive/)?{re.escape(task_stem)}\.md\)\n?'
        return re.sub(pattern, '', content, flags=re.MULTILINE)

    def update_task_checkbox(self, content: str, task_stem: str,
                            new_checkbox: str) -> str:
        """Update checkbox symbol for a task in parent content.

        Args:
            content: Parent file content
            task_stem: Task stem to update (without .md extension)
            new_checkbox: New checkbox symbol (x, ., o, ~, or space)

        Returns:
            Updated content
        """
        pattern = rf"(- )\[[x .o~/]\]( \[[^\]]+\]\()(archive/)?{re.escape(task_stem)}\.md(\))"
        replacement = rf"\g<1>[{new_checkbox}]\g<2>\g<3>{task_stem}.md\g<4>"
        return re.sub(pattern, replacement, content, flags=re.MULTILINE)

    def extract_task_entries(self, content: str) -> list[tuple[str, str, str]]:
        """Extract all task entries from content.

        Args:
            content: Parent file content

        Returns:
            List of (checkbox, text, target_stem) tuples
        """
        entries = []

        for match in LinkPatterns.TASK_ENTRY.finditer(content):
            full_line = match.group(1)
            checkbox = match.group(2)
            target = match.group(3)

            # Extract link text from full line
            link_match = re.search(r'\[([^\]]+)\]', full_line)
            text = link_match.group(1) if link_match else ""

            entries.append((checkbox, text, target))

        return entries

    def validate_links(self, filepath: Path, all_notes: list[str] = None) -> list[str]:
        """Validate all internal links in a file.

        Args:
            filepath: Path to file to validate
            all_notes: Optional list of valid note stems

        Returns:
            List of error messages (empty if valid)
        """
        errors = []
        content = filepath.read_text()

        for link in self.extract_links(content):
            if link.is_external:
                continue

            # Remove archive/ and ../ prefixes, and .md suffix for validation
            target_stem = (link.target
                           .replace('archive/', '')
                           .replace('../', '')
                           .removesuffix('.md'))

            # Check if target exists
            if all_notes and target_stem not in all_notes:
                errors.append(f"Broken link in {filepath.name}: [{link.text}]({link.target})")

        return errors

    def resolve_target_path(self, link_target: str, source_file: Path) -> Path:
        """Resolve link target to absolute path.

        Link targets already include the .md extension (e.g. "stem.md",
        "archive/stem.md", "../stem.md").

        Args:
            link_target: Link target string (may have archive/ or ../ prefix)
            source_file: Path to file containing the link

        Returns:
            Resolved absolute path
        """
        # Handle archive/ prefix: e.g. "archive/stem.md"
        if link_target.startswith('archive/'):
            target_file = link_target[8:]  # Remove 'archive/', keep "stem.md"
            return self.archive_dir / target_file

        # Handle ../ prefix (link from archive to parent): e.g. "../stem.md"
        if link_target.startswith('../'):
            target_file = link_target[3:]  # Remove '../', keep "stem.md"
            return self.notes_dir / target_file

        # Regular link - check if source is in archive
        source_in_archive = self.archive_dir in source_file.parents

        if source_in_archive:
            # Link from archive file - target is in archive (siblings)
            return self.archive_dir / link_target
        else:
            # Link from active file
            return self.notes_dir / link_target



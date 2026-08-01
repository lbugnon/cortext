"""Continuation context: pulling a predecessor project's framing forward.

Setting `continues` is just a frontmatter edit (see :mod:`cor.core.relations`).
The useful part of continuing a project is carrying the old project's Goal into
the new one, so the new file opens with the context you actually need instead
of an empty template.

The predecessor is normally archived and is never modified.
"""

from pathlib import Path

import frontmatter

from .archive import ArchiveManager

#: Heading inserted into the successor to hold predecessor context.
CONTINUES_HEADING = "## Continues"

#: Sections copied from each predecessor, in this order.
#:
#: Goal only, deliberately. The Summary of a finished project describes how it
#: ended, which is history rather than something the new project should carry
#: forward - and it is a link away. The Goal is the part you actually restate.
#: Add more section names here to copy them too.
COPIED_SECTIONS = ("Goal",)


def extract_section(content: str, heading: str) -> str | None:
    """Return the body of a markdown section, or None if absent/empty.

    Matches a level-2 heading by name and returns everything up to the next
    heading of the same or higher level. Comment-only bodies (the ``<!-- ... -->``
    placeholders in the stock templates) count as empty.

    Args:
        content: Full markdown body (no frontmatter)
        heading: Section name without the leading ``##``, e.g. ``"Goal"``

    Returns:
        Stripped section body, or None when the section is missing or empty
    """
    lines = content.split("\n")
    target = heading.strip().lower()

    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        name = stripped.lstrip("#").strip().lower()
        if level == 2 and name == target:
            start = i + 1
            break

    if start is None:
        return None

    body = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= 2:
                break
        body.append(line)

    text = "\n".join(body).strip()
    if not text:
        return None

    # Drop pure-placeholder bodies like "<!-- One-sentence deliverable -->"
    without_comments = []
    in_comment = False
    for line in text.split("\n"):
        s = line.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if s.startswith("<!--"):
            if "-->" not in s:
                in_comment = True
            continue
        without_comments.append(line)

    return text if "\n".join(without_comments).strip() else None


def _title_of(post: frontmatter.Post, stem: str) -> str:
    """First H1 of a note, falling back to its stem."""
    for line in post.content.split("\n", 20):
        if line.startswith("# "):
            return line[2:].strip()
    return stem


def build_predecessor_block(stem: str, path: Path, link_target: str) -> str:
    """Render the context block for one predecessor.

    Args:
        stem: Predecessor stem
        path: Path to the predecessor file (may be inside archive/)
        link_target: Link target to write, relative to the successor. Callers
            may pass the bare stem and let MaintenanceRunner normalize the
            ``archive/`` prefix afterwards.

    Returns:
        Markdown block, without a trailing newline
    """
    post = frontmatter.load(path)
    title = _title_of(post, stem)

    parts = [f"[< Continues: {title}]({link_target})"]

    for section in COPIED_SECTIONS:
        body = extract_section(post.content, section)
        if body:
            parts.append(f"**{section} ({title}):**\n\n{body}")

    return "\n\n".join(parts)


def _existing_predecessors(content: str) -> set[str]:
    """Stems already recorded in the successor's Continues section."""
    section = extract_section(content, CONTINUES_HEADING.lstrip("# ").strip())
    if not section:
        return set()

    import re

    stems = set()
    for match in re.finditer(r"\[<\s*Continues:[^\]]*\]\(([^)]+)\)", section):
        target = match.group(1)
        stems.add(Path(target).stem)
    return stems


def apply_continuation_context(
    notes_dir: Path,
    successor_path: Path,
    predecessor_stems: list[str],
) -> list[str]:
    """Insert predecessor Goal context into the successor's body.

    Idempotent: predecessors already present in the Continues section are
    skipped, so re-running never duplicates a block.

    Links are written with the bare stem. The caller is expected to run
    ``MaintenanceRunner.sync()`` afterwards, which rewrites them to
    ``archive/<stem>.md`` when the predecessor is archived.

    Args:
        notes_dir: Vault root
        successor_path: Path to the project doing the continuing
        predecessor_stems: Stems of the predecessors to pull context from

    Returns:
        Stems that were actually added (excludes already-present ones)
    """
    archive = ArchiveManager(notes_dir)
    post = frontmatter.load(successor_path)
    already = _existing_predecessors(post.content)

    blocks = []
    added = []
    for stem in predecessor_stems:
        if stem in already:
            continue
        found = archive.find_file(stem)
        if found is None:
            continue
        path, _is_archived = found
        blocks.append(build_predecessor_block(stem, path, f"{stem}.md"))
        added.append(stem)

    if not blocks:
        return []

    post.content = _insert_blocks(post.content, blocks)
    with open(successor_path, "wb") as f:
        frontmatter.dump(post, f, sort_keys=False)

    return added


def _insert_blocks(content: str, blocks: list[str]) -> str:
    """Append blocks to the Continues section, creating it after the H1."""
    joined = "\n\n".join(blocks)
    lines = content.split("\n")

    # Existing section: append just before the next same-or-higher heading.
    for i, line in enumerate(lines):
        if line.strip().lower() == CONTINUES_HEADING.lower():
            end = len(lines)
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s.startswith("#"):
                    level = len(s) - len(s.lstrip("#"))
                    if level <= 2:
                        end = j
                        break
            head = "\n".join(lines[:end]).rstrip()
            tail = "\n".join(lines[end:])
            out = f"{head}\n\n{joined}\n"
            return f"{out}\n{tail}" if tail.strip() else out

    # No section yet: insert right after the H1 so the context is the first
    # thing you read, ahead of the template's own sections.
    section = f"{CONTINUES_HEADING}\n\n{joined}\n"
    for i, line in enumerate(lines):
        if line.startswith("# "):
            head = "\n".join(lines[: i + 1]).rstrip()
            tail = "\n".join(lines[i + 1:]).lstrip("\n")
            return f"{head}\n\n{section}\n{tail}" if tail.strip() else f"{head}\n\n{section}"

    return f"{content.rstrip()}\n\n{section}"

"""Shared Markdown section operations for Cortex documents."""

from __future__ import annotations

import re


_H2 = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def sections(content: str) -> dict[str, str]:
    """Return second-level Markdown sections without their headings."""
    matches = list(_H2.finditer(content))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        result[match.group(1)] = content[match.end():end].strip()
    return result


def set_section(
    content: str,
    heading: str,
    value: str,
    *,
    append: bool = False,
) -> str:
    """Set or append an H2 section, creating it when absent."""
    clean_heading = heading.strip().removeprefix("##").strip()
    if not clean_heading or "\n" in clean_heading:
        raise ValueError("Section heading must be one non-empty line.")

    matches = list(_H2.finditer(content))
    target = next(
        (match for match in matches if match.group(1) == clean_heading), None
    )
    clean_value = value.strip()

    if target is None:
        block = f"## {clean_heading}\n"
        if clean_value:
            block += f"\n{clean_value}\n"
        return f"{content.rstrip()}\n\n{block}"

    index = matches.index(target)
    end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
    existing = content[target.end():end].strip()
    if append and existing and clean_value:
        clean_value = f"{existing}\n\n{clean_value}"
    elif append and existing:
        clean_value = existing

    replacement = f"## {clean_heading}\n"
    if clean_value:
        replacement += f"\n{clean_value}\n"
    if end < len(content):
        replacement += "\n"
    return content[:target.start()] + replacement + content[end:]


def add_initial_text(content: str, note_type: str, text: str) -> str:
    """Put initial prose in the canonical section for each entry type."""
    heading = {"task": "Description", "project": "Goal"}.get(note_type)
    if heading:
        return set_section(content, heading, text)
    return f"{content.rstrip()}\n\n{text.strip()}\n"

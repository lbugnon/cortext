"""Reference path and citekey helpers for bibliography management.

Reference notes are stored in ``ref/<citekey>.md``; the authoritative
bibliographic metadata lives in ``ref/references.bib`` (see ``cor/bibtex.py``).
"""

import re
from pathlib import Path
from typing import Optional


def get_ref_dir(notes_dir: Path) -> Path:
    """Return the bibliography directory (``ref/``) for a vault."""
    return notes_dir / "ref"


def get_existing_citekeys(notes_dir: Path) -> set[str]:
    """Get the set of existing citekeys (from ``ref/*.md`` filenames)."""
    ref_dir = get_ref_dir(notes_dir)
    if not ref_dir.exists():
        return set()

    return {p.stem for p in ref_dir.glob("*.md") if not p.name.startswith(".")}


def generate_citekey(
    authors: list[str],
    year: Optional[int],
    title: str,
    existing_keys: set[str],
) -> str:
    """Generate unique citekey in author<year><keyword> format.

    Examples:
        - smith2024neural
        - jones2023
        - smith2024neural2 (if smith2024neural exists)
    """
    # Extract first author's last name
    if authors:
        first_author = authors[0]
        # Handle "Last, First" format
        if "," in first_author:
            last_name = first_author.split(",")[0].strip()
        else:
            # Handle "First Last" format
            parts = first_author.split()
            last_name = parts[-1] if parts else "unknown"
    else:
        last_name = "unknown"

    # Clean last name
    last_name = re.sub(r"[^a-zA-Z]", "", last_name).lower()
    if not last_name:
        last_name = "unknown"

    # Extract keyword from title
    title_words = re.findall(r"[a-zA-Z]+", title.lower())
    # Skip common words
    skip_words = {"the", "a", "an", "of", "for", "and", "in", "on", "to", "with"}
    keyword = ""
    for word in title_words:
        if word not in skip_words and len(word) > 2:
            keyword = word
            break

    # Build base citekey
    year_str = str(year) if year else ""
    base_key = f"{last_name}{year_str}{keyword}"

    # Ensure uniqueness
    if base_key not in existing_keys:
        return base_key

    # Add numeric suffix
    suffix = 2
    while f"{base_key}{suffix}" in existing_keys:
        suffix += 1

    return f"{base_key}{suffix}"


def get_ref_path(citekey: str, notes_dir: Path) -> Path:
    """Get path for a reference file."""
    return get_ref_dir(notes_dir) / f"{citekey}.md"

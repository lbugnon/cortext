"""BibLaTeX file handling for bibliography management.

Maintains a single references.bib file in ref/ directory.
"""

import re
from pathlib import Path
from typing import Optional

from .crossref import CrossrefResult
from bibtexparser import loads as bibtex_loads
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase


def escape_bibtex(text: str) -> str:
    """Escape special LaTeX characters in text."""
    if not text:
        return ""
    # Escape special chars
    replacements = [
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    result = text
    for old, new in replacements:
        result = result.replace(old, new)
    return result


def format_authors_bibtex(authors: list[str]) -> str:
    """Format author list for BibTeX: 'Last, First and Last, First'."""
    if not authors:
        return "Unknown"
    return " and ".join(authors)


def result_to_bib_entry(citekey: str, result: CrossrefResult) -> dict:
    """Convert CrossrefResult to a bibtexparser entry dict."""
    entry: dict[str, str] = {
        "ENTRYTYPE": result.entry_type,
        "ID": citekey,
        "title": escape_bibtex(result.title or citekey),
        "author": format_authors_bibtex(result.authors or ["Unknown"]),
    }

    if result.year:
        entry["year"] = str(result.year)
    if result.journal:
        entry["journal"] = escape_bibtex(result.journal)
    if result.volume:
        entry["volume"] = str(result.volume)
    if result.pages:
        entry["pages"] = str(result.pages)
    if result.publisher:
        entry["publisher"] = escape_bibtex(result.publisher)
    if result.doi:
        entry["doi"] = result.doi
    if result.url:
        entry["url"] = result.url

    return entry


def get_bib_path(notes_dir: Path) -> Path:
    """Get path to references.bib file."""
    return notes_dir / "ref" / "references.bib"


def add_bib_entry(notes_dir: Path, citekey: str, result: CrossrefResult) -> None:
    """Add or update an entry in references.bib using bibtexparser."""
    bib_path = get_bib_path(notes_dir)
    db = BibDatabase()

    if bib_path.exists():
        try:
            db = bibtex_loads(bib_path.read_text())
        except Exception:
            db = BibDatabase()

    # Remove existing entry with same ID if present
    db.entries = [e for e in getattr(db, "entries", []) if e.get("ID") != citekey]

    # Append new entry
    new_entry = result_to_bib_entry(citekey, result)
    db.entries.append(new_entry)

    writer = BibTexWriter()
    writer.indent = "  "
    writer.order_entries_by = None
    content = writer.write(db)
    bib_path.parent.mkdir(exist_ok=True)
    bib_path.write_text(content)


def remove_bib_entry(notes_dir: Path, citekey: str) -> bool:
    """Remove entry from references.bib file. Returns True if removed."""
    bib_path = get_bib_path(notes_dir)
    if not bib_path.exists():
        return False

    try:
        db = bibtex_loads(bib_path.read_text())
    except Exception:
        return False

    original_count = len(getattr(db, "entries", []))
    db.entries = [e for e in getattr(db, "entries", []) if e.get("ID") != citekey]
    new_count = len(db.entries)

    if original_count == new_count:
        return False

    writer = BibTexWriter()
    writer.indent = "  "
    writer.order_entries_by = None
    content = writer.write(db)
    bib_path.write_text(content)
    return True


def has_doi_in_bib(notes_dir: Path, doi: str) -> Optional[str]:
    """Check if DOI exists in references.bib. Returns citekey if found."""
    bib_path = get_bib_path(notes_dir)
    if not bib_path.exists():
        return None
    try:
        db = bibtex_loads(bib_path.read_text())
        target = doi.lower()
        for e in getattr(db, "entries", []):
            entry_doi = (e.get("doi") or "").lower()
            if entry_doi == target:
                return e.get("ID")
        return None
    except Exception:
        return None


def load_bib_db(notes_dir: Path) -> BibDatabase:
    """Load and return the BibDatabase (empty if missing or parse error)."""
    bib_path = get_bib_path(notes_dir)
    if not bib_path.exists():
        return BibDatabase()
    try:
        return bibtex_loads(bib_path.read_text())
    except Exception:
        return BibDatabase()


def list_bib_entries(notes_dir: Path) -> list[dict]:
    """Return list of bib entries (dicts) from references.bib."""
    db = load_bib_db(notes_dir)
    return list(getattr(db, "entries", []))


def get_bib_entry(notes_dir: Path, citekey: str) -> Optional[dict]:
    """Get a single bib entry by citekey (ID)."""
    db = load_bib_db(notes_dir)
    for e in getattr(db, "entries", []):
        if e.get("ID") == citekey:
            return e
    return None

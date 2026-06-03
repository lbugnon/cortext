"""Crossref API integration for bibliography references.

Uses habanero library to fetch paper metadata from DOIs.
"""

import re
from dataclasses import dataclass
from typing import Optional

from habanero import Crossref
import arxiv


@dataclass
class CrossrefResult:
    """Structured result from Crossref lookup."""

    title: str
    authors: list[str]  # "Last, First" format
    year: Optional[int]
    doi: str
    journal: Optional[str]
    volume: Optional[str]
    pages: Optional[str]
    publisher: Optional[str]
    abstract: Optional[str]
    entry_type: str  # BibLaTeX type: article, book, etc.
    url: Optional[str]


# Mapping from Crossref types to BibLaTeX entry types
CROSSREF_TYPE_MAP = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book": "book",
    "book-chapter": "incollection",
    "monograph": "book",
    "report": "techreport",
    "dissertation": "phdthesis",
    "dataset": "misc",
    "posted-content": "unpublished",  # preprints
    "peer-review": "misc",
}


def _format_author(author: dict) -> str:
    """Format author dict to 'Last, First' string."""
    family = author.get("family", "")
    given = author.get("given", "")
    if family and given:
        return f"{family}, {given}"
    return family or given or "Unknown"


def _format_author_from_string(name: str) -> str:
    """Convert name string 'First Last' to 'Last, First' format."""
    if not name:
        return "Unknown"
    name = name.strip()
    parts = name.rsplit(" ", 1)
    if len(parts) == 2:
        return f"{parts[1]}, {parts[0]}"
    return name


def _extract_year(item: dict) -> Optional[int]:
    """Extract publication year from Crossref item."""
    # Try different date fields
    for field in ["published-print", "published-online", "created", "issued"]:
        if field in item:
            date_parts = item[field].get("date-parts", [[]])
            if date_parts and date_parts[0]:
                return date_parts[0][0]
    return None


def _clean_abstract(abstract: Optional[str]) -> Optional[str]:
    """Clean HTML tags from abstract."""
    if not abstract:
        return None
    # Remove JATS XML tags
    clean = re.sub(r"<[^>]+>", "", abstract)
    # Normalize whitespace
    clean = " ".join(clean.split())
    return clean if clean else None


def _extract_arxiv_id(url: str) -> Optional[str]:
    """Extract arXiv ID from various formats.
    
    Handles:
    - 10.48550/arXiv.1706.03762 -> 1706.03762
    - https://arxiv.org/abs/1706.03762 -> 1706.03762
    - 1706.03762 -> 1706.03762 (if valid format)
    """
    if not url:
        return None
    # From DOI format
    m = re.search(r"arXiv\.(\d{4}\.\d{4,5})", url)
    if m:
        return m.group(1)
    # From arXiv URL
    m = re.search(r"arxiv\.org/(abs|pdf)/(\d{4}\.\d{4,5})", url)
    if m:
        return m.group(2)
    # Plain arXiv ID format (YYMM.NNNNN or YYMM.NNNN)
    m = re.match(r"(\d{4}\.\d{4,5})(?:v\d+)?$", url)
    if m:
        return m.group(1)
    return None


def lookup_arxiv(doi: str) -> Optional[CrossrefResult]:
    """Lookup metadata using the python-arxiv library."""
    arxiv_id = _extract_arxiv_id(doi)
    try:
        search = arxiv.Search(id_list=[arxiv_id])
        results = list(search.results())
        if not results:
            return None
        r = results[0]

        # Title
        title = (r.title or "Untitled").strip()

        # Authors -> format "Last, First"
        authors: list[str] = []
        for a in getattr(r, "authors", []):
            name = getattr(a, "name", "").strip()
            if not name:
                continue
            authors.append(_format_author_from_string(name))
        if not authors:
            authors = ["Unknown"]

        # Year
        year = None
        if getattr(r, "published", None):
            try:
                year = int(r.published.year)
            except Exception:
                year = None

        # Abstract
        abstract = None
        if getattr(r, "summary", None):
            abstract = " ".join(r.summary.split())

        # URL
        url = getattr(r, "entry_id", None) or f"https://arxiv.org/abs/{arxiv_id}"

        return CrossrefResult(
            title=title,
            authors=authors,
            year=year,
            doi=f"10.48550/arXiv.{arxiv_id}",
            journal="arXiv preprint",
            publisher="arXiv",
            abstract=abstract,
            entry_type="unpublished",
            url=url,
            volume=None,
            pages=None,
        )
    except Exception:
        return None


def lookup_doi(doi: str) -> Optional[CrossrefResult]:
    """Fetch metadata from Crossref for a DOI.

    Args:
        doi: Digital Object Identifier (with or without prefix)

    Returns:
        CrossrefResult with paper metadata, or None if not found
    """
    # Clean DOI - remove URL prefixes
    doi, publisher = extract_doi_from_url(doi)
    if not doi:
        return None
    doi = doi.strip()

    # Handle arXiv DOIs via arxiv API
    if publisher == "arXiv":
        return lookup_arxiv(doi)

    try:
        cr = Crossref()
        result = cr.works(ids=doi)

        if not result or "message" not in result:
            return None

        item = result["message"]

        # Extract title
        titles = item.get("title", [])
        title = titles[0] if titles else "Untitled"

        # Extract authors
        authors = [_format_author(a) for a in item.get("author", [])]
        if not authors:
            authors = ["Unknown"]

        # Extract year
        year = _extract_year(item)

        # Extract journal/container
        containers = item.get("container-title", [])
        journal = containers[0] if containers else None

        # Map type
        cr_type = item.get("type", "misc")
        entry_type = CROSSREF_TYPE_MAP.get(cr_type, "misc")

        # Extract other fields
        volume = item.get("volume")
        pages = item.get("page")
        publisher = item.get("publisher")
        abstract = _clean_abstract(item.get("abstract"))
        url = item.get("URL")

        return CrossrefResult(
            title=title,
            authors=authors,
            year=year,
            doi=item.get("DOI", doi),
            journal=journal,
            volume=volume,
            pages=pages,
            publisher=publisher,
            abstract=abstract,
            entry_type=entry_type,
            url=url,
        )

    except Exception as e:
        # API errors, network issues, etc.
        print(f"Crossref lookup failed: {e}")
        return None


def extract_doi_from_url(url: str) -> Optional[str]:
    """Extract DOI or identifier from various URL/identifier formats.

    Handles:
    - https://doi.org/10.1234/example
    - https://dx.doi.org/10.1234/example
    - https://arxiv.org/abs/1706.03762
    - 1706.03762 (arXiv ID)
    - 10.1234/example (plain DOI)

    Returns:
        Extracted DOI, arXiv ID, or None if not recognized, and publisher type
    """
    if not url:
        return None, None

    url = url.strip()

    # Check for arXiv URL or ID first
    arxiv_id = _extract_arxiv_id(url)
    if arxiv_id:
        # Return full arXiv DOI for consistency
        return f"10.48550/arXiv.{arxiv_id}", "arXiv"

    # Plain DOI pattern (10.xxxx/...)
    # Match DOI with any non-whitespace characters after the slash
    doi_pattern = r"(10\.\d{4,}/\S+)"

    # Try standard DOI extraction
    match = re.search(doi_pattern, url)
    if match:
        doi = match.group(1)
        # Clean trailing punctuation
        doi = doi.rstrip(".,;")
        # remove version suffix if present (only vN where N is a single digit)
        doi = re.sub(r"(v\d)$", "", doi)
        return doi, "publisher"
    # Standard doi subpatter: /doi/10.xxxx/...
    m = re.search(r"/doi/(10\.\d+/\S+)", url)
    if m:
        doi = m.group(1).rstrip(".,;")
        return doi, "publisher"
    return None, None

"""Reference management commands for bibliography."""

from pathlib import Path
from datetime import datetime 
from ..schema import DATE_TIME

import click

from ..exceptions import ValidationError, NotFoundError, AlreadyExistsError, ExternalServiceError
from ..crossref import lookup_doi, extract_doi_from_url, CrossrefResult
from ..bibtex import add_bib_entry, list_bib_entries, get_bib_entry, remove_bib_entry
from ..core.refs import (
    generate_citekey,
    get_existing_citekeys,
    get_ref_path,
    get_ref_dir,
)
from ..utils import get_notes_dir, require_init, log_info, open_in_editor
from ..completions import complete_ref


def _ensure_ref_dir(notes_dir: Path) -> Path:
    """Ensure ref/ directory exists."""
    ref_dir = get_ref_dir(notes_dir)
    ref_dir.mkdir(exist_ok=True)
    return ref_dir


def _create_ref_file(
    notes_dir: Path,
    citekey: str,
    result: CrossrefResult,
    tags: list[str] = None,
) -> Path:
    """Create reference markdown file from CrossrefResult."""
    ref_dir = _ensure_ref_dir(notes_dir)
    ref_path = ref_dir / f"{citekey}.md"

    # Build plain markdown content
    # Use semicolon to separate authors since they are in "Last, First" format
    authors_str = "; ".join(result.authors) if result.authors else "Unknown"
    abstract = result.abstract or ""
    # load contet from assets
    content = open(Path(__file__).parent.parent / "assets" / "ref.md").read().format(title=result.title, authors=authors_str, abstract=abstract, date=datetime.now().strftime(DATE_TIME))
    ref_path.write_text(content)
    return ref_path


@click.command(short_help="Add a reference from DOI or URL")
@click.argument("identifier")
@click.option("--key", "-k", help="Custom citekey (auto-generated if not provided)")
@click.option("--tags", "-t", multiple=True, help="Tags to add to the reference")
@click.option("--no-edit", is_flag=True, help="Don't open in editor after creating")
@require_init
def add(identifier: str, key: str | None, tags: tuple, no_edit: bool):
    """Add a reference from DOI or URL.

    IDENTIFIER can be:
    - A DOI: 10.1234/example.2024
    - A DOI URL: https://doi.org/10.1234/example
    - A publisher URL containing a DOI

    Examples:
        cor ref add 10.1234/example.2024
        cor ref add https://doi.org/10.1234/example
        cor ref add --key smith2024ml 10.1234/example
        cor ref add -t machine-learning -t nlp 10.1234/example
    """
    notes_dir = get_notes_dir()

    # Extract DOI from identifier
    doi, _ = extract_doi_from_url(identifier)

    if not doi:
        raise ValidationError(
            "No DOI found in the provided identifier.\n"
        )
    # Check if DOI already exists in references.bib
    from ..bibtex import has_doi_in_bib
    existing_citekey = has_doi_in_bib(notes_dir, doi)
    if existing_citekey:
        raise AlreadyExistsError(
            f"Reference with DOI {doi} already exists: {existing_citekey}"
        )

    log_info(f"Looking up DOI: {doi}")

    # Fetch metadata from Crossref
    result = lookup_doi(doi)
    if not result:
        raise ExternalServiceError(
            f"Could not fetch metadata for DOI: {doi}\n"
            "Please check the DOI is correct and try again."
        )

    # Display found info
    authors_str = "; ".join(result.authors[:3])
    if len(result.authors) > 3:
        authors_str += " et al."

    log_info(f"Found: {result.title}")
    log_info(f"       {authors_str} ({result.year or 'n.d.'})")
    if result.journal:
        log_info(f"       {result.journal}")

    # Generate or validate citekey
    existing_keys = get_existing_citekeys(notes_dir)

    if key:
        # User provided custom key
        if key in existing_keys:
            raise AlreadyExistsError(
                f"Citekey '{key}' already exists. Use a different key."
            )
        citekey = key
    else:
        # Auto-generate
        citekey = generate_citekey(
            result.authors, result.year, result.title, existing_keys
        )

    # Create reference file and add to references.bib
    ref_path = _create_ref_file(notes_dir, citekey, result, list(tags))
    try:
        add_bib_entry(notes_dir, citekey, result)
    except Exception as e:
        log_info(f"Warning: could not update references.bib: {e}")

    log_info(f"Created reference: ref/{citekey}.md")
    log_info("Updated bibliography: ref/references.bib")
    log_info(f"Cite with: [{citekey}](ref/{citekey})")

    # Open in editor unless --no-edit
    if not no_edit:
        open_in_editor(ref_path)


@click.command(short_help="List all references")
@click.option("--format", "-f", "fmt", type=click.Choice(["table", "short"]),
              default="table", help="Output format")
@require_init(write=False)
def list_refs(fmt: str):
    """List all references from references.bib.

    Examples:
        cor ref list
        cor ref list --format short
    """
    notes_dir = get_notes_dir()
    entries = list_bib_entries(notes_dir)

    if not entries:
        log_info("No references found. Add one with: cor ref add <doi>")
        return

    # Sort by year (newest first), then by citekey
    def year_of(e):
        y = e.get("year")
        try:
            return int(y)
        except Exception:
            return -1

    entries.sort(key=lambda e: (-year_of(e), e.get("ID", "")))

    if fmt == "short":
        for e in entries:
            log_info(e.get("ID", ""))
    else:
        # Table format
        log_info(f"{'Citekey':<25} {'Year':<6} {'Authors':<25} {'Title':<40}")
        log_info("-" * 100)

        for e in entries:
            authors_str = e.get("author") or "Unknown"
            authors = authors_str.split(" and ") if authors_str else []
            author_display = authors[0] if authors else "Unknown"
            if len(authors) > 1:
                author_display += " et al."
            if len(author_display) > 25:
                author_display = author_display[:22] + "..."

            title = e.get("title") or "Untitled"
            if len(title) > 40:
                title = title[:37] + "..."

            year = e.get("year") or "n.d."

            log_info(f"{e.get('ID',''):<25} {year:<6} {author_display:<25} {title:<40}")

        log_info(f"\nTotal: {len(entries)} references")


@click.command(short_help="Show reference details")
@click.argument("citekey", shell_complete=complete_ref)
@require_init(write=False)
def show(citekey: str):
    """Display detailed information about a reference.

    Examples:
        cor ref show smith2024neural
    """
    notes_dir = get_notes_dir()

    # Load from .bib
    entry = get_bib_entry(notes_dir, citekey)
    if not entry:
        raise NotFoundError(f"Reference not found: {citekey}")

    title = entry.get("title") or citekey
    authors_str = entry.get("author") or "Unknown"
    authors = [a.strip() for a in authors_str.split(" and ") if a.strip()]
    year = entry.get("year") or "n.d."

    # Display details
    log_info(f"\n{click.style(title, bold=True)}")
    log_info(f"Citekey: {citekey}")
    log_info(f"Type: {entry.get('ENTRYTYPE','misc')}")
    log_info(f"Year: {year}")
    log_info(f"Authors: {', '.join(authors) if authors else 'Unknown'}")

    if entry.get("journal"):
        log_info(f"Journal: {entry.get('journal')}")
    if entry.get("doi"):
        log_info(f"DOI: {entry.get('doi')}")
    if entry.get("url"):
        log_info(f"URL: {entry.get('url')}")

    # Note file path
    log_info(f"\nFile: ref/{citekey}.md")
    log_info(f"Cite: [{citekey}](ref/{citekey})")


@click.command(short_help="Edit a reference")
@click.argument("citekey", shell_complete=complete_ref)
@require_init
def edit(citekey: str):
    """Open reference in editor.

    Examples:
        cor ref edit smith2024neural
    """
    notes_dir = get_notes_dir()
    ref_path = get_ref_path(citekey, notes_dir)

    if not ref_path.exists():
        raise NotFoundError(f"Reference not found: {citekey}")

    open_in_editor(ref_path)


@click.command(short_help="Delete a reference")
@click.argument("citekey", shell_complete=complete_ref)
@click.option("--force", "-f", is_flag=True, help="Delete without confirmation")
@require_init
def delete(citekey: str, force: bool):
    """Delete a reference.

    Examples:
        cor ref del smith2024neural
        cor ref del -f smith2024neural
    """
    notes_dir = get_notes_dir()
    ref_path = get_ref_path(citekey, notes_dir)
    entry = get_bib_entry(notes_dir, citekey)

    if not ref_path.exists() and not entry:
        raise NotFoundError(f"Reference not found: {citekey}")

    if not force:
        # Show details from the authoritative .bib entry for confirmation.
        title = (entry.get("title") if entry else None) or citekey
        authors_str = (entry.get("author") if entry else None) or "Unknown"
        authors = [a.strip() for a in authors_str.split(" and ") if a.strip()]
        log_info(f"Delete reference: {title}")
        log_info(f"  Authors: {', '.join(authors) if authors else 'Unknown'}")
        if not click.confirm("Are you sure?"):
            log_info("Cancelled.")
            return

    if ref_path.exists():
        ref_path.unlink()
    remove_bib_entry(notes_dir, citekey)
    log_info(f"Deleted: ref/{citekey}")


def _search_references(entries: list, query: str) -> list[tuple]:
    """Rank bib entries by fuzzy relevance to a query.

    Each entry is scored (0-100) against its citekey, author, title, year,
    journal and abstract. Returns ``(entry, score)`` pairs sorted by descending
    score, keeping substring hits and fuzzy matches at or above a relevance cut.
    """
    from rapidfuzz import fuzz

    q = (query or "").strip().lower()
    if not q:
        return []

    scored = []
    for e in entries:
        haystack = " ".join(
            str(e.get(field, ""))
            for field in ("ID", "author", "title", "year", "journal", "abstract")
        ).lower()
        if not haystack.strip():
            continue
        if q in haystack:
            # Exact (case-insensitive) substring hit.
            score = 100
        else:
            # Fuzzy fallback for typos only — keep the bar high so unrelated
            # entries (e.g. "attention" vs "fermentation") are not matched.
            score = fuzz.token_set_ratio(q, haystack)
            if score < 85:
                continue
        scored.append((e, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored



@click.command(short_help="Search references by text")
@click.argument("query")
@click.option("--limit", "-n", type=int, default=20, help="Max results to show")
@require_init(write=False)
def search(query: str, limit: int):
    """Search references by citekey, authors, title, or abstract.

    Examples:
        cor ref search transformer
        cor ref search "Smith 2024"
        cor ref search neural -n 50
    """
    notes_dir = get_notes_dir()
    q = (query or "").strip()
    if not q:
        raise ValidationError("Empty query.")

    entries = list_bib_entries(notes_dir)
    if not entries:
        log_info("No references found. Add one with: cor ref add <doi>")
        return

    results = _search_references(entries, q)
    results = results[:limit]

    if not results:
        log_info("No matches.")
        return

    for e, score in results:
        authors_str = e.get("author") or "Unknown"
        authors = authors_str.split(" and ") if authors_str else []
        author_display = authors[0] if authors else "Unknown"
        if len(authors) > 1:
            author_display += " et al."
        if len(author_display) > 20:
            author_display = author_display[:17] + "..."

        title = e.get("title") or "Untitled"
        if len(title) > 30:
            title = title[:27] + "..."

        year = e.get("year") or "n.d."

        log_info(f"{e.get('ID',''):<25} {int(score):>5}   {year:<6} {author_display:<20} {title:<30}")


# Create command group
@click.group()
def ref():
    """Manage bibliography references.

    References are stored as markdown notes in ref/ with BibLaTeX-compatible
    metadata. Each reference can include abstract, user notes, and tags.

    Citation format: [AuthorYear](ref/citekey)
    """
    pass


# Register subcommands
ref.add_command(add)
ref.add_command(list_refs, name="list")
ref.add_command(show)
ref.add_command(edit)
ref.add_command(delete, name="del")
ref.add_command(search)


@click.command(short_help="Validate bibliography metadata across all refs")
@require_init(write=False)
def validate_refs():
    """Validate all references in references.bib and matching ref markdown files.

    Checks for missing: title, year, authors, journal, volume/issue, abstract.

    Example:
        cor ref validate
    """
    notes_dir = get_notes_dir()
    entries = list_bib_entries(notes_dir)

    if not entries:
        log_info("No references found. Add one with: cor ref add <doi>")
        return

    total = len(entries)
    problems = []

    for e in entries:
        missing = []
        citekey = e.get("ID", "")

        # Required: title, year, authors
        if not e.get("title"):
            missing.append("title")
        if not e.get("year"):
            missing.append("year")
        authors_str = e.get("author") or ""
        authors = [a.strip() for a in authors_str.split(" and ") if a.strip()]
        if not authors:
            missing.append("authors")

        # Recommended: journal
        if not e.get("journal"):
            missing.append("journal")

        # Recommended: volume or issue (number)
        if not e.get("volume") and not e.get("number"):
            missing.append("volume/issue")

        # Abstract from markdown ref file
        ref_path = get_ref_path(citekey, notes_dir)
        abstract_missing = True
        if ref_path.exists():
            try:
                text = ref_path.read_text()
                lines = text.splitlines()
                in_abs = False
                abstract_content = ""
                for ln in lines:
                    if ln.strip().lower().startswith("## abstract"):
                        in_abs = True
                        abstract_content = ""
                        continue
                    if in_abs and ln.strip().startswith("## "):
                        break
                    if in_abs:
                        abstract_content += ln.strip() + "\n"
                abstract_content = abstract_content.strip()
                if abstract_content and "abstract not available" not in abstract_content.lower():
                    abstract_missing = False
            except Exception:
                pass
        if abstract_missing:
            missing.append("abstract")

        if missing:
            problems.append((citekey, missing))

    for citekey, missing in problems:
        log_info(click.style(f"{citekey}: missing {', '.join(missing)}", fg="red"))

    # Summary
    if problems:
        log_info(f"\nChecked {total} refs: {len(problems)} need attention.")
    else:
        log_info(click.style(f"\nChecked {total} refs: all good.", fg="green"))


# Register validate command
ref.add_command(validate_refs, name="validate")

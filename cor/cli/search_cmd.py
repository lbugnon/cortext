"""Search commands for Cortex CLI."""

import json

import click

from ..ui.theme import rule
from ..search.content import list_notes

from . import cli
from cor.search import search_content, parse_search_query, filter_matches, SearchMatch
from ..utils import require_init, get_notes_dir

# How many raw ripgrep matches to pull before applying metadata filters.
# Filters can reject the vast majority of hits, so the pool has to be much
# larger than the user-facing result limit.
SEARCH_FILTER_POOL = 2000


def _format_match(match: SearchMatch, query: str, show_context: bool = True) -> str:
    """Format a search match for display.

    Args:
        match: SearchMatch object
        query: Original search query (for highlighting)
        show_context: Whether to include context lines

    Returns:
        Formatted string for display
    """
    # Determine if archived
    is_archived = "archive" in match.file.parts
    archive_marker = " [archived]" if is_archived else ""

    # File name (relative to vault)
    notes_dir = get_notes_dir()
    try:
        rel_path = match.file.relative_to(notes_dir)
        file_display = str(rel_path)
    except ValueError:
        file_display = match.file.name

    # Highlight matching text
    content = match.content
    # Simple case-insensitive highlight
    lower_query = query.lower()
    lower_content = content.lower()
    if lower_query in lower_content and query:
        start = lower_content.index(lower_query)
        end = start + len(query)
        highlighted = (
            content[:start]
            + click.style(content[start:end], fg="green", bold=True)
            + content[end:]
        )
    else:
        highlighted = content

    lines = []

    # Header: file:line
    lines.append(
        click.style(f"{file_display}:{match.line}", fg="cyan", bold=True)
        + click.style(archive_marker, fg="yellow")
    )

    if show_context:
        # Context before
        for ctx_line in match.context_before:
            lines.append(f"  {click.style('|', fg='bright_black')} {ctx_line}")

        # Match line
        lines.append(f"  {click.style('|', fg='bright_black')} {highlighted}")

        # Context after
        for ctx_line in match.context_after:
            lines.append(f"  {click.style('|', fg='bright_black')} {ctx_line}")

        lines.append("")  # Empty line between results

    return "\n".join(lines)


def _list_notes_by_filters(filters: dict, archived: bool, limit: int, as_json: bool):
    """List notes matching metadata filters, one line per note.

    Used when the query carries filters but no text to grep for. Prints
    status, title, tags and stem so the result is directly usable as a link
    target, rather than the raw `path:line` grep output.
    """
    from ..schema import STATUS_SYMBOLS, get_status_symbol

    notes_dir = get_notes_dir()
    results = list_notes(notes_dir, filters, include_archived=archived)

    if as_json:
        payload = [
            {
                "stem": note.path.stem,
                "type": note.note_type,
                "status": note.status,
                "archived": "archive" in note.path.parts,
                "tags": note.tags or [],
                "parent": note.path.stem.rsplit(".", 1)[0]
                if "." in note.path.stem else None,
            }
            for note in results[:limit]
        ]
        click.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return

    if not results:
        click.echo("No notes found matching filters.")
        return

    total = len(results)
    for note in results[:limit]:
        # Task statuses have checkbox symbols; project statuses (planning,
        # paused, ...) don't, so show the word instead of an empty box.
        if note.status in STATUS_SYMBOLS:
            symbol = get_status_symbol(note.status)
        elif note.status:
            symbol = f"[{note.status}]"
        else:
            symbol = "[ ]"
        tags = " ".join(f"#{t}" for t in (note.tags or []))
        line = f"{click.style(symbol, fg='cyan')} {note.title}"
        if tags:
            line += f" {click.style(tags, fg='yellow')}"
        line += f" {click.style(note.path.stem, fg='bright_black')}"
        click.echo(line)

    click.echo(rule())
    shown = min(total, limit)
    suffix = f" (of {total})" if total > shown else ""
    click.echo(f"{shown} note" + ("s" if shown != 1 else "") + suffix)


@cli.command()
@click.option("--archived", "-a", is_flag=True, help="Include archived files in search")
@click.option("--limit", "-n", type=int, default=20, help="Maximum number of results")
@click.option("--no-context", is_flag=True, help="Hide context lines (compact output)")
@click.option("--project", help="Filter by exact root project stem.")
@click.option("--status", help="Filter by exact status.")
@click.option("--type", "note_type", type=click.Choice(["project", "task", "note"]))
@click.option("--all", "list_all", is_flag=True, help="List entries without search text.")
@click.option("--json", "as_json", is_flag=True, help="Emit compact JSON.")
@click.argument("query", required=False, default="")
@require_init(write=False)
def search(
    archived: bool,
    limit: int,
    no_context: bool,
    project: str | None,
    status: str | None,
    note_type: str | None,
    list_all: bool,
    as_json: bool,
    query: str,
):
    """Search content across all notes.

    Full-text search using ripgrep. Supports filters:

    \b
    Filters:
      status:VALUE     Filter by status (e.g., status:active)
      #TAG             Filter by tag (e.g., #urgent)
      project:NAME     Filter by project (e.g., project:foundation_model)

    \b
    Examples:
      cor search "neural network"              # Simple text search
      cor search "status:active ML"            # With filters
      cor search "#urgent"                     # Tag search
      cor search "neural status:active"        # Combined
      cor search "neural" -a -n 50             # Include archived, 50 results
      cor search "TODO" --no-context           # Compact output
    """
    # Parse query for filters
    text_query, filters = parse_search_query(query)
    filters.update({
        key: value for key, value in {
            "project": project, "status": status, "type": note_type,
        }.items() if value is not None
    })

    if not text_query and not filters and not list_all:
        click.echo("Error: Empty query. Provide search text or filters.", err=True)
        return

    # Show what we're searching for
    if as_json:
        pass
    elif text_query and filters:
        filter_str = ", ".join(f"{k}={v}" for k, v in filters.items())
        click.echo(f"Searching for '{click.style(text_query, bold=True)}' with filters: {filter_str}")
    elif filters:
        filter_str = ", ".join(f"{k}={v}" for k, v in filters.items())
        click.echo(f"Searching with filters: {filter_str}")
    else:
        click.echo(f"Searching for '{click.style(text_query, bold=True)}'")
    if not as_json:
        click.echo()

    # Filter-only query (e.g. `cor search "status:active"`): there is no text to
    # grep for, so an empty ripgrep pattern would match every line of every note
    # and the output would be a line dump. List matching notes instead.
    if not text_query:
        _list_notes_by_filters(filters, archived, limit, as_json)
        return

    # Search content.
    #
    # When metadata filters are in play the limit must NOT be applied here:
    # filtering happens below, so truncating the ripgrep stream first would
    # discard candidates that the filters were going to keep. That made
    # `cor search "status:done"` report no matches at the default limit while
    # a large -n found plenty. Fetch a generous pool and truncate after filtering.
    fetch_limit = max(limit, SEARCH_FILTER_POOL) if filters else limit
    try:
        matches = search_content(
            query=text_query,
            include_archived=archived,
            limit=fetch_limit,
            context_lines=2 if not no_context else 0,
        )
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        return

    if not matches:
        click.echo("[]" if as_json else "No matches found.")
        return

    # Apply metadata filters
    if filters:
        matches = filter_matches(matches, filters)

    if not matches:
        click.echo("[]" if as_json else "No matches found after applying filters.")
        return

    # Limit results
    matches = matches[:limit]

    if as_json:
        from ..core.notes import parse_metadata

        payload = []
        for match in matches:
            note = parse_metadata(match.file)
            payload.append({
                "stem": match.file.stem,
                "type": note.note_type if note else None,
                "status": note.status if note else None,
                "archived": "archive" in match.file.parts,
                "line": match.line,
                "text": match.content,
                "before": match.context_before,
                "after": match.context_after,
            })
        click.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return

    # Display results
    show_context = not no_context
    for match in matches:
        click.echo(_format_match(match, text_query, show_context))

    # Summary
    total_str = f"{len(matches)} result" + ("s" if len(matches) != 1 else "")
    click.echo(rule())
    click.echo(f"{total_str}")

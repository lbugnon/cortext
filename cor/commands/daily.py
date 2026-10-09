"""``cor daily``: forecast (overdue / today / upcoming) plus ranked next actions.

All the deciding happens in :mod:`cor.core.agenda`; this module only parses
options, gathers vault state, and renders either coloured text or the compact
JSON contract that automation consumes.
"""

from __future__ import annotations

from datetime import datetime

import click

from ..completions import complete_project
from ..config import get_focused_project, load_config
from ..core.agenda import Agenda, AgendaItem, build_agenda, count_inbox_items, resolve_weights
from ..core.notes import due_to_iso, find_notes
from ..core.transactions import vault_revision
from ..ui import theme
from ..utils import get_notes_dir, require_init
from .agent import _emit_json, _machine_errors
from .status import _extract_description

TITLE_WIDTH = 40
NEXT_TITLE_WIDTH = 32
NEXT_PREFIX_WIDTH = 2 + 2 + 2 + NEXT_TITLE_WIDTH + 1  # "  NN. <title> "
PROJECT_GLYPH = "◆"


# --- small formatting helpers -------------------------------------------------

def _style(text: str, **kwargs) -> str:
    """click.style that honours the NO_COLOR convention like the rest of the UI."""
    return text if theme.no_color_requested() else click.style(text, **kwargs)


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(width - 1, 0)] + "…"


def _pad(text: str, width: int, align: str = "<") -> str:
    return f"{text:{align}{width}}"


def _priority_str(priority: str | None, width: int = 6) -> str:
    plain = _pad(priority or "", width)
    if priority == "high":
        return _style(plain, fg="magenta", bold=True)
    if priority == "medium":
        return _style(plain, fg=theme.WARN)
    if priority == "low":
        return _style(plain, fg=theme.DIM)
    return plain


def _symbol(item: AgendaItem) -> str:
    if item.note_type == "project":
        return _style(f" {PROJECT_GLYPH} ", fg=theme.project_color(item.status))
    symbol, color = theme.status_style(item.status)
    return _style(symbol, fg=color)


def _display_title(item: AgendaItem, width: int) -> str:
    """Title padded to ``width``; a due time is appended and never truncated away."""
    if item.due_has_time and item.due:
        clock = item.due.strftime("%H:%M")
        return _pad(f"{_truncate(item.title, width - len(clock) - 1)} {clock}", width)
    return _pad(_truncate(item.title, width), width)


def _where(item: AgendaItem) -> str:
    if item.note_type == "project":
        return "(project due)"
    return item.parent or item.project


def _description_lines(item: AgendaItem, verbose: bool, indent: int) -> list[str]:
    if not verbose or item.note is None:
        return []
    description = _extract_description(item.note)
    return [" " * indent + _style(description, fg=theme.DIM)] if description else []


# --- sections -----------------------------------------------------------------

def _header(agenda: Agenda) -> str:
    day = _style(agenda.date.strftime("%a %d %b"), bold=True)
    if agenda.tag:
        label = "Focusing on" if agenda.tag == agenda.focus else "Filter"
        return f"{day}    {_style(f'[{label}: {agenda.tag}]', fg=theme.ACCENT, bold=True)}"
    return day


def _day_label(item: AgendaItem) -> str:
    days = item.days_until_due
    if days is None or days == 0 or item.due is None:
        return ""
    if days < 0:
        return f"{-days}d"
    if days <= 6:
        return item.due.strftime("%a")
    return item.due.strftime("%d %b")


def _timeline_section(title: str, color: str | None, items: list[AgendaItem], verbose: bool) -> list[str]:
    if not items:
        return []
    lines = ["", _style(title, fg=color, bold=True) if color else _style(title, bold=True)]
    for item in items:
        label = _pad(_day_label(item), 4, ">")
        if color:
            label = _style(label, fg=color)
        blocks = _pad(f"blocks {len(item.blocks)}" if item.blocks else "", 9)
        lines.append(
            f"  {label}  {_symbol(item)} {_display_title(item, TITLE_WIDTH)} "
            f"{_priority_str(item.priority)} {blocks} {_style(_where(item), fg=theme.DIM)}"
        )
        lines += _description_lines(item, verbose, indent=10)
    return lines


def _explain(item: AgendaItem) -> str:
    parts = [f"{name} {value:g}" for name, value in item.urgency_components.items() if value]
    return f"{item.urgency:g} = " + " + ".join(parts) if parts else f"{item.urgency:g}"


def _next_section(items: list[AgendaItem], limit: int, explain: bool, verbose: bool) -> list[str]:
    if not items:
        return []
    header = _style(_pad("NEXT", NEXT_PREFIX_WIDTH), bold=True) + _style("why", fg=theme.DIM)
    lines = ["", header]
    shown = items[:limit] if limit else items
    for index, item in enumerate(shown, 1):
        title = _pad(_truncate(item.title, NEXT_TITLE_WIDTH), NEXT_TITLE_WIDTH)
        reasons = " · ".join(item.reasons) if item.reasons else _style("no signals", fg=theme.DIM)
        lines.append(f"  {index:>2}. {title} {reasons}")
        if explain:
            lines.append(" " * NEXT_PREFIX_WIDTH + _style(_explain(item), fg=theme.DIM))
        lines += _description_lines(item, verbose, indent=NEXT_PREFIX_WIDTH)
    remaining = len(items) - len(shown)
    if remaining > 0:
        lines.append(_style(f"  ... {remaining} more (cor daily -a)", fg=theme.DIM))
    return lines


def _follow_up_rows(agenda: Agenda) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    for item in agenda.waiting:
        age = f"{item.days_since_modified}d" if item.days_since_modified is not None else "?"
        due = f"due {due_to_iso(item.due)}" if item.due else "no due"
        rows.append(("waiting", theme.status_color("waiting"), item.title, f"{age}, {due}", _where(item)))
    for item in agenda.blocked:
        info = f"needs {', '.join(item.blocked_by)}" if item.blocked_by else "marked blocked"
        rows.append(("blocked", theme.status_color("blocked"), item.title, info, _where(item)))
    for project in agenda.stuck_projects:
        last = f"last activity {project.last_activity:%Y-%m-%d}" if project.last_activity else ""
        rows.append(("stuck", theme.WARN, project.title, "active project, no next action", last))
    if agenda.inbox_count:
        noun = "item" if agenda.inbox_count == 1 else "items"
        rows.append(("inbox", theme.ACCENT, f"{agenda.inbox_count} unprocessed {noun}", "", "backlog.md"))
    return rows


def _follow_up_section(agenda: Agenda) -> list[str]:
    rows = _follow_up_rows(agenda)
    if not rows:
        return []
    lines = ["", _style("FOLLOW-UP", fg=theme.WARN, bold=True)]
    for kind, color, title, info, where in rows:
        lines.append(
            f"  {_style(_pad(kind, 8), fg=color)} {_pad(_truncate(title, 24), 24)} "
            f"{_pad(_truncate(info, 30), 30)} {_style(where, fg=theme.DIM)}"
        )
    return lines


def render_text(agenda: Agenda, *, limit: int, explain: bool, verbose: bool) -> str:
    """Render the agenda as the coloured terminal view. ``limit`` 0 means no limit."""
    lines = [_header(agenda)]
    lines += _timeline_section("OVERDUE", theme.ERROR, agenda.overdue, verbose)
    lines += _timeline_section("TODAY", theme.ACCENT, agenda.today, verbose)
    lines += _timeline_section("UPCOMING", None, agenda.upcoming, verbose)
    lines += _next_section(agenda.next_items, limit, explain, verbose)
    if agenda.is_clear:
        lines += ["", _style(
            f"All clear: nothing due in the next {agenda.horizon_days} days and nothing actionable.",
            fg=theme.OK,
        )]
    lines += _follow_up_section(agenda)
    return "\n".join(lines) + "\n"


# --- command ------------------------------------------------------------------

@click.command(short_help="Forecast and ranked next actions for today")
@click.option("--days", "-d", "horizon", default=7, show_default=True,
              type=click.IntRange(0, 365), help="How many days ahead UPCOMING covers")
@click.option("--limit", "-l", default=5, show_default=True, help="Max NEXT items")
@click.option("--all", "-a", "show_all", is_flag=True, help="Show all NEXT items")
@click.option("--explain", is_flag=True, help="Show each NEXT item's urgency score and components")
@click.option("--verbose", "-v", is_flag=True, help="Show task descriptions")
@click.option("--json", "as_json", is_flag=True, help="Emit compact JSON (ignores --limit)")
@click.argument("tag", required=False, shell_complete=complete_project)
@_machine_errors(flag="as_json")
@require_init(write=False)
def daily(horizon: int, limit: int, show_all: bool, explain: bool, verbose: bool,
          as_json: bool, tag: str | None):
    """Show what is due and what to do next.

    \b
    OVERDUE / TODAY / UPCOMING  every open task or project with a due date,
                                by day, with priority and parent
    NEXT                        actionable tasks ranked by urgency, each with
                                the reasons behind its rank
    FOLLOW-UP                   waiting tasks, blocked tasks, active projects
                                with no next action, unprocessed inbox lines

    Urgency adds bounded components: due proximity, priority, active status,
    how many tasks this one unblocks, an active parent project, age, and time
    idle while active. Weights can be overridden under `agenda:` in the config.

    A TAG narrows every section to one project, note tag, or propagated
    project tag; with no tag the focused project (`cor focus`) applies.
    `--json` prints the full result for automation, including the vault
    revision to pass to `cor batch --expect-revision`.
    """
    notes_dir = get_notes_dir()
    now = datetime.now()
    agenda = build_agenda(
        find_notes(notes_dir),
        today=now.date(),
        now=now,
        horizon_days=horizon,
        tag=tag,
        focus=get_focused_project(),
        inbox_count=count_inbox_items(notes_dir / "backlog.md"),
        revision=vault_revision(notes_dir),
        weights=resolve_weights(load_config().get("agenda")),
    )
    if as_json:
        _emit_json(agenda.to_dict())
        return
    click.echo(render_text(agenda, limit=0 if show_all else limit, explain=explain, verbose=verbose), nl=False)

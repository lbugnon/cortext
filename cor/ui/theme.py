"""Semantic colors and symbols - the single source for how status looks.

Symbols come from `schema.STATUS_SYMBOLS` so the on-disk checkbox characters
and the displayed ones cannot drift. Colors live here rather than at the ~191
call sites that used to spell them as bare strings, and rather than in the two
identical maps this replaces (`commands/status.py:TASK_COLORS` and
`tui/colors.py:STATUS_COLORS`).

Color names are deliberately the ones click and rich both understand, so a
token can be handed to `click.style(fg=...)` or used as a rich style without
translation.
"""

from __future__ import annotations

from ..schema import STATUS_SYMBOLS

# Task status -> color.
STATUS_COLORS: dict[str, str] = {
    "todo": "white",
    "active": "cyan",
    "blocked": "red",
    "waiting": "yellow",
    "done": "green",
    "dropped": "magenta",
}

# Project status -> color. A different vocabulary from task status.
PROJECT_COLORS: dict[str, str] = {
    "planning": "blue",
    "active": "green",
    "paused": "yellow",
    "done": "bright_black",
}

# status -> (symbol, color), derived so symbols stay sourced from the schema.
STATUS_STYLES: dict[str, tuple[str, str]] = {
    status: (STATUS_SYMBOLS[status], color)
    for status, color in STATUS_COLORS.items()
}

# Display order for grouped status listings: what needs attention first.
STATUS_ORDER: dict[str, int] = {
    "blocked": 0,
    "active": 1,
    "waiting": 2,
    "todo": 3,
    "dropped": 4,
    "done": 5,
}

# Non-status roles, so "dim grey secondary text" is one decision not fifty.
DIM = "bright_black"
ACCENT = "cyan"
WARN = "yellow"
ERROR = "red"
OK = "green"

_FALLBACK = ("[ ]", "white")


def status_style(status: str | None) -> tuple[str, str]:
    """Return (symbol, color) for a task status, falling back to todo's look."""
    return STATUS_STYLES.get(status or "", _FALLBACK)


def status_symbol(status: str | None) -> str:
    return status_style(status)[0]


def status_color(status: str | None) -> str:
    return status_style(status)[1]


def project_color(status: str | None) -> str:
    return PROJECT_COLORS.get(status or "", "white")


def rule(width: int = 40) -> str:
    """A dim horizontal separator, click-styled.

    Replaces the hand-repeated `click.style("-" * 40, fg="bright_black")`.
    """
    import click

    return click.style("-" * width, fg=DIM)


def no_color_requested() -> bool:
    """True when the NO_COLOR convention says to emit no color.

    click does not implement https://no-color.org itself: `click.style` emits
    escapes regardless, and `click.echo` only strips them when the stream is
    not a terminal. rich *does* honour it, so without this the tree output and
    everything else would disagree on a colour-free terminal.
    """
    import os

    return bool(os.environ.get("NO_COLOR"))

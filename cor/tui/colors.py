"""Status colors and styles for the TUI.

Symbols are sourced from ``schema.STATUS_SYMBOLS`` (single source of truth);
this module only adds the per-status colors and the keyboard bindings used by
the interactive tree.
"""

from ..schema import STATUS_SYMBOLS

# Single source of the display color per status.
STATUS_COLORS: dict[str, str] = {
    "done": "green",
    "blocked": "red",
    "active": "cyan",
    "waiting": "yellow",
    "dropped": "magenta",
    "todo": "white",
}

# status -> (symbol, color), derived from the schema symbols.
STATUS_STYLES: dict[str, tuple[str, str]] = {
    status: (STATUS_SYMBOLS[status], color)
    for status, color in STATUS_COLORS.items()
}

# Keyboard key -> status (TUI status-change bindings).
STATUS_KEY_BINDINGS: dict[str, str] = {
    "x": "done",
    "o": "blocked",
    ".": "active",
    "/": "waiting",
    "~": "dropped",
    "backspace": "todo",
}

"""Status colors and TUI key bindings.

Colors and symbols come from ``cor.ui.theme`` (the single source); this module
adds only the keyboard bindings, which are specific to the interactive tree.
The ``STATUS_COLORS`` / ``STATUS_STYLES`` re-exports are kept so existing
importers keep working.
"""

from ..ui.theme import STATUS_COLORS, STATUS_STYLES  # noqa: F401 - re-exported

# Keyboard key -> status (TUI status-change bindings).
STATUS_KEY_BINDINGS: dict[str, str] = {
    "x": "done",
    "o": "blocked",
    ".": "active",
    "/": "waiting",
    "~": "dropped",
    "backspace": "todo",
}

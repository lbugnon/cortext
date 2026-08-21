"""Static tree rendering for terminal output.

`commands/status.py` used to hand-draw its own guides, computing `"├── "` /
`"└── "` branches and `"│   "` continuation prefixes as it recursed. The
interactive tree (`tui/tree_app.py`) solved the same problem with Textual's
Tree widget. Two implementations of tree drawing, one of them by hand.

rich draws the guides here, so the CLI and the TUI both delegate that job to a
library instead of one of them re-deriving it. What the two genuinely share -
which symbol and which color a status gets - lives in `cor.ui.theme`.

rich is not a new dependency: `textual` already requires `rich>=14.2.0`.
"""

from __future__ import annotations

from .theme import DIM

# The hand-rolled renderer emitted lines through click.echo, which never
# wrapped or truncated - long titles simply ran on and the terminal soft-wrapped
# them. rich, by contrast, defaults to width 80 when stdout is not a tty and
# *crops* what does not fit, which would silently eat long titles. A width this
# large disables both; combined with soft_wrap it adds no padding either.
_NO_WRAP_WIDTH = 10_000

# Status symbols are literally '[x]', '[.]', '[o]' - valid rich markup tags.
# Everything here passes `rich.text.Text` and builds Consoles with
# markup=False so they are never parsed as styling.


def new_tree(label: str = "", *, hide_root: bool = False):
    """A tree whose guides are dim, matching the old `click.style(dim=True)`.

    `hide_root` defaults to False even though callers pass an empty label,
    because rich draws no guides for the children of a *hidden* root - the
    first level would come out flush-left, while the hand-rolled renderer gave
    it `|-- ` guides like every other level. Keeping the root visible restores
    them; `print_tree` then drops the blank line the empty label produces.
    """
    from rich.text import Text
    from rich.tree import Tree

    return Tree(
        Text(label) if isinstance(label, str) else label,
        hide_root=hide_root,
        guide_style=DIM,
    )


def click_text(styled: str):
    """Adopt a click-styled string as rich Text.

    `click.style()` always emits ANSI escapes; it is `click.echo` that strips
    them when stdout is not a terminal. Parsing them back into rich styles lets
    the existing render callbacks keep working unchanged while rich makes the
    final decision about whether to emit color - which is how NO_COLOR and
    non-tty output now degrade correctly.
    """
    from rich.text import Text

    return Text.from_ansi(styled)


def print_tree(tree) -> None:
    """Write a tree to stdout, without wrapping, via click.echo."""
    import io
    import sys

    import click
    from rich.console import Console

    buffer = io.StringIO()
    # Rendered into a buffer rather than straight to stdout for two reasons:
    # the empty root label emits a leading blank line that has to come off, and
    # going through click.echo keeps output ordering consistent with the ~530
    # other click.echo calls (a separate Console would flush independently).
    #
    # Writing to a buffer would normally make rich drop color, since the file
    # is not a terminal - so terminal-ness is mirrored from the real stdout.
    # NO_COLOR is still honoured, because rich checks it independently.
    console = Console(
        file=buffer,
        force_terminal=True if _stdout_is_tty() else None,
        markup=False,   # '[x]' must stay literal, not become a style tag
        emoji=False,    # ':' sequences in titles must stay literal too
        highlight=False,  # no automatic number/URL highlighting
        soft_wrap=True,  # never pad or wrap; see _NO_WRAP_WIDTH
        width=_NO_WRAP_WIDTH,
    )
    console.print(tree)

    rendered = buffer.getvalue()
    if rendered.startswith("\n"):
        rendered = rendered[1:]
    click.echo(rendered.rstrip("\n"))


def _stdout_is_tty() -> bool:
    import sys

    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False

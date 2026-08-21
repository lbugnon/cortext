"""Presentation layer: colors, symbols, and shared renderers.

Output formatting used to be spread across ~530 `click.echo`/`click.style`
calls with 191 hardcoded color names, and the status->color map existed twice
(`commands/status.py` and `tui/colors.py`) with identical contents. This
package is the single place those decisions live.
"""

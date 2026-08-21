"""Tests for the presentation layer (cor/ui)."""

import io
import contextlib

import pytest


class TestThemeIsTheSingleSource:
    """Status colors used to exist twice, identically, in two modules."""

    def test_symbols_come_from_the_schema(self):
        from cor.schema import STATUS_SYMBOLS
        from cor.ui.theme import STATUS_STYLES

        for status, (symbol, _color) in STATUS_STYLES.items():
            assert symbol == STATUS_SYMBOLS[status]

    def test_every_status_has_a_color(self):
        from cor.schema import STATUS_SYMBOLS
        from cor.ui.theme import STATUS_COLORS

        assert set(STATUS_COLORS) == set(STATUS_SYMBOLS)

    def test_unknown_status_falls_back_to_todo_look(self):
        from cor.ui.theme import status_style

        assert status_style("nonsense") == ("[ ]", "white")
        assert status_style(None) == ("[ ]", "white")

    def test_tui_colors_reexports_the_same_objects(self):
        """The TUI map must be the theme's, not a copy that can drift."""
        from cor.tui.colors import STATUS_COLORS as tui_colors
        from cor.ui.theme import STATUS_COLORS as theme_colors

        assert tui_colors is theme_colors

    def test_status_module_uses_the_theme(self):
        from cor.commands.status import TASK_COLORS
        from cor.ui.theme import STATUS_COLORS

        assert TASK_COLORS is STATUS_COLORS


class TestTreeRendering:
    def _render(self, build):
        from cor.ui.tree import new_tree, print_tree

        tree = new_tree()
        build(tree)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_tree(tree)
        return buf.getvalue()

    def test_first_level_gets_guides(self):
        """A hidden root would render level 1 flush-left; the old renderer did not."""
        from rich.text import Text

        out = self._render(lambda t: t.add(Text("[ ] first")))
        assert "├── " in out or "└── " in out

    def test_no_leading_blank_line(self):
        from rich.text import Text

        out = self._render(lambda t: t.add(Text("[ ] first")))
        assert not out.startswith("\n")

    def test_long_labels_are_not_truncated(self):
        """rich crops at width 80 off a tty by default; that would eat titles."""
        from rich.text import Text

        long = "x" * 300
        out = self._render(lambda t: t.add(Text(f"[ ] {long}")))
        assert long in out

    def test_status_symbols_are_not_parsed_as_markup(self):
        """'[x]' and '[.]' are valid rich markup tags; they must stay literal."""
        from rich.text import Text

        out = self._render(lambda t: t.add(Text("[x] done thing")))
        assert "[x]" in out

    def test_nesting_indents(self):
        from rich.text import Text

        def build(t):
            child = t.add(Text("[ ] parent"))
            child.add(Text("[ ] kid"))

        out = self._render(build)
        assert "│" in out or "    " in out
        assert "kid" in out

    def test_click_text_keeps_content_of_styled_string(self):
        import click

        from cor.ui.tree import click_text

        text = click_text(click.style("[o] blocked", fg="red"))
        assert "[o] blocked" in text.plain


class TestNoColor:
    def test_no_color_detected(self, monkeypatch):
        from cor.ui.theme import no_color_requested

        monkeypatch.delenv("NO_COLOR", raising=False)
        assert no_color_requested() is False
        monkeypatch.setenv("NO_COLOR", "1")
        assert no_color_requested() is True

    def test_cli_disables_color_when_no_color_set(self, monkeypatch, temp_vault):
        """click does not implement no-color.org itself, so the group must."""
        import click
        from click.testing import CliRunner

        from cor.cli import cli

        seen = {}

        @cli.command(name="_test_color_probe")
        @click.pass_context
        def probe(ctx):
            seen["color"] = ctx.color

        try:
            monkeypatch.setenv("NO_COLOR", "1")
            CliRunner().invoke(cli, ["_test_color_probe"])
            assert seen["color"] is False

            seen.clear()
            monkeypatch.delenv("NO_COLOR", raising=False)
            CliRunner().invoke(cli, ["_test_color_probe"])
            assert seen["color"] is not False
        finally:
            cli.commands.pop("_test_color_probe", None)


class TestRule:
    def test_rule_is_dim_and_sized(self):
        from cor.ui.theme import DIM, rule

        out = rule(10)
        assert "-" * 10 in out
        # bright_black is the dim token both click and rich understand
        assert DIM == "bright_black"

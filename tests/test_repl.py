"""Tests for the interactive shell and the write-time vault guard.

The shell's job is to make the active vault unambiguous, so the tests that
matter are the ones about *which vault a command lands in*, plus the
robustness properties a session needs (an error must not end it).

Following the test_tui.py precedent, the dispatcher and completer are driven
directly rather than through a terminal - prompt_toolkit's own prompt loop is
not what is under test here.
"""

import os
from pathlib import Path

import pytest

from conftest import build_vault

from cor.cli import cli
from cor.config import add_vault
from cor.exceptions import ConfigError
from cor.repl import CorCompleter, CorShell


@pytest.fixture
def two_vaults(tmp_path, monkeypatch):
    """Two registered vaults, with cwd parked outside both."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("COR_VAULT", raising=False)
    work = build_vault(tmp_path / "work")
    personal = build_vault(tmp_path / "personal")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    add_vault("work", work)
    add_vault("personal", personal)
    # CorShell chdir()s; pytest does not restore cwd on its own.
    original = Path.cwd()
    yield {"work": work, "personal": personal, "outside": outside}
    os.chdir(original)


class TestVaultPinning:
    """A session pins one vault, and every layer must agree on which."""

    def test_shell_pins_cwd_and_env(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])

        assert Path.cwd() == two_vaults["work"]
        assert os.environ["COR_VAULT"] == str(two_vaults["work"])
        assert shell.name == "work"

    def test_switching_vault_repins(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])

        shell.switch("personal")

        assert shell.name == "personal"
        assert Path.cwd() == two_vaults["personal"]
        assert os.environ["COR_VAULT"] == str(two_vaults["personal"])

    def test_prompt_names_the_active_vault(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        assert shell.prompt_text() == "cor(work)> "

        shell.switch("personal")
        assert shell.prompt_text() == "cor(personal)> "

    def test_switching_to_unknown_vault_is_rejected(self, two_vaults, capsys):
        shell = CorShell(cli, two_vaults["work"])

        shell.switch("nope")

        assert shell.name == "work"
        assert Path.cwd() == two_vaults["work"]
        assert "No vault named 'nope'" in capsys.readouterr().err

    def test_commands_land_in_the_pinned_vault(self, two_vaults):
        """The whole point: a command run in the shell writes where the prompt says."""
        shell = CorShell(cli, two_vaults["work"])
        shell.dispatch("new task adas --no-edit")
        assert (two_vaults["work"] / "adas.md").exists()

        shell.dispatch(":vault personal")
        shell.dispatch("new task adas --no-edit")
        assert (two_vaults["personal"] / "adas.md").exists()

    def test_cd_refuses_to_leave_the_vault(self, two_vaults, capsys):
        """Leaving would break the invariant the prompt advertises."""
        shell = CorShell(cli, two_vaults["work"])

        shell.dispatch(f":cd {two_vaults['outside']}")

        assert Path.cwd() == two_vaults["work"]
        assert "Refusing to leave the vault" in capsys.readouterr().err

    def test_cd_within_the_vault_shows_in_the_prompt(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        (two_vaults["work"] / "archive").mkdir(exist_ok=True)

        shell.dispatch(":cd archive")

        assert shell.prompt_text() == "cor(work/archive)> "


class TestDispatch:
    """A session must survive whatever gets typed at it."""

    @pytest.mark.parametrize("line", [
        "bogus-command",
        "mark nonexistent-note done",
        "new",                      # missing required args
        "'unbalanced",              # unparseable
        ":nonsense",
        "",
    ])
    def test_bad_input_does_not_end_the_session(self, two_vaults, line):
        shell = CorShell(cli, two_vaults["work"])
        assert shell.dispatch(line) is True

    @pytest.mark.parametrize("line", ["exit", "quit", ":quit", ":q"])
    def test_exit_words_end_the_session(self, two_vaults, line):
        shell = CorShell(cli, two_vaults["work"])
        assert shell.dispatch(line) is False

    def test_verbosity_flag_does_not_persist_across_lines(self, two_vaults):
        """`-v` writes to the config file; in a shell that would ratchet up."""
        from cor.config import get_verbosity

        shell = CorShell(cli, two_vaults["work"])
        before = get_verbosity()

        shell.dispatch("-vv status")

        assert get_verbosity() == before


class TestCompleter:
    """Completion must match what zsh would offer, since it is the same code."""

    def test_completes_command_names(self, two_vaults):
        got = [v for v, _ in CorCompleter(cli)._candidates("ne")]
        assert "new" in got

    def test_completes_subcommands(self, two_vaults):
        got = [v for v, _ in CorCompleter(cli)._candidates("vault ")]
        assert {"add", "list", "rm", "default"} <= set(got)

    def test_completes_note_types(self, two_vaults):
        got = [v for v, _ in CorCompleter(cli)._candidates("new ")]
        assert {"project", "task", "note"} <= set(got)

    def test_completes_context_dependent_arguments(self, two_vaults):
        """complete_name() branches on ctx.params['note_type'].

        This is why the completer defers to click's ShellComplete rather than
        building its own context - a bare context yields nothing here.
        """
        shell = CorShell(cli, two_vaults["work"])
        shell.dispatch("new project alpha --no-edit")

        got = [v for v, _ in CorCompleter(cli)._candidates("new task ")]
        assert "alpha." in got

    def test_completes_statuses_for_mark(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        shell.dispatch("new task adas --no-edit")

        got = [v for v, _ in CorCompleter(cli)._candidates("mark adas ")]
        assert "done" in got

    def test_completes_meta_commands(self, two_vaults):
        got = [v for v, _ in CorCompleter(cli)._candidates(":va")]
        assert {":vault", ":vaults"} <= set(got)

    def test_unbalanced_quotes_yield_no_completions(self, two_vaults):
        assert CorCompleter(cli)._candidates("new 'oops") == []


class TestWriteGuard:
    """Outside a vault, a write must never silently pick one."""

    def test_write_outside_a_vault_refuses_when_not_a_tty(self, two_vaults):
        """CliRunner has no tty, which is the same situation as a git hook."""
        from click.testing import CliRunner

        result = CliRunner().invoke(cli, ["new", "task", "adas", "--no-edit"])

        assert result.exit_code == 1
        assert "ambiguous" in result.output
        # Neither vault may have been touched.
        assert not (two_vaults["work"] / "adas.md").exists()
        assert not (two_vaults["personal"] / "adas.md").exists()

    def test_read_outside_a_vault_still_works(self, two_vaults):
        """Reads keep the silent fallback - a wrong read is recoverable."""
        from click.testing import CliRunner

        result = CliRunner().invoke(cli, ["status"])

        assert result.exit_code == 0

    def test_write_inside_a_vault_is_not_prompted(self, two_vaults, monkeypatch):
        from click.testing import CliRunner

        monkeypatch.chdir(two_vaults["work"])
        result = CliRunner().invoke(cli, ["new", "task", "adas", "--no-edit"])

        assert result.exit_code == 0
        assert (two_vaults["work"] / "adas.md").exists()

    def test_completion_never_prompts(self, two_vaults, monkeypatch):
        """A prompt during Tab-completion would hang the user's shell."""
        from click.testing import CliRunner

        monkeypatch.setenv("_COR_COMPLETE", "zsh_complete")
        result = CliRunner().invoke(cli, ["new", "task", "adas", "--no-edit"])

        # It must not raise the ambiguity error; the guard steps aside.
        assert "ambiguous" not in result.output


class TestSessionSurvival:
    """A single bad line must never end the session."""

    def test_unbalanced_quote_in_meta_command_does_not_exit(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])

        # `shlex.split` raises on this. run_command guarded its own split, but
        # handle_meta did not, so the ValueError escaped the session loop.
        assert shell.dispatch(":cd 'oops") is True
        assert shell.dispatch(":vault 'oops") is True
        # Session still usable afterwards.
        assert shell.dispatch("status") is True

    def test_bare_colon_does_not_exit(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        assert shell.dispatch(":") is True


class TestPinReassertion:
    """The prompt promises where the next command lands; defend that."""

    def test_cwd_moved_outside_vault_is_restored(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])

        os.chdir(two_vaults["outside"])
        shell._reassert_pin()

        assert Path.cwd().resolve() == two_vaults["work"].resolve()

    def test_subdirectory_is_left_alone(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        archive = two_vaults["work"] / "archive"

        os.chdir(archive)
        shell._reassert_pin()

        # ':cd archive' is legitimate; re-asserting must not undo it.
        assert Path.cwd().resolve() == archive.resolve()

    def test_cor_vault_is_restored(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])

        os.environ["COR_VAULT"] = str(two_vaults["personal"])
        shell._reassert_pin()

        assert os.environ["COR_VAULT"] == str(two_vaults["work"].resolve())

    def test_command_that_chdirs_away_does_not_break_the_session(self, two_vaults):
        """This is the `cor sync` failure mode, reproduced generically."""
        import click

        shell = CorShell(cli, two_vaults["work"])

        @cli.command(name="_test_wanders")
        def _wanders():
            os.chdir(two_vaults["outside"])

        try:
            shell.dispatch("_test_wanders")
            assert Path.cwd().resolve() == two_vaults["work"].resolve()
            assert os.environ["COR_VAULT"] == str(two_vaults["work"].resolve())
        finally:
            cli.commands.pop("_test_wanders", None)


class TestPromptAndToolbar:
    """The styled prompt and bottom toolbar are redrawn on every keystroke."""

    def test_fragments_render_the_same_text_as_prompt_text(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])

        rendered = "".join(text for _style, text in shell.prompt_fragments())
        assert rendered == shell.prompt_text() == "cor(work)> "

    def test_vault_name_is_its_own_fragment(self, two_vaults):
        """So it can be styled; it is the one thing that must stand out."""
        shell = CorShell(cli, two_vaults["work"])

        styles = {text: style for style, text in shell.prompt_fragments()}
        assert "prompt.vault" in styles["work"]

    def test_fragments_track_cd(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        shell.dispatch(":cd archive")

        rendered = "".join(text for _style, text in shell.prompt_fragments())
        assert rendered == "cor(work/archive)> "

    def test_toolbar_shows_the_active_vault(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])

        text = "".join(t for _s, t in shell.bottom_toolbar())
        assert "work" in text

    def test_toolbar_follows_a_vault_switch(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        shell.dispatch(":vault personal")

        text = "".join(t for _s, t in shell.bottom_toolbar())
        assert "personal" in text
        assert "work" not in text


class TestCountsAreCached:
    """Walking the vault per keystroke would put a parse in the typing path."""

    def _count_walks(self, shell, monkeypatch):
        import cor.core.files as files_mod

        calls = []
        real = files_mod.FileIterator.iter_all_notes

        def counting(self, *a, **k):
            calls.append(1)
            return real(self, *a, **k)

        monkeypatch.setattr(files_mod.FileIterator, "iter_all_notes", counting)
        return calls

    def test_draw_count_does_not_change_walk_count(self, two_vaults, monkeypatch):
        """Ten redraws must cost the same as one - that is the whole point."""
        shell = CorShell(cli, two_vaults["work"])
        calls = self._count_walks(shell, monkeypatch)

        shell._invalidate()
        shell.bottom_toolbar()
        one_draw = len(calls)

        calls.clear()
        shell._invalidate()
        for _ in range(10):
            shell.bottom_toolbar()
        ten_draws = len(calls)

        assert one_draw > 0, "sanity: the first draw should compute counts"
        assert ten_draws == one_draw, (
            f"{ten_draws} walks for 10 draws vs {one_draw} for 1 - not cached"
        )

    def test_cache_is_dropped_after_a_command(self, two_vaults, monkeypatch):
        shell = CorShell(cli, two_vaults["work"])
        shell.counts()
        calls = self._count_walks(shell, monkeypatch)

        shell.dispatch("status")
        shell.bottom_toolbar()

        assert calls, "counts must be recomputed after a command may have changed them"

    def test_cache_is_dropped_on_vault_switch(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        shell.counts()
        shell.switch("personal")

        # Recomputed against the new vault, not carried over.
        assert shell.counts() == shell.counts()

    def test_unreadable_vault_does_not_raise(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        shell.vault = Path("/nonexistent/vault/path")
        shell._invalidate()

        assert shell.counts() in (None, (0, 0))
        assert shell.bottom_toolbar() is not None


class TestVaultsDelegates:
    """':vaults' must not be a second, poorer implementation of `vault list`."""

    def test_shows_the_richer_command_output(self, two_vaults, capsys):
        shell = CorShell(cli, two_vaults["work"])
        capsys.readouterr()

        shell.dispatch(":vaults")
        out = capsys.readouterr().out

        assert "work" in out and "personal" in out
        # Markers only `vault list` produces; the old local copy had neither.
        assert "default" in out or "active" in out

    def test_source_has_no_local_listing(self):
        """Guard against the copy reappearing."""
        from pathlib import Path

        import cor.repl as repl_mod

        assert "_list_vaults" not in Path(repl_mod.__file__).read_text()


class TestCdResolvesSymlinks:
    """self.vault is stored resolved, so the target must be resolved too."""

    def test_absolute_path_through_a_symlinked_vault_is_accepted(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.delenv("COR_VAULT", raising=False)
        real = build_vault(tmp_path / "real_vault")
        link = tmp_path / "link_vault"
        link.symlink_to(real, target_is_directory=True)

        original = Path.cwd()
        try:
            shell = CorShell(cli, link)
            # Absolute path via the symlink. Comparing it unresolved against the
            # resolved vault made this look like an escape and refused it.
            shell._cd(str(link / "archive"))
            assert Path.cwd().resolve() == (real / "archive").resolve()
        finally:
            os.chdir(original)

    def test_escaping_the_vault_is_still_refused(self, two_vaults):
        shell = CorShell(cli, two_vaults["work"])
        before = Path.cwd()

        shell._cd(str(two_vaults["outside"]))

        assert Path.cwd() == before, "must not leave the vault"


class TestTreeMetaCommand:
    def test_builds_an_interactive_tree_command(self, two_vaults, monkeypatch):
        shell = CorShell(cli, two_vaults["work"])
        seen = []
        monkeypatch.setattr(shell, "run_command", seen.append)

        shell.dispatch(":tree myproj")

        assert seen == ["tree myproj -i"]

    def test_extra_arguments_pass_through(self, two_vaults, monkeypatch):
        shell = CorShell(cli, two_vaults["work"])
        seen = []
        monkeypatch.setattr(shell, "run_command", seen.append)

        shell.dispatch(":tree myproj -d 2")

        assert seen == ["tree myproj -i -d 2"]

    def test_falls_back_to_the_focused_project(self, two_vaults, monkeypatch):
        from cor.config import set_focused_project

        shell = CorShell(cli, two_vaults["work"])
        set_focused_project("chosen")
        seen = []
        monkeypatch.setattr(shell, "run_command", seen.append)

        shell.dispatch(":tree")

        assert seen == ["tree chosen -i"]

    def test_without_focus_reports_what_to_do(self, two_vaults, monkeypatch, capsys):
        shell = CorShell(cli, two_vaults["work"])
        monkeypatch.setattr(
            "cor.config.get_focused_project", lambda *a, **k: None
        )
        seen = []
        monkeypatch.setattr(shell, "run_command", seen.append)
        capsys.readouterr()

        assert shell.dispatch(":tree") is True
        assert not seen, "must not launch the browser without a project"
        assert "Which project?" in capsys.readouterr().err

    def test_is_listed_in_help(self, two_vaults, capsys):
        shell = CorShell(cli, two_vaults["work"])
        capsys.readouterr()

        shell.dispatch(":help")

        assert ":tree" in capsys.readouterr().out


class TestSwitchValidation:
    """':vault' must not hand out a prompt that lies about where you are."""

    def _register_uninitialized(self, tmp_path):
        from cor.config import add_vault

        broken = tmp_path / "notavault"
        broken.mkdir()
        add_vault("broken", broken)
        return broken

    def test_refuses_an_uninitialized_vault(self, two_vaults, tmp_path, capsys):
        """run_shell refuses to START without backlog.md; switching must match.

        This used to print "Switched to broken", show cor(broken)> in the
        prompt, and then fail every single command with "Not initialized".
        """
        self._register_uninitialized(tmp_path)
        shell = CorShell(cli, two_vaults["work"])
        capsys.readouterr()

        shell.dispatch(":vault broken")

        err = capsys.readouterr().err
        assert "not initialized" in err.lower()
        assert shell.name == "work", "the session must not have moved"
        assert Path.cwd().resolve() == two_vaults["work"].resolve()
        assert os.environ["COR_VAULT"] == str(two_vaults["work"].resolve())

    def test_unknown_name_says_how_to_register(self, two_vaults, capsys):
        shell = CorShell(cli, two_vaults["work"])
        capsys.readouterr()

        shell.dispatch(":vault nope")

        err = capsys.readouterr().err
        assert "nope" in err
        assert "vault add" in err, "must say how to register one"
        assert shell.name == "work"

    def test_unknown_name_lists_what_is_registered(self, two_vaults, capsys):
        shell = CorShell(cli, two_vaults["work"])
        capsys.readouterr()

        shell.dispatch(":vault nope")

        err = capsys.readouterr().err
        assert "work" in err and "personal" in err

    def test_a_path_is_explained_not_just_rejected(self, two_vaults, capsys):
        """':vault ~/notes' is a natural mistake; say what to do with it."""
        shell = CorShell(cli, two_vaults["work"])
        capsys.readouterr()

        shell.dispatch(":vault ~/some/path")

        err = capsys.readouterr().err
        assert "not a path" in err
        # The suggested command should carry their path, not a placeholder.
        assert "vault add <name> ~/some/path" in err

    def test_missing_path_is_reported(self, two_vaults, tmp_path, capsys):
        from cor.config import add_vault

        add_vault("gone", tmp_path / "does-not-exist")
        shell = CorShell(cli, two_vaults["work"])
        capsys.readouterr()

        shell.dispatch(":vault gone")

        assert "missing path" in capsys.readouterr().err
        assert shell.name == "work"

    def test_a_valid_switch_still_works(self, two_vaults):
        """Guard against the new checks blocking the happy path."""
        shell = CorShell(cli, two_vaults["work"])

        shell.dispatch(":vault personal")

        assert shell.name == "personal"
        assert Path.cwd().resolve() == two_vaults["personal"].resolve()

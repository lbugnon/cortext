"""Interactive shell for cor.

With several vaults on one machine, the question "which vault will this land
in?" has to be answerable before you press Enter, not after. A one-shot
command answers it implicitly, from cwd or an env var you cannot see. This
shell answers it explicitly: you pick a vault when the session starts, its
name sits in the prompt on every line, and ':vault' is the only way it
changes.

The shell is additive. Every command still works as `cor <command>` from a
script, a git hook, or the neovim plugin - those callers are the reason the
CLI must stay non-interactive, and nothing here changes them.

Implementation note: commands run *in-process* via the click group rather
than as subprocesses, so a session pays the ~340ms import cost once instead
of per command. The vault is pinned by chdir'ing into it and exporting
COR_VAULT, which makes resolution rules 1 and 2 agree with the prompt for
in-process calls and for anything a command shells out to ($EDITOR, git, the
tree TUI).
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import click

from .config import (
    is_vault_initialized,
    VAULT_SOURCE_CONFIG,
    get_vaults,
    resolve_vault,
    vault_name_for,
    vault_source_label,
)
from .exceptions import ConfigError, CorError

# Distinguishes "not computed yet" from a computed None (vault unreadable).
_UNSET = object()

_SHELL_STYLE = None  # built on first run_shell(); see _SHELL_STYLE_RULES

_SHELL_STYLE_RULES = {
    # The vault name is the one thing that must be unmissable on every line.
    "prompt.punct": "#888888",
    "prompt.vault": "bold #00afaf",
    "prompt.rel": "#888888",
    "bottom-toolbar": "bg:#333333 #cccccc",
    "toolbar.key": "bg:#333333 #888888",
    "toolbar.value": "bg:#333333 bold #00afaf",
    "toolbar.dim": "bg:#333333 #888888",
}

_BANNER_HELP = "Type a command without the 'cor' prefix.  :help for shell commands, :quit to exit."


def _history_path() -> Path:
    """Return the shell history file, honouring XDG_STATE_HOME."""
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "cor" / "history"


class CorCompleter:
    """Completion for the shell, delegating to click's own machinery.

    Rather than re-implementing argument resolution, this hands the parsed
    words to click.shell_completion.ShellComplete - the exact code path zsh
    and bash use. That matters because completion callbacks read state off
    the resolved context: complete_name() in cor/completions.py branches on
    ctx.params["note_type"] to decide whether to offer project prefixes, so
    a hand-built context would silently return nothing for `new task <TAB>`.

    Reusing click here means shell completion and in-shell completion cannot
    drift apart.
    """

    def __init__(self, group: click.Group):
        self.group = group

    def get_completions(self, document, complete_event):  # noqa: D102 - prompt_toolkit API
        from prompt_toolkit.completion import Completion

        text = document.text_before_cursor
        incomplete = self._incomplete(text)
        for value, display in self._candidates(text):
            yield Completion(value, start_position=-len(incomplete), display=display)

    @staticmethod
    def _incomplete(text: str) -> str:
        """The partial word under the cursor."""
        if not text or text[-1].isspace():
            return ""
        try:
            return shlex.split(text)[-1]
        except ValueError:
            return text.split()[-1]

    def _candidates(self, text: str) -> list[tuple[str, str]]:
        """Return (value, display) completion pairs for a partial input line."""
        try:
            words = shlex.split(text)
        except ValueError:
            return []  # unbalanced quotes; nothing sensible to offer yet

        incomplete = self._incomplete(text)
        if incomplete and words:
            words = words[:-1]

        # Meta-commands are the shell's own, unknown to click.
        meta = [
            (name, f"{name}  {_META_HELP[name]}")
            for name in sorted(_META_HELP)
            if name.startswith(incomplete)
        ]
        if incomplete.startswith(":"):
            return meta

        from click.shell_completion import ShellComplete

        try:
            completer = ShellComplete(self.group, {}, "cor", "_COR_COMPLETE")
            items = completer.get_completions(words, incomplete)
        except Exception:
            # A callback touching a half-configured vault, or an input click
            # cannot parse, must not take down the whole shell.
            return []

        out = [
            (item.value, item.value if not item.help else f"{item.value}  ({item.help})")
            for item in items
            if item.type == "plain"
        ]
        # Offer ':' commands alongside top-level command names.
        if not words:
            out += meta
        return out


_META_HELP = {
    ":vault": "show the active vault, or switch: :vault <name>",
    ":vaults": "list registered vaults",
    ":tree": "browse a project in the full-screen tree: :tree [project]",
    ":cd": "change directory within the vault",
    ":pwd": "show the current directory",
    ":help": "show this help",
    ":quit": "leave the shell (also Ctrl-D, 'exit', 'quit')",
}


class CorShell:
    """A shell session pinned to one vault."""

    def __init__(self, group: click.Group, vault: Path):
        self.group = group
        self.vault = vault
        self._counts = _UNSET
        self._pin(vault)

    def _pin(self, vault: Path) -> None:
        """Make every layer agree on which vault is active.

        chdir satisfies resolution rule 1 (nearest ancestor with backlog.md)
        for in-process commands and for subprocesses that inherit cwd;
        COR_VAULT satisfies rule 2 for anything started elsewhere. Setting
        both means no code path can disagree with what the prompt says.
        """
        self.vault = vault.resolve()
        os.chdir(self.vault)
        os.environ["COR_VAULT"] = str(self.vault)
        self._invalidate()

    @property
    def name(self) -> str:
        return vault_name_for(self.vault)

    def _rel(self) -> str:
        """The path inside the vault, if the session has ':cd'ed into one."""
        cwd = Path.cwd().resolve()
        if cwd == self.vault:
            return ""
        try:
            return "/" + str(cwd.relative_to(self.vault))
        except ValueError:
            return ""

    def prompt_text(self) -> str:
        """The prompt as plain text - the vault name is the whole point."""
        return f"cor({self.name}{self._rel()})> "

    def prompt_fragments(self):
        """The prompt, styled. Same text as prompt_text()."""
        from prompt_toolkit.formatted_text import FormattedText

        return FormattedText([
            ("class:prompt.punct", "cor("),
            ("class:prompt.vault", self.name),
            ("class:prompt.rel", self._rel()),
            ("class:prompt.punct", ")> "),
        ])

    def counts(self) -> tuple[int, int] | None:
        """(notes, projects) for the active vault, or None if unreadable.

        Cached: the bottom toolbar is redrawn on every keystroke, and walking
        the vault there would put a ~25ms parse in the typing path. The cache is
        dropped after each command and on a vault switch (see _invalidate), so
        the figures are recomputed at most once per command.
        """
        if self._counts is _UNSET:
            from .core.files import FileIterator

            try:
                files = FileIterator(self.vault)
                self._counts = (
                    sum(1 for _ in files.iter_all_notes()),
                    sum(1 for _ in files.iter_projects()),
                )
            except Exception:
                self._counts = None
        return self._counts

    def _invalidate(self) -> None:
        self._counts = _UNSET

    def bottom_toolbar(self):
        """Vault, focused project and size, shown under the prompt."""
        from prompt_toolkit.formatted_text import FormattedText

        fragments = [("class:toolbar.key", " vault "),
                     ("class:toolbar.value", self.name)]

        try:
            from .config import get_focused_project

            focus = get_focused_project()
        except Exception:
            focus = None
        if focus:
            fragments += [("class:toolbar.key", "  focus "),
                          ("class:toolbar.value", focus)]

        counts = self.counts()
        if counts:
            notes, projects = counts
            fragments.append(
                ("class:toolbar.dim", f"  {notes} notes, {projects} projects")
            )
        fragments.append(("class:toolbar.dim", "  :help"))
        return FormattedText(fragments)

    def print_header(self) -> None:
        counts = self.counts()
        if counts:
            notes, projects = counts
            summary = f"{notes} notes, {projects} projects"
        else:
            summary = "vault not readable"

        click.echo()
        click.echo(
            click.style(f"  vault: {self.name}", fg="cyan", bold=True)
            + click.style(f"  ({self.vault})", fg="cyan")
        )
        click.echo(click.style(f"  {summary}", dim=True))
        click.echo(click.style(f"  {_BANNER_HELP}", dim=True))
        click.echo()

    # --- meta-commands ---

    def handle_meta(self, line: str) -> bool:
        """Run a ':' shell command. Returns False to end the session."""
        try:
            parts = shlex.split(line)
        except ValueError as e:
            # `:cd 'oops` used to raise straight out of the session loop.
            # run_command already guards its own split; this is the other half.
            click.secho(f"Parse error: {e}", fg="red", err=True)
            return True
        if not parts:
            return True
        cmd, args = parts[0], parts[1:]

        if cmd in (":quit", ":q", ":exit"):
            return False

        if cmd == ":help":
            width = max(len(k) for k in _META_HELP)
            for key in sorted(_META_HELP):
                click.echo(f"  {key:<{width}}  {_META_HELP[key]}")
            click.echo()
            click.echo("  Everything else is a normal cor command, minus the 'cor' prefix.")
            click.echo("  'help' or '--help' lists those.")
            return True

        if cmd == ":vault":
            if not args:
                click.echo(
                    f"{click.style(self.name, fg='cyan', bold=True)}  {self.vault}"
                )
                return True
            self.switch(args[0])
            return True

        if cmd == ":vaults":
            # Delegated to the real command rather than reimplemented: the
            # local copy this replaces showed only the active vault, while
            # `vault list` also marks the default, the resolution source, and
            # vaults that are registered but not initialized. Two listings of
            # the same thing drift.
            self.run_command("vault list")
            return True

        if cmd == ":tree":
            self._tree(args)
            return True

        if cmd == ":pwd":
            click.echo(Path.cwd())
            return True

        if cmd == ":cd":
            self._cd(args[0] if args else str(self.vault))
            return True

        click.secho(f"Unknown shell command: {cmd}  (try :help)", fg="red", err=True)
        return True

    def switch(self, name: str) -> None:
        vaults = get_vaults()
        if name not in vaults:
            self._explain_unknown_vault(name, vaults)
            return
        target = vaults[name]
        if not target.exists():
            click.secho(f"Vault '{name}' points at a missing path: {target}", fg="red", err=True)
            return
        # run_shell refuses to *start* on a directory without backlog.md, so
        # switching into one must fail too. It used to succeed: the prompt then
        # said cor(name)> while every command answered "Not initialized".
        if not is_vault_initialized(target):
            click.secho(
                f"Vault '{name}' is not initialized - no backlog.md at {target}.",
                fg="red",
                err=True,
            )
            click.secho(
                "  Run 'cor init' inside it, or ':vault' to see where you are.",
                dim=True,
                err=True,
            )
            return
        self._pin(target)
        click.echo(f"Switched to {click.style(self.name, fg='cyan', bold=True)}  ({self.vault})")

    def _explain_unknown_vault(self, name: str, vaults: dict) -> None:
        """Say what went wrong *and* what to do about it.

        Reporting only "Known: notes" leaves someone with a single registered
        vault stuck, because nothing on that path mentions that vaults are
        registered with `vault add` - the likeliest reason a switch fails is
        that the target was never registered at all.
        """
        looks_like_path = "/" in name or name.startswith("~")
        if looks_like_path:
            click.secho(
                "':vault' takes a registered vault name, not a path.", fg="red", err=True
            )
        else:
            known = ", ".join(sorted(vaults)) or "none"
            click.secho(f"No vault named '{name}'. Registered: {known}", fg="red", err=True)

        click.secho(
            f"  Register one with:  vault add <name> {name if looks_like_path else '<path>'}",
            dim=True,
            err=True,
        )
        if len(vaults) <= 1:
            click.secho(
                "  ':vaults' lists what is registered; a config with only the "
                "legacy 'vault:' key has exactly one.",
                dim=True,
                err=True,
            )

    def _tree(self, args: list[str]) -> None:
        """Open the full-screen tree browser on a project.

        A convenience over typing `tree <project> -i`: it supplies -i, and
        defaults to the focused project so the common case is a bare ':tree'.

        No terminal handover is needed here - session.prompt() has already
        returned by the time a line is dispatched, so the terminal is back in
        its normal mode and Textual can take it over directly. run_command's
        finally block re-asserts the vault pin and drops the cached counts
        afterwards, so the toolbar reflects anything changed in the browser.
        """
        if args:
            focus, extra = args[0], args[1:]
        else:
            try:
                from .config import get_focused_project

                focus = get_focused_project()
            except Exception:
                focus = None
            extra = []
            if not focus:
                click.secho(
                    "Which project? Use ':tree <project>', or set a default "
                    "with 'focus <project>'.",
                    fg="red",
                    err=True,
                )
                return

        parts = ["tree", focus, "-i", *extra]
        self.run_command(" ".join(shlex.quote(p) for p in parts))

    def _cd(self, target: str) -> None:
        # Both branches resolve: self.vault is stored resolved, so comparing an
        # unresolved path against it made a symlinked directory *inside* the
        # vault look like an escape attempt and get refused.
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        path = candidate.resolve()

        if not path.is_dir():
            click.secho(f"Not a directory: {path}", fg="red", err=True)
            return
        # Leaving the vault would silently break the pinning invariant the
        # prompt advertises, so keep the session inside it.
        try:
            path.relative_to(self.vault)
        except ValueError:
            click.secho(
                "Refusing to leave the vault. Use ':vault <name>' to switch vaults.",
                fg="red",
                err=True,
            )
            return
        os.chdir(path)

    # --- command dispatch ---

    def run_command(self, line: str) -> None:
        """Execute one cor command in-process."""
        from .config import get_verbosity, set_verbosity

        try:
            args = shlex.split(line)
        except ValueError as e:
            click.secho(f"Parse error: {e}", fg="red", err=True)
            return

        # The group's -v flag *persists* verbosity to the config file. In a
        # shell that would ratchet up permanently across a session, so
        # snapshot and restore around each line.
        try:
            verbosity = get_verbosity()
        except Exception:
            verbosity = None

        try:
            self.group.main(args=args, prog_name="cor", standalone_mode=False)
        except SystemExit:
            # _handle_cor_error and click both exit the process on error;
            # in a shell that would end the session after one typo.
            pass
        except CorError as e:
            click.secho(f"Error: {e}", fg="red", err=True)
        except click.ClickException as e:
            e.show()
        except click.exceptions.Abort:
            click.echo("Aborted.")
        except KeyboardInterrupt:
            click.echo("Interrupted.")
        except Exception as e:
            click.secho(f"Unexpected error: {type(e).__name__}: {e}", fg="red", err=True)
        finally:
            if verbosity is not None:
                try:
                    if get_verbosity() != verbosity:
                        set_verbosity(verbosity)
                except Exception:
                    pass
            self._reassert_pin()
            self._invalidate()

    def _reassert_pin(self) -> None:
        """Restore the vault pin if a command moved it.

        The prompt promises that the named vault is where the next command
        lands. That promise held only as long as every command left cwd and
        COR_VAULT alone - and `cor sync` did not, which left the session
        outside its own vault while the prompt still claimed otherwise.
        Rather than trusting each command, re-establish the invariant here,
        at the one boundary the shell controls.

        Note this deliberately does not fight ':cd': a session may sit in a
        subdirectory, so anything at or below the vault root is left alone.
        """
        try:
            cwd = Path.cwd().resolve()
        except OSError:
            cwd = None  # cwd was deleted under us
        inside = False
        if cwd is not None:
            try:
                cwd.relative_to(self.vault)
                inside = True
            except ValueError:
                inside = False
        if not inside:
            os.chdir(self.vault)
        if os.environ.get("COR_VAULT") != str(self.vault):
            os.environ["COR_VAULT"] = str(self.vault)

    def dispatch(self, line: str) -> bool:
        """Route one input line. Returns False when the session should end."""
        line = line.strip()
        if not line:
            return True
        if line in ("exit", "quit"):
            return False
        if line.startswith(":"):
            return self.handle_meta(line)
        self.run_command(line)
        return True


def _select_startup_vault() -> Path:
    """Decide which vault the session opens on.

    cwd and COR_VAULT are honoured, so `cd ~/notes/work && cor` does the
    obvious thing. Only when both are silent does the shell ask - starting a
    session on a guess is exactly the ambiguity this is meant to remove.
    """
    from .utils import pick_vault_interactively

    try:
        vault, source = resolve_vault()
    except ConfigError:
        vaults = get_vaults()
        if not vaults:
            raise
        return pick_vault_interactively("No vault configured.")

    if source == VAULT_SOURCE_CONFIG:
        vaults = get_vaults()
        if len(vaults) > 1:
            return pick_vault_interactively(
                "Not inside a vault, and COR_VAULT is unset."
            )
        click.secho(
            f"Using default vault ({vault_source_label(source)}).", dim=True, err=True
        )
    return vault


def run_shell(group: click.Group) -> None:
    """Start the interactive shell on the click group `group`."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, ThreadedCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.shortcuts import CompleteStyle
    from prompt_toolkit.styles import Style

    vault = _select_startup_vault()
    if not is_vault_initialized(vault):
        raise CorError(
            f"{vault} is not an initialized vault (no backlog.md). Run 'cor init' there."
        )

    shell = CorShell(group, vault)
    shell.print_header()

    completer_impl = CorCompleter(group)

    class _Completer(Completer):
        def get_completions(self, document, complete_event):
            yield from completer_impl.get_completions(document, complete_event)

    history_file = _history_path()
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(history_file))
    except OSError:
        history = None  # read-only home; a session without history still works

    # Completion parses frontmatter across the vault - ~21ms on a few hundred
    # notes. That is fine for a Tab press, but complete_while_typing (on by
    # default) reruns it per keystroke, and an unwrapped completer does that on
    # the event loop, stalling input. ThreadedCompleter moves it off the loop.
    global _SHELL_STYLE
    if _SHELL_STYLE is None:
        _SHELL_STYLE = Style.from_dict(_SHELL_STYLE_RULES)

    session = PromptSession(
        completer=ThreadedCompleter(_Completer()),
        history=history,
        style=_SHELL_STYLE,
        auto_suggest=AutoSuggestFromHistory(),
        bottom_toolbar=shell.bottom_toolbar,
        # `tree <TAB>` can offer ~95 notes; one per line is unreadable.
        complete_style=CompleteStyle.MULTI_COLUMN,
        reserve_space_for_menu=6,
        enable_history_search=True,
    )

    while True:
        try:
            line = session.prompt(shell.prompt_fragments())
        except KeyboardInterrupt:
            continue  # Ctrl-C clears the line, as in a normal shell
        except EOFError:
            break  # Ctrl-D
        try:
            if not shell.dispatch(line):
                break
        except KeyboardInterrupt:
            click.echo("Interrupted.")
        except Exception as e:
            # One bad line must never end the session. run_command covers
            # command dispatch; this is the backstop for everything else the
            # shell does with a line.
            click.secho(
                f"Unexpected error: {type(e).__name__}: {e}", fg="red", err=True
            )

    click.echo("bye")

"""Cor CLI - Command modules.

This package contains the CLI command implementations, organized by category:
- init: Vault initialization commands
- notes: Note/task management commands  
- config: Configuration and settings commands
- maintenance: Sync, hooks, and maintenance commands
"""

import os
import sys
import subprocess
from pathlib import Path

import click

from .. import __version__
from ..config import get_verbosity, set_verbosity
from ..exceptions import (
    CorError,
    NotFoundError,
    ExternalServiceError,
)


HOOKS_DIR = Path(__file__).parent.parent / "hooks"


def _handle_cor_error(e: CorError) -> None:
    """Handle CorError exceptions with nice formatting.
    
    This function is called when a CorError is caught at the CLI level.
    It prints a user-friendly error message and exits with code 1.
    """
    click.secho(f"Error: {e}", fg="red", err=True)
    sys.exit(1)


class CorCLI(click.Group):
    """Custom CLI group that catches and formats CorError exceptions."""
    
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except CorError as e:
            _handle_cor_error(e)


@click.group(cls=CorCLI, invoke_without_command=True, context_settings={
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
})
@click.version_option(__version__, prog_name="Cor")
@click.option(
    "--verbose", "-v",
    count=True,
    help="Increase verbosity level (can be used multiple times: -v, -vv, -vvv)"
)
@click.pass_context
def cli(ctx, verbose: int):
    """Cor - Plain text knowledge management for the terminal.

    A lightweight tool for managing projects, tasks, and notes using
    plain text files and git.

    Run `cor` with no command to open the interactive shell, which pins one
    vault for the session and shows its name in the prompt.
    """
    # resilient_parsing means click is resolving a context for completion,
    # not really running us. Doing anything here would recurse: the shell's
    # completer calls back into this group.
    if ctx.resilient_parsing:
        return

    # click.echo consults the active context for its colour default, so setting
    # this once here covers every echo in the command that follows - including
    # the ~530 call sites that pass an explicit fg=.
    from ..ui.theme import no_color_requested

    if no_color_requested():
        ctx.color = False

    if verbose > 0:
        current_level = get_verbosity()
        new_level = min(current_level + verbose, 3)
        set_verbosity(new_level)

    if ctx.invoked_subcommand is None:
        # Bare `cor` opens the shell. Only when a human is present: piping
        # into `cor` used to print help, and scripts should keep getting
        # something inert rather than a hung prompt.
        if not sys.stdin.isatty():
            click.echo(ctx.get_help())
            return
        from ..repl import run_shell
        run_shell(cli)


@cli.command(name="shell")
def shell_cmd():
    """Open the interactive shell (same as running `cor` with no command).

    Pins one vault for the session, shows its name in every prompt, and lets
    you run commands without the `cor` prefix. Use ':vault <name>' to switch
    and ':help' for the shell's own commands.
    """
    from ..repl import run_shell
    run_shell(cli)


def _install_pre_commit_hook() -> None:
    """Install the pre-commit git hook."""
    import shutil
    import stat
    
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ExternalServiceError("Not in a git repository.")

    git_dir = Path(result.stdout.strip())
    hooks_target = git_dir / "hooks"
    hooks_target.mkdir(exist_ok=True)

    source = HOOKS_DIR / "pre-commit"
    target = hooks_target / "pre-commit"

    if not source.exists():
        raise NotFoundError(f"Hook source not found: {source}")

    if target.exists():
        click.echo(f"Overwriting existing {target}")

    shutil.copy(source, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    click.echo(f"Installed pre-commit hook to {target}")


def _install_shell_completion() -> None:
    """Install shell completion based on detected shell."""
    shell = os.environ.get("SHELL", "")
    
    if "zsh" in shell:
        _install_zsh_completion()
    elif "bash" in shell:
        _install_bash_completion()
    else:
        click.echo("Shell not detected. For shell completion, add to your shell config:")
        click.echo('  # For zsh: eval "$(_COR_COMPLETE=zsh_source cor)"')
        click.echo('  # For bash: eval "$(_COR_COMPLETE=bash_source cor)"')


def _install_zsh_completion():
    """Install completion for zsh by updating ~/.zshrc."""
    zshrc = Path.home() / ".zshrc"
    
    completion_block = '''
# Cor shell completion
if command -v cor &> /dev/null; then
    setopt MENU_COMPLETE

    _cor_completion() {
        local -a completions completions_partial
        local -a response
        (( ! $+commands[cor] )) && return 1

        response=("${(@f)$(env COMP_WORDS="${words[*]}" COMP_CWORD=$((CURRENT-1)) _COR_COMPLETE=zsh_complete cor)}")

        local i=1
        local rlen=${#response}
        while (( i <= rlen )); do
            local type=${response[i]}
            local key=${response[i+1]:-}
            local descr=${response[i+2]:-}
            (( i += 3 ))
            if [[ "$type" == "plain" && -n "$key" ]]; then
                if [[ "$key" == *. ]]; then
                    completions_partial+=("$key")
                else
                    completions+=("$key")
                fi
            fi
        done

        if [[ ${#completions_partial} -eq 0 && ${#completions} -eq 0 ]]; then
            return 1
        fi

        if [[ ${#completions_partial} -gt 0 ]]; then
            compadd -Q -U -S '' -V partial -- ${completions_partial[@]}
        fi
        if [[ ${#completions} -gt 0 ]]; then
            compadd -Q -U -V unsorted -- ${completions[@]}
        fi
    }
    compdef _cor_completion cor
fi
'''
    
    if zshrc.exists():
        content = zshrc.read_text()
        if "# Cor shell completion" in content or "_cor_completion" in content:
            click.echo("Shell completion already configured in ~/.zshrc")
            return
    
    with zshrc.open("a") as f:
        f.write(completion_block)
    
    click.echo("Added shell completion to ~/.zshrc")
    click.echo("Run 'source ~/.zshrc' or restart your shell to enable")


def _install_bash_completion():
    """Install completion for bash by updating ~/.bashrc."""
    bashrc = Path.home() / ".bashrc"
    
    completion_block = '''
# Cor shell completion
if command -v cor &> /dev/null; then
    eval "$(_COR_COMPLETE=bash_source cor)"
fi
'''
    
    if bashrc.exists():
        content = bashrc.read_text()
        if "# Cor shell completion" in content or "_COR_COMPLETE=bash_source" in content:
            click.echo("Shell completion already configured in ~/.bashrc")
            return
    
    with bashrc.open("a") as f:
        f.write(completion_block)
    
    click.echo("Added shell completion to ~/.bashrc")
    click.echo("Run 'source ~/.bashrc' or restart your shell to enable")


def _uninstall_pre_commit_hook() -> None:
    """Remove Cor git hooks."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ExternalServiceError("Not in a git repository.")

    git_dir = Path(result.stdout.strip())
    target = git_dir / "hooks" / "pre-commit"

    if target.exists():
        target.unlink()
        click.echo(f"Removed {target}")
    else:
        click.echo("No pre-commit hook found.")


# Import and register all commands
from .init import init, example_vault
from .config import config_cmd, focus, vault_group
from .notes import new, edit, tag, delete, mark, due, expand, link
from .maintenance import sync, maintenance
from ..commands.refactor import rename, group
from ..commands.inbox import inbox
from ..commands.dependencies import depend
from ..commands.relations import rel
from ..commands.refs import ref
from ..commands.status import daily, projects, weekly, tree, status
from ..commands.calendar import auth as calendar_auth
from ..commands.calendar import sync as calendar_sync
from ..commands.calendar import status as calendar_status
from ..commands.calendar import logout as calendar_logout
from .search_cmd import search

cli.add_command(init)
cli.add_command(example_vault)
cli.add_command(config_cmd)
cli.add_command(vault_group)
cli.add_command(focus)
cli.add_command(inbox)
cli.add_command(new)
cli.add_command(edit)
cli.add_command(tag)
cli.add_command(delete)
cli.add_command(delete, name="del")  # Alias
cli.add_command(mark)
cli.add_command(due)
cli.add_command(expand)
cli.add_command(link)
cli.add_command(sync)
cli.add_command(maintenance)
cli.add_command(rename)
cli.add_command(rename, name="move")  # Alias
cli.add_command(group)
cli.add_command(depend)
cli.add_command(rel)
cli.add_command(ref)
cli.add_command(daily)
cli.add_command(projects)
cli.add_command(weekly)
cli.add_command(tree)
cli.add_command(status)
cli.add_command(search)
cli.add_command(shell_cmd)

# Calendar commands group
@cli.group(name="calendar", cls=CorCLI)
def calendar_group():
    """Google Calendar integration for due dates."""
    pass

calendar_group.add_command(calendar_auth, name="auth")
calendar_group.add_command(calendar_sync, name="sync")
calendar_group.add_command(calendar_status, name="status")
calendar_group.add_command(calendar_logout, name="logout")
cli.add_command(calendar_group)

__all__ = ["cli"]

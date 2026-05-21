"""Configuration commands for Cor CLI."""

import os
from pathlib import Path

import click

from ..exceptions import ValidationError, NotFoundError, ConfigError
from . import cli
from ..config import (
    load_config,
    config_file,
    set_vault_path,
    get_verbosity,
    set_verbosity,
    get_focused_project,
    set_focused_project,
    clear_focused_project,
    get_remote_inbox,
    set_remote_inbox,
    get_timezone,
    set_timezone,
)
from ..utils import get_notes_dir, require_init


@cli.command(name="config")
@click.argument("key", type=click.Choice(["verbosity", "vault", "inbox", "timezone"]), required=False)
@click.argument("value", required=False)
def config_cmd(key: str | None, value: str | None):
    """Manage Cor configuration.

    View or modify global settings for verbosity, vault location, timezone, and remote inbox.

    \b
    Configuration Keys:
      verbosity    Output detail level (0=silent, 1=normal, 2=verbose, 3=debug)
      vault        Path to your notes directory
      timezone     Timezone for calendar events (e.g., America/Argentina/Buenos_Aires)
      inbox        Telegram bot token for mobile inbox

    \b
    Examples:
      cor config                      Show all settings
      cor config verbosity 2          Set verbose output
      cor config vault ~/my-notes     Change vault location
      cor config timezone America/Argentina/Buenos_Aires  Set timezone
      cor config inbox <bot-token>    Configure Telegram inbox
    """
    # Show all config if no key provided
    if key is None:
        config_data = load_config()
        click.echo(click.style("Cor Configuration", bold=True))
        click.echo(config_data)
        click.echo()
        
        return

    if key == "verbosity":
        if value is None:
            # Show current value
            current = get_verbosity()
            click.echo(f"Verbosity level: {current}")
            click.echo("Levels: 0=silent, 1=normal, 2=verbose, 3=debug")
        else:
            # Set new value
            try:
                level = int(value)
                if not 0 <= level <= 3:
                    raise ValueError()
                set_verbosity(level)
                click.echo(f"Verbosity set to {level}")
            except ValueError:
                raise ValidationError(f"Invalid verbosity level: {value}. Must be 0-3.")

    elif key == "vault":
        if value is None:
            # Show current vault configuration
            notes_dir = get_notes_dir()
            config_data = load_config()
            env_vault = os.environ.get("CORTEX_VAULT")

            click.echo(click.style("Vault Configuration", bold=True))
            click.echo()

            if env_vault:
                click.echo(f"CORTEX_VAULT env: {env_vault} " + click.style("(active)", fg="green"))
            if config_data.get("vault"):
                status = "(active)" if not env_vault else "(overridden)"
                click.echo(f"Config file: {config_data['vault']} " + click.style(status, fg="yellow" if env_vault else "green"))
            if not env_vault and not config_data.get("vault"):
                click.echo(f"Current directory: {Path.cwd()} " + click.style("(active)", fg="green"))

            click.echo()
            click.echo(f"Active vault: {click.style(str(notes_dir), fg='cyan', bold=True)}")
            if (notes_dir / "backlog.md").exists():
                click.echo(click.style("  (initialized)", fg="green"))
            else:
                click.echo(click.style("  (not initialized - run 'cor init')", fg="yellow"))
        else:
            # Set vault path
            path = Path(value).expanduser().resolve()
            if not path.exists():
                raise NotFoundError(f"Path does not exist: {path}")
            if not path.is_dir():
                raise ValidationError(f"Path is not a directory: {path}")
            set_vault_path(path)
            click.echo(f"Vault path set to: {path}")
            click.echo(f"Config saved to: {config_file()}")

    elif key == "timezone":
        if value is None:
            # Show current timezone configuration
            current_tz = get_timezone()
            click.echo(click.style("Timezone Configuration", bold=True))
            click.echo()
            click.echo(f"Current timezone: {click.style(current_tz, fg='cyan', bold=True)}")
            click.echo()
            click.echo("This timezone is used when interpreting due dates with times")
            click.echo("for Google Calendar sync.")
            click.echo()
            click.echo("Common timezones:")
            click.echo("  America/Argentina/Buenos_Aires  (Buenos Aires)")
            click.echo("  America/New_York                (Eastern US)")
            click.echo("  America/Los_Angeles             (Pacific US)")
            click.echo("  Europe/London                   (London)")
            click.echo("  Europe/Paris                    (Paris)")
            click.echo("  Asia/Tokyo                      (Tokyo)")
            click.echo()
            click.echo("Run 'cor config timezone <timezone>' to change")
        else:
            # Validate timezone
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(value)  # Will raise if invalid
                set_timezone(value)
                click.echo(click.style(f"Timezone set to: {value}", fg="green"))
                click.echo(f"Config saved to: {config_file()}")
            except Exception as e:
                raise ValidationError(f"Invalid timezone: {value}. Use IANA timezone names like 'America/Argentina/Buenos_Aires'")

    elif key == "inbox":
        if value is None:
            # Show current inbox configuration
            bot_token = get_remote_inbox()
            env_token = os.environ.get("TELEGRAM_BOT_TOKEN")

            click.echo(click.style("Remote Inbox Configuration", bold=True))
            click.echo()

            if env_token:
                click.echo("TELEGRAM_BOT_TOKEN env: " + click.style("(configured)", fg="green"))

            config_data = load_config()
            if config_data.get("remote_inbox"):
                status = "(active)" if not env_token else "(overridden)"
                masked = config_data['remote_inbox'][:8] + "..." if len(config_data['remote_inbox']) > 8 else "***"
                click.echo(f"Config file: {masked} " + click.style(status, fg="yellow" if env_token else "green"))

            if bot_token:
                click.echo()
                click.echo(click.style("Remote inbox is configured", fg="green"))
                click.echo("Send messages to your Telegram bot to capture them in backlog")
            else:
                click.echo()
                click.echo(click.style("Remote inbox not configured", fg="yellow"))
                click.echo("To setup: Create a bot via @BotFather, then run:")
                click.echo("  cor config inbox <bot-token>")
        else:
            # Set bot token
            set_remote_inbox(value)
            click.echo(click.style("Remote inbox configured", fg="green"))
            click.echo(f"Config saved to: {config_file()}")
            click.echo()
            click.echo("Messages sent to your Telegram bot will be pulled during 'cor sync'")


@cli.command()
@click.argument("project", required=False)
@require_init
def focus(project: str | None):
    """Set or show the focused project.

    When a project is focused, 'cor new', 'cor edit', and other commands
    will default to that project. This simplifies working on a single
    project without typing the full name each time.

    \b
    Examples:
      cor focus              # Show current focus
      cor focus myproject    # Focus on myproject
      cor focus off          # Clear focus
    """
    notes_dir = get_notes_dir()
    
    # Show current focus if no argument provided
    if project is None:
        focused = get_focused_project()
        if focused:
            click.echo(click.style(f"Focusing on: {focused}", fg="cyan", bold=True))
            click.echo(f"Run 'cor focus off' to clear")
        else:
            click.echo("No project focused. Run 'cor focus <project>' to set one.")
        return
    
    # Clear focus
    if project.lower() == "off":
        clear_focused_project()
        click.echo(click.style("Focus cleared.", fg="green"))
        return
    
    # Validate project exists
    from ..search import resolve_file_fuzzy
    import frontmatter

    project_path = notes_dir / f"{project}.md"
    if not project_path.exists():
        result = resolve_file_fuzzy(project, include_archived=False)
        if result is None:
            raise NotFoundError(f"Project not found: {project}")
        stem, _ = result
        if "." in stem:
            raise ValidationError(
                f"'{stem}' is not a project. Can only focus on top-level projects."
            )
        project = stem
        project_path = notes_dir / f"{project}.md"

    # Reject top-level notes — focus is for filtering work under a project.
    try:
        post = frontmatter.load(project_path)
        if post.metadata.get("type") == "note":
            raise ValidationError(
                f"'{project}' is a top-level note, not a project. "
                "Focus only works with projects."
            )
    except ValidationError:
        raise
    except Exception:
        pass

    set_focused_project(project)
    click.echo(click.style(f"Focusing on: {project}", fg="green", bold=True))
    click.echo("Commands like 'cor new', 'cor edit' will default to this project.")


@cli.command(name="inbox")
@click.option("--full-sync", "full_sync", is_flag=True, help="Pull all messages including previously read ones")
@click.option("--delete-after", "delete_after", is_flag=True, help="Delete messages from Telegram after pulling")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what would be pulled without modifying backlog")
@require_init
def inbox_cmd(full_sync: bool, delete_after: bool, dry_run: bool):
    """Test remote inbox connection and pull messages.

    Checks if the Telegram bot is configured and shows pending messages.
    Use --full-sync to pull all message history (not just unread).
    Use --delete-after to clean up messages from Telegram after pulling.
    """
    from ..commands.inbox import test_telegram_connection, pull_remote_inbox

    bot_token = get_remote_inbox()
    if not bot_token:
        click.echo(click.style("Remote inbox not configured", fg="yellow"))
        click.echo("Run: cor config inbox <bot-token>")
        return

    # If dry-run, just show connection info
    if dry_run:
        test_telegram_connection(bot_token)
        return

    # Pull messages from Telegram
    notes_dir = get_notes_dir()
    try:
        added = pull_remote_inbox(
            notes_dir,
            bot_token,
            full_sync=full_sync,
            delete_after_sync=delete_after
        )
        if added:
            click.echo(click.style(f"✓ Pulled {added} items from Telegram inbox", fg="green"))
        else:
            click.echo(click.style("No new messages to pull", fg="yellow"))
    except click.ClickException as e:
        click.echo(click.style(f"Error: {e.message}", fg="red"), err=True)

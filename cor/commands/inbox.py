"""Remote inbox via Telegram bot for Cor CLI."""

import json
from datetime import datetime
from pathlib import Path
# urllib.request is imported lazily in the two functions that talk to
# Telegram; it costs ~75ms and every `cor` command was paying it.

import click
import frontmatter

from ..exceptions import NotFoundError, ExternalServiceError
from ..schema import DATE_TIME
from ..config import get_remote_inbox
from ..utils import get_notes_dir, require_init
from .log import log as _inbox_add
from .process import process as _inbox_process


def _get_message_id(update: dict) -> int | None:
    """Extract message ID from update."""
    message = update.get("message", {})
    return message.get("message_id")


def _extract_message_text(update: dict) -> str:
    """Extract text from a Telegram update."""
    message = update.get("message", {})
    return message.get("text", "").strip()


def _get_existing_inbox_items(backlog_path: Path) -> set[str]:
    """Extract existing inbox items from backlog to avoid duplicates.
    
    Returns a set of normalized message texts (lowercase, stripped).
    """
    if not backlog_path.exists():
        return set()
    
    post = frontmatter.load(backlog_path)
    lines = (post.content or "").splitlines()
    
    existing = set()
    in_inbox = False
    
    for line in lines:
        # Check if we're entering the Inbox section
        if line.strip() == "## Inbox":
            in_inbox = True
            continue
        # Check if we're leaving the Inbox section
        if in_inbox and line.startswith("## "):
            in_inbox = False
            continue
        # Extract item text from inbox lines
        if in_inbox and line.strip().startswith("- "):
            # Remove "- " prefix and normalize
            item_text = line.strip()[2:].strip().lower()
            if item_text:
                existing.add(item_text)
    
    return existing


def test_telegram_connection(bot_token: str):
    """Test Telegram bot connection and show bot info.

    Args:
        bot_token: Telegram bot API token

    Raises:
        ExternalServiceError: If API call fails
    """
    from urllib import request, error
    
    base_url = f"https://api.telegram.org/bot{bot_token}"

    # Get bot info
    try:
        with request.urlopen(f"{base_url}/getMe") as response:
            data = json.loads(response.read())

        if data.get("ok"):
            bot_info = data.get("result", {})
            click.echo(click.style("✓ Bot connection successful", fg="green"))
            click.echo(f"  Bot name: {bot_info.get('first_name')}")
            click.echo(f"  Username: @{bot_info.get('username')}")
            click.echo()
        else:
            raise ExternalServiceError(f"Bot error: {data.get('description')}")

        # Check for updates
        with request.urlopen(f"{base_url}/getUpdates") as response:
            data = json.loads(response.read())

        if data.get("ok"):
            updates = data.get("result", [])
            click.echo(f"Pending messages: {len(updates)}")
            if updates:
                click.echo("\nMessages:")
                for update in updates:
                    msg = update.get("message", {})
                    text = msg.get("text", "")
                    from_user = msg.get("from", {}).get("first_name", "Unknown")
                    click.echo(f"  - {from_user}: {text}")
            else:
                click.echo(click.style("\nNo messages found.", fg="yellow"))
                click.echo("Make sure you:")
                click.echo("  1. Started your bot (send /start)")
                click.echo("  2. Sent at least one message to it")
    except error.URLError as e:
        raise ExternalServiceError(f"Connection failed: {e}")


def pull_remote_inbox(
    notes_dir: Path, 
    bot_token: str, 
    full_sync: bool = False,
    delete_after_sync: bool = False
) -> int:
    """Fetch Telegram messages, append to backlog, delete/acknowledge them.

    Args:
        notes_dir: Path to vault root
        bot_token: Telegram bot API token
        full_sync: If True, fetch all messages (including previously acknowledged)
                   and deduplicate against existing backlog entries
        delete_after_sync: If True, actually delete messages from Telegram chat
                          instead of just acknowledging them

    Returns:
        Number of messages added to backlog

    Raises:
        ExternalServiceError: If API calls fail
    """
    from urllib import request, error

    base_url = f"https://api.telegram.org/bot{bot_token}"
    backlog_path = notes_dir / "backlog.md"
    
    if not backlog_path.exists():
        raise NotFoundError("No backlog.md found. Run 'cor init' first.")

    # Get existing items for deduplication (only needed for full_sync)
    existing_items = _get_existing_inbox_items(backlog_path) if full_sync else set()
    if full_sync and existing_items:
        click.echo(f"Debug: Found {len(existing_items)} existing items in backlog", err=True)

    # 1. Fetch messages from Telegram
    try:
        # For full sync, we use offset=0 or no offset to get recent history
        # For normal sync, we get only unacknowledged messages
        if full_sync:
            # Use offset=-1 to get all messages (including old ones)
            # Actually, Telegram API: negative offset values return all updates
            # But we need to handle pagination for large histories
            updates_url = f"{base_url}/getUpdates?offset=0&limit=100"
        else:
            updates_url = f"{base_url}/getUpdates"
            
        with request.urlopen(updates_url) as response:
            data = json.loads(response.read())
    except error.URLError as e:
        raise ExternalServiceError(f"Failed to fetch Telegram messages: {e}")
    except json.JSONDecodeError:
        raise ExternalServiceError("Invalid response from Telegram API")

    if not data.get("ok"):
        error_msg = data.get("description", "Unknown error")
        raise ExternalServiceError(f"Telegram API error: {error_msg}")

    updates = data.get("result", [])

    if not updates:
        return 0

    # 2. Extract message texts and filter duplicates
    messages_to_add = []
    message_ids = []  # All message IDs for acknowledgment
    delete_ids = []   # Message IDs to delete (if delete_after_sync)

    for update in updates:
        update_id = update.get("update_id")
        text = _extract_message_text(update)
        message_id = _get_message_id(update)

        if text and update_id is not None:
            message_ids.append(update_id)
            
            # Check for duplicates in full_sync mode
            if full_sync:
                normalized_text = text.lower().strip()
                if normalized_text in existing_items:
                    click.echo(f"Debug: Skipping duplicate: '{text[:50]}...'", err=True)
                    continue
            
            messages_to_add.append(text)
            if message_id and delete_after_sync:
                delete_ids.append((update.get("message", {}).get("chat", {}).get("id"), message_id))

    if not messages_to_add:
        if full_sync:
            click.echo("No new messages to add (all duplicates).")
        # Still acknowledge if needed
        if message_ids and not delete_after_sync and not full_sync:
            last_update_id = max(message_ids)
            try:
                offset_url = f"{base_url}/getUpdates?offset={last_update_id + 1}"
                request.urlopen(offset_url).read()
            except error.URLError:
                pass
        return 0

    # 3. Append to backlog
    post = frontmatter.load(backlog_path)
    lines = (post.content or "").splitlines()

    # Ensure Inbox section exists
    inbox_idx = next((i for i, line in enumerate(lines) if line.strip() == "## Inbox"), None)
    if inbox_idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("## Inbox")
        inbox_idx = len(lines) - 1

    # Find insertion point (end of inbox section, before next heading)
    insert_idx = inbox_idx + 1
    for j in range(inbox_idx + 1, len(lines)):
        if lines[j].startswith("## "):
            insert_idx = j
            break
        insert_idx = j + 1

    # Insert all messages
    for msg in messages_to_add:
        lines.insert(insert_idx, f"- {msg}")
        insert_idx += 1

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"

    post["modified"] = datetime.now().strftime(DATE_TIME)
    post.content = new_content

    with open(backlog_path, "wb") as f:
        frontmatter.dump(post, f, sort_keys=False)

    # 4. Clear messages from Telegram
    if delete_after_sync and delete_ids:
        # Actually delete messages from the chat
        deleted_count = 0
        for chat_id, message_id in delete_ids:
            try:
                delete_url = f"{base_url}/deleteMessage?chat_id={chat_id}&message_id={message_id}"
                with request.urlopen(delete_url) as response:
                    delete_data = json.loads(response.read())
                    if delete_data.get("ok"):
                        deleted_count += 1
            except (error.URLError, json.JSONDecodeError):
                # Non-fatal: message might be too old to delete or other error
                pass
        
        if deleted_count > 0:
            click.echo(
                click.style(
                    f"Deleted {deleted_count} messages from Telegram",
                    fg="cyan",
                ),
                err=True,
            )
    elif message_ids:
        # Use offset to acknowledge updates (mark as read)
        last_update_id = max(message_ids)
        try:
            offset_url = f"{base_url}/getUpdates?offset={last_update_id + 1}"
            request.urlopen(offset_url).read()
        except error.URLError:
            # Non-fatal: messages were saved, just not cleared from Telegram
            click.echo(
                click.style(
                    "Warning: Messages saved but couldn't clear from Telegram",
                    fg="yellow",
                ),
                err=True,
            )

    return len(messages_to_add)


@click.group(name="inbox")
def inbox():
    """Capture and process backlog items (manual entries and Telegram).

    \b
    Subcommands:
      add TEXT   Append a line to the backlog Inbox
      pull       Pull messages from the configured Telegram bot
      process    Interactively file backlog items into projects/tasks
    """
    pass


@inbox.command(name="pull")
@click.option("--full-sync", "full_sync", is_flag=True, help="Pull all messages including previously read ones")
@click.option("--delete-after", "delete_after", is_flag=True, help="Delete messages from Telegram after pulling")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what would be pulled without modifying backlog")
@require_init
def inbox_pull(full_sync: bool, delete_after: bool, dry_run: bool):
    """Pull messages from the configured Telegram bot into the backlog.

    Use --full-sync to pull all message history (not just unread).
    Use --delete-after to clean up messages from Telegram after pulling.
    """
    bot_token = get_remote_inbox()
    if not bot_token:
        click.echo(click.style("Remote inbox not configured", fg="yellow"))
        click.echo("Run: cor config inbox <bot-token>")
        return

    if dry_run:
        test_telegram_connection(bot_token)
        return

    notes_dir = get_notes_dir()
    try:
        added = pull_remote_inbox(
            notes_dir,
            bot_token,
            full_sync=full_sync,
            delete_after_sync=delete_after,
        )
        if added:
            click.echo(click.style(f"✓ Pulled {added} items from Telegram inbox", fg="green"))
        else:
            click.echo(click.style("No new messages to pull", fg="yellow"))
    except click.ClickException as e:
        click.echo(click.style(f"Error: {e.message}", fg="red"), err=True)


# Reuse the standalone capture/processing commands as inbox subcommands.
inbox.add_command(_inbox_add, name="add")
inbox.add_command(_inbox_process, name="process")

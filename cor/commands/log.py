"""Backlog logging command for Cor CLI."""

from datetime import datetime

import click
import frontmatter

from ..core.files import save_note

from ..exceptions import ValidationError, NotFoundError
from ..utils import get_notes_dir, require_init, log_info
from ..schema import DATE_TIME


@click.command(short_help="Append text to backlog inbox")
@click.argument("text", nargs=-1)
@require_init
def log(text: str):
    """Append a line item to the backlog inbox.

    Ensures the Inbox section exists and adds the provided text as a bullet.
    Updates the backlog modified timestamp.
    """
    normalized = (" ".join(text) or "").strip()
    if not normalized:
        raise ValidationError("Text cannot be empty.")

    notes_dir = get_notes_dir()
    backlog_path = notes_dir / "backlog.md"
    if not backlog_path.exists():
        raise NotFoundError("No backlog.md found. Run 'cor init' first.")

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

    lines.insert(insert_idx, f"- {normalized}")

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"

    post["modified"] = datetime.now().strftime(DATE_TIME)
    post.content = new_content

    save_note(backlog_path, post)

    log_info(click.style("Appended to backlog inbox.", fg="green"))

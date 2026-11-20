"""When did an entry, or a whole project, last actually move?

``modified`` is stamped on every commit and on every batch write, so a bulk
commit or a reschedule makes an idle project look busy. The status stamps
(``started`` when a task first becomes active, ``completed`` when it is
closed) and the creation date are honest signals; ``modified`` is only the
fallback for entries that carry no stamp.

Pure functions over parsed notes: no I/O, so the agenda, ``cor projects`` and
``cor weekly`` can share one definition of "last activity".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..schema import DATE_TIME

#: Mirrors ``cor.core.notes.CLOSED_STATUSES`` (that module imports this one).
_CLOSED = ("done", "dropped")

#: Sentinel for "the previous status is not known" (new file, no git history).
UNKNOWN_STATUS = object()

#: Default number of idle days after which a project is flagged as stale in
#: ``cor projects`` and by ``Note.is_stale``. Overridable as
#: ``agenda.project_stale_days`` in the config.
STALE_DAYS_DEFAULT = 14


@dataclass(frozen=True)
class Activity:
    """The most recent sign of work on an entry and where it came from."""

    when: datetime | None
    source: str  # "completed" | "started" | "created" | "modified" | "none"


def note_activity(note) -> Activity:
    """Return the latest activity of one entry.

    A stamped entry (``started`` or ``completed`` set) is dated by the latest
    of its stamps and its creation, ignoring ``modified`` entirely. An
    unstamped entry falls back to the latest of ``created`` and ``modified``.
    """
    started = getattr(note, "started", None)
    completed = getattr(note, "completed", None)
    created = getattr(note, "created", None)
    modified = getattr(note, "modified", None)

    if started or completed:
        candidates = [(when, source) for when, source in (
            (completed, "completed"), (started, "started"), (created, "created"),
        ) if when]
    else:
        candidates = [(when, source) for when, source in (
            (modified, "modified"), (created, "created"),
        ) if when]
    if not candidates:
        return Activity(None, "none")
    when, source = max(candidates, key=lambda pair: pair[0])
    return Activity(when, source)


def _is_member(stem: str, root: str) -> bool:
    return stem == root or stem.startswith(root + ".")


def project_activity(project_stem: str, notes: Iterable) -> Activity:
    """Latest activity across a project and everything filed under it.

    Pass active and archived notes together: closed tasks live in
    ``archive/``, and their ``completed`` stamps are the clearest evidence a
    project moved.
    """
    best = Activity(None, "none")
    for note in notes:
        if not _is_member(note.path.stem, project_stem):
            continue
        activity = note_activity(note)
        if activity.when and (best.when is None or activity.when > best.when):
            best = activity
    return best


def project_last_advanced(project_stem: str, notes: Iterable) -> datetime | None:
    """When the project last moved, or None when nothing under it is dated."""
    return project_activity(project_stem, notes).when


def apply_activity_stamps(post, *, previous_status, now: datetime | None = None) -> bool:
    """Stamp ``started``/``completed`` on a frontmatter post after a status change.

    ``previous_status`` is the status before the change, or
    :data:`UNKNOWN_STATUS` when there is nothing to compare against. Rules:

    - entering ``active`` sets ``started`` once; it is kept on reopen, since it
      records when work first began;
    - entering ``done``/``dropped`` sets ``completed`` when absent;
    - leaving a closed status clears ``completed`` so it never contradicts
      ``status``;
    - a known, unchanged status stamps nothing, so a bulk commit of untouched
      files leaves legacy entries unstamped instead of dating them "now".

    Returns True when the post was changed.
    """
    if post.get("type") not in ("task", "project"):
        return False
    status = post.get("status")
    stamp = (now or datetime.now()).strftime(DATE_TIME)
    fresh = previous_status is UNKNOWN_STATUS
    changed = False

    if status == "active" and not post.get("started") and (fresh or previous_status != "active"):
        post["started"] = stamp
        changed = True
    if status in _CLOSED and not post.get("completed") and (fresh or previous_status not in _CLOSED):
        post["completed"] = stamp
        changed = True
    if status not in _CLOSED and post.get("completed"):
        del post["completed"]
        changed = True
    return changed


def resolve_stale_days(agenda_config) -> int:
    """The project staleness threshold from an ``agenda:`` config block."""
    if isinstance(agenda_config, dict):
        value = agenda_config.get("project_stale_days")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return STALE_DAYS_DEFAULT

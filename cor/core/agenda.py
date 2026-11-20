"""Forecast and ranked next actions over a vault's notes.

Pure computation: no click, no colours, no I/O beyond an optional backlog
read. That keeps it unit-testable with in-memory ``Note`` objects and lets any
renderer (the ``cor daily`` text view, its ``--json`` output, a future
scheduled briefing) consume the same result.

The view has three parts:

- **Timeline** (overdue / today / upcoming): every open task or project with a
  due date, bucketed by calendar day relative to ``today``.
- **Next**: one ranked list of actionable leaf tasks, ordered by an additive,
  bounded urgency score. Each component produces a human-readable reason, so
  the text and JSON views can never disagree about *why* an item ranks where
  it does.
- **Follow-up**: stale deadlines, waiting tasks, blocked tasks, active projects
  with no next action, idle projects, and the number of unprocessed inbox
  lines.

Two rules keep deadlines and neglected projects in balance:

- A deadline long past is not "very urgent", it is a *stale* deadline that
  needs rescheduling or dropping. Beyond ``overdue_stale_days`` an overdue
  task leaves NEXT and is listed for triage instead.
- A live project that has not moved for longer than its cadence is *idle*.
  It is listed, and its single best task gets a bounded ``project_idle``
  bonus so it surfaces when nothing is due. One task per project, so a
  project with many old tasks cannot flood the list, and the cap sits below
  "due today", so a real deadline always wins.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..dependencies import calculate_inverse_dependencies, check_dependencies_met
from ..schema import DATE_TIME
from .activity import project_last_advanced
from .notes import CLOSED_STATUSES, Note, build_project_tags, due_to_iso, matches_tag

#: Task states that can be worked on right now.
ACTIONABLE_STATUSES = ("todo", "active")
#: Project states whose tasks are never recommended as next actions.
INACTIVE_PROJECT_STATUSES = ("paused", "done")
#: Urgency components, in the order their reasons are reported.
COMPONENTS = ("due", "priority", "active", "blocking", "active_project", "untouched", "project_idle")
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

#: Default urgency weights and thresholds. All components are additive and
#: bounded. Override any key under ``agenda:`` in the config.
DEFAULT_WEIGHTS: dict[str, float] = {
    # Any overdue item outranks anything merely upcoming; lateness compounds
    # until the cap, after which the deadline itself is the ceiling.
    "overdue_base": 6.0,
    "overdue_per_day": 0.5,
    "overdue_cap": 12.0,
    # Overdue for longer than this is a stale deadline: it leaves NEXT and is
    # listed under FOLLOW-UP to be rescheduled or dropped.
    "overdue_stale_days": 14,
    # Above the best upcoming value, below any overdue one.
    "due_today": 5.0,
    # Linear decay inside the horizon: 4 * (1 - d / (horizon + 1)).
    "due_horizon_max": 4.0,
    # A dated item beyond the horizon still edges out an undated one.
    "due_soon_bump": 1.0,
    "due_soon_days": 14,
    # Explicit priority is rare, so when set it must be felt.
    "priority_high": 4.0,
    "priority_medium": 2.0,
    "priority_low": 0.5,
    # Finish what you started.
    "active": 2.0,
    # Each open dependent this item unblocks is throughput for others, but a
    # hub task never outranks a deadline.
    "blocking_each": 1.0,
    "blocking_cap": 3.0,
    # Tie-break toward projects you declared active.
    "active_project": 1.0,
    # Active but idle: finish it or drop it.
    "untouched": 1.0,
    "untouched_days": 7,
    # A project is idle once it has not moved for more than its cadence
    # (per-project ``cadence:`` or this default). Its best task then gets
    # idle_base + idle_per_day * (idle - cadence), capped at idle_cap, which
    # stays below due_today so a deadline always wins.
    "project_cadence_days": 7,
    "idle_base": 1.0,
    "idle_per_day": 0.25,
    "idle_cap": 4.0,
    # Idle for longer than this is flagged as stale by ``cor projects``.
    "project_stale_days": 14,
}


def resolve_weights(overrides: Mapping | None) -> dict[str, float]:
    """Merge numeric overrides for known keys into the defaults.

    Unknown keys and non-numeric values are ignored so a typo in the config
    can never break the daily view.
    """
    weights = dict(DEFAULT_WEIGHTS)
    if isinstance(overrides, Mapping):
        for key, value in overrides.items():
            if key in weights and isinstance(value, (int, float)) and not isinstance(value, bool):
                weights[key] = float(value)
    return weights


def count_inbox_items(backlog_path: str | Path) -> int:
    """Count ``- `` bullets under ``## Inbox`` in the backlog (0 when missing)."""
    try:
        text = Path(backlog_path).read_text()
    except OSError:
        return 0
    count = 0
    in_inbox = False
    for line in text.splitlines():
        if line.strip() == "## Inbox":
            in_inbox = True
            continue
        if not in_inbox:
            continue
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("- ") and len(stripped) > 2:
            count += 1
    return count


def _stamp(value: datetime | None) -> str | None:
    return value.strftime(DATE_TIME) if value else None


@dataclass
class AgendaItem:
    """One open task or project as seen by the agenda."""

    stem: str
    title: str
    note_type: str
    status: str | None
    priority: str | None
    due: datetime | None
    due_has_time: bool
    days_until_due: int | None
    project: str
    parent: str | None
    tags: list[str]
    modified: datetime | None
    days_since_modified: int | None
    days_since_created: int | None
    blocks: list[str]
    blocked_by: list[str]
    missing_requirements: list[str]
    has_open_children: bool
    urgency: float = 0.0
    urgency_components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    #: Overdue for longer than ``overdue_stale_days``: triage, not NEXT.
    stale: bool = False
    #: Days since the item's project last moved (None when unknown).
    project_idle_days: int | None = None
    #: Set by a multi-vault caller; a single-vault agenda leaves it None.
    vault: str | None = None
    note: Note | None = field(default=None, repr=False, compare=False)

    @property
    def due_date(self) -> date | None:
        return self.due.date() if self.due else None

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority or "", len(PRIORITY_RANK))

    @property
    def due_key(self):
        """Sort key for the timeline buckets: soonest, then priority, then stem."""
        return (self.due, self.priority_rank, self.stem)

    @property
    def rank_key(self):
        """Sort key for NEXT: urgency, then due, priority, older first, stem."""
        return (-self.urgency, self.due_date or date.max, self.priority_rank,
                -(self.days_since_created or 0), self.stem)

    def to_dict(self) -> dict:
        """The stable JSON record. Dates use the vault's frontmatter formats."""
        return {
            "stem": self.stem,
            "title": self.title,
            "type": self.note_type,
            "status": self.status,
            "priority": self.priority,
            "due": due_to_iso(self.due),
            "due_has_time": self.due_has_time,
            "days_until_due": self.days_until_due,
            "project": self.project,
            "parent": self.parent,
            "tags": list(self.tags),
            "modified": _stamp(self.modified),
            "days_since_modified": self.days_since_modified,
            "blocks": list(self.blocks),
            "blocked_by": list(self.blocked_by),
            "urgency": self.urgency,
            "reasons": list(self.reasons),
            "urgency_components": dict(self.urgency_components),
            "stale": self.stale,
            "project_idle_days": self.project_idle_days,
            "vault": self.vault,
        }


@dataclass
class StuckProject:
    """An active project with no task that can be worked on."""

    stem: str
    title: str
    status: str | None
    last_activity: datetime | None
    vault: str | None = None

    def to_dict(self) -> dict:
        return {
            "stem": self.stem,
            "title": self.title,
            "status": self.status,
            "last_activity": _stamp(self.last_activity),
            "vault": self.vault,
        }


@dataclass
class IdleProject:
    """A live project with work available that has not moved for too long."""

    stem: str
    title: str
    status: str | None
    idle_days: int
    cadence_days: int
    open_tasks: int
    last_activity: datetime | None
    vault: str | None = None

    def to_dict(self) -> dict:
        return {
            "stem": self.stem,
            "title": self.title,
            "status": self.status,
            "idle_days": self.idle_days,
            "cadence_days": self.cadence_days,
            "open_tasks": self.open_tasks,
            "last_activity": _stamp(self.last_activity),
            "vault": self.vault,
        }


@dataclass
class Agenda:
    """Everything ``cor daily`` shows, before any rendering decision."""

    date: date
    #: Vault content hash; None for a view merged over several vaults (use
    #: ``vaults[i]["revision"]`` instead).
    revision: str | None
    focus: str | None
    tag: str | None
    horizon_days: int
    overdue: list[AgendaItem]
    today: list[AgendaItem]
    upcoming: list[AgendaItem]
    next_items: list[AgendaItem]
    waiting: list[AgendaItem]
    blocked: list[AgendaItem]
    stuck_projects: list[StuckProject]
    inbox_count: int
    stale: list[AgendaItem] = field(default_factory=list)
    idle_projects: list[IdleProject] = field(default_factory=list)
    #: One record per vault read; empty for a plain single-vault agenda.
    vaults: list[dict] = field(default_factory=list)

    @property
    def overdue_live(self) -> list[AgendaItem]:
        """Overdue items still worth showing as such (stale ones are triage)."""
        return [item for item in self.overdue if not item.stale]

    @property
    def is_clear(self) -> bool:
        """True when the visible timeline and NEXT are both empty."""
        return not (self.overdue_live or self.today or self.upcoming or self.next_items)

    def to_dict(self) -> dict:
        items = lambda seq: [item.to_dict() for item in seq]  # noqa: E731
        return {
            "ok": True,
            "date": self.date.isoformat(),
            "revision": self.revision,
            "focus": self.focus,
            "tag": self.tag,
            "horizon_days": self.horizon_days,
            "overdue": items(self.overdue),
            "today": items(self.today),
            "upcoming": items(self.upcoming),
            "next": items(self.next_items),
            "follow_up": {
                "stale": items(self.stale),
                "waiting": items(self.waiting),
                "blocked": items(self.blocked),
                "stuck_projects": [project.to_dict() for project in self.stuck_projects],
                "idle_projects": [project.to_dict() for project in self.idle_projects],
                "inbox_count": self.inbox_count,
            },
            "counts": {
                "overdue": len(self.overdue),
                "today": len(self.today),
                "upcoming": len(self.upcoming),
                "next": len(self.next_items),
                "stale": len(self.stale),
                "waiting": len(self.waiting),
                "blocked": len(self.blocked),
                "stuck_projects": len(self.stuck_projects),
                "idle_projects": len(self.idle_projects),
            },
            "vaults": list(self.vaults),
        }


def score_item(
    item: AgendaItem,
    *,
    horizon_days: int,
    project_status: str | None,
    weights: Mapping[str, float],
    titles: Mapping[str, str] | None = None,
    idle_bonus: float = 0.0,
    idle_days: int | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    """Return ``(urgency, components, reasons)`` for one item.

    Every component is filled (0.0 when it does not apply) and every reason is
    derived from the branch that produced a non-zero component, so the two can
    never drift apart. ``idle_bonus`` is the project-idle component the caller
    grants to one task per idle project; ``idle_days`` labels its reason.
    """
    w = weights
    titles = titles or {}
    components = {name: 0.0 for name in COMPONENTS}
    reasons: list[str] = []

    d = item.days_until_due
    if d is not None:
        if d < 0:
            components["due"] = min(w["overdue_cap"], w["overdue_base"] + w["overdue_per_day"] * -d)
            suffix = " (stale)" if -d > w["overdue_stale_days"] else ""
            reasons.append(f"overdue {-d}d{suffix}")
        elif d == 0:
            components["due"] = w["due_today"]
            clock = f" {item.due:%H:%M}" if item.due_has_time and item.due else ""
            reasons.append(f"due today{clock}")
        else:
            value = 0.0
            if d <= horizon_days:
                value += w["due_horizon_max"] * (1 - d / (horizon_days + 1))
            if d <= w["due_soon_days"]:
                value += w["due_soon_bump"]
            components["due"] = value
            if value > 0:
                reasons.append("due tomorrow" if d == 1 else f"due {d}d")

    if item.priority in PRIORITY_RANK:
        components["priority"] = w[f"priority_{item.priority}"]
        reasons.append(item.priority)

    if item.status == "active":
        components["active"] = w["active"]
        reasons.append("active")

    if item.blocks:
        components["blocking"] = min(w["blocking_cap"], w["blocking_each"] * len(item.blocks))
        if len(item.blocks) == 1:
            reasons.append(f"unblocks {titles.get(item.blocks[0], item.blocks[0])}")
        else:
            reasons.append(f"blocks {len(item.blocks)}")

    if project_status == "active":
        components["active_project"] = w["active_project"]

    if (
        item.status == "active"
        and item.days_since_modified is not None
        and item.days_since_modified > w["untouched_days"]
    ):
        components["untouched"] = w["untouched"]
        reasons.append(f"untouched {item.days_since_modified}d")

    if idle_bonus > 0:
        components["project_idle"] = idle_bonus
        reasons.append(f"project idle {idle_days}d" if idle_days is not None else "project idle")

    components = {name: round(value, 2) for name, value in components.items()}
    return round(sum(components.values()), 2), components, reasons


def idle_bonus_for(idle_days: int | None, cadence_days: float, weights: Mapping[str, float]) -> float:
    """The ``project_idle`` component for a project idle ``idle_days`` days (0 within cadence)."""
    if idle_days is None or idle_days <= cadence_days:
        return 0.0
    return min(weights["idle_cap"], weights["idle_base"] + weights["idle_per_day"] * (idle_days - cadence_days))


def _parent_stem(stem: str) -> str | None:
    return stem.rsplit(".", 1)[0] if "." in stem else None


def _root_stem(stem: str) -> str:
    return stem.split(".", 1)[0]


def _make_item(
    note: Note,
    *,
    today: date,
    now: datetime,
    notes: Sequence[Note],
    by_stem: Mapping[str, Note],
    inverse: Mapping[str, list[str]],
    open_children: set[str],
) -> AgendaItem:
    stem = note.path.stem
    blocks = sorted(
        dependent
        for dependent in inverse.get(stem, [])
        if dependent in by_stem and by_stem[dependent].status not in CLOSED_STATUSES
    )
    blocked_by = list(check_dependencies_met(note, list(notes))[1]) if note.requires else []
    missing = [target for target in (note.requires or []) if target not in by_stem]
    return AgendaItem(
        stem=stem,
        title=note.title,
        note_type=note.note_type,
        status=note.status,
        priority=note.priority,
        due=note.due,
        due_has_time=note.due_has_time,
        days_until_due=note.days_until_due(today),
        project=_root_stem(stem),
        parent=_parent_stem(stem),
        tags=list(note.tags or []),
        modified=note.modified,
        days_since_modified=(now - note.modified).days if note.modified else None,
        days_since_created=(now - note.created).days if note.created else None,
        blocks=blocks,
        blocked_by=blocked_by,
        missing_requirements=missing,
        has_open_children=stem in open_children,
        note=note,
    )


def build_agenda(
    notes: Sequence[Note],
    *,
    today: date,
    now: datetime | None = None,
    horizon_days: int = 7,
    tag: str | None = None,
    focus: str | None = None,
    inbox_count: int = 0,
    revision: str = "",
    weights: Mapping[str, float] | None = None,
    archived_notes: Sequence = (),
) -> Agenda:
    """Compute the agenda for ``today`` from already-parsed notes.

    ``tag`` (explicit filter) wins over ``focus`` (the configured project);
    either narrows every section using the usual tag semantics: project name,
    note tag, or a tag inherited from the parent project.

    ``archived_notes`` (metadata is enough) only feed project activity: a
    closed task's ``completed`` stamp is the clearest sign a project moved.
    They never appear in any section.
    """
    now = now or datetime.now()
    weights = resolve_weights(weights)
    effective_tag = tag or focus
    notes = list(notes)

    project_tags = build_project_tags(notes)
    by_stem = {n.path.stem: n for n in notes}
    titles = {stem: n.title for stem, n in by_stem.items()}
    project_notes = {n.path.stem: n for n in notes if n.note_type == "project"}
    project_status = {stem: n.status for stem, n in project_notes.items()}
    inverse = calculate_inverse_dependencies(notes)
    open_children = {
        _parent_stem(n.path.stem)
        for n in notes
        if n.note_type == "task" and n.status not in CLOSED_STATUSES and "." in n.path.stem
    }

    # Project activity: when did each project last move, and is it idle?
    for_activity = notes + list(archived_notes)
    last_activity: dict[str, datetime | None] = {}
    idle_days: dict[str, int | None] = {}
    for root in project_notes:
        last = project_last_advanced(root, for_activity)
        last_activity[root] = last
        idle_days[root] = (today - last.date()).days if last else None

    def cadence_of(root: str) -> float:
        cadence = getattr(project_notes.get(root), "cadence", None)
        return float(cadence) if cadence is not None else weights["project_cadence_days"]

    items: list[AgendaItem] = []
    for note in notes:
        if note.note_type not in ("task", "project") or note.status in CLOSED_STATUSES:
            continue
        if not matches_tag(note, effective_tag, project_tags):
            continue
        item = _make_item(
            note, today=today, now=now, notes=notes, by_stem=by_stem,
            inverse=inverse, open_children=open_children,
        )
        item.urgency, item.urgency_components, item.reasons = score_item(
            item,
            horizon_days=horizon_days,
            project_status=project_status.get(item.project),
            weights=weights,
            titles=titles,
        )
        item.project_idle_days = idle_days.get(item.project)
        d = item.days_until_due
        item.stale = d is not None and -d > weights["overdue_stale_days"]
        items.append(item)

    dated = [item for item in items if item.days_until_due is not None]
    overdue = sorted((i for i in dated if i.days_until_due < 0), key=lambda i: i.due_key)
    due_today = sorted((i for i in dated if i.days_until_due == 0), key=lambda i: i.due_key)
    upcoming = sorted((i for i in dated if 0 < i.days_until_due <= horizon_days), key=lambda i: i.due_key)
    stale = sorted((i for i in overdue if i.stale and i.note_type == "task"), key=lambda i: i.due_key)

    candidates = [
        item
        for item in items
        if item.note_type == "task"
        and item.status in ACTIONABLE_STATUSES
        and not item.blocked_by
        and not item.has_open_children
        and not item.stale
        and project_status.get(item.project) not in INACTIVE_PROJECT_STATUSES
    ]

    # One bounded bonus per idle project, on its best task only.
    by_project: dict[str, list[AgendaItem]] = {}
    for item in candidates:
        by_project.setdefault(item.project, []).append(item)
    for root, members in by_project.items():
        bonus = idle_bonus_for(idle_days.get(root), cadence_of(root), weights)
        if bonus <= 0:
            continue
        best = min(members, key=lambda i: i.rank_key)
        best.urgency, best.urgency_components, best.reasons = score_item(
            best,
            horizon_days=horizon_days,
            project_status=project_status.get(root),
            weights=weights,
            titles=titles,
            idle_bonus=bonus,
            idle_days=idle_days.get(root),
        )

    next_items = sorted(candidates, key=lambda i: i.rank_key)

    waiting = sorted(
        (i for i in items if i.note_type == "task" and i.status == "waiting"),
        key=lambda i: (-(i.days_since_modified or 0), i.stem),
    )
    blocked = sorted(
        (
            i for i in items
            if i.note_type == "task"
            and (i.status == "blocked" or (i.blocked_by and i.status in ACTIONABLE_STATUSES))
        ),
        key=lambda i: (i.due_date or date.max, i.stem),
    )

    stuck: list[StuckProject] = []
    idle: list[IdleProject] = []
    for root, note in project_notes.items():
        if note.status in INACTIVE_PROJECT_STATUSES or not matches_tag(note, effective_tag, project_tags):
            continue
        members = [n for n in notes if n.path.stem != root and _root_stem(n.path.stem) == root]
        actionable = any(n.note_type == "task" and n.status in ACTIONABLE_STATUSES for n in members)
        if note.status == "active" and not actionable:
            stuck.append(StuckProject(
                stem=root, title=note.title, status=note.status, last_activity=last_activity[root],
            ))
            continue
        days = idle_days.get(root)
        cadence = cadence_of(root)
        if root in by_project and days is not None and days > cadence:
            idle.append(IdleProject(
                stem=root,
                title=note.title,
                status=note.status,
                idle_days=days,
                cadence_days=int(cadence),
                open_tasks=sum(1 for n in members if n.note_type == "task" and n.status not in CLOSED_STATUSES),
                last_activity=last_activity[root],
            ))
    stuck.sort(key=lambda project: project.stem)
    idle.sort(key=lambda project: (-project.idle_days, project.stem))

    return Agenda(
        date=today,
        revision=revision,
        focus=focus,
        tag=effective_tag,
        horizon_days=horizon_days,
        overdue=overdue,
        today=due_today,
        upcoming=upcoming,
        next_items=next_items,
        waiting=waiting,
        blocked=blocked,
        stuck_projects=stuck,
        inbox_count=inbox_count,
        stale=stale,
        idle_projects=idle,
    )

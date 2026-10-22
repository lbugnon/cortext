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
- **Follow-up**: waiting tasks, blocked tasks, active projects with no next
  action, and the number of unprocessed inbox lines.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..dependencies import calculate_inverse_dependencies, check_dependencies_met
from ..schema import DATE_TIME
from .notes import CLOSED_STATUSES, Note, build_project_tags, due_to_iso, matches_tag

#: Task states that can be worked on right now.
ACTIONABLE_STATUSES = ("todo", "active")
#: Project states whose tasks are never recommended as next actions.
INACTIVE_PROJECT_STATUSES = ("paused", "done")
#: Urgency components, in the order their reasons are reported.
COMPONENTS = ("due", "priority", "active", "blocking", "active_project", "age", "untouched")
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

#: Default urgency weights. All components are additive and bounded, so the
#: maximum score is about 23. Override any key under ``agenda:`` in the config.
DEFAULT_WEIGHTS: dict[str, float] = {
    # Any overdue item outranks anything merely upcoming; lateness compounds
    # until the cap, after which the deadline itself is the ceiling.
    "overdue_base": 6.0,
    "overdue_per_day": 0.5,
    "overdue_cap": 12.0,
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
    # Anti-starvation only.
    "age_per_day": 0.02,
    "age_cap": 1.0,
    # Active but idle: finish it or drop it.
    "untouched": 1.0,
    "untouched_days": 7,
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
    note: Note | None = field(default=None, repr=False, compare=False)

    @property
    def due_date(self) -> date | None:
        return self.due.date() if self.due else None

    @property
    def priority_rank(self) -> int:
        return PRIORITY_RANK.get(self.priority or "", len(PRIORITY_RANK))

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
        }


@dataclass
class StuckProject:
    """An active project with no task that can be worked on."""

    stem: str
    title: str
    status: str | None
    last_activity: datetime | None

    def to_dict(self) -> dict:
        return {
            "stem": self.stem,
            "title": self.title,
            "status": self.status,
            "last_activity": _stamp(self.last_activity),
        }


@dataclass
class Agenda:
    """Everything ``cor daily`` shows, before any rendering decision."""

    date: date
    revision: str
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

    @property
    def is_clear(self) -> bool:
        """True when the timeline and NEXT are both empty."""
        return not (self.overdue or self.today or self.upcoming or self.next_items)

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
                "waiting": items(self.waiting),
                "blocked": items(self.blocked),
                "stuck_projects": [project.to_dict() for project in self.stuck_projects],
                "inbox_count": self.inbox_count,
            },
            "counts": {
                "overdue": len(self.overdue),
                "today": len(self.today),
                "upcoming": len(self.upcoming),
                "next": len(self.next_items),
                "waiting": len(self.waiting),
                "blocked": len(self.blocked),
                "stuck_projects": len(self.stuck_projects),
            },
        }


def score_item(
    item: AgendaItem,
    *,
    horizon_days: int,
    project_status: str | None,
    weights: Mapping[str, float],
    titles: Mapping[str, str] | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    """Return ``(urgency, components, reasons)`` for one item.

    Every component is filled (0.0 when it does not apply) and every reason is
    derived from the branch that produced a non-zero component, so the two can
    never drift apart.
    """
    w = weights
    titles = titles or {}
    components = {name: 0.0 for name in COMPONENTS}
    reasons: list[str] = []

    d = item.days_until_due
    if d is not None:
        if d < 0:
            components["due"] = min(w["overdue_cap"], w["overdue_base"] + w["overdue_per_day"] * -d)
            reasons.append(f"overdue {-d}d")
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

    if item.days_since_created is not None:
        components["age"] = min(w["age_cap"], w["age_per_day"] * max(0, item.days_since_created))

    if (
        item.status == "active"
        and item.days_since_modified is not None
        and item.days_since_modified > w["untouched_days"]
    ):
        components["untouched"] = w["untouched"]
        reasons.append(f"untouched {item.days_since_modified}d")

    components = {name: round(value, 2) for name, value in components.items()}
    return round(sum(components.values()), 2), components, reasons


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
) -> Agenda:
    """Compute the agenda for ``today`` from already-parsed notes.

    ``tag`` (explicit filter) wins over ``focus`` (the configured project);
    either narrows every section using the usual tag semantics: project name,
    note tag, or a tag inherited from the parent project.
    """
    now = now or datetime.now()
    weights = resolve_weights(weights)
    effective_tag = tag or focus
    notes = list(notes)

    project_tags = build_project_tags(notes)
    by_stem = {n.path.stem: n for n in notes}
    titles = {stem: n.title for stem, n in by_stem.items()}
    project_status = {n.path.stem: n.status for n in notes if n.note_type == "project"}
    inverse = calculate_inverse_dependencies(notes)
    open_children = {
        _parent_stem(n.path.stem)
        for n in notes
        if n.note_type == "task" and n.status not in CLOSED_STATUSES and "." in n.path.stem
    }

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
        items.append(item)

    def by_due(item: AgendaItem):
        return (item.due, item.priority_rank, item.stem)

    dated = [item for item in items if item.days_until_due is not None]
    overdue = sorted((i for i in dated if i.days_until_due < 0), key=by_due)
    due_today = sorted((i for i in dated if i.days_until_due == 0), key=by_due)
    upcoming = sorted((i for i in dated if 0 < i.days_until_due <= horizon_days), key=by_due)

    next_items = [
        item
        for item in items
        if item.note_type == "task"
        and item.status in ACTIONABLE_STATUSES
        and not item.blocked_by
        and not item.has_open_children
        and project_status.get(item.project) not in INACTIVE_PROJECT_STATUSES
    ]
    next_items.sort(key=lambda i: (-i.urgency, i.due_date or date.max, i.priority_rank, i.stem))

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
    for note in notes:
        if note.note_type != "project" or note.status != "active":
            continue
        if not matches_tag(note, effective_tag, project_tags):
            continue
        root = note.path.stem
        members = [n for n in notes if n.path.stem != root and _root_stem(n.path.stem) == root]
        if any(n.note_type == "task" and n.status in ACTIONABLE_STATUSES for n in members):
            continue
        activity = [n.modified for n in members if n.modified]
        stuck.append(StuckProject(
            stem=root,
            title=note.title,
            status=note.status,
            last_activity=max(activity) if activity else note.modified,
        ))
    stuck.sort(key=lambda project: project.stem)

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
    )

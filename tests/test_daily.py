"""Tests for `cor daily`: the pure agenda (forecast + next) and the CLI around it."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import frontmatter
import pytest

from cor.cli import cli
from cor.core.agenda import (
    COMPONENTS,
    DEFAULT_WEIGHTS,
    build_agenda,
    count_inbox_items,
    resolve_weights,
)
from cor.core.notes import Note

TODAY = date(2026, 9, 13)
NOW = datetime(2026, 9, 13, 9, 0)


def at(days: int, hour: int = 0, minute: int = 0) -> datetime:
    """A datetime ``days`` from TODAY (negative = past)."""
    return datetime.combine(TODAY + timedelta(days=days), datetime.min.time()).replace(hour=hour, minute=minute)


def mk(stem: str, *, type: str = "task", status: str = "todo", due: datetime | None = None,
       priority: str | None = None, requires=(), modified: datetime | None = None,
       created: datetime | None = None, tags=(), title: str | None = None, content: str = "",
       cadence: int | None = None, started: datetime | None = None,
       completed: datetime | None = None) -> Note:
    leaf = stem.rsplit(".", 1)[-1]
    return Note(
        path=Path(f"{stem}.md"),
        title=title or leaf.replace("_", " ").capitalize(),
        note_type=type,
        status=status,
        created=created,
        modified=modified,
        due=due,
        priority=priority,
        tags=list(tags),
        requires=list(requires),
        continues=[],
        related=[],
        content=content,
        cadence=cadence,
        started=started,
        completed=completed,
    )


def agenda(notes, **kwargs):
    return build_agenda(notes, today=TODAY, now=NOW, **kwargs)


def stems(items) -> list[str]:
    return [item.stem for item in items]


def by_stem(items, stem: str):
    return next(item for item in items if item.stem == stem)


# --- timeline ------------------------------------------------------------------

class TestTimeline:
    def test_due_today_lands_in_today(self):
        # Regression: the old view compared a datetime to a date and never fired.
        ag = agenda([mk("research", type="project", status="active"), mk("research.measure", due=at(0))])
        assert stems(ag.today) == ["research.measure"]
        assert ag.overdue == [] and ag.upcoming == []

    def test_bucketing_and_horizon(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.a", due=at(-3)), mk("p.b", due=at(0)), mk("p.c", due=at(1)),
            mk("p.d", due=at(7)), mk("p.e", due=at(8)),
        ]
        ag = agenda(notes)
        assert stems(ag.overdue) == ["p.a"]
        assert stems(ag.today) == ["p.b"]
        assert stems(ag.upcoming) == ["p.c", "p.d"]
        assert stems(agenda(notes, horizon_days=3).upcoming) == ["p.c"]
        assert agenda(notes, horizon_days=0).upcoming == []
        # Undated and beyond-horizon items still reach NEXT.
        assert "p.e" in stems(ag.next_items)

    def test_closed_items_never_appear(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.done", status="done", due=at(-3)),
            mk("p.dropped", status="dropped", due=at(-3)),
        ]
        ag = agenda(notes)
        assert ag.overdue == ag.today == ag.upcoming == ag.next_items == []
        assert ag.waiting == ag.blocked == []

    def test_project_due_in_timeline_but_not_next(self):
        ag = agenda([mk("p", type="project", status="active", due=at(2)), mk("p.t")])
        assert stems(ag.upcoming) == ["p"]
        assert ag.upcoming[0].note_type == "project"
        assert stems(ag.next_items) == ["p.t"]

    def test_timed_due_is_kept_and_reported(self):
        ag = agenda([mk("p", type="project", status="active"), mk("p.t", due=at(0, 15, 0))])
        item = ag.today[0]
        assert item.due_has_time is True
        assert item.days_until_due == 0
        assert "due today 15:00" in item.reasons
        assert item.to_dict()["due"] == "2026-09-13 15:00"

    def test_overdue_sorted_by_due_then_priority(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.late_low", due=at(-1), priority="low"),
            mk("p.late_high", due=at(-1), priority="high"),
            mk("p.later", due=at(-5)),
        ]
        assert stems(agenda(notes).overdue) == ["p.later", "p.late_high", "p.late_low"]


# --- next ----------------------------------------------------------------------

class TestNext:
    def test_exclusions(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.blocked", status="blocked"),
            mk("p.gate"),
            mk("p.needs_gate", requires=["p.gate"]),
            mk("p.grp", status="active"),
            mk("p.grp.leaf", status="active"),
            mk("q", type="project", status="paused"), mk("q.t"),
            mk("r", type="project", status="done"), mk("r.t"),
            mk("p.waiting", status="waiting"),
        ]
        ag = agenda(notes)
        assert set(stems(ag.next_items)) == {"p.gate", "p.grp.leaf"}
        gate = by_stem(ag.next_items, "p.gate")
        assert gate.blocks == ["p.needs_gate"]
        assert "unblocks Needs gate" in gate.reasons

    def test_missing_requirement_does_not_block(self):
        ag = agenda([mk("p", type="project", status="active"), mk("p.t", requires=["ghost"])])
        item = by_stem(ag.next_items, "p.t")
        assert item.blocked_by == [] and item.missing_requirements == ["ghost"]
        assert ag.blocked == []

    def test_ordering_overdue_today_upcoming_undated(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.undated"), mk("p.soon", due=at(3)), mk("p.today", due=at(0)), mk("p.late", due=at(-2)),
        ]
        assert stems(agenda(notes).next_items) == ["p.late", "p.today", "p.soon", "p.undated"]

    def test_components_sum_to_urgency_with_all_keys(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.a", status="active", due=at(-2), priority="medium", modified=at(-9), created=at(-100)),
            mk("p.b", due=at(5)),
        ]
        for item in agenda(notes).next_items:
            assert set(item.urgency_components) == set(COMPONENTS)
            assert item.urgency == pytest.approx(round(sum(item.urgency_components.values()), 2))

    def test_reasons_match_components(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.hub", status="active", due=at(-12), priority="high", modified=NOW),
            mk("p.x", requires=["p.hub"]), mk("p.y", requires=["p.hub"]),
        ]
        hub = by_stem(agenda(notes).next_items, "p.hub")
        assert hub.reasons == ["overdue 12d", "high", "active", "blocks 2"]
        # due capped at 12, high 4, active 2, blocks 2 -> 2, active project 1
        assert hub.urgency == pytest.approx(21.0)
        assert hub.urgency_components["due"] == pytest.approx(DEFAULT_WEIGHTS["overdue_cap"])

    def test_untouched_only_for_active(self):
        notes = [
            mk("p", type="project", status="active"),
            mk("p.idle_active", status="active", modified=at(-10)),
            mk("p.idle_todo", modified=at(-10)),
            mk("p.fresh_active", status="active", modified=at(-2)),
        ]
        items = agenda(notes).next_items
        assert "untouched 10d" in by_stem(items, "p.idle_active").reasons
        assert not any(r.startswith("untouched") for r in by_stem(items, "p.idle_todo").reasons)
        assert not any(r.startswith("untouched") for r in by_stem(items, "p.fresh_active").reasons)

    def test_blocking_is_capped(self):
        notes = [mk("p", type="project", status="active"), mk("p.hub")]
        notes += [mk(f"p.d{i}", requires=["p.hub"]) for i in range(5)]
        hub = by_stem(agenda(notes).next_items, "p.hub")
        assert hub.urgency_components["blocking"] == pytest.approx(DEFAULT_WEIGHTS["blocking_cap"])
        assert "blocks 5" in hub.reasons

    def test_due_tomorrow_and_soon_bump(self):
        notes = [mk("p", type="project", status="active"), mk("p.t", due=at(1)), mk("p.far", due=at(12)), mk("p.none")]
        items = agenda(notes).next_items
        assert "due tomorrow" in by_stem(items, "p.t").reasons
        far = by_stem(items, "p.far")
        assert far.urgency_components["due"] == pytest.approx(DEFAULT_WEIGHTS["due_soon_bump"])
        assert far.urgency > by_stem(items, "p.none").urgency

    def test_older_created_breaks_ties(self):
        notes = [mk("p", type="project", status="active"),
                 mk("p.new", created=at(-1)), mk("p.old", created=at(-50)), mk("p.undated")]
        items = agenda(notes).next_items
        assert len({i.urgency for i in items}) == 1
        assert stems(items) == ["p.old", "p.new", "p.undated"]


# --- stale deadlines -----------------------------------------------------------

class TestStale:
    def test_stale_deadline_leaves_next_and_is_listed(self):
        notes = [mk("p", type="project", status="active"), mk("p.dead", due=at(-20)), mk("p.t")]
        ag = agenda(notes)
        dead = by_stem(ag.overdue, "p.dead")
        assert dead.stale is True
        assert "overdue 20d (stale)" in dead.reasons
        assert stems(ag.stale) == ["p.dead"]
        assert stems(ag.next_items) == ["p.t"]
        assert stems(ag.overdue_live) == []
        assert ag.to_dict()["follow_up"]["stale"][0]["stem"] == "p.dead"

    def test_overdue_within_grace_stays(self):
        ag = agenda([mk("p", type="project", status="active"), mk("p.late", due=at(-10)), mk("p.t")])
        late = by_stem(ag.next_items, "p.late")
        assert late.stale is False and late.reasons == ["overdue 10d"]
        assert ag.stale == [] and stems(ag.overdue_live) == ["p.late"]

    def test_stale_threshold_is_configurable(self):
        notes = [mk("p", type="project", status="active"), mk("p.late", due=at(-10))]
        ag = agenda(notes, weights={"overdue_stale_days": 5})
        assert stems(ag.stale) == ["p.late"] and ag.next_items == []

    def test_stale_project_deadline_hidden_but_not_triaged_as_task(self):
        ag = agenda([mk("p", type="project", status="active", due=at(-30)), mk("p.t")])
        assert by_stem(ag.overdue, "p").stale is True
        assert ag.overdue_live == [] and ag.stale == []
        assert stems(ag.next_items) == ["p.t"]


# --- idle projects -------------------------------------------------------------

class TestIdleProjects:
    def test_idle_project_bonus_on_best_task_only(self):
        notes = [
            mk("q", type="project", status="active", modified=at(-30)),
            mk("q.a", priority="high", modified=at(-30)), mk("q.b", modified=at(-30)),
        ]
        ag = agenda(notes)
        a, b = by_stem(ag.next_items, "q.a"), by_stem(ag.next_items, "q.b")
        assert a.project_idle_days == b.project_idle_days == 30
        # 30 idle - 7 cadence = 23 days past cadence -> 1 + 0.25 * 23 = 6.75, capped at 4
        assert a.urgency_components["project_idle"] == pytest.approx(DEFAULT_WEIGHTS["idle_cap"])
        assert "project idle 30d" in a.reasons
        assert b.urgency_components["project_idle"] == 0.0
        assert not any(r.startswith("project idle") for r in b.reasons)
        assert stems(ag.next_items) == ["q.a", "q.b"]

    def test_idle_bonus_grows_from_cadence(self):
        notes = [mk("q", type="project", status="active", modified=at(-11)), mk("q.t", modified=at(-11))]
        t = by_stem(agenda(notes).next_items, "q.t")
        # 11 - 7 = 4 days past cadence -> 1 + 0.25 * 4 = 2.0
        assert t.urgency_components["project_idle"] == pytest.approx(2.0)

    def test_idle_bonus_never_beats_due_today_or_overdue(self):
        notes = [
            mk("p", type="project", status="active", modified=at(0)),
            mk("p.today", due=at(0), modified=at(0)), mk("p.late", due=at(-1), modified=at(0)),
            mk("p.plain", modified=at(0)),
            mk("q", type="project", status="active", modified=at(-60)), mk("q.t", modified=at(-60)),
        ]
        items = agenda(notes).next_items
        assert stems(items) == ["p.late", "p.today", "q.t", "p.plain"]
        assert "project idle 60d" in by_stem(items, "q.t").reasons

    def test_idle_respects_cadence_override(self):
        slow = [mk("q", type="project", status="active", modified=at(-20), cadence=30), mk("q.t", modified=at(-20))]
        ag = agenda(slow)
        assert by_stem(ag.next_items, "q.t").urgency_components["project_idle"] == 0.0
        assert ag.idle_projects == []
        default = [mk("q", type="project", status="active", modified=at(-20)), mk("q.t", modified=at(-20))]
        ag = agenda(default)
        assert by_stem(ag.next_items, "q.t").urgency_components["project_idle"] > 0
        assert [p.stem for p in ag.idle_projects] == ["q"]
        assert ag.idle_projects[0].cadence_days == 7

    def test_idle_projects_listed_sorted_by_idle_days(self):
        notes = [
            mk("a", type="project", status="active", modified=at(-15)), mk("a.t", modified=at(-15)),
            mk("b", type="project", status="planning", modified=at(-30)),
            mk("b.t1", modified=at(-30)), mk("b.t2", status="blocked", modified=at(-30)),
            mk("b.done", status="done", modified=at(-30)),
            mk("fresh", type="project", status="active", modified=at(-1)), mk("fresh.t", modified=at(-1)),
            mk("stuck", type="project", status="active", modified=at(-40)),
            mk("stuck.w", status="waiting", modified=at(-40)),
            mk("paused", type="project", status="paused", modified=at(-90)), mk("paused.t", modified=at(-90)),
        ]
        ag = agenda(notes)
        idle = ag.idle_projects
        assert [(p.stem, p.idle_days, p.open_tasks) for p in idle] == [("b", 30, 2), ("a", 15, 1)]
        assert idle[0].status == "planning" and idle[0].last_activity == at(-30)
        assert [p.stem for p in ag.stuck_projects] == ["stuck"]
        payload = ag.to_dict()["follow_up"]["idle_projects"][0]
        assert set(payload) == {"stem", "title", "status", "idle_days", "cadence_days", "open_tasks",
                                "last_activity", "vault"}

    def test_no_activity_date_means_no_bonus(self):
        ag = agenda([mk("q", type="project", status="active"), mk("q.t")])
        t = by_stem(ag.next_items, "q.t")
        assert t.project_idle_days is None
        assert t.urgency_components["project_idle"] == 0.0
        assert ag.idle_projects == []

    def test_archived_completed_stamp_counts_as_activity(self):
        live = [mk("q", type="project", status="active", modified=at(-40)), mk("q.t", modified=at(-40))]
        archived = [mk("q.old", status="done", modified=at(-40), completed=at(-2))]
        ag = agenda(live, archived_notes=archived)
        t = by_stem(ag.next_items, "q.t")
        assert t.project_idle_days == 2
        assert t.urgency_components["project_idle"] == 0.0
        assert ag.idle_projects == []
        assert stems(ag.next_items) == ["q.t"]  # archived notes never appear

    def test_tag_filter_applies_to_idle_projects(self):
        notes = [
            mk("a", type="project", status="active", modified=at(-20)), mk("a.t", modified=at(-20)),
            mk("b", type="project", status="active", modified=at(-20)), mk("b.t", modified=at(-20)),
        ]
        assert [p.stem for p in agenda(notes, tag="a").idle_projects] == ["a"]


# --- filtering -----------------------------------------------------------------

class TestFiltering:
    NOTES = [
        mk("a", type="project", status="active", tags=["ml"]), mk("a.t"),
        mk("b", type="project", status="active"), mk("b.t", tags=["urgent"]),
        mk("c", type="project", status="active"), mk("c.t"),
    ]

    def test_tag_by_project_note_tag_and_propagated_tag(self):
        assert stems(agenda(self.NOTES, tag="a").next_items) == ["a.t"]
        assert stems(agenda(self.NOTES, tag="urgent").next_items) == ["b.t"]
        assert stems(agenda(self.NOTES, tag="ml").next_items) == ["a.t"]

    def test_focus_applies_only_without_explicit_tag(self):
        focused = agenda(self.NOTES, focus="a")
        assert focused.tag == "a" and stems(focused.next_items) == ["a.t"]
        explicit = agenda(self.NOTES, tag="b", focus="a")
        assert explicit.tag == "b" and stems(explicit.next_items) == ["b.t"]

    def test_no_match_is_clear(self):
        ag = agenda(self.NOTES, tag="nothing", inbox_count=2)
        assert ag.is_clear and ag.inbox_count == 2


# --- follow-up -----------------------------------------------------------------

class TestFollowUp:
    def test_waiting_blocked_and_stuck(self):
        notes = [
            mk("s", type="project", status="active", modified=at(-30)),
            mk("s.w", status="waiting", modified=at(-10), requires=["s.gate"]),
            mk("s.w2", status="waiting", modified=at(-3), due=at(4)),
            mk("s.gate", status="blocked"),
            mk("z", type="project", status="active", modified=at(-5)),
            mk("ok", type="project", status="active"), mk("ok.t"),
            mk("plan", type="project", status="planning"),
        ]
        ag = agenda(notes)
        assert stems(ag.waiting) == ["s.w", "s.w2"]          # most idle first
        assert stems(ag.blocked) == ["s.gate"]                # waiting+unmet is not double-listed
        assert [p.stem for p in ag.stuck_projects] == ["s", "z"]
        assert by_stem(ag.stuck_projects, "s").last_activity == at(-3)
        assert by_stem(ag.stuck_projects, "z").last_activity == at(-5)

    def test_unmet_requires_on_actionable_task_is_blocked(self):
        ag = agenda([mk("p", type="project", status="active"), mk("p.gate"), mk("p.t", status="active", requires=["p.gate"])])
        assert stems(ag.blocked) == ["p.t"]
        assert ag.blocked[0].blocked_by == ["p.gate"]


# --- helpers -------------------------------------------------------------------

class TestHelpers:
    def test_resolve_weights(self):
        assert resolve_weights(None) == DEFAULT_WEIGHTS
        merged = resolve_weights({"priority_high": 9, "unknown": 3, "active": "abc", "idle_cap": True})
        assert merged["priority_high"] == 9.0
        assert merged["active"] == DEFAULT_WEIGHTS["active"]
        assert merged["idle_cap"] == DEFAULT_WEIGHTS["idle_cap"]
        assert "unknown" not in merged

    def test_count_inbox_items(self, tmp_path):
        backlog = tmp_path / "backlog.md"
        backlog.write_text("# Backlog\n\n## Inbox\n- one\n- two\n-\n\n## Other\n- three\n")
        assert count_inbox_items(backlog) == 2
        assert count_inbox_items(tmp_path / "missing.md") == 0

    def test_to_dict_contract(self):
        notes = [mk("p", type="project", status="active"), mk("p.t", due=at(0, 15), priority="high", modified=at(-1))]
        payload = agenda(notes, revision="abc123", inbox_count=1).to_dict()
        assert payload["ok"] is True and payload["revision"] == "abc123"
        assert set(payload) >= {"date", "focus", "tag", "horizon_days", "overdue", "today", "upcoming",
                                "next", "follow_up", "counts", "vaults"}
        assert set(payload["follow_up"]) == {"stale", "waiting", "blocked", "stuck_projects",
                                             "idle_projects", "inbox_count"}
        assert payload["counts"]["today"] == 1 and payload["counts"]["next"] == 1
        assert payload["vaults"] == []
        item = payload["today"][0]
        assert set(item) == {"stem", "title", "type", "status", "priority", "due", "due_has_time",
                             "days_until_due", "project", "parent", "tags", "modified",
                             "days_since_modified", "blocks", "blocked_by", "urgency", "reasons",
                             "urgency_components", "stale", "project_idle_days", "vault"}
        assert item["stale"] is False and item["project_idle_days"] == 1 and item["vault"] is None
        assert item["due"] == "2026-09-13 15:00" and item["modified"] == "2026-09-12 00:00"
        json.dumps(payload)  # must be serialisable as-is


# --- CLI -----------------------------------------------------------------------

def _set_meta(path: Path, **fields) -> None:
    post = frontmatter.load(path)
    for key, value in fields.items():
        post[key] = value
    path.write_text(frontmatter.dumps(post) + "\n")


@pytest.fixture
def daily_vault(runner, temp_vault):
    """An active project with tasks due yesterday, today (timed), and undated."""
    today = date.today()
    runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
    for stem in ("late", "timed", "plain", "extra"):
        runner.invoke(cli, ["new", "task", f"myproj.{stem}", f"Task {stem}", "--no-edit"])
    _set_meta(temp_vault / "myproj.md", status="active", due=(today + timedelta(days=1)).isoformat())
    _set_meta(temp_vault / "myproj.late.md", due=(today - timedelta(days=2)).isoformat(), priority="high")
    _set_meta(temp_vault / "myproj.timed.md", due=f"{today.isoformat()} 15:00")
    return temp_vault


class TestDailyCli:
    def test_text_sections(self, runner, daily_vault):
        result = runner.invoke(cli, ["daily"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0, result.output
        for heading in ("OVERDUE", "TODAY", "UPCOMING", "NEXT", "why"):
            assert heading in result.output
        assert "15:00" in result.output
        assert "◆" in result.output and "(project due)" in result.output
        assert "2d  [ ] Late" in result.output
        assert "overdue 2d · high" in result.output

    def test_json_shape_ignores_limit(self, runner, daily_vault):
        result = runner.invoke(cli, ["daily", "--json", "--limit", "1"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert len(payload["revision"]) > 10
        assert payload["counts"]["next"] == len(payload["next"]) == 4
        assert payload["counts"]["today"] == 1 and payload["counts"]["overdue"] == 1
        assert payload["upcoming"][0]["type"] == "project"

    def test_explain_and_limit(self, runner, daily_vault):
        limited = runner.invoke(cli, ["daily", "--limit", "1", "--explain"], env={"NO_COLOR": "1"})
        assert limited.exit_code == 0, limited.output
        assert "... 3 more (cor daily -a)" in limited.output
        assert " = due " in limited.output
        everything = runner.invoke(cli, ["daily", "-a"], env={"NO_COLOR": "1"})
        assert "more (cor daily -a)" not in everything.output
        assert "Extra" in everything.output

    def test_verbose_shows_description(self, runner, daily_vault):
        # `cor new task <stem> "text"` stores the text under ## Description.
        plain = runner.invoke(cli, ["daily", "-a"], env={"NO_COLOR": "1"})
        assert "Task plain" not in plain.output
        verbose = runner.invoke(cli, ["daily", "-a", "-v"], env={"NO_COLOR": "1"})
        assert "Task plain" in verbose.output

    def test_all_clear_with_follow_ups(self, runner, temp_vault):
        runner.invoke(cli, ["new", "project", "quiet", "--no-edit"])
        runner.invoke(cli, ["new", "task", "quiet.pending", "Pending", "--no-edit"])
        _set_meta(temp_vault / "quiet.md", status="active")
        _set_meta(temp_vault / "quiet.pending.md", status="waiting")
        (temp_vault / "backlog.md").write_text("# Backlog\n\n## Inbox\n- capture me\n")
        result = runner.invoke(cli, ["daily"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0, result.output
        assert "All clear" in result.output
        assert "FOLLOW-UP" in result.output
        assert "waiting  Pending" in result.output
        assert "stuck    Quiet" in result.output
        assert "1 unprocessed item" in result.output

    def test_json_error_is_machine_readable(self, runner, tmp_path):
        empty = tmp_path / "not-a-vault"
        empty.mkdir()
        result = runner.invoke(cli, ["daily", "--json"], env={"COR_VAULT": str(empty)})
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False and payload["error"]["code"]

    def test_stale_hidden_from_overdue_shown_in_followup(self, runner, daily_vault):
        runner.invoke(cli, ["new", "task", "myproj.dead", "Task dead", "--no-edit"])
        _set_meta(daily_vault / "myproj.dead.md", due=(date.today() - timedelta(days=40)).isoformat())
        result = runner.invoke(cli, ["daily", "-a"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0, result.output
        assert result.output.count("Dead") == 1
        assert "stale    Dead" in result.output and "overdue 40d: reschedule/drop" in result.output
        assert "2d  [ ] Late" in result.output  # overdue within grace still listed

    def test_idle_row_in_followup(self, runner, daily_vault):
        runner.invoke(cli, ["new", "project", "sleepy", "--no-edit"])
        runner.invoke(cli, ["new", "task", "sleepy.one", "One", "--no-edit"])
        runner.invoke(cli, ["new", "task", "sleepy.two", "Two", "--no-edit"])
        old = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d %H:%M")
        for stem in ("sleepy", "sleepy.one", "sleepy.two"):
            _set_meta(daily_vault / f"{stem}.md", created=old, modified=old)
        _set_meta(daily_vault / "sleepy.md", status="active")
        result = runner.invoke(cli, ["daily", "-a", "--explain"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0, result.output
        assert "idle     Sleepy" in result.output and "idle 20d, 2 open tasks" in result.output
        assert result.output.count("project idle 20d") == 1
        assert "project_idle 4" in result.output

    def test_json_exposes_stale_and_idle(self, runner, daily_vault):
        runner.invoke(cli, ["new", "task", "myproj.dead", "Task dead", "--no-edit"])
        _set_meta(daily_vault / "myproj.dead.md", due=(date.today() - timedelta(days=40)).isoformat())
        payload = json.loads(runner.invoke(cli, ["daily", "--json"]).output)
        assert [i["stem"] for i in payload["follow_up"]["stale"]] == ["myproj.dead"]
        assert "myproj.dead" not in [i["stem"] for i in payload["next"]]
        dead = next(i for i in payload["overdue"] if i["stem"] == "myproj.dead")
        assert dead["stale"] is True and dead["project_idle_days"] == 0 and dead["vault"] == "notes"
        assert [v["name"] for v in payload["vaults"]] == ["notes"]
        assert payload["vaults"][0]["revision"] == payload["revision"]
        assert payload["follow_up"]["idle_projects"] == [] and payload["counts"]["stale"] == 1

    def test_focus_indicator(self, runner, daily_vault):
        runner.invoke(cli, ["new", "project", "other", "--no-edit"])
        runner.invoke(cli, ["new", "task", "other.side", "Side task", "--no-edit"])
        assert runner.invoke(cli, ["focus", "myproj"]).exit_code == 0
        result = runner.invoke(cli, ["daily", "-a"], env={"NO_COLOR": "1"})
        assert "[Focusing on: myproj]" in result.output
        assert "Side" not in result.output
        explicit = runner.invoke(cli, ["daily", "other"], env={"NO_COLOR": "1"})
        assert "[Filter: other]" in explicit.output and "Side" in explicit.output


# --- several vaults --------------------------------------------------------------

from conftest import build_vault  # noqa: E402
from cor.config import add_vault, set_focused_project  # noqa: E402


def _entry(vault: Path, stem: str, *, type: str, status: str, **meta) -> Path:
    """Write one minimal entry straight to disk (no CLI, no git)."""
    today = date.today().isoformat()
    lines = [f"created: {today}", f"modified: {today}", f"type: {type}", f"status: {status}"]
    lines += [f"{key}: {value}" for key, value in meta.items()]
    title = stem.rsplit(".", 1)[-1].replace("_", " ").capitalize()
    path = vault / f"{stem}.md"
    path.write_text("---\n" + "\n".join(lines) + "\n---\n# " + title + "\n")
    return path


@pytest.fixture
def two_vaults(tmp_path, monkeypatch):
    """Two registered vaults sharing a project stem, cwd parked outside both."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv("COR_VAULT", raising=False)
    work = build_vault(tmp_path / "work")
    personal = build_vault(tmp_path / "personal")
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    add_vault("work", work)
    add_vault("personal", personal)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for vault in (work, personal):
        _entry(vault, "proj", type="project", status="active")
        _entry(vault, "proj.late", type="task", status="todo", due=yesterday, parent="proj")
        _entry(vault, "proj.plain", type="task", status="todo", parent="proj")
    _entry(work, "other", type="project", status="active")
    _entry(work, "other.side", type="task", status="todo", parent="other")
    (personal / "backlog.md").write_text("# Backlog\n\n## Inbox\n- a thought\n")
    return {"work": work, "personal": personal, "outside": outside, "tmp": tmp_path}


class TestMultiVault:
    def test_all_vaults_labels_rows_and_merges(self, runner, two_vaults):
        result = runner.invoke(cli, ["daily", "--all-vaults", "-a"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0, result.output
        assert "vaults: personal, work" in result.output
        assert result.output.count("personal › proj") >= 1 and result.output.count("work › proj") >= 1
        # Same stem in both vaults: two OVERDUE rows, labelled.
        overdue = [line for line in result.output.splitlines() if "[ ] Late" in line]
        assert len(overdue) == 2 and "personal › proj" in overdue[0] and "work › proj" in overdue[1]
        side = next(line for line in result.output.splitlines() if "Side" in line)
        assert "work › " in side  # NEXT rows carry the vault in the why column
        assert "1 unprocessed item" in result.output

    def test_vault_option_picks_one_without_prefix(self, runner, two_vaults):
        result = runner.invoke(cli, ["daily", "--vault", "work", "-a"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0, result.output
        assert "›" not in result.output and "vaults:" not in result.output
        assert "Side" in result.output and "unprocessed" not in result.output

    def test_json_shape_across_vaults(self, runner, two_vaults):
        payload = json.loads(runner.invoke(cli, ["daily", "--all-vaults", "--json"]).output)
        assert payload["ok"] is True and payload["revision"] is None
        assert [v["name"] for v in payload["vaults"]] == ["personal", "work"]
        for record in payload["vaults"]:
            assert set(record) == {"name", "path", "revision", "focus", "inbox_count", "error"}
            assert len(record["revision"]) > 10 and record["error"] is None
        assert {item["vault"] for item in payload["next"]} == {"personal", "work"}
        assert payload["follow_up"]["inbox_count"] == 1
        # NEXT keeps the per-vault order, vault name as the final tie-break.
        assert [(i["stem"], i["vault"]) for i in payload["next"]][:2] == [
            ("proj.late", "personal"), ("proj.late", "work")]

    def test_single_vault_json_keeps_revision_and_labels(self, runner, two_vaults):
        payload = json.loads(runner.invoke(cli, ["daily", "--vault", "work", "--json"]).output)
        assert isinstance(payload["revision"], str) and len(payload["revision"]) > 10
        assert [v["name"] for v in payload["vaults"]] == ["work"]
        assert payload["vaults"][0]["revision"] == payload["revision"]
        assert {item["vault"] for item in payload["next"]} == {"work"}

    def test_dead_vault_is_reported_not_fatal(self, runner, two_vaults):
        add_vault("dead", two_vaults["tmp"] / "nowhere")
        result = runner.invoke(cli, ["daily", "--all-vaults", "--json"])
        assert result.exit_code == 0, result.output
        assert "Skipping vault 'dead'" in result.output  # warned on stderr
        payload = json.loads(result.stdout)             # stdout stays pure JSON
        dead = next(v for v in payload["vaults"] if v["name"] == "dead")
        assert dead["error"] == "vault directory not found" and dead["revision"] is None
        assert {item["vault"] for item in payload["next"]} == {"personal", "work"}
        text = runner.invoke(cli, ["daily", "--all-vaults"], env={"NO_COLOR": "1"})
        assert "dead (unreadable)" in text.output

    def test_all_vaults_uses_each_vaults_focus(self, runner, two_vaults, monkeypatch):
        monkeypatch.chdir(two_vaults["work"])
        set_focused_project("other")
        monkeypatch.chdir(two_vaults["outside"])
        result = runner.invoke(cli, ["daily", "--all-vaults", "-a"], env={"NO_COLOR": "1"})
        assert result.exit_code == 0, result.output
        assert "work (focus other)" in result.output
        assert "Side" in result.output and "work › proj" not in result.output
        assert "personal › proj" in result.output  # the other vault is not narrowed

    def test_unknown_vault_name_errors(self, runner, two_vaults):
        result = runner.invoke(cli, ["daily", "--vault", "nope"])
        assert result.exit_code != 0
        assert "No vault named 'nope'" in result.output
        as_json = json.loads(runner.invoke(cli, ["daily", "--vault", "nope", "--json"]).output)
        assert as_json["ok"] is False

    def test_outside_a_vault_reads_but_never_writes_by_guess(self, runner, two_vaults):
        # Reading across vaults needs no cwd; writing without a vault still refuses.
        assert runner.invoke(cli, ["daily", "--all-vaults"]).exit_code == 0
        result = runner.invoke(cli, ["new", "task", "proj.more", "More", "--no-edit"])
        assert result.exit_code != 0 and "ambiguous" in result.output

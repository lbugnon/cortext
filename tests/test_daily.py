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
       created: datetime | None = None, tags=(), title: str | None = None, content: str = "") -> Note:
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
        merged = resolve_weights({"priority_high": 9, "unknown": 3, "active": "abc", "age_cap": True})
        assert merged["priority_high"] == 9.0
        assert merged["active"] == DEFAULT_WEIGHTS["active"]
        assert merged["age_cap"] == DEFAULT_WEIGHTS["age_cap"]
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
                                "next", "follow_up", "counts"}
        assert set(payload["follow_up"]) == {"waiting", "blocked", "stuck_projects", "inbox_count"}
        assert payload["counts"]["today"] == 1 and payload["counts"]["next"] == 1
        item = payload["today"][0]
        assert set(item) == {"stem", "title", "type", "status", "priority", "due", "due_has_time",
                             "days_until_due", "project", "parent", "tags", "modified",
                             "days_since_modified", "blocks", "blocked_by", "urgency", "reasons",
                             "urgency_components"}
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

    def test_focus_indicator(self, runner, daily_vault):
        runner.invoke(cli, ["new", "project", "other", "--no-edit"])
        runner.invoke(cli, ["new", "task", "other.side", "Side task", "--no-edit"])
        assert runner.invoke(cli, ["focus", "myproj"]).exit_code == 0
        result = runner.invoke(cli, ["daily", "-a"], env={"NO_COLOR": "1"})
        assert "[Focusing on: myproj]" in result.output
        assert "Side" not in result.output
        explicit = runner.invoke(cli, ["daily", "other"], env={"NO_COLOR": "1"})
        assert "[Filter: other]" in explicit.output and "Side" in explicit.output

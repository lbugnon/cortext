"""Tests for cor.core.activity: the shared definition of "last moved"."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from cor.core.activity import (
    STALE_DAYS_DEFAULT,
    note_activity,
    project_activity,
    project_last_advanced,
    resolve_stale_days,
)
from cor.core.notes import NoteMetadata

D = lambda day: datetime(2026, 9, day, 12, 0)  # noqa: E731


def meta(stem: str, **fields) -> NoteMetadata:
    fields.setdefault("note_type", "task")
    return NoteMetadata(path=Path(f"{stem}.md"), title=stem, **fields)


class TestNoteActivity:
    def test_unstamped_uses_latest_of_created_and_modified(self):
        assert note_activity(meta("t", created=D(1), modified=D(5))) == note_activity(meta("t", modified=D(5)))
        assert note_activity(meta("t", created=D(1), modified=D(5))).source == "modified"
        assert note_activity(meta("t", created=D(9), modified=D(5))).source == "created"

    def test_stamped_ignores_modified(self):
        # A bulk commit re-stamped `modified`; the completion stamp is the truth.
        act = note_activity(meta("t", created=D(1), completed=D(3), modified=D(20)))
        assert act.when == D(3) and act.source == "completed"
        act = note_activity(meta("t", created=D(1), started=D(4), modified=D(20)))
        assert act.when == D(4) and act.source == "started"
        act = note_activity(meta("t", created=D(8), started=D(4)))
        assert act.when == D(8) and act.source == "created"

    def test_nothing_dated(self):
        assert note_activity(meta("t")) == note_activity(meta("u"))
        assert note_activity(meta("t")).when is None and note_activity(meta("t")).source == "none"


class TestProjectActivity:
    NOTES = [
        meta("p", note_type="project", modified=D(2)),
        meta("p.a", created=D(1), modified=D(4)),
        meta("p.grp.b", created=D(1), modified=D(6)),
        meta("p.old", status="done", modified=D(25), completed=D(8)),  # re-stamped, completed earlier
        meta("prefix_other", note_type="project", modified=D(28)),      # not a member of p
        meta("q.t", modified=D(27)),
    ]

    def test_prefers_completed_over_modified(self):
        assert project_last_advanced("p", self.NOTES) == D(8)
        assert project_activity("p", self.NOTES).source == "completed"

    def test_membership_is_by_dot_prefix(self):
        assert project_last_advanced("prefix_other", self.NOTES) == D(28)
        assert project_last_advanced("q", self.NOTES) == D(27)
        assert project_last_advanced("missing", self.NOTES) is None

    def test_project_note_itself_counts(self):
        assert project_last_advanced("p", [meta("p", note_type="project", modified=D(2))]) == D(2)


def test_resolve_stale_days():
    assert resolve_stale_days(None) == STALE_DAYS_DEFAULT == 14
    assert resolve_stale_days({"project_stale_days": 30}) == 30
    assert resolve_stale_days({"project_stale_days": True}) == 14
    assert resolve_stale_days({"project_stale_days": -1}) == 14


# --- stamps ----------------------------------------------------------------------

import frontmatter  # noqa: E402

from cor.core.activity import UNKNOWN_STATUS, apply_activity_stamps  # noqa: E402


def post(**meta):
    p = frontmatter.loads("---\ntype: task\n---\n# T\n")
    for key, value in meta.items():
        p[key] = value
    return p


class TestStamps:
    def test_started_set_once_on_active(self):
        p = post(status="active")
        assert apply_activity_stamps(p, previous_status="todo", now=D(3)) is True
        assert p["started"] == "2026-09-03 12:00"
        p["status"] = "todo"
        apply_activity_stamps(p, previous_status="active", now=D(5))
        p["status"] = "active"
        assert apply_activity_stamps(p, previous_status="todo", now=D(6)) is False
        assert p["started"] == "2026-09-03 12:00"

    def test_completed_on_done_and_cleared_on_reopen(self):
        p = post(status="done")
        assert apply_activity_stamps(p, previous_status="active", now=D(4)) is True
        assert p["completed"] == "2026-09-04 12:00"
        p["status"] = "todo"
        assert apply_activity_stamps(p, previous_status="done", now=D(9)) is True
        assert "completed" not in p.metadata
        p["status"] = "dropped"
        assert apply_activity_stamps(p, previous_status="todo", now=D(10)) is True
        assert p["completed"] == "2026-09-10 12:00"

    def test_unchanged_status_not_stamped(self):
        # A bulk commit of legacy files must not date them "now".
        assert apply_activity_stamps(post(status="done"), previous_status="done") is False
        assert apply_activity_stamps(post(status="active"), previous_status="active") is False
        assert apply_activity_stamps(post(status="todo"), previous_status="todo") is False

    def test_unknown_previous_stamps(self):
        p = post(status="done")
        assert apply_activity_stamps(p, previous_status=UNKNOWN_STATUS, now=D(2)) is True
        assert p["completed"] == "2026-09-02 12:00"
        q = post(status="active")
        assert apply_activity_stamps(q, previous_status=UNKNOWN_STATUS, now=D(2)) is True
        assert q["started"] == "2026-09-02 12:00"
        assert apply_activity_stamps(post(status="todo"), previous_status=UNKNOWN_STATUS) is False

    def test_projects_stamped_notes_untouched(self):
        p = post(status="active"); p["type"] = "project"
        assert apply_activity_stamps(p, previous_status="planning", now=D(1)) is True
        n = post(status="done"); n["type"] = "note"
        assert apply_activity_stamps(n, previous_status=UNKNOWN_STATUS) is False


# --- through the CLI -------------------------------------------------------------

import json  # noqa: E402
from datetime import timedelta  # noqa: E402

from cor.cli import cli  # noqa: E402
from cor.core.operations import update_entry  # noqa: E402
from cor.exceptions import ValidationError  # noqa: E402
import pytest  # noqa: E402


class TestStampsThroughCli:
    def test_mark_stamps_and_weekly_counts_completed(self, runner, temp_vault):
        assert runner.invoke(cli, ["new", "project", "p", "--no-edit"]).exit_code == 0
        assert runner.invoke(cli, ["new", "task", "p.t", "Thing", "--no-edit"]).exit_code == 0
        assert runner.invoke(cli, ["mark", "p.t", "active"]).exit_code == 0
        meta = frontmatter.load(temp_vault / "p.t.md").metadata
        assert meta["started"] and "completed" not in meta
        assert runner.invoke(cli, ["mark", "p.t", "done"]).exit_code == 0
        archived = frontmatter.load(temp_vault / "archive" / "p.t.md").metadata
        assert archived["completed"] and archived["started"] == meta["started"]
        weekly = runner.invoke(cli, ["weekly"], env={"NO_COLOR": "1"})
        assert "Completed: 1 tasks" in weekly.output
        assert "predate the completed: stamp" not in weekly.output
        # Reopening clears the completion stamp again.
        assert runner.invoke(cli, ["mark", "-a", "p.t", "todo"]).exit_code == 0
        assert "completed" not in frontmatter.load(temp_vault / "p.t.md").metadata

    def test_weekly_ignores_unstamped_closed_tasks(self, runner, temp_vault):
        runner.invoke(cli, ["new", "project", "p", "--no-edit"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        (temp_vault / "archive" / "p.legacy.md").write_text(
            f"---\ncreated: {now}\nmodified: {now}\ntype: task\nstatus: done\nparent: p\n---\n# Legacy\n"
        )
        weekly = runner.invoke(cli, ["weekly"], env={"NO_COLOR": "1"})
        assert weekly.exit_code == 0, weekly.output
        assert "No completed tasks this week." in weekly.output
        assert "1 finished tasks predate the completed: stamp" in weekly.output

    def test_projects_stale_days_from_config(self, runner, temp_vault):
        old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M")
        (temp_vault / "p.md").write_text(
            f"---\ncreated: {old}\nmodified: {old}\ntype: project\nstatus: active\n---\n# P\n\n## Tasks\n"
        )
        (temp_vault / "p.t.md").write_text(
            f"---\ncreated: {old}\nmodified: {old}\ntype: task\nstatus: todo\nparent: p\n---\n# T\n"
        )
        fresh = runner.invoke(cli, ["projects"], env={"NO_COLOR": "1"})
        assert fresh.exit_code == 0, fresh.output
        assert "(stale)" not in fresh.output and "~" in fresh.output  # dated by modified
        config = temp_vault.parent / "cor" / "config.yaml"
        config.write_text(config.read_text() + "agenda:\n  project_stale_days: 5\n")
        stale = runner.invoke(cli, ["projects"], env={"NO_COLOR": "1"})
        assert "(stale)" in stale.output

    def test_get_json_exposes_stamps_and_update_refuses_them(self, runner, temp_vault):
        runner.invoke(cli, ["new", "project", "p", "--no-edit"])
        runner.invoke(cli, ["new", "task", "p.t", "Thing", "--no-edit"])
        runner.invoke(cli, ["mark", "p.t", "active"])
        payload = json.loads(runner.invoke(cli, ["get", "p.t", "--json"]).output)
        assert payload["metadata"]["started"]
        with pytest.raises(ValidationError):
            update_entry(temp_vault, "p.t", metadata={"completed": "2026-01-01 00:00"})

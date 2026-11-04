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

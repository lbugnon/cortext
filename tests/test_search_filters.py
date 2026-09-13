"""Tests for the `priority:` and `due:` search filters and the shared JSON record."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from cor.cli import cli
from cor.exceptions import ValidationError
from cor.search.content import list_notes, note_record, parse_search_query, validate_filters

TODAY = date(2026, 9, 13)


def iso(days: int, base: date = TODAY) -> str:
    return (base + timedelta(days=days)).isoformat()


def write(vault: Path, stem: str, *, type: str = "task", status: str = "todo", body: str = "", **meta) -> Path:
    lines = ["---", f"type: {type}", f"status: {status}"]
    lines += [f"{key}: {value}" for key, value in meta.items() if value is not None]
    lines += ["---", f"# {stem.rsplit('.', 1)[-1].capitalize()}", "", body, ""]
    path = vault / f"{stem}.md"
    path.write_text("\n".join(lines))
    return path


@pytest.fixture
def due_vault(temp_vault):
    write(temp_vault, "p", type="project", status="active")
    write(temp_vault, "p.late", due=iso(-3), priority="high")
    write(temp_vault, "p.done_late", status="done", due=iso(-3))
    write(temp_vault, "p.today", due=iso(0))
    write(temp_vault, "p.soon", due=f"{iso(5)} 15:00", priority="medium")
    write(temp_vault, "p.far", due=iso(20), priority="low")
    write(temp_vault, "p.none")
    return temp_vault


def stems(notes) -> list[str]:
    return sorted(note.path.stem for note in notes)


class TestParseAndValidate:
    def test_parse_priority_and_due(self):
        text, filters = parse_search_query("priority:high due:week ml")
        assert text == "ml"
        assert filters == {"priority": "high", "due": "week"}
        assert parse_search_query("due:<=2026-09-30")[1] == {"due": "<=2026-09-30"}

    @pytest.mark.parametrize("filters", [
        {"priority": "high"}, {"priority": "none"}, {"due": "overdue"}, {"due": "today"},
        {"due": "week"}, {"due": "none"}, {"due": "2026-09-30"}, {"due": "<=2026-09-30"},
        {"due": ">=2026-09-30"}, {},
    ])
    def test_valid_filters_pass(self, filters):
        validate_filters(filters)

    @pytest.mark.parametrize("filters", [
        {"priority": "urgent"}, {"due": "bogus"}, {"due": "2026-13-45"}, {"due": "<2026-09-30"},
    ])
    def test_invalid_filters_raise(self, filters):
        with pytest.raises(ValidationError):
            validate_filters(filters)


class TestDueAndPriorityFilters:
    def test_overdue_excludes_closed(self, due_vault):
        assert stems(list_notes(due_vault, {"due": "overdue"}, today=TODAY)) == ["p.late"]

    def test_today_week_none(self, due_vault):
        assert stems(list_notes(due_vault, {"due": "today"}, today=TODAY)) == ["p.today"]
        assert stems(list_notes(due_vault, {"due": "week"}, today=TODAY)) == ["p.soon", "p.today"]
        assert stems(list_notes(due_vault, {"due": "none", "type": "task"}, today=TODAY)) == ["p.none"]

    def test_exact_and_bounded_dates(self, due_vault):
        assert stems(list_notes(due_vault, {"due": iso(5)}, today=TODAY)) == ["p.soon"]
        assert stems(list_notes(due_vault, {"due": f"<={iso(0)}"}, today=TODAY)) == ["p.done_late", "p.late", "p.today"]
        assert stems(list_notes(due_vault, {"due": f">={iso(5)}"}, today=TODAY)) == ["p.far", "p.soon"]

    def test_priority(self, due_vault):
        assert stems(list_notes(due_vault, {"priority": "high"})) == ["p.late"]
        assert stems(list_notes(due_vault, {"priority": "none", "type": "task"})) == ["p.done_late", "p.none", "p.today"]

    def test_record_fields(self, due_vault):
        note = list_notes(due_vault, {"due": iso(5)}, today=TODAY)[0]
        record = note_record(note)
        assert record["stem"] == "p.soon" and record["title"] == "Soon"
        assert record["due"] == f"{iso(5)} 15:00" and record["due_has_time"] is True
        assert record["priority"] == "medium" and record["project"] == "p" and record["parent"] == "p"
        assert set(record) == {
            "stem", "title", "type", "status", "archived", "priority", "due", "due_has_time",
            "days_until_due", "created", "modified", "tags", "requires", "parent", "project",
        }


class TestSearchCli:
    @pytest.fixture
    def live_vault(self, temp_vault):
        today = date.today()
        write(temp_vault, "p", type="project", status="active")
        write(temp_vault, "p.late", due=iso(-3, today), priority="high", body="separation features")
        write(temp_vault, "p.soon", due=iso(5, today))
        write(temp_vault, "p.none")
        (temp_vault / "AGENTS.md").write_text("# Agents\n\nseparation is mentioned here too\n")
        return temp_vault

    def test_query_and_option_forms_agree(self, runner, live_vault):
        query = runner.invoke(cli, ["search", "--json", "due:overdue priority:high"])
        options = runner.invoke(cli, ["search", "--json", "--all", "--due", "overdue", "--priority", "high"])
        assert query.exit_code == 0 and options.exit_code == 0, query.output + options.output
        assert json.loads(query.output) == json.loads(options.output)
        assert [entry["stem"] for entry in json.loads(query.output)] == ["p.late"]

    def test_week_filter_and_record_keys(self, runner, live_vault):
        result = runner.invoke(cli, ["search", "--json", "due:week"])
        payload = json.loads(result.output)
        assert [entry["stem"] for entry in payload] == ["p.soon"]
        assert payload[0]["days_until_due"] == 5 and payload[0]["priority"] is None

    def test_human_output_unchanged(self, runner, live_vault):
        result = runner.invoke(cli, ["search", "priority:high"])
        assert result.exit_code == 0
        assert "Late" in result.output and "p.late" in result.output
        assert not result.output.lstrip().startswith("[")

    def test_invalid_filter_is_an_error(self, runner, live_vault):
        result = runner.invoke(cli, ["search", "due:bogus"])
        assert result.exit_code != 0
        assert "Invalid due filter" in result.output

    def test_text_search_json_shares_record_and_skips_documents(self, runner, live_vault):
        result = runner.invoke(cli, ["search", "separation", "--json", "--no-context"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert [entry["stem"] for entry in payload] == ["p.late"]
        entry = payload[0]
        assert entry["priority"] == "high" and entry["due"] and "line" in entry and "text" in entry

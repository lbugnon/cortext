"""Tests for natural language date and tag parsing."""

import pytest
from datetime import datetime, timedelta
from cor.utils import parse_natural_language_text


class TestParseNaturalLanguageText:
    """Test natural language text parsing for dates and tags."""

    def test_parse_due_date_simple(self):
        """Test parsing 'due tomorrow'."""
        text = "finish the pipeline due tomorrow"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "finish the pipeline"
        assert due_date is not None
        assert tags == []
        assert status is None
        assert priority is None
        # Check that parsed date is roughly tomorrow (within reasonable bounds)
        tomorrow = datetime.now() + timedelta(days=1)
        assert abs((due_date - tomorrow).days) <= 1

    def test_parse_due_date_with_colon(self):
        """Test parsing 'due: <date>'."""
        text = "complete report due: next friday"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "complete report"
        assert due_date is not None
        assert tags == []
        assert status is None
        assert priority is None

    def test_parse_due_date_next_week(self):
        """Test parsing 'due next week'."""
        text = "review code due next week"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        # Note: "next week" may not be parsed by all date parsers
        # If it fails to parse, the text should remain unchanged
        if due_date:
            assert cleaned == "review code"
            assert status is None
            assert priority is None
            # Should be roughly 7 days from now
            next_week = datetime.now() + timedelta(days=7)
            assert abs((due_date - next_week).days) <= 3
        else:
            # If not parsed, text should be unchanged
            assert cleaned == text

    def test_parse_tags_single(self):
        """Test parsing single tag."""
        text = "fix bug tag urgent"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "fix bug"
        assert due_date is None
        assert tags == ["urgent"]
        assert status is None
        assert priority is None

    def test_parse_tags_multiple(self):
        """Test parsing multiple tags."""
        text = "implement feature tag ml nlp research"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "implement feature"
        assert due_date is None
        assert tags == ["ml", "nlp", "research"]
        assert status is None
        assert priority is None

    def test_parse_tags_with_colon(self):
        """Test parsing 'tag: <tags>'."""
        text = "update docs tag: documentation urgent"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "update docs"
        assert due_date is None
        assert tags == ["documentation", "urgent"]
        assert status is None
        assert priority is None

    def test_parse_both_due_and_tags(self):
        """Test parsing both due date and tags."""
        text = "finish project due next friday tag urgent ml"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "finish project"
        assert due_date is not None
        assert tags == ["urgent", "ml"]
        assert status is None
        assert priority is None

    def test_parse_both_reversed_order(self):
        """Test parsing tags before due date."""
        text = "fix issue tag bugfix due tomorrow"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "fix issue"
        assert due_date is not None
        assert tags == ["bugfix"]
        assert status is None
        assert priority is None

    def test_parse_no_keywords(self):
        """Test text without due or tag keywords."""
        text = "just a normal task description"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "just a normal task description"
        assert due_date is None
        assert tags == []
        assert status is None
        assert priority is None

    def test_parse_empty_text(self):
        """Test empty text."""
        cleaned, due_date, tags, status, priority = parse_natural_language_text("")
        
        assert cleaned == ""
        assert due_date is None
        assert tags == []
        assert status is None
        assert priority is None

    def test_parse_none_text(self):
        """Test None text."""
        cleaned, due_date, tags, status, priority = parse_natural_language_text(None)
        
        assert cleaned is None
        assert due_date is None
        assert tags == []
        assert status is None
        assert priority is None

    def test_parse_due_date_specific(self):
        """Test parsing specific date formats."""
        text = "meeting notes due 2026-02-15"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "meeting notes"
        assert due_date is not None
        assert due_date.year == 2026
        assert due_date.month == 2
        assert due_date.day == 15
        assert status is None
        assert priority is None

    def test_parse_tags_with_hyphens(self):
        """Test parsing tags with hyphens."""
        text = "new feature tag machine-learning high-priority"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "new feature"
        assert due_date is None
        assert "machine-learning" in tags
        assert "high-priority" in tags
        assert status is None
        assert priority is None

    def test_parse_case_insensitive(self):
        """Test that keywords are case-insensitive."""
        text = "task DUE tomorrow TAG urgent"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "task"
        assert due_date is not None
        assert tags == ["urgent"]
        assert status is None
        assert priority is None


class TestParseNaturalLanguageShortHourFormat:
    """Test short hour format 'in Xh' that search_dates doesn't handle."""

    def test_parse_due_in_5h(self):
        """Test parsing 'due in 5h' (short form)."""
        text = "reminder due in 5h"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "reminder"
        assert due_date is not None
        assert status is None
        assert priority is None
        # Should be approximately 5 hours from now
        expected = datetime.now() + timedelta(hours=5)
        diff = abs((due_date - expected).total_seconds())
        assert diff < 60  # Within 1 minute

    def test_parse_due_in_2h(self):
        """Test parsing 'due in 2h'."""
        text = "quick task due in 2h"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "quick task"
        assert due_date is not None
        assert status is None
        assert priority is None
        expected = datetime.now() + timedelta(hours=2)
        diff = abs((due_date - expected).total_seconds())
        assert diff < 60

    def test_parse_due_in_5h_with_tags(self):
        """Test parsing 'due in 5h' with tags."""
        text = "deploy due in 5h tag urgent"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "deploy"
        assert due_date is not None
        assert "urgent" in tags
        assert status is None
        assert priority is None
        expected = datetime.now() + timedelta(hours=5)
        diff = abs((due_date - expected).total_seconds())
        assert diff < 60


class TestParseNaturalLanguageTimeKeywords:
    """Test time keywords (morning, afternoon, etc.) in natural language date parsing."""

    def test_parse_due_tomorrow_morning(self):
        """Test parsing 'due tomorrow morning'."""
        text = "review code due tomorrow morning"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "review code"
        assert due_date is not None
        assert status is None
        assert priority is None
        assert due_date.hour == 9
        assert due_date.minute == 0
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        assert due_date.date() == tomorrow

    def test_parse_due_tomorrow_afternoon(self):
        """Test parsing 'due tomorrow afternoon'."""
        text = "submit report due tomorrow afternoon"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "submit report"
        assert due_date is not None
        assert status is None
        assert priority is None
        assert due_date.hour == 14
        assert due_date.minute == 0
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        assert due_date.date() == tomorrow

    def test_parse_due_tomorrow_evening(self):
        """Test parsing 'due tomorrow evening'."""
        text = "meeting due tomorrow evening"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "meeting"
        assert due_date is not None
        assert status is None
        assert priority is None
        assert due_date.hour == 18
        assert due_date.minute == 0
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        assert due_date.date() == tomorrow

    def test_parse_due_noon(self):
        """Test parsing 'due tomorrow noon'."""
        text = "lunch meeting due tomorrow noon"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "lunch meeting"
        assert due_date is not None
        assert status is None
        assert priority is None
        assert due_date.hour == 12
        assert due_date.minute == 0
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        assert due_date.date() == tomorrow

    def test_parse_due_night(self):
        """Test parsing 'due tomorrow night'."""
        text = "security check due tomorrow night"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "security check"
        assert due_date is not None
        assert status is None
        assert priority is None
        assert due_date.hour == 21
        assert due_date.minute == 0
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        assert due_date.date() == tomorrow

    def test_parse_due_with_time_and_tags(self):
        """Test parsing due date with morning keyword and tags."""
        text = "deploy app due tomorrow morning tag urgent production"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "deploy app"
        assert due_date is not None
        assert status is None
        assert priority is None
        assert due_date.hour == 9
        assert due_date.minute == 0
        assert "urgent" in tags
        assert "production" in tags

    def test_parse_due_next_friday_morning(self):
        """Test parsing 'due next friday morning'."""
        text = "demo due next friday morning"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "demo"
        assert due_date is not None
        assert status is None
        assert priority is None
        assert due_date.hour == 9
        assert due_date.minute == 0
        # Should be a Friday
        assert due_date.weekday() == 4  # Friday is 4 (Monday=0)

    def test_parse_due_with_explicit_time_overrides_keyword(self):
        """Test that explicit time (8pm) takes precedence over keywords."""
        text = "meeting due tomorrow 8pm"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "meeting"
        assert due_date is not None
        assert status is None
        assert priority is None
        # dateparser handles 8pm correctly
        assert due_date.hour == 20
        assert due_date.minute == 0


class TestParseNaturalLanguageStatus:
    """Test status (mark) parsing in natural language text."""

    def test_parse_mark_active(self):
        """Test parsing 'mark active'."""
        text = "start work mark active"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "start work"
        assert due_date is None
        assert tags == []
        assert status == "active"
        assert priority is None

    def test_parse_mark_blocked(self):
        """Test parsing 'mark blocked'."""
        text = "review code mark blocked"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "review code"
        assert due_date is None
        assert tags == []
        assert status == "blocked"
        assert priority is None

    def test_parse_mark_done(self):
        """Test parsing 'mark done'."""
        text = "deploy app mark done"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "deploy app"
        assert due_date is None
        assert tags == []
        assert status == "done"
        assert priority is None

    def test_parse_mark_waiting(self):
        """Test parsing 'mark waiting'."""
        text = "quick fix mark waiting"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "quick fix"
        assert due_date is None
        assert tags == []
        assert status == "waiting"
        assert priority is None

    def test_parse_mark_dropped(self):
        """Test parsing 'mark dropped'."""
        text = "cancelled task mark dropped"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "cancelled task"
        assert due_date is None
        assert tags == []
        assert status == "dropped"
        assert priority is None

    def test_parse_mark_todo(self):
        """Test parsing 'mark todo'."""
        text = "reset task mark todo"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "reset task"
        assert due_date is None
        assert tags == []
        assert status == "todo"
        assert priority is None

    def test_parse_mark_with_colon(self):
        """Test parsing 'mark: <status>'."""
        text = "start work mark: active"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "start work"
        assert status == "active"
        assert priority is None

    def test_parse_status_keyword(self):
        """Test parsing 'status <status>' as alternative to 'mark'."""
        text = "start work status active"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "start work"
        assert status == "active"
        assert priority is None

    def test_parse_mark_with_tags(self):
        """Test parsing mark with tags."""
        text = "deploy app mark active tag production"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "deploy app"
        assert due_date is None
        assert "production" in tags
        assert status == "active"
        assert priority is None

    def test_parse_mark_with_due_date(self):
        """Test parsing mark with due date."""
        text = "finish task due tomorrow mark active"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "finish task"
        assert due_date is not None
        assert tags == []
        assert status == "active"
        assert priority is None

    def test_parse_mark_due_and_tags(self):
        """Test parsing mark with due date and tags."""
        text = "write docs due tomorrow 8pm mark active tag urgent"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "write docs"
        assert due_date is not None
        assert due_date.hour == 20
        assert "urgent" in tags
        assert status == "active"
        assert priority is None

    def test_parse_invalid_status_ignored(self):
        """Test that invalid status values are ignored."""
        text = "start work mark invalid_status"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "start work mark invalid_status"
        assert due_date is None
        assert tags == []
        assert status is None
        assert priority is None

    def test_parse_mark_case_insensitive(self):
        """Test that mark keyword is case-insensitive."""
        text = "start work MARK ACTIVE"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "start work"
        assert status == "active"
        assert priority is None


class TestParseNaturalLanguagePriority:
    """Test priority parsing in natural language text."""

    def test_parse_priority_high(self):
        """Test parsing 'priority high'."""
        text = "important task priority high"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "important task"
        assert due_date is None
        assert tags == []
        assert status is None
        assert priority == "high"

    def test_parse_priority_medium(self):
        """Test parsing 'priority medium'."""
        text = "normal task priority medium"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "normal task"
        assert due_date is None
        assert tags == []
        assert status is None
        assert priority == "medium"

    def test_parse_priority_low(self):
        """Test parsing 'priority low'."""
        text = "backlog task priority low"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "backlog task"
        assert due_date is None
        assert tags == []
        assert status is None
        assert priority == "low"

    def test_parse_priority_with_colon(self):
        """Test parsing 'priority: <level>'."""
        text = "urgent fix priority: high"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "urgent fix"
        assert priority == "high"

    def test_parse_priority_with_due_date(self):
        """Test parsing priority with due date."""
        text = "finish task due tomorrow priority high"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "finish task"
        assert due_date is not None
        assert tags == []
        assert status is None
        assert priority == "high"

    def test_parse_priority_with_tags(self):
        """Test parsing priority with tags."""
        text = "deploy app priority high tag production"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "deploy app"
        assert due_date is None
        assert "production" in tags
        assert status is None
        assert priority == "high"

    def test_parse_priority_with_status(self):
        """Test parsing priority with status."""
        text = "start work mark active priority high"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "start work"
        assert due_date is None
        assert tags == []
        assert status == "active"
        assert priority == "high"

    def test_parse_priority_all_features(self):
        """Test parsing priority with all other features."""
        text = "deploy app due tomorrow mark active tag production priority high"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        assert cleaned == "deploy app"
        assert due_date is not None
        assert "production" in tags
        assert status == "active"
        assert priority == "high"

    def test_parse_invalid_priority_ignored(self):
        """Test that invalid priority values are ignored."""
        text = "some task priority critical"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        
        # Invalid priority should be left in text
        assert "priority critical" in cleaned
        assert priority is None

    def test_parse_priority_case_insensitive(self):
        """Test that priority keyword is case-insensitive."""
        text = "task PRIORITY HIGH"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)

        assert cleaned == "task"
        assert priority == "high"

    def test_keywords_require_whitespace_separation(self):
        """Keywords glued to a word are NOT treated as keywords.

        Documents the parser contract: 'tag', 'due', etc. are only recognized
        when whitespace-separated. 'tagurgent' must not produce a tag.
        """
        text = "do the thing tagurgent"
        cleaned, due_date, tags, status, priority = parse_natural_language_text(text)
        assert tags == []
        assert "tagurgent" in cleaned

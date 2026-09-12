"""Tests for the shared Markdown section operations."""

import pytest

from cor.core.content import add_initial_text, sections, set_section
from cor.utils import format_title


def test_format_title_displays_stem_separators_as_spaces():
    assert format_title("choose-next_step") == "Choose next step"


def test_set_section_replaces_only_exact_heading():
    original = "# Entry\n\n## Goal\n\nOld\n\n## Goal Detail\n\nKeep\n"
    changed = set_section(original, "Goal", "New")

    assert sections(changed) == {"Goal": "New", "Goal Detail": "Keep"}


def test_set_section_appends_with_spacing():
    changed = set_section("# Entry\n\n## Results\n\nFirst\n", "Results", "Second", append=True)

    assert sections(changed)["Results"] == "First\n\nSecond"


def test_set_section_creates_missing_section():
    changed = set_section("# Entry\n", "Decision", "Keep compact")

    assert sections(changed)["Decision"] == "Keep compact"


def test_section_heading_must_be_one_line():
    with pytest.raises(ValueError):
        set_section("# Entry\n", "Bad\nHeading", "value")


def test_initial_note_text_is_not_lost():
    assert add_initial_text("# Idea\n", "note", "Distance proxy").endswith(
        "Distance proxy\n"
    )

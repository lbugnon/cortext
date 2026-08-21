"""Tests for cor/core/links.py.

Only `LinkPatterns` survives in that module; the `LinkManager` class and its
tests were removed because nothing in production called them. These patterns
*are* live - `sync/runner.py` uses LINK, EXTERNAL_PREFIXES and TASK_ENTRY - so
their coverage is kept.
"""

from cor.core.links import LinkPatterns


class TestLinkPatterns:
    """Test regex patterns."""

    def test_basic_link_pattern(self):
        """Test basic link pattern matches."""
        text = "[Title](target)"
        matches = list(LinkPatterns.LINK.finditer(text))

        assert len(matches) == 1
        assert matches[0].group(1) == "Title"
        assert matches[0].group(2) == "target"

    def test_archive_link_pattern(self):
        """Test archive link pattern."""
        text = "[Title](archive/file)"
        matches = list(LinkPatterns.ARCHIVE_LINK.finditer(text))

        assert len(matches) == 1
        assert matches[0].group(2) == "file"

    def test_task_entry_pattern(self):
        """Test task entry pattern matches."""
        text = "- [x] [Task Title](task.name)"
        matches = list(LinkPatterns.TASK_ENTRY.finditer(text))

        assert len(matches) == 1
        assert matches[0].group(2) == "x"  # checkbox
        assert matches[0].group(3) == "task.name"  # target

    def test_external_prefixes(self):
        """External-link detection, as sync/runner.py performs it."""
        prefixes = LinkPatterns.EXTERNAL_PREFIXES

        assert "https://example.com".startswith(prefixes)
        assert "http://example.com".startswith(prefixes)
        assert "mailto:test@example.com".startswith(prefixes)
        assert "#section".startswith(prefixes)

        assert not "internal.md".startswith(prefixes)
        assert not "archive/file.md".startswith(prefixes)

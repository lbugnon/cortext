"""Tests for content search functionality.

Tests cover:
- search_content() basic text search
- search_content() with filters
- parse_search_query() filter parsing
- filter_matches() metadata filtering
- CLI search command
"""

import pytest
from click.testing import CliRunner
from datetime import date

from cor.cli import cli
from cor.search import (
    search_content,
    parse_search_query,
    filter_matches,
    SearchMatch,
)


@pytest.fixture
def vault_with_content(temp_vault):
    """Create a vault with various content for searching.
    
    Creates:
    - mlproject.md (project with "machine learning" and "neural network" content)
    - mlproject.training.md (task with "training" and "optimization" content)
    - mlproject.data.md (task with "data" and "preprocessing" content)
    - archived project in archive/oldproject.md
    """
    today = date.today().isoformat()
    
    # Create ML project
    ml_project = temp_vault / "mlproject.md"
    ml_project.write_text(f"""\
---
created: {today}
modified: {today}
type: project
status: active
tags: [ml, research]
---
# ML Project

## Summary
Research on machine learning and neural networks.

## Goal
Build a neural network classifier.

## Done When
Model achieves 95% accuracy on test set.
""")

    # Create training task
    training_task = temp_vault / "mlproject.training.md"
    training_task.write_text(f"""\
---
created: {today}
modified: {today}
type: task
status: active
parent: mlproject
tags: [training, optimization]
---
# Training Pipeline

[< ML Project](mlproject.md)

## Description
Implement the training loop with gradient descent optimization.
Neural network weights are updated iteratively.

## Solution
Use PyTorch for automatic differentiation.
""")

    # Create data task
    data_task = temp_vault / "mlproject.data.md"
    data_task.write_text(f"""\
---
created: {today}
modified: {today}
type: task
status: todo
parent: mlproject
tags: [data, preprocessing]
---
# Data Pipeline

[< ML Project](mlproject.md)

## Description
Clean and preprocess the dataset for training.
Remove outliers and normalize features.
""")

    # Create archived project
    archive_dir = temp_vault / "archive"
    archive_dir.mkdir(exist_ok=True)
    old_project = archive_dir / "oldproject.md"
    old_project.write_text(f"""\
---
created: {today}
modified: {today}
type: project
status: done
---
# Old Project

## Summary
Previous work on machine learning algorithms.
Neural networks were explored but abandoned.
""")

    return {
        "vault": temp_vault,
        "ml_project": ml_project,
        "training_task": training_task,
        "data_task": data_task,
        "old_project": old_project,
    }


class TestSearchContent:
    """Test search_content() function."""

    def test_basic_text_search(self, vault_with_content, monkeypatch):
        """Search should find text in files."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        results = search_content("neural network", limit=10)
        
        # Should find matches in ml_project.md and training_task.md
        assert len(results) >= 2
        
        # Check that results have correct structure
        for match in results:
            assert isinstance(match, SearchMatch)
            assert match.file.exists()
            assert match.line > 0
            assert "neural" in match.content.lower()

    def test_search_excludes_archived_by_default(self, vault_with_content, monkeypatch):
        """Search should not include archived files by default."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        results = search_content("abandoned", include_archived=False)
        
        # "abandoned" only appears in archived file
        assert len(results) == 0

    def test_search_includes_archived_when_requested(self, vault_with_content, monkeypatch):
        """Search should include archived files when include_archived=True."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        results = search_content("abandoned", include_archived=True)
        
        # Should find the archived file
        assert len(results) == 1
        assert "archive" in results[0].file.parts

    def test_search_respects_limit(self, vault_with_content, monkeypatch):
        """Search should respect the limit parameter."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        results = search_content("the", limit=2)  # Common word
        
        assert len(results) <= 2

    def test_search_returns_context(self, vault_with_content, monkeypatch):
        """Search should return context lines."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        results = search_content("gradient descent", context_lines=2)
        
        assert len(results) > 0
        match = results[0]
        # Should have context lines
        assert len(match.context_before) <= 2
        assert len(match.context_after) <= 2

    def test_case_insensitive_search(self, vault_with_content, monkeypatch):
        """Search should be case-insensitive."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        results_lower = search_content("neural", limit=10)
        results_upper = search_content("NEURAL", limit=10)
        
        # Should find same results regardless of case
        assert len(results_lower) == len(results_upper)

    def test_no_matches_returns_empty_list(self, vault_with_content, monkeypatch):
        """Search with no matches should return empty list."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        results = search_content("xyznonexistent123")
        
        assert results == []


class TestParseSearchQuery:
    """Test parse_search_query() function."""

    def test_plain_text_query(self):
        """Plain text query should be returned as-is."""
        query = "machine learning"
        text, filters = parse_search_query(query)
        
        assert text == "machine learning"
        assert filters == {}

    def test_status_filter(self):
        """status:VALUE should be parsed as filter."""
        query = "neural status:active"
        text, filters = parse_search_query(query)
        
        assert text == "neural"
        assert filters == {"status": "active"}

    def test_tag_filter(self):
        """#tag should be parsed as tag filter."""
        query = "training #ml"
        text, filters = parse_search_query(query)
        
        assert text == "training"
        assert filters == {"tags": ["ml"]}

    def test_multiple_tags(self):
        """Multiple #tags should be collected."""
        query = "training #ml #research"
        text, filters = parse_search_query(query)
        
        assert text == "training"
        assert filters == {"tags": ["ml", "research"]}

    def test_project_filter(self):
        """project:NAME should be parsed as project filter."""
        query = "optimization project:mlproject"
        text, filters = parse_search_query(query)
        
        assert text == "optimization"
        assert filters == {"project": "mlproject"}

    def test_multiple_filters(self):
        """Multiple filters should be combined."""
        query = "neural status:active #ml project:mlproject"
        text, filters = parse_search_query(query)
        
        assert text == "neural"
        assert filters["status"] == "active"
        assert filters["tags"] == ["ml"]
        assert filters["project"] == "mlproject"

    def test_only_filters_no_text(self):
        """Query with only filters should have empty text."""
        query = "status:active #urgent"
        text, filters = parse_search_query(query)
        
        assert text == ""
        assert filters["status"] == "active"
        assert filters["tags"] == ["urgent"]

    def test_empty_hash_not_tag(self):
        """Lone # should not be treated as tag."""
        query = "test #"
        text, filters = parse_search_query(query)
        
        assert text == "test #"
        assert "tags" not in filters


class TestFilterMatches:
    """Test filter_matches() function."""

    def test_filter_by_status(self, vault_with_content, monkeypatch):
        """Filter by status should work."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        # Get all matches for "neural"
        matches = search_content("neural", limit=10)
        assert len(matches) >= 2  # Should have multiple matches
        
        # Filter to only active status
        filtered = filter_matches(matches, {"status": "active"})
        
        # Should only return active tasks
        for match in filtered:
            content = match.file.read_text()
            assert "status: active" in content

    def test_filter_by_tag(self, vault_with_content, monkeypatch):
        """Filter by tags should work."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        matches = search_content("Pipeline", limit=10)
        assert len(matches) >= 2
        
        # Filter to only training tag
        filtered = filter_matches(matches, {"tags": ["training"]})
        
        # Should only return training task
        assert len(filtered) >= 1
        for match in filtered:
            assert "training" in match.file.name.lower()

    def test_filter_by_project(self, vault_with_content, monkeypatch):
        """Filter by project should work."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        matches = search_content("the", limit=10)  # Common word
        
        # Filter to mlproject
        filtered = filter_matches(matches, {"project": "mlproject"})
        
        for match in filtered:
            assert match.file.stem.startswith("mlproject.") or match.file.stem == "mlproject"

    def test_filter_no_matches(self, vault_with_content, monkeypatch):
        """Filter with no matches should return empty list."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        matches = search_content("neural", limit=10)
        
        # Filter to non-existent status
        filtered = filter_matches(matches, {"status": "nonexistent"})
        
        assert filtered == []

    def test_empty_filters_returns_all(self, vault_with_content, monkeypatch):
        """Empty filters should return all matches."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        matches = search_content("neural", limit=10)
        
        filtered = filter_matches(matches, {})
        
        assert len(filtered) == len(matches)


class TestSearchCommand:
    """Test cor search CLI command."""

    def test_search_basic(self, runner, vault_with_content, monkeypatch):
        """cor search should find content."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result = runner.invoke(cli, ["search", "neural"])
        
        assert result.exit_code == 0
        assert "neural" in result.output.lower()
        assert "mlproject" in result.output

    def test_search_with_limit(self, runner, vault_with_content, monkeypatch):
        """cor search -n should limit results."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result = runner.invoke(cli, ["search", "-n", "1", "the"])
        
        assert result.exit_code == 0
        assert "1 result" in result.output or "results" in result.output

    def test_search_include_archived(self, runner, vault_with_content, monkeypatch):
        """cor search -a should include archived files."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result = runner.invoke(cli, ["search", "-a", "abandoned"])
        
        assert result.exit_code == 0
        assert "archived" in result.output.lower()

    def test_search_with_status_filter(self, runner, vault_with_content, monkeypatch):
        """cor search with status: filter should work."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result = runner.invoke(cli, ["search", "Pipeline status:active"])
        
        assert result.exit_code == 0
        # Should show filtered results
        assert "result" in result.output

    def test_search_no_matches(self, runner, vault_with_content, monkeypatch):
        """cor search with no matches should show message."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result = runner.invoke(cli, ["search", "xyznonexistent123"])
        
        assert result.exit_code == 0
        assert "No matches found" in result.output

    def test_search_empty_query(self, runner, vault_with_content, monkeypatch):
        """cor search with empty query should show error."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result = runner.invoke(cli, ["search", ""])
        
        assert result.exit_code == 0
        assert "Empty query" in result.output

    def test_search_shows_file_and_line(self, runner, vault_with_content, monkeypatch):
        """Search results should show file:line."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result = runner.invoke(cli, ["search", "neural"])
        
        assert result.exit_code == 0
        # Should show file paths with line numbers
        assert ".md:" in result.output

    def test_search_no_context(self, runner, vault_with_content, monkeypatch):
        """cor search --no-context should hide context lines."""
        monkeypatch.chdir(vault_with_content["vault"])
        
        result_full = runner.invoke(cli, ["search", "neural"])
        result_compact = runner.invoke(cli, ["search", "--no-context", "neural"])
        
        assert result_full.exit_code == 0
        assert result_compact.exit_code == 0
        # Compact output should be shorter
        assert len(result_compact.output) < len(result_full.output)

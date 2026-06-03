"""Tests for bibliography reference commands (`cor ref`)."""

import pytest
from click.testing import CliRunner

from cor.cli import cli
from cor.commands.refs import _search_references


BIB = """@article{vaswani2017,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and Shazeer, Noam},
  year = {2017},
}
@article{smith2020,
  title = {Sourdough Fermentation Dynamics},
  author = {Smith, Jane},
  year = {2020},
}
"""


@pytest.fixture
def runner():
    return CliRunner()


def test_search_references_ranks_substring():
    entries = [
        {"ID": "vaswani2017", "author": "Vaswani, Ashish", "title": "Attention Is All You Need", "year": "2017"},
        {"ID": "smith2020", "author": "Smith, Jane", "title": "Sourdough Fermentation", "year": "2020"},
    ]
    results = _search_references(entries, "attention")
    assert results, "expected at least one match"
    top_entry, top_score = results[0]
    assert top_entry["ID"] == "vaswani2017"
    assert isinstance(top_score, (int, float))
    # An unrelated query should not match anything.
    assert _search_references(entries, "quantum chromodynamics") == []


def test_search_references_empty_query():
    assert _search_references([{"ID": "x", "title": "y"}], "   ") == []


def test_ref_search_cli_runs_and_matches(temp_vault, runner):
    """Regression: `cor ref search` previously crashed (returned a
    NotImplementedError instance that was then sliced)."""
    (temp_vault / "backlog.md").write_text("---\ntype: backlog\n---\n## Inbox\n")
    ref_dir = temp_vault / "ref"
    ref_dir.mkdir()
    (ref_dir / "references.bib").write_text(BIB)

    result = runner.invoke(cli, ["ref", "search", "attention"])
    assert result.exit_code == 0, result.output
    assert "vaswani2017" in result.output
    assert "smith2020" not in result.output


def test_ref_search_cli_no_refs(temp_vault, runner):
    (temp_vault / "backlog.md").write_text("---\ntype: backlog\n---\n## Inbox\n")
    result = runner.invoke(cli, ["ref", "search", "anything"])
    assert result.exit_code == 0, result.output
    assert "No references" in result.output

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


def _seed_refs(temp_vault):
    (temp_vault / "backlog.md").write_text("---\ntype: backlog\n---\n## Inbox\n")
    ref_dir = temp_vault / "ref"
    ref_dir.mkdir()
    (ref_dir / "references.bib").write_text(BIB)
    (ref_dir / "vaswani2017.md").write_text("---\ntype: note\ntags: []\n---\n# Attention\n")
    return ref_dir


def test_ref_del_removes_markdown_and_bib_entry(temp_vault, runner):
    """Regression: del must also remove the .bib entry (was leaving it dangling)."""
    ref_dir = _seed_refs(temp_vault)
    result = runner.invoke(cli, ["ref", "del", "-f", "vaswani2017"])
    assert result.exit_code == 0, result.output
    assert not (ref_dir / "vaswani2017.md").exists()
    bib = (ref_dir / "references.bib").read_text()
    assert "vaswani2017" not in bib
    assert "smith2020" in bib  # unrelated entry untouched


def test_ref_del_confirmation_shows_title_from_bib(temp_vault, runner):
    """Regression: confirmation showed a blank title (read from frontmatter that
    never held it); it must now read the title from the .bib."""
    ref_dir = _seed_refs(temp_vault)
    result = runner.invoke(cli, ["ref", "del", "vaswani2017"], input="n\n")
    assert "Attention Is All You Need" in result.output
    assert (ref_dir / "vaswani2017.md").exists()  # cancelled, nothing removed

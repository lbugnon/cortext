"""Tests for `cor extract` - promoting lines of a note into a task."""

import pytest

from cor.cli import cli
from cor.sync import MaintenanceRunner


NOTE = """---
type: note
created: 2026-08-21 10:00
modified: 2026-08-21 10:00
tags: []
parent: myproj
---
# Meeting notes

[< Myproj](myproj.md)

Points from the sync:

1. Reviewed the pipeline output
2. Agreed on the metric
3. Someone should fix login redirect on Safari
   - only reproduces on iOS 17
   - probably the cookie domain

Next meeting friday.
"""

# Line numbers of the interesting block in NOTE (1-based, as nvim reports them).
BLOCK_FIRST, BLOCK_LAST = 16, 18
TASK_STEM = "myproj.someone_should_fix_login_redirect"


@pytest.fixture
def vault_with_note(runner, temp_vault, monkeypatch):
    """A vault holding `myproj` and a note with a bullet worth extracting."""
    monkeypatch.chdir(temp_vault)
    runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
    (temp_vault / "myproj.meeting-notes.md").write_text(NOTE)
    return temp_vault


def lines_of(path):
    return path.read_text().split("\n")


def index_of(path, text):
    """Index of the line equal to `text`.

    Assertions go by content, not by number: `MaintenanceRunner` rewrites the
    file through frontmatter.dump at the end, which inserts a blank line after
    the closing `---` and shifts every body line down by one.
    """
    lines = lines_of(path)
    assert text in lines, f"{text!r} not in:\n" + "\n".join(lines)
    return lines.index(text)


class TestExtract:
    """The happy paths."""

    def test_creates_task_and_links_from_note(self, runner, vault_with_note):
        result = runner.invoke(
            cli,
            ["extract", "myproj.meeting-notes", "--lines", f"{BLOCK_FIRST}-{BLOCK_LAST}"],
        )
        assert result.exit_code == 0, result.output

        task = vault_with_note / f"{TASK_STEM}.md"
        assert task.exists(), f"Task not created: {result.output}"

        content = task.read_text()
        assert "type: task" in content
        assert "parent: myproj" in content
        # The whole block landed in the description, dedented one level.
        assert "Someone should fix login redirect on Safari" in content
        assert "\n- only reproduces on iOS 17" in content
        assert "\n- probably the cookie domain" in content

        # The note keeps a link where the bullet was, with its original marker.
        note = vault_with_note / "myproj.meeting-notes.md"
        note_lines = lines_of(note)
        at = index_of(note, f"3. [Someone should fix login redirect]({TASK_STEM}.md)")
        assert note_lines[at - 1] == "2. Agreed on the metric"
        assert note_lines[at + 1] == ""
        assert note_lines[at + 2] == "Next meeting friday."
        assert "only reproduces on iOS 17" not in "\n".join(note_lines)

    def test_task_indexed_in_project(self, runner, vault_with_note):
        runner.invoke(
            cli,
            ["extract", "myproj.meeting-notes", "--lines", f"{BLOCK_FIRST}-{BLOCK_LAST}"],
        )
        project = (vault_with_note / "myproj.md").read_text()
        assert f"- [ ] [Someone should fix login redirect]({TASK_STEM}.md)" in project

    def test_last_output_line_is_the_new_file(self, runner, vault_with_note):
        result = runner.invoke(
            cli, ["extract", "myproj.meeting-notes", "--lines", str(BLOCK_FIRST)]
        )
        last = [ln for ln in result.output.split("\n") if ln.strip()][-1]
        assert last == str(vault_with_note / f"{TASK_STEM}.md")

    def test_single_line_and_derived_name(self, runner, vault_with_note):
        """Name comes from the first six words, minus a trailing filler word."""
        result = runner.invoke(
            cli, ["extract", "myproj.meeting-notes", "--lines", "15"]
        )
        assert result.exit_code == 0, result.output
        # "2. Agreed on the metric" -> agreed_on_the_metric
        assert (vault_with_note / "myproj.agreed_on_the_metric.md").exists()

    def test_explicit_name_is_used_as_sibling(self, runner, vault_with_note):
        runner.invoke(
            cli,
            ["extract", "myproj.meeting-notes", "--lines", "16", "--name", "fix_login"],
        )
        assert (vault_with_note / "myproj.fix_login.md").exists()

    def test_dotted_name_chooses_the_parent(self, runner, vault_with_note):
        """A dotted --name retargets the parent, creating groups as needed."""
        result = runner.invoke(
            cli,
            ["extract", "myproj.meeting-notes", "--lines", "16",
             "--name", "myproj.bugs.fix_login"],
        )
        assert result.exit_code == 0, result.output

        task = vault_with_note / "myproj.bugs.fix_login.md"
        assert task.exists()
        assert "parent: myproj.bugs" in task.read_text()

        group = vault_with_note / "myproj.bugs.md"
        assert group.exists(), "Missing task group should be auto-created"
        assert "- [ ] [Fix login](myproj.bugs.fix_login.md)" in group.read_text()

    def test_keep_leaves_the_text_in_place(self, runner, vault_with_note):
        runner.invoke(
            cli, ["extract", "myproj.meeting-notes", "--lines", "15", "--keep"]
        )
        note = vault_with_note / "myproj.meeting-notes.md"
        at = index_of(note, "2. Agreed on the metric")
        assert lines_of(note)[at + 1] == (
            "2. [Agreed on the metric](myproj.agreed_on_the_metric.md)"
        )

    def test_blank_lines_around_the_range_are_ignored(self, runner, vault_with_note):
        """An overshooting selection must not swallow the paragraph break."""
        result = runner.invoke(
            cli, ["extract", "myproj.meeting-notes", "--lines", "15-19"]
        )
        assert result.exit_code == 0, result.output

        # Lines 15-18 became one link line; line 19 was blank and stayed put.
        note = vault_with_note / "myproj.meeting-notes.md"
        at = index_of(note, "2. [Agreed on the metric](myproj.agreed_on_the_metric.md)")
        note_lines = lines_of(note)
        assert note_lines[at + 1] == ""
        assert note_lines[at + 2] == "Next meeting friday."

    def test_indented_block_keeps_relative_nesting(self, runner, temp_vault, monkeypatch):
        monkeypatch.chdir(temp_vault)
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        note = temp_vault / "myproj.ideas.md"
        note.write_text(
            "---\ntype: note\nparent: myproj\n---\n# Ideas\n\n"
            "  - [ ] rewrite the loader\n"
            "    - it blocks on IO\n"
        )
        result = runner.invoke(cli, ["extract", "myproj.ideas", "--lines", "7-8"])
        assert result.exit_code == 0, result.output

        body = (temp_vault / "myproj.rewrite_the_loader.md").read_text()
        assert "\nrewrite the loader\n  - it blocks on IO\n" in body

        # Indentation is preserved, but the checkbox is not: a `- [ ]` link is a
        # task entry to MaintenanceRunner, which sorts those within the parent.
        assert (
            "  - [Rewrite the loader](myproj.rewrite_the_loader.md)"
            in note.read_text()
        )


class TestExtractFromParent:
    """Extracting out of the very file that becomes the task's parent."""

    @pytest.fixture
    def project_with_prose(self, runner, temp_vault, monkeypatch):
        monkeypatch.chdir(temp_vault)
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.other", "--no-edit"])

        project = temp_vault / "myproj.md"
        content = project.read_text().replace(
            "## Goal\n", "## Goal\n- write the migration script\n- keep this prose\n"
        )
        assert "- keep this prose" in content
        project.write_text(content)
        return temp_vault

    def _inline_link_line(self, project):
        for line in project.read_text().split("\n"):
            if line.startswith("- [Write the migration script]"):
                return line
        return None

    def test_inline_link_and_tasks_entry_coexist(self, runner, project_with_prose):
        project = project_with_prose / "myproj.md"
        target = index_of(project, "- write the migration script") + 1

        result = runner.invoke(cli, ["extract", "myproj", "--lines", str(target)])
        assert result.exit_code == 0, result.output

        content = project.read_text()
        # The inline link replaced the prose bullet ...
        assert "- [Write the migration script](myproj.write_the_migration_script.md)" in content
        assert "- write the migration script" not in content
        assert "- keep this prose" in content
        # ... and the checkbox entry the Tasks section needs was still added.
        assert (
            "- [ ] [Write the migration script](myproj.write_the_migration_script.md)"
            in content
        )

    def test_sync_does_not_disturb_the_inline_link(self, runner, project_with_prose):
        """sort_tasks_in_parent rewrites the span between task entries."""
        project = project_with_prose / "myproj.md"
        target = index_of(project, "- write the migration script") + 1
        runner.invoke(cli, ["extract", "myproj", "--lines", str(target)])

        runner.invoke(cli, ["mark", "myproj.other", "done"])
        MaintenanceRunner(project_with_prose).sync([str(project)])

        content = project.read_text()
        assert self._inline_link_line(project) is not None, "Inline link was moved or lost"
        assert "- keep this prose" in content, "Prose next to the link was lost"


class TestExtractErrors:
    """Bad input must leave both files alone."""

    def test_target_exists(self, runner, vault_with_note):
        runner.invoke(cli, ["new", "task", "myproj.taken", "--no-edit"])
        before = (vault_with_note / "myproj.meeting-notes.md").read_text()

        result = runner.invoke(
            cli,
            ["extract", "myproj.meeting-notes", "--lines", "16", "--name", "taken"],
        )
        assert result.exit_code == 1
        assert "already exists" in result.output
        assert (vault_with_note / "myproj.meeting-notes.md").read_text() == before

    @pytest.mark.parametrize(
        "spec,expected",
        [
            ("900", "has 21 lines"),
            ("3", "frontmatter"),
            ("13", "empty"),
            ("5x", "Invalid --lines"),
            ("0", "Invalid line range"),
            ("8-4", "Invalid line range"),
        ],
    )
    def test_rejected_ranges(self, runner, vault_with_note, spec, expected):
        before = (vault_with_note / "myproj.meeting-notes.md").read_text()

        result = runner.invoke(cli, ["extract", "myproj.meeting-notes", "--lines", spec])
        assert result.exit_code != 0
        assert expected in result.output
        assert (vault_with_note / "myproj.meeting-notes.md").read_text() == before

    def test_undesirable_name_from_pure_punctuation(self, runner, temp_vault, monkeypatch):
        monkeypatch.chdir(temp_vault)
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        note = temp_vault / "myproj.ideas.md"
        note.write_text("---\ntype: note\nparent: myproj\n---\n# Ideas\n\n- ...\n")

        result = runner.invoke(cli, ["extract", "myproj.ideas", "--lines", "7"])
        assert result.exit_code == 1
        assert "--name" in result.output

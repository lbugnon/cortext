"""Tests for note relations: continues, related, requires.

Covers:
- `cor rel add/rm/show` and `cor new project --continues`
- Multi-predecessor continuation and the copied Goal/Summary context
- Computed inverses (continued_by, blocks) and symmetric `related`
- Archive-relative link prefixes on the copied context
- Idempotency, self-links, cycles, type validation
- Rename/delete integrity across all relation fields
"""

import subprocess
import sys
from datetime import date
from pathlib import Path

import frontmatter
import pytest
from click.testing import CliRunner

from cor.cli import cli
from cor.core.continuation import extract_section
from cor.core.notes import NoteMetadata
from cor.core.relations import _all_notes, add_relation
from cor.dependencies import calculate_inverse, get_relations
from cor.exceptions import ValidationError


def write_project(vault: Path, stem: str, *, status="planning", goal=None,
                  summary=None, **extra) -> Path:
    """Create a project file with optional Goal/Summary bodies."""
    today = date.today().isoformat()
    meta = [f"created: {today}", f"modified: {today}", "type: project",
            f"status: {status}"]
    for key, value in extra.items():
        meta.append(f"{key}: {value}")

    body = [f"# {stem}", ""]
    if goal:
        body += ["## Goal", "", goal, ""]
    if summary:
        body += ["## Summary", "", summary, ""]
    body += ["## Tasks", ""]

    path = vault / f"{stem}.md"
    path.write_text("---\n" + "\n".join(meta) + "\n---\n" + "\n".join(body))
    return path


def archive_project(vault: Path, stem: str, **kwargs) -> Path:
    """Create a project directly in archive/, as a done project would be."""
    path = write_project(vault, stem, status="done", **kwargs)
    target = vault / "archive" / f"{stem}.md"
    path.rename(target)
    return target


def fm(path: Path) -> dict:
    return frontmatter.load(path).metadata


@pytest.fixture
def continuation_vault(temp_vault):
    """Two archived predecessors with real Goal/Summary text, plus a successor."""
    archive_project(
        temp_vault, "old-a",
        goal="Ship the first screening pipeline.",
        summary="Finished in Q2; old-a hit its targets.",
    )
    archive_project(
        temp_vault, "old-b",
        goal="Validate the assay end to end.",
        summary="Wrapped up after the assay stabilised.",
    )
    write_project(temp_vault, "new-thing")
    return temp_vault


class TestContinues:
    """`continues` links a new project to the finished work it picks up."""

    def test_multiple_predecessors(self, runner, continuation_vault):
        result = runner.invoke(
            cli, ["rel", "add", "new-thing", "old-a", "old-b", "--as", "continues"]
        )

        assert result.exit_code == 0, result.output
        assert fm(continuation_vault / "new-thing.md")["continues"] == ["old-a", "old-b"]

    def test_copies_goal(self, runner, continuation_vault):
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        body = (continuation_vault / "new-thing.md").read_text()
        assert "## Continues" in body
        assert "Ship the first screening pipeline." in body

    def test_does_not_copy_summary(self, runner, continuation_vault):
        """Only COPIED_SECTIONS travels; the Summary is history, and a link away."""
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        body = (continuation_vault / "new-thing.md").read_text()
        assert "old-a hit its targets." not in body
        # ...but the predecessor is still reachable in full.
        assert "](archive/old-a.md)" in body

    def test_copied_sections_is_the_single_switch(self, runner, continuation_vault, monkeypatch):
        """Adding a section name to the constant is all it takes to copy it."""
        import cor.core.continuation as continuation

        monkeypatch.setattr(continuation, "COPIED_SECTIONS", ("Goal", "Summary"))
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        body = (continuation_vault / "new-thing.md").read_text()
        assert "old-a hit its targets." in body

    def test_predecessor_file_untouched(self, runner, continuation_vault):
        """The archived predecessor keeps its own state; no reverse edge is written."""
        before = (continuation_vault / "archive" / "old-a.md").read_text()

        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        after = (continuation_vault / "archive" / "old-a.md").read_text()
        assert after == before
        assert "continued_by" not in after

    def test_archived_predecessor_link_uses_archive_prefix(self, runner, continuation_vault):
        """Regression guard for the archive link-prefix bug class.

        The successor is active and the predecessor is archived, so the link
        must be `archive/old-a.md`. A bare `old-a.md` would not resolve.
        """
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        body = (continuation_vault / "new-thing.md").read_text()
        assert "](archive/old-a.md)" in body
        assert "](old-a.md)" not in body

    def test_active_predecessor_link_is_bare(self, runner, temp_vault):
        """Both ends active -> no prefix."""
        write_project(temp_vault, "pred", goal="Do the thing.")
        write_project(temp_vault, "succ")

        runner.invoke(cli, ["rel", "add", "succ", "pred", "--as", "continues"])

        body = (temp_vault / "succ.md").read_text()
        assert "](pred.md)" in body
        assert "archive/pred.md" not in body

    def test_idempotent(self, runner, continuation_vault):
        """Re-running must not duplicate the list entry or the context block."""
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        path = continuation_vault / "new-thing.md"
        body = path.read_text()
        assert fm(path)["continues"] == ["old-a"]
        assert body.count("## Continues") == 1
        assert body.count("[< Continues:") == 1
        assert body.count("Ship the first screening pipeline.") == 1

    def test_second_predecessor_appends(self, runner, continuation_vault):
        """Adding a predecessor later extends the existing section."""
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])
        runner.invoke(cli, ["rel", "add", "new-thing", "old-b", "--as", "continues"])

        path = continuation_vault / "new-thing.md"
        assert fm(path)["continues"] == ["old-a", "old-b"]
        body = path.read_text()
        assert body.count("[< Continues:") == 2
        assert body.count("## Continues") == 1


class TestComputedInverse:
    """Reverse directions are derived by scanning, never stored."""

    def test_continued_by_is_computed(self, runner, continuation_vault):
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        result = runner.invoke(cli, ["rel", "show", "old-a"])

        assert result.exit_code == 0, result.output
        assert "Continued by" in result.output
        assert "new-thing" in result.output

    def test_one_predecessor_many_successors(self, runner, continuation_vault):
        """A project that split in two shows both successors."""
        write_project(continuation_vault, "fork-b")
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])
        runner.invoke(cli, ["rel", "add", "fork-b", "old-a", "--as", "continues"])

        notes = _all_notes(continuation_vault)
        inverse = calculate_inverse(notes, "continues", ("project",))

        assert sorted(inverse["old-a"]) == ["fork-b", "new-thing"]

    def test_show_lists_forward_direction(self, runner, continuation_vault):
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        result = runner.invoke(cli, ["rel", "show", "new-thing"])

        assert result.exit_code == 0, result.output
        assert "Continues" in result.output
        assert "old-a" in result.output


class TestRelated:
    """`related` is symmetric: storing it on one side is enough."""

    def test_visible_from_the_other_side(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")

        runner.invoke(cli, ["rel", "add", "alpha", "beta"])

        # Only alpha stores it...
        assert fm(temp_vault / "alpha.md")["related"] == ["beta"]
        assert "related" not in fm(temp_vault / "beta.md")

        # ...but beta still reports it.
        result = runner.invoke(cli, ["rel", "show", "beta"])
        assert "Related" in result.output
        assert "alpha" in result.output

    def test_no_duplicate_when_stored_both_ways(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")

        runner.invoke(cli, ["rel", "add", "alpha", "beta"])
        runner.invoke(cli, ["rel", "add", "beta", "alpha"])

        notes = _all_notes(temp_vault)
        alpha = next(n for n in notes if n.path.stem == "alpha")
        info = get_relations(alpha, notes)

        assert info.related == ["beta"]

    def test_remove_finds_edge_on_far_side(self, runner, temp_vault):
        """Removing from the side that does not store it still works."""
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")
        runner.invoke(cli, ["rel", "add", "alpha", "beta"])

        result = runner.invoke(cli, ["rel", "rm", "beta", "alpha"])

        assert result.exit_code == 0, result.output
        assert fm(temp_vault / "alpha.md")["related"] == []

    def test_related_cycle_is_allowed(self, runner, temp_vault):
        """Symmetric relations have no meaningful cycle."""
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")

        runner.invoke(cli, ["rel", "add", "alpha", "beta"])
        result = runner.invoke(cli, ["rel", "add", "beta", "alpha"])

        assert result.exit_code == 0, result.output

    def test_related_allowed_on_notes(self, runner, temp_vault):
        """Unlike `continues`, `related` is valid on plain notes."""
        write_project(temp_vault, "alpha")
        today = date.today().isoformat()
        (temp_vault / "idea.md").write_text(
            f"---\ncreated: {today}\nmodified: {today}\ntype: note\n---\n# Idea\n"
        )

        result = runner.invoke(cli, ["rel", "add", "idea", "alpha"])

        assert result.exit_code == 0, result.output
        assert fm(temp_vault / "idea.md")["related"] == ["alpha"]


class TestValidation:
    """Illegal relations are rejected before anything is written."""

    def test_self_link_rejected(self, runner, continuation_vault):
        result = runner.invoke(
            cli, ["rel", "add", "new-thing", "new-thing", "--as", "continues"]
        )

        assert result.exit_code != 0
        assert "itself" in result.output

    def test_cycle_rejected(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")
        runner.invoke(cli, ["rel", "add", "alpha", "beta", "--as", "continues"])

        result = runner.invoke(cli, ["rel", "add", "beta", "alpha", "--as", "continues"])

        assert result.exit_code != 0
        assert "cycle" in result.output.lower()

    def test_longer_cycle_rejected(self, runner, temp_vault):
        for stem in ("a1", "b1", "c1"):
            write_project(temp_vault, stem)
        runner.invoke(cli, ["rel", "add", "a1", "b1", "--as", "continues"])
        runner.invoke(cli, ["rel", "add", "b1", "c1", "--as", "continues"])

        result = runner.invoke(cli, ["rel", "add", "c1", "a1", "--as", "continues"])

        assert result.exit_code != 0
        assert "cycle" in result.output.lower()

    def test_continues_rejected_on_task(self, runner, continuation_vault):
        runner.invoke(cli, ["new", "task", "new-thing.sub", "--no-edit"])

        result = runner.invoke(
            cli, ["rel", "add", "new-thing.sub", "old-a", "--as", "continues"]
        )

        assert result.exit_code != 0
        assert "not valid on a task" in result.output

    def test_nothing_written_on_rejection(self, runner, continuation_vault):
        before = (continuation_vault / "new-thing.md").read_text()

        runner.invoke(cli, ["rel", "add", "new-thing", "new-thing", "--as", "continues"])

        assert (continuation_vault / "new-thing.md").read_text() == before


class TestNewProjectContinuesFlag:
    """`cor new project --continues` matches `new` followed by `rel add`."""

    def test_flag_creates_same_state(self, runner, continuation_vault):
        runner.invoke(cli, ["new", "project", "viaflag", "--no-edit",
                            "-c", "old-a", "-c", "old-b"])

        path = continuation_vault / "viaflag.md"
        assert fm(path)["continues"] == ["old-a", "old-b"]
        body = path.read_text()
        assert "](archive/old-a.md)" in body
        assert "Ship the first screening pipeline." in body

    def test_flag_rejected_for_tasks(self, runner, continuation_vault):
        result = runner.invoke(
            cli, ["new", "task", "new-thing.sub", "--no-edit", "-c", "old-a"]
        )

        assert result.exit_code != 0
        assert "only valid for projects" in result.output


class TestRenameAndDeleteIntegrity:
    """Relations hold bare stems, so rename/delete must rewrite them."""

    def test_rename_updates_continues(self, runner, continuation_vault):
        """Renaming an archived predecessor must retarget its successors."""
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])
        assert fm(continuation_vault / "new-thing.md")["continues"] == ["old-a"]

        result = runner.invoke(cli, ["rename", "-a", "old-a", "renamed-a"])

        assert result.exit_code == 0, result.output
        assert (continuation_vault / "archive" / "renamed-a.md").exists()
        assert fm(continuation_vault / "new-thing.md")["continues"] == ["renamed-a"]

    def test_rename_updates_related(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")
        runner.invoke(cli, ["rel", "add", "alpha", "beta"])

        runner.invoke(cli, ["rename", "beta", "beta-v2"])

        assert fm(temp_vault / "alpha.md")["related"] == ["beta-v2"]

    def test_delete_prunes_continues(self, runner, continuation_vault):
        """Regression: top-level notes returned early before relation cleanup."""
        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "old-b",
                            "--as", "continues"])

        runner.invoke(cli, ["delete", "-a", "old-b"], input="y\n")

        assert fm(continuation_vault / "new-thing.md")["continues"] == ["old-a"]

    def test_delete_prunes_related(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")
        runner.invoke(cli, ["rel", "add", "alpha", "beta"])

        runner.invoke(cli, ["delete", "beta"], input="y\n")

        assert fm(temp_vault / "alpha.md")["related"] == []


class TestDependShimUnaffected:
    """`cor depend` keeps its existing behaviour alongside `cor rel`."""

    def test_depend_add_still_works(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")

        result = runner.invoke(cli, ["depend", "add", "alpha", "beta"])

        assert result.exit_code == 0, result.output
        assert fm(temp_vault / "alpha.md")["requires"] == ["beta"]

    def test_rel_add_requires_is_equivalent(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")

        result = runner.invoke(cli, ["rel", "add", "alpha", "beta", "--as", "requires"])

        assert result.exit_code == 0, result.output
        assert fm(temp_vault / "alpha.md")["requires"] == ["beta"]

    def test_blocks_still_computed(self, runner, temp_vault):
        write_project(temp_vault, "alpha")
        write_project(temp_vault, "beta")
        runner.invoke(cli, ["depend", "add", "alpha", "beta"])

        result = runner.invoke(cli, ["rel", "show", "beta"])

        assert "Blocks" in result.output
        assert "alpha" in result.output


class TestPreCommitStability:
    """State written by `cor rel` must already be normalized."""

    def test_hook_leaves_files_unchanged(self, runner, continuation_vault):
        from test_precommit import run_precommit, stage_files

        runner.invoke(cli, ["rel", "add", "new-thing", "old-a", "--as", "continues"])

        path = continuation_vault / "new-thing.md"
        before = path.read_text()

        subprocess.run(["git", "add", "-A"], cwd=continuation_vault, capture_output=True)
        returncode, stdout, stderr = run_precommit(continuation_vault)

        assert returncode == 0, f"{stdout}\n{stderr}"
        # `modified` is intentionally refreshed by the hook; the body is not.
        assert path.read_text().split("---", 2)[2] == before.split("---", 2)[2]


class TestExtractSection:
    """Section extraction underpins the context copy."""

    def test_returns_body(self):
        content = "# T\n\n## Goal\n\nDo the thing.\n\n## Scope\n\nOther\n"
        assert extract_section(content, "Goal") == "Do the thing."

    def test_missing_section(self):
        assert extract_section("# T\n\n## Scope\n\nx\n", "Goal") is None

    def test_empty_section(self):
        assert extract_section("# T\n\n## Goal\n\n## Scope\n\nx\n", "Goal") is None

    def test_placeholder_comment_counts_as_empty(self):
        """The stock templates ship comment-only sections; don't copy those."""
        content = "# T\n\n## Goal\n<!-- One-sentence deliverable -->\n\n## Scope\n"
        assert extract_section(content, "Goal") is None

    def test_stops_at_next_heading(self):
        content = "# T\n\n## Goal\n\nline one\n\n### Sub\n\nkept\n\n## Scope\n\nnope\n"
        section = extract_section(content, "Goal")
        assert "kept" in section
        assert "nope" not in section


class TestListCoercion:
    """Relation fields are hand-editable, so scalars must be tolerated."""

    def test_scalar_continues_is_coerced(self, temp_vault):
        write_project(temp_vault, "pred")
        write_project(temp_vault, "succ", continues="pred")

        note = NoteMetadata.from_file(temp_vault / "succ.md")

        assert note.continues == ["pred"]

    def test_scalar_does_not_substring_match(self, temp_vault):
        """Without coercion, "pre" in "pred" would be a false positive."""
        write_project(temp_vault, "pred")
        write_project(temp_vault, "succ", continues="pred")

        note = NoteMetadata.from_file(temp_vault / "succ.md")

        assert "pre" not in note.continues


class TestFuzzyConfidenceNonInteractive:
    """A weak fuzzy match must never be acted on without a human present.

    Regression: `resolve_file_fuzzy` returned `matches[0]` regardless of score
    whenever stdin was not a TTY (scripts, git hooks, the nvim plugin). With
    partial_ratio and score_cutoff=50, a short query scores ~50 against almost
    anything, so `cor rename old-a renamed-a` silently renamed an unrelated
    `via-flag` project and reported success.
    """

    @pytest.fixture
    def mixed_vault(self, runner, temp_vault):
        for stem in ("new-thing", "side-quest", "via-flag"):
            write_project(temp_vault, stem)
        archive_project(temp_vault, "old-a")
        return temp_vault

    def test_weak_match_does_not_rename_wrong_file(self, runner, mixed_vault):
        """'old-a' scores ~50 against 'via-flag'; that must not be acted on."""
        result = runner.invoke(cli, ["rename", "old-a", "renamed-a"])

        assert result.exit_code != 0
        assert "No confident match" in result.output
        # The unrelated project is untouched, and nothing was created.
        assert (mixed_vault / "via-flag.md").exists()
        assert not (mixed_vault / "renamed-a.md").exists()

    def test_error_lists_candidates(self, runner, mixed_vault):
        result = runner.invoke(cli, ["rename", "old-a", "renamed-a"])

        assert "Candidates:" in result.output
        assert "%" in result.output

    def test_exact_name_still_resolves(self, runner, mixed_vault):
        """The exact-match fast path is unaffected."""
        result = runner.invoke(cli, ["rename", "via-flag", "via-flag-v2"])

        assert result.exit_code == 0, result.output
        assert (mixed_vault / "via-flag-v2.md").exists()

    def test_exact_archived_name_resolves_with_flag(self, runner, mixed_vault):
        result = runner.invoke(cli, ["rename", "-a", "old-a", "renamed-a"])

        assert result.exit_code == 0, result.output
        assert (mixed_vault / "archive" / "renamed-a.md").exists()

    def test_strong_partial_match_still_works(self, runner, mixed_vault):
        """A high-confidence prefix must still auto-resolve non-interactively."""
        result = runner.invoke(cli, ["mark", "side-ques", "active"])

        assert result.exit_code == 0, result.output
        assert fm(mixed_vault / "side-quest.md")["status"] == "active"


class TestFrontmatterNullNoise:
    """Unset optional fields stay blank instead of becoming literal `null`.

    Regression: the stock templates ship empty `priority:` / `due:` keys as a
    reminder that the fields exist. YAML parses those as None and the default
    representer wrote them back as `null`, so every project file accumulated
    `priority: null` / `due: null` the first time any command touched it.
    """

    def test_empty_fields_survive_a_rewrite(self, runner, temp_vault):
        runner.invoke(cli, ["new", "project", "fresh", "--no-edit"])
        runner.invoke(cli, ["tag", "fresh", "demo"])

        body = (temp_vault / "fresh.md").read_text()
        assert "null" not in body
        assert "priority:" in body

    def test_real_values_still_written(self, runner, temp_vault):
        runner.invoke(cli, ["new", "project", "fresh", "--no-edit"])
        runner.invoke(cli, ["due", "fresh", "friday"])

        meta = fm(temp_vault / "fresh.md")
        assert meta["due"] is not None
        assert meta["priority"] is None  # blank still round-trips to None

    def test_none_roundtrips(self):
        """A blank value must still parse back as None, not empty string."""
        import frontmatter

        post = frontmatter.loads("---\npriority:\n---\nbody\n")
        reparsed = frontmatter.loads(frontmatter.dumps(post))

        assert reparsed["priority"] is None


class TestPredecessorCompletion:
    """`-c` must complete archived projects.

    Regression: the flag used complete_existing_name, which only includes
    archived files when ctx.params has `archived` set -- a flag `cor new` does
    not define. A project you are continuing has by definition been closed and
    archived, so the completion never offered the one thing it was for.
    """

    class _Ctx:
        params: dict = {}

    @pytest.fixture
    def versioned_vault(self, temp_vault):
        write_project(temp_vault, "proj_v3")            # active
        archive_project(temp_vault, "proj_v1")
        archive_project(temp_vault, "proj_v2")
        write_project(temp_vault, "unrelated")
        return temp_vault

    def _values(self, items):
        return sorted(getattr(c, "value", c) for c in items)

    def test_offers_archived_projects(self, versioned_vault):
        from cor.completions import complete_predecessor_project

        items = complete_predecessor_project(self._Ctx(), None, "proj")

        assert self._values(items) == ["proj_v1", "proj_v2", "proj_v3"]

    def test_old_completion_missed_them(self, versioned_vault):
        """Documents why the dedicated completion exists."""
        from cor.completions import complete_existing_name

        items = complete_existing_name(self._Ctx(), None, "proj")

        assert self._values(items) == ["proj_v3"]

    def test_archived_entries_are_labelled(self, versioned_vault):
        from cor.completions import complete_predecessor_project

        items = complete_predecessor_project(self._Ctx(), None, "proj")
        helps = {getattr(c, "value", c): getattr(c, "help", "") for c in items}

        assert "Archived" in helps["proj_v1"]
        assert helps["proj_v3"] == "Project"

    def test_excludes_tasks_and_notes(self, runner, versioned_vault):
        from cor.completions import complete_predecessor_project

        runner.invoke(cli, ["new", "task", "proj_v3.sub", "--no-edit"])

        items = complete_predecessor_project(self._Ctx(), None, "proj")

        assert "proj_v3.sub" not in self._values(items)

    def test_relation_target_narrows_for_continues(self, runner, versioned_vault):
        """`cor rel add X --as continues <TAB>` offers projects only."""
        from cor.completions import complete_relation_target

        runner.invoke(cli, ["new", "task", "proj_v3.sub", "--no-edit"])

        ctx = self._Ctx()
        ctx.params = {"field": "continues"}
        assert "proj_v3.sub" not in self._values(
            complete_relation_target(ctx, None, "proj")
        )

    def test_relation_target_includes_archive_for_related(self, runner, versioned_vault):
        """Default (`related`) offers any note, archived included."""
        from cor.completions import complete_relation_target

        ctx = self._Ctx()
        ctx.params = {"field": "related"}
        values = self._values(complete_relation_target(ctx, None, "proj"))

        assert "proj_v1" in values      # archived
        assert "proj_v3" in values


class TestCompletionSelfExclusion:
    """Already-referenced notes drop out of the completion list.

    `ctx.params` is empty while completing a dangling option value
    (`cor new project foo -c <TAB>`) because click aborts parsing there, so the
    subject has to be recovered from the leftover tokens in `ctx.args`.
    """

    @pytest.fixture
    def versioned_vault(self, temp_vault):
        write_project(temp_vault, "proj_v3")
        archive_project(temp_vault, "proj_v1")
        archive_project(temp_vault, "proj_v2")
        return temp_vault

    def _complete(self, args, incomplete=""):
        from click.shell_completion import ShellComplete

        sc = ShellComplete(cli, {}, "cor", "_COR_COMPLETE")
        return sorted(c.value for c in sc.get_completions(args, incomplete))

    def test_new_project_does_not_offer_itself(self, versioned_vault):
        """`-c` on an existing project name must not offer that project."""
        offered = self._complete(["new", "project", "proj_v3", "-c"])

        assert "proj_v3" not in offered
        assert "proj_v1" in offered

    def test_unknown_new_name_excludes_nothing(self, versioned_vault):
        offered = self._complete(["new", "project", "brand_new", "-c"])

        assert {"proj_v1", "proj_v2", "proj_v3"}.issubset(set(offered))

    def test_repeated_flag_drops_earlier_choice(self, versioned_vault):
        """A second -c must not re-offer what the first one already took."""
        offered = self._complete(
            ["new", "project", "brand_new", "-c", "proj_v1", "-c"]
        )

        assert "proj_v1" not in offered
        assert "proj_v2" in offered

    def test_rel_add_excludes_subject(self, versioned_vault):
        offered = self._complete(
            ["rel", "add", "proj_v3", "--as", "continues"]
        )

        assert "proj_v3" not in offered
        assert "proj_v1" in offered

    def test_rel_add_excludes_prior_targets(self, versioned_vault):
        offered = self._complete(
            ["rel", "add", "proj_v3", "--as", "continues", "proj_v1"]
        )

        assert offered == ["proj_v2"]

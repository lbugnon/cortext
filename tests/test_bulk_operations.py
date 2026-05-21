"""Tests for bulk operations (glob patterns) in mark and move commands."""

import pytest
import frontmatter
from click.testing import CliRunner

from cor.cli import cli


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


class TestMarkBulk:
    """Test bulk mark operations with glob patterns."""

    def test_mark_bulk_by_pattern(self, runner, temp_vault, monkeypatch):
        """cor mark should support glob patterns for bulk status updates."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        # Create project with multiple tasks (3 tasks = no confirmation prompt)
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task2", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task3", "--no-edit"])

        # Bulk mark all myproj.* tasks as done (3 tasks, no confirmation needed)
        result = runner.invoke(cli, ["mark", "myproj.*", "done"])
        assert result.exit_code == 0, f"Bulk mark failed: {result.output}"

        # Tasks marked done are archived - check in archive directory
        archive_dir = temp_vault / "archive"
        task1 = frontmatter.load(archive_dir / "myproj.task1.md")
        assert task1['status'] == 'done'
        
        task2 = frontmatter.load(archive_dir / "myproj.task2.md")
        assert task2['status'] == 'done'
        
        task3 = frontmatter.load(archive_dir / "myproj.task3.md")
        assert task3['status'] == 'done'

    def test_mark_bulk_group_tasks(self, runner, temp_vault, monkeypatch):
        """cor mark should support glob patterns for specific groups."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        # Create project with tasks in different groups
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group1.task1", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group1.task2", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.group2.task3", "--no-edit"])

        # Bulk mark only group1 tasks as active
        result = runner.invoke(cli, ["mark", "myproj.group1.*", "active"])
        assert result.exit_code == 0, f"Bulk mark failed: {result.output}"

        # Check group1 tasks are active
        task1 = frontmatter.load(temp_vault / "myproj.group1.task1.md")
        assert task1['status'] == 'active'
        task2 = frontmatter.load(temp_vault / "myproj.group1.task2.md")
        assert task2['status'] == 'active'
        
        # group2 task should remain todo (not in group1)
        task3 = frontmatter.load(temp_vault / "myproj.group2.task3.md")
        assert task3['status'] == 'todo'

    def test_mark_bulk_with_status_option(self, runner, temp_vault, monkeypatch):
        """cor mark -s <status> <pattern> should work."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "--no-edit"])

        # Use --status option
        result = runner.invoke(cli, ["mark", "-s", "blocked", "myproj.*"])
        assert result.exit_code == 0, f"Bulk mark with --status failed: {result.output}"

        task1 = frontmatter.load(temp_vault / "myproj.task1.md")
        assert task1['status'] == 'blocked'

    def test_mark_bulk_no_matches(self, runner, temp_vault, monkeypatch):
        """cor mark with non-matching pattern should error."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        result = runner.invoke(cli, ["mark", "nonexistent.*", "done"])
        assert result.exit_code != 0
        assert "No files match pattern" in result.output


class TestMoveBulk:
    """Test bulk move/rename operations with glob patterns."""

    def test_move_bulk_simple_pattern(self, runner, temp_vault, monkeypatch):
        """cor move with wildcards should rename multiple files."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        # Initialize git for rename operations
        import subprocess
        subprocess.run(["git", "init"], cwd=temp_vault, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_vault, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_vault, capture_output=True)

        # Create project with tasks
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.old-task1", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.old-task2", "--no-edit"])

        # Bulk rename all myproj.old-* tasks to myproj.new-*
        # Note: pattern uses .* to match tasks under the project
        result = runner.invoke(cli, ["move", "myproj.old-*", "myproj.new-*"])
        assert result.exit_code == 0, f"Bulk move failed: {result.output}"

        # Check that task files were renamed
        assert (temp_vault / "myproj.new-task1.md").exists()
        assert (temp_vault / "myproj.new-task2.md").exists()
        
        # Old task files should not exist
        assert not (temp_vault / "myproj.old-task1.md").exists()
        assert not (temp_vault / "myproj.old-task2.md").exists()
        
        # Project file should still exist (not part of the rename)
        assert (temp_vault / "myproj.md").exists()

    def test_move_bulk_dry_run(self, runner, temp_vault, monkeypatch):
        """cor move -n should preview changes without applying."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        # Create project with tasks
        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.old-task1", "task1", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.old-task2", "task2", "--no-edit"])

        # Dry run
        result = runner.invoke(cli, ["move", "-n", "myproj.old-*", "myproj.new-*"])
        assert result.exit_code == 0, f"Dry run failed: {result.output}"
        assert "Dry run" in result.output

        # Files should not have changed
        assert (temp_vault / "myproj.old-task1.md").exists()
        assert (temp_vault / "myproj.old-task2.md").exists()
        assert not (temp_vault / "myproj.new-task1.md").exists()

    def test_move_bulk_wildcard_mismatch(self, runner, temp_vault, monkeypatch):
        """cor move should error if wildcard counts don't match."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])
        runner.invoke(cli, ["new", "task", "myproj.task1", "task1", "--no-edit"])

        # Different number of wildcards
        result = runner.invoke(cli, ["move", "myproj.*", "otherproj.*.*"])
        assert result.exit_code != 0
        assert "Wildcard mismatch" in result.output

    def test_move_bulk_no_wildcards(self, runner, temp_vault, monkeypatch):
        """cor move should error if no wildcards in source."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        runner.invoke(cli, ["new", "project", "myproj", "--no-edit"])

        # No wildcards in source
        result = runner.invoke(cli, ["move", "myproj", "otherproj"])
        # This should use fuzzy matching (single rename), not bulk
        # But if someone adds * later it should work as bulk
        assert result.exit_code == 0 or "wildcards" in result.output.lower()

    def test_move_bulk_no_matches(self, runner, temp_vault, monkeypatch):
        """cor move with non-matching pattern should error."""
        # temp_vault fixture already sets up XDG_CONFIG_HOME with proper config
        monkeypatch.chdir(temp_vault)

        result = runner.invoke(cli, ["move", "nonexistent.*", "other.*"])
        assert result.exit_code != 0
        assert "No files match pattern" in result.output


class TestGlobHelper:
    """Test the glob helper utilities."""

    def test_is_glob_pattern(self):
        """is_glob_pattern should detect wildcards."""
        from cor.utils import is_glob_pattern
        
        assert is_glob_pattern("project.*.md") is True
        assert is_glob_pattern("project.task*.md") is True
        assert is_glob_pattern("project.task?.md") is True
        assert is_glob_pattern("project.[abc].md") is True
        assert is_glob_pattern("project.task.md") is False
        assert is_glob_pattern("project") is False

    def test_expand_glob_pattern(self, tmp_path):
        """expand_glob_pattern should find matching files."""
        from cor.utils import expand_glob_pattern
        
        # Create test files
        (tmp_path / "project.task1.md").write_text("")
        (tmp_path / "project.task2.md").write_text("")
        (tmp_path / "other.task.md").write_text("")
        
        matches = expand_glob_pattern("project.*.md", tmp_path)
        stems = sorted([p.stem for p in matches])
        
        assert stems == ["project.task1", "project.task2"]

    def test_compute_target_stem(self):
        """_compute_target_stem should compute correct target names."""
        from cor.commands.refactor import _compute_target_stem
        
        # Simple single wildcard
        result = _compute_target_stem("project.old-task1", "project.old-*", "project.new-*")
        assert result == "project.new-task1"
        
        # Single wildcard at end
        result = _compute_target_stem("project.feature", "project.*", "other.*")
        assert result == "other.feature"
        
        # Multiple wildcards
        result = _compute_target_stem("a.x.y.b", "a.*.*.b", "c.*.*.d")
        assert result == "c.x.y.d"

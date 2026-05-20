"""Initialization commands for Cor CLI."""

import subprocess
import stat
import shutil
from datetime import datetime
from pathlib import Path

import click

from . import cli, _install_pre_commit_hook, _install_shell_completion
from ..config import set_vault_path, _config_file, set_remote_inbox
from ..schema import DATE_TIME
from ..utils import get_notes_dir, get_templates_dir, log_info, log_verbose
import os


def _install_nvim_plugin(yes: bool = False):
    """Check for LazyVim and offer to install the nvim plugin."""
    lazyvim_plugins_dir = Path.home() / ".config" / "nvim" / "lua" / "plugins"
    if not lazyvim_plugins_dir.exists():
        return

    dest = lazyvim_plugins_dir / "cortex.lua"
    already_installed = dest.exists()

    log_info("LazyVim detected.")
    prompt = "Update nvim plugin?" if already_installed else "Install nvim plugin for link insertion and backlinks?"
    if not yes:
        if not click.confirm(prompt, default=True):
            return

    src = Path(__file__).parent.parent / "assets" / "cortex.lua"
    dest.write_text(src.read_text())
    log_info(f"Nvim plugin installed: {dest}")

    if not shutil.which("fd"):
        click.echo("  Warning: 'fd' not found. Install it for the plugin to work (e.g. sudo pacman -S fd).")


def _setup_calendar():
    """Set up Google Calendar integration during init."""
    from ..commands.calendar import _get_credentials
    
    click.echo()
    click.echo(click.style("Google Calendar Setup", bold=True))
    click.echo("This will sync task due dates to Google Calendar.")
    
    # Check if already authenticated
    if _get_credentials():
        click.echo(click.style("✓ Already authenticated", fg="green"))
        return
    
    click.echo()
    click.echo("You'll need to:")
    click.echo("  1. Authenticate with your Google account")
    click.echo("  2. Grant access to manage your calendars")
    click.echo()
    
    if not click.confirm("Continue with Google Calendar setup?", default=True):
        click.echo("Skipped. Run 'cor calendar auth' later to set up.")
        return
    
    try:
        from ..commands.calendar import auth as calendar_auth
        # Run the auth command
        ctx = click.get_current_context()
        ctx.invoke(calendar_auth)
    except Exception as e:
        click.echo(click.style(f"Setup failed: {e}", fg="yellow"))
        click.echo("You can set up later with: cor calendar auth")


def _setup_telegram():
    """Set up Telegram inbox integration during init."""
    from ..config import get_remote_inbox
    
    click.echo()
    click.echo(click.style("Telegram Inbox Setup", bold=True))
    click.echo("This allows you to capture notes via Telegram messages.")
    
    # Check if already configured
    if get_remote_inbox():
        click.echo(click.style("✓ Already configured", fg="green"))
        return
    
    click.echo()
    click.echo("To set up:")
    click.echo("  1. Message @BotFather on Telegram to create a bot")
    click.echo("  2. Copy the bot token provided")
    click.echo()
    
    if not click.confirm("Do you have a bot token?", default=True):
        click.echo("Skipped. Run 'cor config inbox <token>' later to set up.")
        return
    
    bot_token = click.prompt("Enter your bot token", hide_input=True)
    bot_token = bot_token.strip()
    
    if not bot_token:
        click.echo("No token provided. Skipped.")
        return
    
    # Test the token and save
    try:
        from ..commands.inbox import test_telegram_connection
        test_telegram_connection(bot_token)
        set_remote_inbox(bot_token)
        click.echo()
        click.echo(click.style("✓ Telegram inbox configured", fg="green"))
        click.echo("Messages sent to your bot will be pulled during 'cor sync'")
    except Exception as e:
        click.echo(click.style(f"Failed to configure: {e}", fg="yellow"))
        click.echo("You can set up later with: cor config inbox <token>")


@cli.command()
@click.pass_context
@click.option("yes", "--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompts")
@click.option("--with-calendar", is_flag=True, default=False, help="Set up Google Calendar integration")
@click.option("--with-telegram", is_flag=True, default=False, help="Set up Telegram inbox integration")
def init(ctx, yes: bool, with_calendar: bool, with_telegram: bool):
    """Initialize a new Cor vault.

    Creates the vault structure (notes/, templates/, backlog.md).
    Initializes git repository if not already present.
    Installs git hooks and configures shell completion automatically.
    Sets this directory as your vault path in the global config.

    Use --with-calendar to set up Google Calendar sync for due dates.
    Use --with-telegram to set up Telegram inbox for capturing notes.
    """
    # Ask for confirmation to set this directory as vault
    vault_path = Path.cwd()
    log_info(f"Initializing Cor vault in: {vault_path}")
    if not yes:
        if not click.confirm("Continue?", default=True):
            click.echo("Aborted.")
            return
    set_vault_path(vault_path)
    
    # Ensure config file has secure permissions
    cfg_file = _config_file()
    if cfg_file.exists():
        os.chmod(cfg_file, 0o600)
    
    # Get directories AFTER setting vault path
    notes_dir = get_notes_dir()
    templates_dir = get_templates_dir()

    # Create directories
    notes_dir.mkdir(exist_ok=True)
    templates_dir.mkdir(exist_ok=True)

    # Create backlog.md
    backlog_path = notes_dir / "backlog.md"
    if not backlog_path.exists():
        backlog_template = (Path(__file__).parent.parent / "assets" / "backlog.md").read_text()
        now = datetime.now().strftime(DATE_TIME)
        backlog_path.write_text(backlog_template.format(date=now))
        log_verbose(f"Created {backlog_path}")

    # Create default templates
    assets_dir = Path(__file__).parent.parent / "assets"
    for filename in ["project.md", "task.md", "note.md"]:
        path = templates_dir / filename
        if not path.exists():
            content = (assets_dir / filename).read_text()
            path.write_text(content)
            log_verbose(f"Created {path}")

    log_info("Cor vault initialized.")

    # Optional: Google Calendar integration
    if with_calendar or (not yes and click.confirm("Set up Google Calendar integration?", default=False)):
        _setup_calendar()

    # Optional: Telegram inbox integration
    if with_telegram or (not yes and click.confirm("Set up Telegram inbox integration?", default=False)):
        _setup_telegram()

    # Create default .gitignore if not exists
    gitignore_path = vault_path / ".gitignore"
    if not gitignore_path.exists():
        gitignore_content = """# Cor - Ignore temporal files
# Files starting with . or # are considered temporal/backup files
.*
#*
"""
        gitignore_path.write_text(gitignore_content)
        log_verbose(f"Created {gitignore_path}")

    # Check if git repository exists, create if it doesn't
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        cwd=vault_path,
    )
    if result.returncode != 0:
        # Initialize git repository
        log_verbose("Initializing git repository...")
        subprocess.run(["git", "init"], cwd=vault_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Cor"], cwd=vault_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "cor@local"], cwd=vault_path, capture_output=True)
        log_info("Git repository initialized.")
    
    # Install git hooks
    _install_pre_commit_hook()
    _install_shell_completion()

    # Offer LazyVim plugin installation
    _install_nvim_plugin(yes)


@cli.command()
@click.pass_context
def example_vault(ctx):
    """Create a comprehensive example vault to demonstrate Cor features.
    
    This command will create a sample vault with:
    - Multiple projects with different statuses
    - Tasks with various statuses (todo, active, done, blocked, waiting, dropped)
    - Task groups (hierarchical organization)
    - Notes under projects
    - Standalone notes
    - Academic references (papers from CrossRef)
    
    Perfect for exploring Cor capabilities and learning the workflow.
    """
    notes_dir = get_notes_dir()
    
    # Check if vault is initialized
    if not (notes_dir / "backlog.md").exists():
        if click.confirm("Vault not initialized. Initialize now?", default=True):
            ctx.invoke(init, yes=True)
            # Re-fetch notes_dir after init sets the vault path
            notes_dir = get_notes_dir()
        else:
            click.echo("Aborted.")
            return

    # Check if vault has content
    existing_files = list(notes_dir.glob("*.md"))
    if len(existing_files) > 1:  # More than backlog.md
        click.echo(f"Warning: Vault already contains {len(existing_files)} files.")
        if not click.confirm("Continue and add example content?", default=False):
            click.echo("Aborted.")
            return
    
    log_info("Creating example vault...")
    
    # Import subprocess to run cor commands
    def run_cor(*args):
        """Run a cor command."""
        result = subprocess.run(["cor", "-vv", *args], capture_output=True, text=True)
        if result.returncode != 0:
            click.echo(f"Error running: cor {' '.join(args)}", err=True)
            click.echo(result.stderr, err=True)
            return False
        return True
    
    ## ===== PROJECT 1: Foundation Model (active) =====
    run_cor("new", "project", "foundation_model", "--no-edit")
    # Create tasks in different statuses
    run_cor("new", "task", "foundation_model.dataset_curation", "-t", "Curate multi-domain corpus with strict filtering", "--no-edit")
    run_cor("new", "task", "foundation_model.training_pipeline", "-t", "Stand up distributed training stack", "--no-edit")
    run_cor("new", "task", "foundation_model.eval_harness", "-t", "Wire up eval harness for benchmarks", "--no-edit")
    run_cor("new", "task", "foundation_model.ablation_suite", "-t", "Design ablation study matrix", "--no-edit")
    
    # Mark tasks with different statuses
    run_cor("mark", "foundation_model.dataset_curation", "blocked")
    run_cor("mark", "foundation_model.training_pipeline", "active")
    run_cor("mark", "foundation_model.eval_harness", "waiting")
    run_cor("mark", "foundation_model.ablation_suite", "todo")
    
    # Create a task group for experiments
    run_cor("new", "task", "foundation_model.experiments.lr_sweep", "-t", "Run LR sweep across batch sizes", "--no-edit")
    run_cor("new", "task", "foundation_model.experiments.clip_tuning", "-t", "Tune gradient clipping thresholds", "--no-edit")
    run_cor("new", "task", "foundation_model.experiments.checkpoint_policy", "-t", "Test checkpoint cadence impact", "--no-edit")
    
    run_cor("mark", "foundation_model.experiments.lr_sweep", "done")
    run_cor("mark", "foundation_model.experiments.clip_tuning", "active")
    run_cor("mark", "foundation_model.experiments.checkpoint_policy", "todo")

    # Create another task group for data
    run_cor("new", "task", "foundation_model.data.tokenizer_refresh", "-t", "Re-train tokenizer with new domains", "--no-edit")
    run_cor("new", "task", "foundation_model.data.safety_filter", "-t", "Iterate on safety filtering rules", "--no-edit")
    
    run_cor("mark", "foundation_model.data.tokenizer_refresh", "active")
    run_cor("mark", "foundation_model.data.safety_filter", "todo")
    
    # Create notes under project
    run_cor("new", "note", "foundation_model.lab_notes", "-t", "Daily lab notebook entries", "--no-edit")
    run_cor("new", "note", "foundation_model.decisions", "-t", "Key modeling decisions and rationale", "--no-edit")
    
    # ===== PROJECT 2: Evaluation Suite (planning) =====
    run_cor("new", "project", "evaluation_suite", "--no-edit")
    
    run_cor("new", "task", "evaluation_suite.benchmark_catalog", "-t", "Select core academic and industry benchmarks", "--no-edit")
    run_cor("new", "task", "evaluation_suite.metric_defs", "-t", "Define metrics for safety and quality", "--no-edit")
    run_cor("new", "task", "evaluation_suite.reporting", "-t", "Automate eval report generation", "--no-edit")
    
    run_cor("mark", "evaluation_suite.benchmark_catalog", "todo")
    run_cor("mark", "evaluation_suite.metric_defs", "todo")
    run_cor("mark", "evaluation_suite.reporting", "todo")
    
    # ===== PROJECT 3: Paper Draft (paused) =====
    run_cor("new", "project", "paper", "--no-edit")
    
    run_cor("new", "task", "paper.related_work", "-t", "Summarize adjacent scaling papers", "--no-edit")
    run_cor("new", "task", "paper.method", "-t", "Write method section draft", "--no-edit")
    run_cor("new", "task", "paper.experiments", "-t", "Select figures for results", "--no-edit")
    
    run_cor("mark", "paper.related_work", "done")
    run_cor("mark", "paper.method", "active")
    run_cor("mark", "paper.experiments", "dropped")
    
    # ===== PROJECT 4: Baking (planning) =====
    run_cor("new", "project", "baking", "--no-edit")

    run_cor(
        "new",
        "task",
        "baking.test_new_flour",
        "-t",
        "Try high-protein flour against baseline",
        "--no-edit",
    )
    run_cor(
        "new",
        "task",
        "baking.new_recipe_from_link",
        "-t",
        "Review and plan bake from bookmarked recipe",
        "--no-edit",
    )
    run_cor(
        "new",
        "note",
        "baking.recipe_notebook",
        "-t",
        "Panettone formula notes from shared link",
        "--no-edit",
    )

    run_cor("mark", "baking.test_new_flour", "todo")
    run_cor("mark", "baking.new_recipe_from_link", "waiting")
    
    # ===== STANDALONE NOTES =====
    run_cor("new", "note", "random-ideas", "-t", "Brainstorm ideas for future projects", "--no-edit")
    run_cor("new", "note", "learning-log", "-t", "Track learning progress", "--no-edit")
    
    # ===== REFERENCES =====
    log_info("Adding reference examples...")
    # Add influential papers related to the foundation model project
    run_cor("ref", "add", "10.48550/arXiv.1706.03762", "--key", "vaswani2017attention", "--no-edit")  # Attention Is All You Need
    run_cor("ref", "add", "10.48550/arXiv.1810.04805", "--key", "devlin2018bert", "--no-edit")  # BERT
    run_cor("ref", "add", "10.48550/arXiv.2005.14165", "--key", "brown2020gpt3", "--no-edit")  # GPT-3
    run_cor("ref", "add", "10.48550/arXiv.2203.02155", "--no-edit")  # InstructGPT, key is optional
    
    # ===== RENAME A PROJECT =====
    run_cor("rename", "evaluation_suite", "eval-suite")
    
    click.echo("\n" + "="*60)
    click.echo("✓ Example vault created successfully!")
    click.echo("="*60)
    click.echo("\nTry these commands to explore:")
    click.echo("  cor daily           # See what needs attention")
    click.echo("  cor projects        # Overview of all projects")
    click.echo("  cor tree            # Hierarchical view")
    click.echo("  cor weekly          # Summarize recent work")
    click.echo("\nEdit files with:")
    click.echo("  cor edit foundation_model")
    click.echo("  cor edit foundation_model.training_pipeline")
    click.echo("\nExplore references:")
    click.echo("  cor ref list        # View all references")
    click.echo("  cor ref show vaswani2017attention")
    click.echo("  [to be implemented] cor ref search transformer")



"""Interactive project tree browser using Textual."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.suggester import Suggester
from textual.widgets import Input, Static, Tree

from ..schema import STATUS_SYMBOLS
from .colors import STATUS_STYLES, STATUS_KEY_BINDINGS

# key -> (status, symbol, color), derived so symbols stay sourced from schema.
STATUS_KEYS: dict[str, tuple[str, str, str]] = {
    key: (status, STATUS_SYMBOLS[status], STATUS_STYLES[status][1])
    for key, status in STATUS_KEY_BINDINGS.items()
}

_HELP = "x/o/./~/⌫:status  X/O:+note  n/N:new  m:move  d:delete  e:edit  j/k:nav  q:quit"


class NotesSuggester(Suggester):
    """Inline ghost-text completer for note stems, backed by complete_new_parent."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=False)

    async def get_suggestion(self, value: str) -> str | None:
        from ..completions import complete_new_parent
        results = complete_new_parent(None, None, value)
        return results[0].value if results else None


class TaskNode:
    """Data class for task tree nodes."""

    def __init__(self, stem: str, title: str, status: str | None, note_type: str, file_path: Path):
        self.stem = stem
        self.title = title
        self.status = status or "todo"
        self.note_type = note_type
        self.file_path = file_path

    def get_rich_label(self) -> Text:
        symbol, color = STATUS_STYLES.get(self.status, ("[ ]", "white"))
        text = Text()
        text.append(symbol, style=color)
        text.append(f" {self.title}")
        return text

    @property
    def is_task(self) -> bool:
        return self.note_type == "task"


class ProjectTreeApp(App):
    """Interactive project tree browser."""

    CSS = """
    Screen {
        background: $background;
    }

    #header {
        height: auto;
        padding: 1 2 0 2;
    }

    #main-container {
        width: 100%;
        height: 1fr;
    }

    #tree-container {
        width: 60%;
        height: 100%;
    }

    #preview-pane {
        width: 40%;
        height: 100%;
        border-left: solid $surface-darken-1;
        background: $surface-darken-1;
        padding: 0 1;
        overflow: auto;
    }

    #preview-pane:focus {
        border-left: solid $primary;
    }

    #preview-content {
        width: 100%;
        height: auto;
        padding: 1;
    }

    Tree {
        background: $background;
        border: none;
        padding: 0 1;
        scrollbar-size: 1 1;
        scrollbar-background: $background;
        scrollbar-color: $surface;
    }

    Tree > .tree--guides {
        color: $text-disabled;
    }

    Tree > .tree--guides-hover {
        color: $text-muted;
    }

    Tree > .tree--guides-selected {
        color: $text-muted;
    }

    Tree > .tree--cursor {
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }

    Tree > .tree--highlight {
        background: $surface-darken-1;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        padding: 0 2;
        background: $surface;
        color: $text-muted;
    }

    #prompt-input {
        dock: bottom;
        height: 1;
        background: $surface;
        border: none;
        padding: 0 2;
        color: $text;
        display: none;
    }

    #prompt-input:focus {
        border: none;
    }
    """

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("j", "cursor_down", "↓"),
        ("k", "cursor_up", "↑"),
        ("x", "mark_done", "done"),
        ("o", "mark_blocked", "blocked"),
        (".", "mark_active", "active"),
        ("/", "mark_waiting", "waiting"),
        ("~", "mark_dropped", "dropped"),
        ("backspace", "mark_todo", "todo"),
        ("X", "mark_done_with_note", "done+note"),
        ("O", "mark_blocked_with_note", "blocked+note"),
        ("n", "new_sibling", "new"),
        ("N", "new_subtask", "new child"),
        ("m", "move_task", "move"),
        ("d", "delete_task", "delete"),
        ("e", "edit_task", "edit"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, focus: str, notes_dir: Path, verbose: bool = False, sort: str = "status"):
        self.focus = focus
        self.notes_dir = notes_dir
        self.verbose = verbose
        self.sort = sort
        self.focus_note = None
        self.all_notes: list = []
        self.task_nodes: dict[str, TaskNode] = {}
        self._input_callback: Callable[[str], None] | None = None
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        with Horizontal(id="main-container"):
            with Horizontal(id="tree-container"):
                tree = Tree(self.focus)
                tree.show_root = False
                yield tree
            with Static(id="preview-pane"):
                yield Static("", id="preview-content")
        yield Static(_HELP, id="status-bar")
        yield Input(id="prompt-input", suggester=NotesSuggester())

    def on_mount(self) -> None:
        self._load_data()
        self._populate_tree()
        self.query_one("#header", Static).update(self._header_text())

    def _header_text(self) -> Text:
        text = Text()
        if self.focus_note:
            text.append(self.focus_note.title, style="bold")
            text.append(f"\n({self.focus_note.status or 'no status'})", style="dim")
        else:
            text.append(self.focus, style="bold")
        return text

    def _load_data(self) -> None:
        from ..core.notes import find_notes

        self.all_notes = find_notes(self.notes_dir)
        archive_dir = self.notes_dir / "archive"
        if archive_dir.exists():
            self.all_notes.extend(find_notes(archive_dir))

        self.focus_note = None
        for note in self.all_notes:
            if note.path.stem == self.focus:
                self.focus_note = note
                break

        if not self.focus_note:
            for path in [
                self.notes_dir / f"{self.focus}.md",
                self.notes_dir / "archive" / f"{self.focus}.md",
            ]:
                if path.exists():
                    from ..core.notes import Note
                    try:
                        self.focus_note = Note.from_file(path)
                        self.all_notes.append(self.focus_note)
                        break
                    except Exception:
                        pass

    def _populate_tree(self, preserve_cursor_stem: str | None = None) -> None:
        tree = self.query_one(Tree)
        for child in list(tree.root.children):
            child.remove()

        if not self.focus_note:
            tree.root.add(f"Not found: {self.focus}")
            return

        tasks_by_parent: dict[str, list] = {}
        is_project = self.focus_note.note_type == "project"

        for note in self.all_notes:
            if note.note_type != "task":
                continue
            parts = note.path.stem.split(".")
            if len(parts) < 2:
                continue
            if is_project:
                if parts[0] != self.focus:
                    continue
            else:
                if not note.path.stem.startswith(self.focus + "."):
                    continue
            parent = ".".join(parts[:-1])
            tasks_by_parent.setdefault(parent, []).append(note)

        def sort_tasks(tasks: list) -> list:
            if self.sort == "alphabetical":
                return sorted(tasks, key=lambda t: t.path.stem.lower())
            status_order = {"blocked": 0, "active": 1, "waiting": 2, "todo": 3, "dropped": 4, "done": 5}
            return sorted(tasks, key=lambda t: (status_order.get(t.status, 3), t.path.stem))

        self.task_nodes = {}
        for tasks in tasks_by_parent.values():
            for task in tasks:
                self.task_nodes[task.path.stem] = TaskNode(
                    stem=task.path.stem,
                    title=task.title,
                    status=task.status,
                    note_type=task.note_type,
                    file_path=task.path,
                )

        self._build_subtree(tree.root, self.focus, tasks_by_parent, sort_tasks)
        tree.root.expand()

        if preserve_cursor_stem:
            self._set_cursor_to_task(preserve_cursor_stem)

    def _build_subtree(self, parent_node, parent_stem, tasks_by_parent, sort_fn) -> None:
        if parent_stem not in tasks_by_parent:
            return

        tasks = sort_fn(tasks_by_parent[parent_stem])
        separator_transitions = [
            ({"blocked", "active", "waiting"}, {"todo"}),
            ({"todo"}, {"dropped", "done"}),
        ]
        prev_status: str | None = None

        for task in tasks:
            task_node = self.task_nodes.get(task.path.stem)
            if not task_node:
                continue

            if prev_status is not None:
                for from_set, to_set in separator_transitions:
                    if prev_status in from_set and task.status in to_set:
                        sep_node = parent_node.add(Text("─────", style="dim"))
                        sep_node.allow_expand = False
                        break

            has_children = task.path.stem in tasks_by_parent
            node = parent_node.add(task_node.get_rich_label(), data=task_node)

            if has_children:
                self._build_subtree(node, task.path.stem, tasks_by_parent, sort_fn)
                node.expand()
            else:
                node.allow_expand = False

            prev_status = task.status

    # ── Input prompt machinery ────────────────────────────────────────────────

    def _prompt_input(self, placeholder: str, callback: Callable[[str], None], prefill: str = "", cursor_position: int | None = None) -> None:
        """Show the inline input bar, call callback(text) on Enter."""
        self._input_callback = callback
        inp = self.query_one("#prompt-input", Input)
        inp.placeholder = placeholder
        inp.value = prefill
        self.query_one("#status-bar", Static).display = False
        inp.display = True
        inp.focus()
        # Set cursor position after focus (if specified)
        if cursor_position is not None:
            inp.cursor_position = cursor_position

    def _close_prompt(self) -> None:
        inp = self.query_one("#prompt-input", Input)
        inp.display = False
        self.query_one("#status-bar", Static).display = True
        self.query_one(Tree).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        callback = self._input_callback
        self._input_callback = None
        self._close_prompt()
        if callback:
            callback(text)

    def on_key(self, event) -> None:
        if event.key == "escape" and self._input_callback is not None:
            self._input_callback = None
            self._close_prompt()
            event.stop()

    # ── Status changes ────────────────────────────────────────────────────────

    def _change_status(self, new_status: str, task: TaskNode | None = None, note: str = "") -> None:
        if task is None:
            task = self._get_selected_task()
        if not task:
            self.notify("No task selected", severity="warning")
            return
        if not task.is_task:
            self.notify("Not a task", severity="warning")
            return

        # Determine where to move cursor after status change
        if new_status in ("done", "dropped"):
            # For terminal statuses, try next task first, then previous
            next_stem = self._get_next_task_stem()
            prev_stem = self._get_prev_task_stem() if not next_stem else None
            preserve = next_stem or prev_stem
        else:
            # For non-terminal statuses, stay on the same task
            preserve = task.stem

        try:
            if note:
                self._append_to_file(task.file_path, note)

            import subprocess
            result = subprocess.run(
                ["cor", "mark", task.stem, new_status],
                capture_output=True, text=True, cwd=self.notes_dir,
            )
            if result.returncode != 0:
                self.notify(f"Error: {result.stderr}", severity="error")
                return

            self._load_data()
            self._populate_tree(preserve_cursor_stem=preserve)
            self.query_one("#header", Static).update(self._header_text())

            symbol, _ = STATUS_STYLES.get(new_status, ("[ ]", "white"))
            # Escape markup characters in the symbol (e.g., [/] for waiting status)
            escaped_symbol = symbol.replace("[", r"\[")
            self.notify(f"{escaped_symbol} {new_status}", timeout=1.5)

        except Exception as e:
            self.notify(f"Error: {e}", severity="error")

    def _prompt_for_note(self, new_status: str) -> None:
        task = self._get_selected_task()
        if not task or not task.is_task:
            self.notify("No task selected", severity="warning")
            return
        symbol, _ = STATUS_STYLES.get(new_status, ("[ ]", "white"))
        self._prompt_input(
            placeholder=f"{symbol} {new_status} — note (enter / esc)",
            callback=lambda text: self._change_status(new_status, task=task, note=text),
        )

    def _append_to_file(self, file_path: Path, text: str) -> None:
        content = file_path.read_text().rstrip()
        file_path.write_text(content + f"\n\n{text}\n")

    # ── New task ──────────────────────────────────────────────────────────────

    def _new_task(self, prefix: str) -> None:
        """Prompt for a task name under prefix and create it.
        
        Supports natural language for due dates and tags:
        - "task1 due tomorrow"
        - "task1 due next friday tag urgent"
        - "task1 mark active"
        """
        def create(input_text: str) -> None:
            if not input_text or input_text == prefix:
                return
            
            # The input might be: "taskname this is description due tomorrow"
            # We need to extract the task name (first word) and pass the rest as text
            full = input_text if input_text.startswith(prefix) else prefix + input_text
            
            # Split to get task name and extra text
            # Task name is the first dot-separated segment after the prefix
            # Everything else is description text for NLP parsing
            parts = full.split()
            if not parts:
                return
            
            task_name = parts[0]  # e.g., "cor.group.taskname"
            extra_text = parts[1:]  # e.g., ["this", "is", "description", "due", "tomorrow"]
            
            import subprocess
            cmd = ["cor", "new", "task", task_name, "--no-edit"]
            if extra_text:
                cmd.extend(extra_text)
            
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, cwd=self.notes_dir,
            )
            if result.returncode != 0:
                self.notify(f"Error: {result.stderr}", severity="error")
                return
            self._load_data()
            self._populate_tree(preserve_cursor_stem=task_name)
            self.notify(f"Created: {task_name}", timeout=1.5)

        # Place cursor right after the prefix so user can type the task name
        self._prompt_input(
            placeholder=f"task name [due tomorrow] [tag urgent]...",
            callback=create,
            prefill=prefix,
            cursor_position=len(prefix)
        )

    # ── Move / reparent ───────────────────────────────────────────────────────

    def _run_cor(self, *args: str) -> str | None:
        """Run a cor subcommand. Returns stderr on failure, None on success."""
        import subprocess
        result = subprocess.run(
            ["cor", *args], capture_output=True, text=True, cwd=self.notes_dir,
        )
        return result.stderr if result.returncode != 0 else None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        task = event.node.data if isinstance(event.node.data, TaskNode) else None
        bar = self.query_one("#status-bar", Static)
        preview = self.query_one("#preview-content", Static)
        
        if task:
            symbol, color = STATUS_STYLES.get(task.status, ("[ ]", "white"))
            text = Text()
            text.append(f"{symbol} ", style=color)
            text.append(f"{task.title}  ", style="bold")
            text.append(_HELP, style="dim")
            bar.update(text)
            
            # Update preview pane with note content
            self._update_preview(task, preview)
        else:
            bar.update(Text(_HELP, style="dim"))
            preview.update("")

    def _update_preview(self, task: TaskNode, preview_widget: Static) -> None:
        """Load and display note content in the preview pane."""
        try:
            from ..core.notes import Note
            note = Note.from_file(task.file_path)
            
            preview_text = Text()
            
            # Title
            preview_text.append(f"{note.title}\n", style="bold underline")
            preview_text.append("─" * min(len(note.title), 40) + "\n\n", style="dim")
            
            # Metadata
            if note.note_type:
                preview_text.append(f"Type: ", style="dim")
                preview_text.append(f"{note.note_type}\n", style="cyan")
            if note.status:
                symbol, color = STATUS_STYLES.get(note.status, ("[ ]", "white"))
                preview_text.append(f"Status: ", style="dim")
                preview_text.append(f"{symbol} {note.status}\n", style=color)
            if note.due:
                due_str = note.due.strftime("%Y-%m-%d") if hasattr(note.due, 'strftime') else str(note.due)
                preview_text.append(f"Due: ", style="dim")
                preview_text.append(f"{due_str}\n", style="yellow" if note.is_overdue else "")
            if note.priority:
                preview_text.append(f"Priority: ", style="dim")
                preview_text.append(f"{note.priority}\n", style="red" if note.priority == "high" else "")
            if note.tags:
                preview_text.append(f"Tags: ", style="dim")
                preview_text.append(f"{', '.join(note.tags)}\n", style="magenta")
            if note.requires:
                preview_text.append(f"Requires: ", style="dim")
                preview_text.append(f"{', '.join(note.requires)}\n", style="blue")
            
            preview_text.append("\n", style="")
            
            # Content (skip first h1 heading since we showed title)
            content_lines = note.content.split('\n')
            content_started = False
            for line in content_lines:
                # Skip frontmatter delimiters
                if line.strip() == '---':
                    continue
                # Skip until we pass the first h1
                if line.startswith('# ') and not content_started:
                    content_started = True
                    continue
                if content_started:
                    preview_text.append(line + '\n', style="")
            
            preview_widget.update(preview_text)
        except Exception:
            preview_widget.update(Text("Unable to load preview", style="dim"))

    def _get_selected_task(self) -> TaskNode | None:
        node = self.query_one(Tree).cursor_node
        if node and isinstance(node.data, TaskNode):
            return node.data
        return None

    def _get_next_task_stem(self) -> str | None:
        current = self.query_one(Tree).cursor_node
        if not current:
            return None
        sib = current.next_sibling
        if sib and isinstance(sib.data, TaskNode):
            return sib.data.stem
        if current.parent:
            sib = current.parent.next_sibling
            if sib and isinstance(sib.data, TaskNode):
                return sib.data.stem
        return None

    def _get_prev_task_stem(self) -> str | None:
        current = self.query_one(Tree).cursor_node
        if not current:
            return None
        sib = current.previous_sibling
        if sib and isinstance(sib.data, TaskNode):
            return sib.data.stem
        if current.parent:
            sib = current.parent.previous_sibling
            if sib and isinstance(sib.data, TaskNode):
                return sib.data.stem
        return None

    def _set_cursor_to_task(self, stem: str | None) -> bool:
        if not stem:
            return False

        def find_node(node):
            if isinstance(node.data, TaskNode) and node.data.stem == stem:
                return node
            for child in node.children:
                found = find_node(child)
                if found:
                    return found
            return None

        target = find_node(self.query_one(Tree).root)
        if target:
            self.query_one(Tree).select_node(target)
            return True
        return False

    def _edit_task(self, task: TaskNode) -> None:
        from ..utils import open_in_editor, read_h1, title_to_stem, format_title
        h1_before = read_h1(task.file_path)
        with self.suspend():
            open_in_editor(task.file_path)
        h1_after = read_h1(task.file_path)

        new_stem = task.stem
        if h1_after and h1_after != h1_before:
            old_leaf = task.stem.split(".")[-1]
            new_leaf = title_to_stem(h1_after)
            if new_leaf and new_leaf != old_leaf and h1_after.lower() != format_title(old_leaf).lower():
                candidate = ".".join(task.stem.split(".")[:-1] + [new_leaf]) if "." in task.stem else new_leaf
                err = self._run_cor("rename", task.stem, candidate)
                if not err:
                    new_stem = candidate
                    self.notify(f"Renamed \u2192 {new_stem}", timeout=2)
                else:
                    self.notify(f"Rename failed: {err}", severity="warning")

        self._load_data()
        self._populate_tree(preserve_cursor_stem=new_stem)
        self.query_one("#header", Static).update(self._header_text())

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_cursor_down(self) -> None:
        self.query_one(Tree).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(Tree).action_cursor_up()

    def action_mark_done(self) -> None:
        self._change_status("done")

    def action_mark_blocked(self) -> None:
        self._change_status("blocked")

    def action_mark_active(self) -> None:
        self._change_status("active")

    def action_mark_waiting(self) -> None:
        self._change_status("waiting")

    def action_mark_dropped(self) -> None:
        self._change_status("dropped")

    def action_mark_todo(self) -> None:
        self._change_status("todo")

    def action_mark_done_with_note(self) -> None:
        self._prompt_for_note("done")

    def action_mark_blocked_with_note(self) -> None:
        self._prompt_for_note("blocked")

    def action_new_sibling(self) -> None:
        """Create a new task at the same level as the selected task."""
        task = self._get_selected_task()
        if task:
            parts = task.stem.split(".")
            prefix = ".".join(parts[:-1]) + "."
        else:
            prefix = self.focus + "."
        self._new_task(prefix)

    def action_new_subtask(self) -> None:
        """Create a new task as a child of the selected task."""
        task = self._get_selected_task()
        prefix = (task.stem if task else self.focus) + "."
        self._new_task(prefix)

    def action_move_task(self) -> None:
        """Move selected task to a different group."""
        task = self._get_selected_task()
        if not task or not task.is_task:
            self.notify("No task selected", severity="warning")
            return

        parts = task.stem.split(".")
        current_parent = ".".join(parts[:-1])

        def move(target: str) -> None:
            if not target or target == current_parent:
                return
            err = self._run_cor("move", task.stem, target)
            if err:
                self.notify(f"Error: {err}", severity="error")
                return
            leaf = parts[-1]
            new_stem = target + "." + leaf
            self._load_data()
            self._populate_tree(preserve_cursor_stem=new_stem)
            self.notify(f"Moved to {target}", timeout=1.5)

        self._prompt_input(
            placeholder=f"move {task.stem} → target group",
            callback=move,
            prefill=current_parent,
        )

    def action_delete_task(self) -> None:
        task = self._get_selected_task()
        if not task or not task.is_task:
            self.notify("No task selected", severity="warning")
            return
        next_stem = self._get_next_task_stem()

        def confirm(answer: str) -> None:
            if answer.lower() != "y":
                return
            err = self._run_cor("del", task.stem)
            if err:
                self.notify(f"Error: {err}", severity="error")
                return
            self._load_data()
            self._populate_tree(preserve_cursor_stem=next_stem)
            self.notify(f"Deleted: {task.stem}", timeout=1.5)

        self._prompt_input(
            placeholder=f"delete {task.stem}? (y/N)",
            callback=confirm,
        )

    def action_edit_task(self) -> None:
        task = self._get_selected_task()
        if task:
            self._edit_task(task)
        else:
            self.notify("No task selected", severity="warning")

# Plan: Interactive Project View for CortexPKM

## Request Summary

User wants an interactive project view to navigate, change task status, edit tasks, and go back. They've tried a dashboard/kanban approach but are unsure if that's the best way to help prioritize and maintain projects. They're asking if Textual or similar TUI frameworks could work.

**Key requirements from follow-up:**
- Vim-style navigation: `j/k` for up/down
- Status change keys: `x` (done), `o` (blocked), `/` (waiting), `.` (active), `Backspace` (todo), `~` (dropped)
- Focus on single project view (for now)
- Conserve the look & feel of `cor tree`
- Evaluate: extend `cor tree` vs new command

## Phase 1: Request Validation

### Does this make sense?

**Yes.** The user is experiencing friction:
- Current commands (`cor tree`, `cor daily`, `cor mark`) require multiple separate invocations
- No way to quickly scan through tasks and update them without context switching
- Vim keys suggest the user is a power user who lives in the terminal

### Is it worth doing?

**Yes, with the right scope.** Benefits:
- Faster iteration on task management
- Single-screen view of a project's state
- Quick status changes without leaving the terminal
- Can still defer to editor for actual thinking/writing

### Does it align with CortexPKM philosophy?

**Yes.** The philosophy states:
- "Writing is thinking" - the editor is the main interface
- "Tools assist, not replace"
- "Minimal tooling"

An interactive TUI view:
- ✅ Keeps you in the terminal (minimal)
- ✅ Assists navigation/status changes (doesn't replace writing)
- ✅ Can open editor when needed
- ✅ Read-only browsing doesn't compete with "writing is thinking"

### Can existing solutions handle this?

**Partially.** Current state:
- `cor tree <project>` - Shows task tree but static
- `cor mark <task> <status>` - Changes status but requires typing full names
- `cor edit <task>` - Opens editor
- `simple-term-menu` is already a dependency (used for fuzzy completion)

**Gap:** No single interactive interface that combines viewing + quick actions.

## Phase 2: Command Structure Decision

### Evaluated: Extend `cor tree` vs New `cor browse` Command

**Option A: Extend `cor tree` with `-i/--interactive` flag**
```bash
cor tree myproject -i      # Interactive mode
cor tree myproject -v -i   # Interactive with verbose (descriptions)
```

**Option B: New `cor browse` command**
```bash
cor browse myproject       # Interactive mode
```

### Decision: **Extend `cor tree` with `-i/--interactive` flag**

**Rationale:**
1. **User explicitly wants to conserve `cor tree` look & feel** - extending keeps the same output format
2. **Natural mental model** - "I'm looking at this tree, now I want to interact with it"
3. **Code reuse** - shares data loading, filtering, and display logic with existing `tree` command
4. **Familiar interface** - same arguments as `cor tree` (depth, sort, etc.)
5. **Less cognitive overhead** - one command for project hierarchy, just add `-i` to make it interactive

**Integration approach:**
- Add `-i, --interactive` flag to existing `tree` command
- When `-i` is used, launch Textual app instead of printing static output
- Non-interactive mode remains unchanged (backward compatible)

## Phase 3: Research Findings

### Textual Framework Analysis

**Pros:**
- Modern Python TUI framework (actively maintained)
- Excellent built-in Tree widget (perfect for `cor tree` structure)
- Built-in key binding system (easy to add vim keys)
- Can render the exact same tree structure as existing `tree` command
- Good documentation and examples

**Cons:**
- Adds a significant dependency (~10MB)
- Learning curve for maintainers
- Requires async/event loop understanding

**Verdict:** Worth it for the Tree widget alone - recreating tree rendering with curses is painful.

### Alternative: simple-term-menu

Already used in CortexPKM. Could be extended:
- **Pros:** Already a dependency
- **Cons:** No tree widget, would need to build custom rendering
- **Verdict:** Not suitable - tree rendering is the core requirement

### Alternative: Custom curses

- **Pros:** No new dependencies, stdlib only
- **Cons:** Complex, error-prone, no built-in tree widget
- **Verdict:** Too much work for MVP

## Phase 4: Proposed Approach

### Recommended Solution: Textual-based Interactive Tree

**Design Philosophy - "Vim-like Tree Navigation":**

```
my-project (active)
│
├─ [o] Blocked: Set up database dependencies
├─ [.] Active: Implement API endpoints             <-- cursor here
│  └─ [ ] Todo: Add validation middleware
├─ [/] Waiting: Review from team
│
├─ [ ] Todo: Write documentation
└─ [ ] Todo: Deploy to staging

j/k: navigate  x:done o:blocked .:active /:waiting ~:dropped  <BS>:todo  e:edit  q:quit
```

**Keybinding Design:**

| Key | Action | Symbol | Mnemonic |
|-----|--------|--------|----------|
| `j` | Move down | - | Vim down |
| `k` | Move up | - | Vim up |
| `x` | Mark done | `[x]` | Cross out / eXit |
| `o` | Mark blocked | `[o]` | Obstructed |
| `.` | Mark active | `[.]` | In progress (dot) |
| `/` | Mark waiting | `[/]` | Pending |
| `~` | Mark dropped | `[~]` | Wavy/cancelled |
| `Backspace` | Mark todo | `[ ]` | Clear/reset |
| `e` | Edit in $EDITOR | - | Edit |
| `q` | Quit | - | Quit |
| `?` | Show help | - | Help |

**Why these keys:**
- All status keys are single press, no modifiers needed
- Symbols match the status symbols used in existing `cor tree` output
- Vim navigation (`j/k`) is standard for terminal users
- `Backspace` for "clear to todo" feels natural (deleting the status)

### Files to Modify/Create

```
cor/
├── cli/
│   └── status.py            # MODIFY: Add -i/--interactive flag to tree command
└── tui/                     # NEW: TUI package
    ├── __init__.py
    ├── tree_app.py          # Main Textual App (single file for MVP)
    └── widgets.py           # Custom widgets (tree with cursor)
```

**Keeping it minimal:** For MVP, put everything in 2-3 files. Don't over-engineer the package structure.

### Exact Implementation Plan

#### Step 1: Add Textual Dependency
```toml
# pyproject.toml
dependencies = [
    # ... existing ...
    "textual>=0.50.0",
]
```

#### Step 2: Create TUI Module
**`cor/tui/__init__.py`** - Package init

**`cor/tui/tree_app.py`** - Single-file Textual app:
```python
from textual.app import App, ComposeResult
from textual.widgets import Tree, Static, Footer
from textual.containers import Vertical

class ProjectTreeApp(App):
    """Interactive project tree browser."""
    
    CSS = """
    /* Minimal styling to match cor tree output */
    """
    
    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("x", "mark_done", "Done"),
        ("o", "mark_blocked", "Blocked"),
        (".", "mark_active", "Active"),
        ("/", "mark_waiting", "Waiting"),
        ("~", "mark_dropped", "Dropped"),
        ("backspace", "mark_todo", "Todo"),
        ("e", "edit_task", "Edit"),
        ("q", "quit", "Quit"),
        ("?", "toggle_help", "Help"),
    ]
    
    def __init__(self, project_name: str, notes_dir: Path, **kwargs):
        self.project_name = project_name
        self.notes_dir = notes_dir
        super().__init__(**kwargs)
    
    def compose(self) -> ComposeResult:
        # Build tree from existing cor tree logic
        tree = Tree(self.project_name)
        self._populate_tree(tree)
        yield tree
        yield Footer()
    
    def action_mark_done(self):
        self._change_status("done")
    
    def action_mark_blocked(self):
        self._change_status("blocked")
    
    # ... etc
    
    def _change_status(self, new_status: str):
        """Change status of selected task and reload."""
        # Use existing mark command logic
        # Write to file, run sync, refresh tree
```

#### Step 3: Modify `cor tree` Command
**`cor/commands/status.py`** - Modify `tree` function:
```python
@click.command(short_help="Show a project's or group's task tree")
@click.option("--verbose", "-v", is_flag=True, help="Show task descriptions")
@click.option("--depth", "-d", type=int, default=None, help="Maximum depth")
@click.option("--sort", "-s", type=click.Choice(["status", "alphabetical"]), default="status")
@click.option("--interactive", "-i", is_flag=True, help="Interactive mode (navigate and edit)")  # NEW
@click.argument("focus", shell_complete=complete_existing_name)
@require_init
def tree(verbose: bool, depth: int | None, sort: str, interactive: bool, focus: str):  # Add interactive param
    """Show task tree for a project or task group.
    
    Use -i/--interactive for vim-style navigation and quick status changes.
    """
    if interactive:
        from ..tui.tree_app import ProjectTreeApp
        app = ProjectTreeApp(focus, get_notes_dir())
        app.run()
        return
    
    # ... existing static tree code unchanged ...
```

#### Step 4: Tree Rendering (Match Existing Look & Feel)

The existing `cor tree` output uses:
```
project-name
│
├─ [.] Active task
├─ [o] Blocked task
│  └─ [ ] Subtask
└─ [x] Done task
```

Textual's Tree widget can render this exactly. Key considerations:
- Use same branch characters (`├─`, `└─`, `│`)
- Keep status symbols (`[.]`, `[o]`, `[x]`, etc.) with same colors
- Cursor highlight on the whole line
- Maintain same sorting (blocked > active > waiting > todo > dropped > done)

### Implementation Details

**Data Loading:**
- Reuse existing `find_notes()` and tree building logic from `status.py`
- Pass pre-built task hierarchy to TUI app

**Status Changes:**
- Call existing `mark_note_status()` logic (extracted from `cor/commands/notes.py`)
- Run maintenance sync (same as pre-commit hook)
- Refresh tree data and re-render

**Editing:**
- Spawn `$EDITOR` via `subprocess.run()` 
- Block until editor closes
- Refresh tree on return

## Phase 5: Decisions NOT to Do

### Rejected: New `cor browse` Command
**Why:** User wants to conserve `cor tree` look & feel. Extending keeps the same interface.

### Rejected: Multi-Project View (for MVP)
**Why:** User wants to focus on single project case first. Can add later.

### Rejected: Custom Widgets for Everything
**Why:** Textual's built-in Tree is sufficient. Keep code minimal.

### Rejected: Mouse Support (for MVP)
**Why:** User specified vim keys. Keyboard-only is simpler and matches philosophy.

### Rejected: Inline Description Editing
**Why:** Violates "editor is main interface". Use `e` to open $EDITOR.

## Phase 6: Testing Strategy

1. **Test tree rendering matches existing output**
   - Same tree structure, symbols, colors
   - Cursor doesn't break formatting

2. **Test keybindings**
   - `j/k` navigation
   - Status changes update files correctly
   - `e` opens editor
   - `q` quits cleanly

3. **Test integration**
   - Status changes trigger maintenance sync
   - Editor changes reflected on refresh

4. **Use Textual's testing utilities:**
   ```python
   from textual.pilot import Pilot
   
   async def test_navigation():
       app = ProjectTreeApp("test-project", tmp_path)
       async with app.run_test() as pilot:
           await pilot.press("j", "j", "x")  # Down, down, mark done
           # Assert status changed
   ```

## Phase 7: Future Enhancements (Post-MVP)

- **Filter view:** Show only certain statuses (`f` to filter)
- **Create task:** `n` to add new task under cursor
- **Expand/collapse:** `h/l` or `Enter` to toggle groups
- **Multi-project:** Sidebar with project list
- **Search:** `/` to search within project

## Conclusion

**This is worth doing** with Textual, extending the existing `cor tree` command:

- ✅ **Matches user requirements:** Vim keys, specific status keybindings, single project focus
- ✅ **Conserves look & feel:** Extends `cor tree` instead of new command
- ✅ **Minimal intrusion:** Single flag addition, no breaking changes
- ✅ **Stays true to philosophy:** Editor for writing, TUI for browsing/quick actions
- ✅ **Good UX:** Vim keys for terminal power users

**Estimated effort:** 1-2 days for MVP, 3 days with tests.

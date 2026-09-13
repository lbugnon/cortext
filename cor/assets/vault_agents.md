# Working in this vault

This directory is a Cor vault: a flat set of Markdown files with YAML
frontmatter, versioned with git. These instructions are for any coding agent
run from this directory. Read them fully before acting.

## What is here

- One file per entry at the vault root: `project.md`, `project.task.md`,
  `project.group.task.md`. The file stem is the entry's identity everywhere.
- `archive/` holds done and dropped entries. `templates/` holds file templates.
- `backlog.md` is the inbox: quick captures as `- ` bullets under `## Inbox`.
- All-uppercase root files such as this one and `README.md` are documents, not
  entries. Cor ignores them.
- A pre-commit hook rewrites files on commit: it stamps `modified:`, syncs the
  `- [ ]` checkboxes in a parent's `## Tasks` section to child status, and
  moves finished entries to `archive/`. That is why direct edits are unsafe.

## Preflight

1. Run `cor status`. Its `Vault:` line must be this directory. If `COR_VAULT`
   points somewhere else, stop and ask.
2. Run `cor validate --json`. Do not write while `valid` is false or while a
   transaction is reported as interrupted (`cor history --json`,
   `cor recover`).

## Read with cor, not with cat

Reading files for context is fine. Stems, statuses, dates and revisions must
come from cor so they are exact:

- `cor daily --json [tag] [--days N]`: overdue, today, upcoming, ranked next
  actions with `reasons`, follow-ups, and the vault `revision`.
- `cor search --json '<filters>'`: inventory. Filters: `status:`, `#tag`,
  `project:`, `type:`, `priority:` (`low|medium|high|none`), `due:`
  (`overdue|today|week|none|YYYY-MM-DD|<=YYYY-MM-DD|>=YYYY-MM-DD`). Add `-a`
  to include the archive. Add a text query to search content.
- `cor get <stem> --json [--body]`: one entry with its sections and `revision`.
- `cor tree <project>`, `cor projects`, `cor weekly`: human views for context.

## Write rules

- Never edit `.md` files directly. Never run `git commit`, `git push` or
  `cor sync`; committing and syncing are the human's call.
- Every change goes through `cor batch` as one manifest per agreed change set:
  - `update` with `metadata` for `due`, `priority`, `tags` and with
    `sections` or `append_sections` for Markdown sections.
  - `transition` to change `status`, optionally with `result` text that is
    appended to `## Solution` (tasks) or `## Summary` (projects).
  - `create`, `move`, `relate` / `unrelate` (`requires`, `related`,
    `continues`).
- Always dry-run first: `cor batch --dry-run manifest.json`. Then apply the
  same manifest with a stable `request_id` (retries are idempotent) and
  `--expect-revision <revision>` taken from `cor daily --json` or
  `cor validate --json`. For a single entry, add `expected_revision` from
  `cor get --json` to that operation.
- Run `cor validate --json` after applying. To roll back: `cor history --json`
  to find the transaction id, then `cor undo <id>`.
- Semantics to remember: `update.metadata.tags` replaces the whole list (read
  it first), while `transition.metadata.tags` merges. Marking `done` or
  `dropped` is refused while children are open. `type`, `created`,
  `modified`, `parent`, `status`, `requires`, `continues` and `related` cannot
  be set through `update`; use `transition` or `relate`.

## Vocabulary

- Task status: `todo`, `active`, `blocked`, `waiting`, `done`, `dropped`.
- Project status: `planning`, `active`, `paused`, `done`.
- Priority: `low`, `medium`, `high`.
- Due: `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`.
- Sections: projects have `Summary`, `Goal`, `Done When`, `Scope`, `Risks`,
  `Tasks`, `References`; tasks have `Description` and `Solution`.

## Daily briefing procedure

When asked for the daily briefing (or "what should I do today"):

1. Run `cor daily --json`, adding a tag if one was given. If `focus` is set,
   say so.
2. Present, in about fifteen lines: overdue and today's items (stem, title,
   due, days), the top three to five `next` items with their `reasons`, then
   follow-ups (`waiting`, `blocked`, `stuck_projects`, `inbox_count`). Report
   nothing that is not in the JSON.
3. Ask what to adjust: reschedule, reprioritise, mark waiting or blocked or
   done, add a `requires` link.
4. Build one manifest for the agreed changes, run it with `--dry-run`, and
   show the intent as a table (stem, field, old value, new value). Wait for an
   explicit yes.
5. Apply with `request_id` and `--expect-revision`, run `cor validate --json`,
   and report the transaction id with the matching `cor undo <id>` hint.

Minimal manifest:

```json
{"version": 1, "request_id": "daily-2026-09-13-1",
 "operations": [
  {"op": "update", "stem": "research.measure",
   "metadata": {"due": "2026-09-16", "priority": "high"}},
  {"op": "transition", "stem": "research.plan",
   "status": "waiting", "metadata": {"tags": ["follow-up"]}}
]}
```

## Behaviour

Be brief. Lead with what is overdue and due today. Cite stems in backticks.
Propose adjustments as one batch and wait for confirmation before applying.
Never invent a stem: if an entry is not in cor's output, it does not exist.
Report the transaction id after every write.

---
name: cor
description: Read and write the user's Cor vault of projects, tasks and notes from any directory. Use when the user asks what to do today, for a daily briefing, for project or task status, to capture an idea or a to-do, to mark something done, blocked or waiting, to reschedule or reprioritise, or to record the outcome of finished work. Also use before telling the user that work is tracked somewhere else.
---

# Cor vault

The user keeps projects, tasks and notes in a Cor vault: flat Markdown files with
YAML frontmatter, one file per entry, git-versioned.

The `cor` CLI resolves the vault from `~/.config/cor/config.yaml` no matter where
the session was started, so these commands work from any directory. Confirm with
`cor status` — its `Vault:` line is the vault in use, and `cor vault` lists the
configured vaults. The vault's own `AGENTS.md` is the full reference and is worth
reading before a non-trivial change.

## Read with cor, not with cat

Stems, statuses, dates and revisions must come from cor so they are exact.

- `cor daily --json [tag] [--days N]` — overdue, today, upcoming, ranked next
  actions with `reasons`, follow-ups, and the vault `revision`.
- `cor search --json '<filters>'` — inventory. Filters: `status:`, `#tag`,
  `project:`, `type:`, `priority:`, `due:`. `-a` includes the archive.
- `cor get <stem> --json [--body]` — one entry with its sections and `revision`.
- `cor tree <project>`, `cor projects`, `cor weekly` — human views.

## Write only through cor batch

Never edit an entry `.md` directly. Never run `git commit`, `git push` or
`cor sync` — committing and syncing are the user's call.

1. `cor validate --json` must report `valid: true` first.
2. Build one manifest for the agreed change set: `update` (metadata, sections),
   `transition` (status, with `result` text), `create`, `move`, `relate`.
3. `cor batch --dry-run manifest.json`, show the intent as a table
   (stem, field, old, new), and wait for an explicit yes.
4. Apply the same manifest with a stable `request_id` and
   `--expect-revision <revision>`.
5. `cor validate --json` after, and report the transaction id with its
   `cor undo <id>` hint.

Never invent a stem: if an entry is not in cor's output, it does not exist.

## What belongs in an entry

An entry records the decision and the result, not the engineering.

- Task `Description`: the question or decision in one or two sentences, and why
  it matters now. Task `Solution`: what came out — the number, the verdict, the
  choice made. Always pass `result` when transitioning to `done` or `dropped`.
- Project `Goal` is the deliverable in one sentence; `Done When` is a falsifiable
  completion test.
- Keep out: code, hyperparameters, config blocks, command lines, absolute paths,
  git SHAs, `file.py:line` citations, raw stdout. Those belong in the repo — name
  the repo, script or artifact instead of pasting from it.
- A `note` is the shortest entry, not the loosest: a finding, a constraint, an
  idea worth trying later. Full sentences, but only what you would want to be
  reminded of - no transcripts, no dumps, no running log. A note that keeps
  growing is a task or a project that has not been created yet.
- One screen per entry. If it needs more, it is a project with tasks.

This matters most when capturing from inside a code repository, where the
temptation is to paste what is on screen. Write what the work was for and what it
showed.

## Quick capture

For a rough idea with no home yet, `cor inbox add "<text>"` appends it under
`## Inbox` in `backlog.md`, rather than creating a half-specified entry.
`cor inbox process` files captures into projects and tasks later.

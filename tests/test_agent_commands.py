"""Tests for exact agent commands and atomic batch execution."""

import json

from conftest import build_vault

from cor.cli import cli
from cor.core.storage import atomic_write_text
from cor.core.transactions import (
    VaultTransaction,
    _ACTIVE_TRANSACTION,
    vault_revision,
)


def invoke_json(runner, args, *, input_text=None):
    result = runner.invoke(cli, args, input=input_text)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_batch_cor_vault_overrides_cwd_for_data_and_history(
    runner, tmp_path, monkeypatch
):
    cwd_vault = build_vault(tmp_path / "cwd")
    selected_vault = build_vault(tmp_path / "selected")
    monkeypatch.chdir(cwd_vault)
    monkeypatch.setenv("COR_VAULT", str(selected_vault))
    manifest = {
        "operations": [
            {"op": "create", "type": "project", "stem": "research"}
        ]
    }

    output = invoke_json(runner, ["batch"], input_text=json.dumps(manifest))

    assert (selected_vault / "research.md").exists()
    assert not (cwd_vault / "research.md").exists()
    history = (
        selected_vault / ".git" / "cortex" / "history"
        / output["transaction"] / "transaction.json"
    )
    assert history.exists()
    assert not (cwd_vault / ".git" / "cortex").exists()


def test_get_returns_compact_structured_entry(runner, temp_vault):
    runner.invoke(cli, ["new", "project", "research", "--no-edit"])

    record = invoke_json(runner, ["get", "research", "--json"])

    assert record["stem"] == "research"
    assert record["type"] == "project"
    assert record["status"] == "planning"
    assert len(record["revision"]) == 64
    assert "body" not in record


def test_update_sets_section_and_rejects_stale_revision(runner, temp_vault):
    runner.invoke(cli, ["new", "project", "research", "--no-edit"])
    before = invoke_json(runner, ["get", "research", "--json"])

    after = invoke_json(
        runner,
        [
            "update", "research", "--section", "Goal",
            "--expect-revision", before["revision"], "--json", "Ship model",
        ],
    )
    assert after["sections"]["Goal"] == "Ship model"

    stale = runner.invoke(
        cli,
        [
            "update", "research", "--section", "Goal",
            "--expect-revision", before["revision"], "Again",
        ],
    )
    assert stale.exit_code != 0
    assert "changed since" in stale.output


def test_batch_composes_create_update_transition_and_relation(runner, temp_vault):
    manifest = {
        "operations": [
            {
                "op": "create", "type": "project", "stem": "research",
                "metadata": {"status": "active"},
                "sections": {"Goal": "Ship model"},
            },
            {
                "op": "create", "type": "task", "stem": "research.measure",
                "sections": {"Description": "Measure F1"},
            },
            {
                "op": "create", "type": "note", "stem": "research.idea",
                "sections": {"Summary": "Try separation"},
            },
            {
                "op": "relate", "stem": "research.idea",
                "relation": "related", "targets": ["research.measure"],
            },
            {
                "op": "transition", "stem": "research.measure",
                "status": "done", "result": "F1 was 0.32",
            },
        ]
    }

    output = invoke_json(runner, ["batch"], input_text=json.dumps(manifest))

    assert output["state"] == "committed"
    assert output["before_revision"] != output["after_revision"]
    assert (temp_vault / "research.md").exists()
    assert (temp_vault / "research.idea.md").exists()
    assert (temp_vault / "archive" / "research.measure.md").exists()
    task = invoke_json(runner, ["get", "research.measure", "--json"])
    assert task["sections"]["Solution"] == "F1 was 0.32"


def test_batch_failure_rolls_back_every_operation(runner, temp_vault):
    manifest = {
        "operations": [
            {"op": "create", "type": "project", "stem": "research"},
            {"op": "unknown", "stem": "research"},
        ]
    }

    result = runner.invoke(cli, ["batch"], input=json.dumps(manifest))

    assert result.exit_code != 0
    assert "Unknown operation" in result.output
    assert not (temp_vault / "research.md").exists()


def test_batch_dry_run_reports_but_restores(runner, temp_vault):
    manifest = {
        "operations": [
            {"op": "create", "type": "project", "stem": "research"}
        ]
    }

    output = invoke_json(
        runner, ["batch", "--dry-run"], input_text=json.dumps(manifest)
    )

    assert output["state"] == "dry-run"
    assert "research.md" in output["changed"]
    assert not (temp_vault / "research.md").exists()


def test_batch_vault_revision_guard_releases_lock(runner, temp_vault):
    stale_revision = vault_revision(temp_vault)
    (temp_vault / "backlog.md").write_text("changed")
    manifest = {
        "operations": [
            {"op": "create", "type": "project", "stem": "research"}
        ]
    }

    rejected = runner.invoke(
        cli,
        ["batch", "--expect-revision", stale_revision],
        input=json.dumps(manifest),
    )
    assert rejected.exit_code != 0
    assert "changed since" in rejected.output

    accepted = runner.invoke(cli, ["batch"], input=json.dumps(manifest))
    assert accepted.exit_code == 0, accepted.output


def test_search_json_combines_inventory_and_text_search(runner, temp_vault):
    manifest = {
        "operations": [
            {"op": "create", "type": "project", "stem": "research"},
            {
                "op": "create", "type": "task", "stem": "research.measure",
                "sections": {"Description": "Measure separation features"},
            },
            {"op": "create", "type": "note", "stem": "research.idea"},
        ]
    }
    runner.invoke(cli, ["batch"], input=json.dumps(manifest))

    inventory = invoke_json(
        runner,
        ["search", "--all", "--project", "research", "--type", "task", "--json"],
    )
    assert [entry["stem"] for entry in inventory] == ["research.measure"]

    matches = invoke_json(
        runner,
        ["search", "separation", "--project", "research", "--json", "--no-context"],
    )
    assert [entry["stem"] for entry in matches] == ["research.measure"]

    assert invoke_json(runner, ["search", "absent", "--json"]) == []


def test_validate_reports_structured_errors(runner, temp_vault):
    valid = invoke_json(runner, ["validate", "--json"])
    assert valid["valid"] is True

    (temp_vault / "bad.md").write_text(
        "---\ntype: task\nstatus: impossible\n---\n# Bad\n"
    )
    invalid = invoke_json(runner, ["validate", "--json"])
    assert invalid["valid"] is False
    assert any(error["kind"] == "frontmatter" for error in invalid["errors"])


def test_history_is_compact_and_undo_restores_batch(runner, temp_vault):
    before = vault_revision(temp_vault)
    manifest = {
        "operations": [
            {"op": "create", "type": "project", "stem": "research"}
        ]
    }
    batch = invoke_json(runner, ["batch"], input_text=json.dumps(manifest))

    history = invoke_json(runner, ["history", "--limit", "1", "--json"])
    assert history[0]["id"] == batch["transaction"]
    assert "before" not in history[0]

    undone = invoke_json(runner, ["undo", batch["transaction"]])
    assert undone["undone"] == batch["transaction"]
    assert undone["after_revision"] == before
    assert not (temp_vault / "research.md").exists()


def test_undo_refuses_to_overwrite_newer_work(runner, temp_vault):
    manifest = {
        "operations": [
            {"op": "create", "type": "project", "stem": "research"}
        ]
    }
    batch = invoke_json(runner, ["batch"], input_text=json.dumps(manifest))
    (temp_vault / "backlog.md").write_text("newer work")

    result = runner.invoke(cli, ["undo", batch["transaction"]])

    assert result.exit_code != 0
    assert "refusing undo" in result.output
    assert (temp_vault / "research.md").exists()


def test_batch_request_id_makes_retry_idempotent(runner, temp_vault):
    manifest = {
        "request_id": "create-research-v1",
        "operations": [
            {"op": "create", "type": "project", "stem": "research"}
        ],
    }

    first = invoke_json(runner, ["batch"], input_text=json.dumps(manifest))
    second = invoke_json(runner, ["batch"], input_text=json.dumps(manifest))

    assert first["state"] == "committed"
    assert second["state"] == "already-committed"
    assert second["transaction"] == first["transaction"]
    assert len(list(temp_vault.glob("research.md"))) == 1


def test_batch_rejects_reused_request_id_with_different_operations(runner, temp_vault):
    first = {
        "request_id": "request-1",
        "operations": [
            {"op": "create", "type": "project", "stem": "research"}
        ],
    }
    second = {
        "request_id": "request-1",
        "operations": [
            {"op": "create", "type": "project", "stem": "different"}
        ],
    }
    runner.invoke(cli, ["batch"], input=json.dumps(first))

    result = runner.invoke(cli, ["batch"], input=json.dumps(second))

    assert result.exit_code != 0
    assert "different batch" in result.output
    assert not (temp_vault / "different.md").exists()


def test_machine_errors_are_stable_json(runner, temp_vault):
    manifest = {"operations": [{"op": "unknown", "stem": "missing"}]}

    batch = runner.invoke(cli, ["batch"], input=json.dumps(manifest))
    get = runner.invoke(cli, ["get", "missing", "--json"])

    assert batch.exit_code == 1
    assert json.loads(batch.output) == {
        "ok": False,
        "error": {"code": "validation_error", "message": "Unknown operation: unknown"},
    }
    assert get.exit_code == 1
    assert json.loads(get.output)["error"]["code"] == "not_found"


def test_batch_rejects_invalid_field_types_without_writing(runner, temp_vault):
    manifests = [
        {"operations": [{
            "op": "create", "type": "project", "stem": "research",
            "sections": {"Goal": 4},
        }]},
        {"operations": [{
            "op": "create", "type": "project", "stem": "research",
            "create_parents": "false",
        }]},
    ]

    for manifest in manifests:
        result = runner.invoke(cli, ["batch"], input=json.dumps(manifest))
        assert result.exit_code == 1
        assert json.loads(result.output)["error"]["code"] == "validation_error"
        assert not (temp_vault / "research.md").exists()


def test_recover_command_restores_interrupted_write(runner, temp_vault):
    original = (temp_vault / "backlog.md").read_bytes()
    transaction = VaultTransaction(temp_vault)
    transaction.__enter__()
    atomic_write_text(temp_vault / "backlog.md", "partial\n")
    _ACTIVE_TRANSACTION.reset(transaction._token)
    transaction._token = None
    transaction._release_lock()

    blocked = runner.invoke(
        cli,
        ["batch"],
        input=json.dumps({"operations": [{
            "op": "create", "type": "project", "stem": "research",
        }]}),
    )
    recovered = invoke_json(runner, ["recover", transaction.transaction_id])

    assert blocked.exit_code == 1
    assert json.loads(blocked.output)["error"]["code"] == "conflict"
    assert recovered["recovered"] == transaction.transaction_id
    assert (temp_vault / "backlog.md").read_bytes() == original


def test_batch_move_updates_hierarchy_links_and_relations(runner, temp_vault):
    setup = {
        "operations": [
            {"op": "create", "type": "project", "stem": "first"},
            {"op": "create", "type": "project", "stem": "second"},
            {"op": "create", "type": "task", "stem": "first.measure"},
            {"op": "create", "type": "task", "stem": "second.consumer"},
            {
                "op": "relate", "stem": "second.consumer",
                "relation": "requires", "targets": ["first.measure"],
            },
        ]
    }
    runner.invoke(cli, ["batch"], input=json.dumps(setup))

    moved = invoke_json(
        runner,
        ["batch"],
        input_text=json.dumps({"operations": [{
            "op": "move", "stem": "first.measure", "to": "second.measure",
        }]}),
    )

    assert moved["results"][0]["destination"] == "second.measure"
    assert not (temp_vault / "first.measure.md").exists()
    task = invoke_json(runner, ["get", "second.measure", "--json"])
    assert task["metadata"]["parent"] == "second"
    assert "](second.md)" in (temp_vault / "second.measure.md").read_text()
    assert "second.measure.md" not in (temp_vault / "first.md").read_text()
    assert "second.measure.md" in (temp_vault / "second.md").read_text()
    consumer = invoke_json(runner, ["get", "second.consumer", "--json"])
    assert consumer["metadata"]["requires"] == ["second.measure"]


def test_batch_move_preserves_archive_and_moves_descendants(runner, temp_vault):
    setup = {
        "operations": [
            {"op": "create", "type": "project", "stem": "project"},
            {"op": "create", "type": "task", "stem": "project.group"},
            {"op": "create", "type": "task", "stem": "project.group.done"},
            {"op": "transition", "stem": "project.group.done", "status": "done"},
        ]
    }
    runner.invoke(cli, ["batch"], input=json.dumps(setup))

    invoke_json(
        runner,
        ["batch"],
        input_text=json.dumps({"operations": [{
            "op": "move", "stem": "project.group", "to": "project.experiments",
        }]}),
    )

    assert (temp_vault / "project.experiments.md").exists()
    archived = temp_vault / "archive" / "project.experiments.done.md"
    assert archived.exists()
    assert "parent: project.experiments" in archived.read_text()
    assert "](../project.experiments.md)" in archived.read_text()


def test_batch_move_note_does_not_add_task_entry(runner, temp_vault):
    setup = {"operations": [
        {"op": "create", "type": "project", "stem": "project"},
        {"op": "create", "type": "task", "stem": "project.area"},
        {"op": "create", "type": "note", "stem": "project.idea"},
        {"op": "move", "stem": "project.idea", "to": "project.area.idea"},
    ]}

    invoke_json(runner, ["batch"], input_text=json.dumps(setup))

    assert (temp_vault / "project.area.idea.md").exists()
    assert "project.area.idea.md" not in (temp_vault / "project.area.md").read_text()


def test_batch_move_collision_rolls_back_earlier_move(runner, temp_vault):
    setup = {"operations": [
        {"op": "create", "type": "project", "stem": "project"},
        {"op": "create", "type": "task", "stem": "project.one"},
        {"op": "create", "type": "task", "stem": "project.two"},
    ]}
    runner.invoke(cli, ["batch"], input=json.dumps(setup))

    result = runner.invoke(
        cli,
        ["batch"],
        input=json.dumps({"operations": [
            {"op": "move", "stem": "project.one", "to": "project.renamed"},
            {"op": "move", "stem": "project.two", "to": "project.renamed"},
        ]}),
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "already_exists"
    assert (temp_vault / "project.one.md").exists()
    assert (temp_vault / "project.two.md").exists()
    assert not (temp_vault / "project.renamed.md").exists()

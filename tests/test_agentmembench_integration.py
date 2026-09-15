"""Contract checks for the pinned AgentMemBench operational adapter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.integrations.agentmembench import PRMEAdapter
from benchmarks.integrations import install_agentmembench
from benchmarks.integrations import register_agentmembench
from benchmarks.integrations import verify_agentmembench


def test_adapter_isolates_users_retires_results_and_preserves_generations(
    tmp_path: Path,
) -> None:
    adapter = PRMEAdapter(
        SimpleNamespace(history_dir=tmp_path / "history"),
        "contract-run",
    )
    adapter.reset()
    alice_ids = adapter.add("Alice's private code is ALICE_ONLY.", "alice")
    adapter.add("Bob's private code is BOB_ONLY.", "bob")

    assert "ALICE_ONLY" in "\n".join(
        adapter.search("What is the private code?", "alice", 5)
    )
    assert "BOB_ONLY" not in "\n".join(
        adapter.search("What is the private code?", "alice", 5)
    )
    adapter.delete(alice_ids)
    assert "ALICE_ONLY" not in "\n".join(
        adapter.search("What is the private code?", "alice", 5)
    )

    adapter.reset()
    assert adapter.search("What is the private code?", "bob", 5) == []
    adapter.close()

    root = tmp_path / "history" / "prme" / "contract-run"
    first = json.loads(
        (root / "generation-0001" / "agentmembench-prme-manifest.json").read_text()
    )
    second = json.loads(
        (root / "generation-0002" / "agentmembench-prme-manifest.json").read_text()
    )
    manifest = json.loads((root / "agentmembench-prme-manifest.json").read_text())
    assert first["status"] == second["status"] == "complete"
    assert first["operation_counts"] == {
        "add_calls": 2,
        "archived_ids": 1,
        "search_calls": 3,
    }
    assert second["operation_counts"]["search_calls"] == 1
    assert manifest["status"] == "complete"
    assert manifest["generation_count"] == 2
    assert manifest["delete_semantics"] == "archive_retrieval_retirement"
    assert manifest["prme"]["revision"]
    assert len(manifest["adapter_sha256"]) == 64


def _upstream_layout(root: Path, *, include_choices: bool = True) -> Path:
    harness = root / "agentmembench" / "evaluation" / "unified_benchmark.py"
    harness.parent.mkdir(parents=True)
    (root / "agentmembench" / "config.py").write_text("# config\n")
    choices = install_agentmembench._CHOICES_ANCHOR if include_choices else ""
    harness.write_text(
        "def make_adapter(system, config, collection):\n"
        + install_agentmembench._ADAPTER_ANCHOR
        + "\n"
        + choices
        + "\n"
        + install_agentmembench._JUDGE_FAILURE_ANCHOR
        + "\n"
        + install_agentmembench._JUDGE_REASONING_ANCHOR,
        encoding="utf-8",
    )
    return harness


def test_installer_is_idempotent_and_exact(tmp_path: Path, monkeypatch) -> None:
    harness = _upstream_layout(tmp_path)
    monkeypatch.setattr(
        install_agentmembench,
        "_checkout_revision",
        lambda root: install_agentmembench.UPSTREAM_REVISION,
    )

    first = install_agentmembench.install(tmp_path)
    second = install_agentmembench.install(tmp_path)

    assert first["adapter"] == first["harness"] == "installed"
    assert second["adapter"] == second["harness"] == "unchanged"
    installed = (
        tmp_path / "agentmembench" / "prme_adapter.py"
    ).read_bytes()
    assert installed == install_agentmembench._ADAPTER_SOURCE.read_bytes()
    text = harness.read_text()
    assert text.count('if system == "prme"') == 1
    assert text.count('"letta", "prme"') == 1
    assert text.count("retrieval judge failed after retries") == 1
    assert 'return parsed.get("hit") is True' not in text
    assert text.count('reasoning_effort="none"') == 1


def test_installer_rolls_back_when_harness_anchor_is_ambiguous(
    tmp_path: Path, monkeypatch
) -> None:
    harness = _upstream_layout(tmp_path, include_choices=False)
    original = harness.read_bytes()
    monkeypatch.setattr(
        install_agentmembench,
        "_checkout_revision",
        lambda root: install_agentmembench.UPSTREAM_REVISION,
    )

    with pytest.raises(RuntimeError, match="CLI choices"):
        install_agentmembench.install(tmp_path)

    assert harness.read_bytes() == original
    assert not (tmp_path / "agentmembench" / "prme_adapter.py").exists()


def test_registration_binds_exact_sources_and_parameters(
    tmp_path: Path, monkeypatch
) -> None:
    upstream = tmp_path / "upstream"
    _upstream_layout(upstream)
    monkeypatch.setattr(
        install_agentmembench,
        "_checkout_revision",
        lambda root: install_agentmembench.UPSTREAM_REVISION,
    )
    install_agentmembench.install(upstream)
    data = upstream / "data.jsonl"
    data.write_text('{"synthetic":true}\n')

    def fake_git(root: Path, *args: str) -> str:
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return ""
        if root == upstream.resolve():
            return install_agentmembench.UPSTREAM_REVISION
        return "a" * 40

    monkeypatch.setattr(register_agentmembench, "_git", fake_git)
    judge = {
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "fixed-reader",
        "model_digest": "b" * 64,
        "temperature": 0,
        "reasoning_effort": "none",
        "max_tokens": 32,
        "response_format": "json_object",
        "concurrency": 32,
        "attempts": 3,
        "failure_policy": "abort",
    }
    monkeypatch.setattr(
        register_agentmembench,
        "ollama_model_identity",
        lambda _base_url, _model: judge,
    )
    registration = register_agentmembench.register(
        prme_root=tmp_path,
        upstream_root=upstream,
        data=data,
        run_id="contract-run",
        phases="retrieval,conflict,isolation",
        conflict_pairs=10,
        isolation_users=4,
        isolation_facts=2,
        workers="1,4",
        scales="10,100",
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_model="fixed-reader",
    )

    assert registration["status"] == "registered"
    assert registration["schema_version"] == 2
    assert registration["arguments"]["phases"] == [
        "retrieval",
        "conflict",
        "isolation",
    ]
    assert registration["arguments"]["workers"] == [1, 4]
    assert registration["source"]["upstream_revision"] == (
        install_agentmembench.UPSTREAM_REVISION
    )
    assert registration["source"]["installed_adapter_sha256"] == (
        registration["source"]["adapter_sha256"]
    )
    assert registration["judge"] == judge


def test_ollama_judge_identity_binds_exact_digest(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps(
                {"models": [{"name": "fixed-reader", "digest": "c" * 64}]}
            ).encode()

    observed = {}

    def open_url(url, *, timeout):
        observed.update(url=url, timeout=timeout)
        return Response()

    monkeypatch.setattr(register_agentmembench.urlrequest, "urlopen", open_url)

    identity = register_agentmembench.ollama_model_identity(
        "http://127.0.0.1:11434/v1/", "fixed-reader"
    )

    assert observed == {"url": "http://127.0.0.1:11434/api/tags", "timeout": 10}
    assert identity["model_digest"] == "c" * 64
    assert identity["failure_policy"] == "abort"


def test_verifier_rejects_unregistered_judge_arguments() -> None:
    arguments = {
        "retrieval_records": 10,
        "group_size": 2,
        "conflict_pairs": 5,
        "isolation_users": 3,
        "isolation_facts": 2,
        "deletion_records": 4,
        "concurrency_records": 6,
        "scale_read_queries": 3,
        "top_k": 5,
        "seed": 2027,
        "warmup_writes": 0,
        "phases": ["retrieval"],
        "workers": [1],
        "scales": [10],
        "llm_base_url": "http://127.0.0.1:11434/v1",
        "llm_model": "fixed-reader",
    }
    registration = {
        "system": "prme",
        "run_id": "contract-run",
        "arguments": arguments,
    }
    result = {
        "schema_version": "unified-benchmark-v2",
        "system": "prme",
        "run_id": "contract-run",
        "collection": "amb_kdd27_prme_contract_run",
        "arguments": {
            **arguments,
            "phases": "retrieval",
            "workers": "1",
            "scales": "10",
        },
    }
    verify_agentmembench._validate_result_arguments(result, registration)
    result["arguments"]["llm_model"] = "changed-reader"
    with pytest.raises(RuntimeError, match="llm_model"):
        verify_agentmembench._validate_result_arguments(result, registration)


def test_verifier_generation_plan_and_output_redaction() -> None:
    arguments = {
        "phases": ["retrieval", "conflict", "deletion", "concurrency", "scale"],
        "warmup_writes": 2,
        "retrieval_records": 7,
        "conflict_pairs": 5,
        "isolation_users": 3,
        "isolation_facts": 2,
        "deletion_records": 4,
        "concurrency_records": 6,
        "workers": [1, 4],
        "scales": [10],
        "scale_read_queries": 3,
    }
    assert verify_agentmembench._expected_generations(arguments) == [
        {"add_calls": 2, "search_calls": 1, "archived_ids": 0},
        {"add_calls": 0, "search_calls": 0, "archived_ids": 0},
        {"add_calls": 7, "search_calls": 7, "archived_ids": 0},
        {"add_calls": 10, "search_calls": 5, "archived_ids": 0},
        {"add_calls": 4, "search_calls": 8, "archived_ids": 4},
        {"add_calls": 6, "search_calls": 0, "archived_ids": 0},
        {"add_calls": 6, "search_calls": 0, "archived_ids": 0},
        {"add_calls": 10, "search_calls": 3, "archived_ids": 0},
    ]
    result = {
        "phases": {
            "retrieval": {"recall_at_k": 1.0, "details": [{"memory": "secret"}]},
            "conflict": {"new_fact_rate": 1.0},
            "deletion": {"post_delete_absence_rate": 1.0},
            "concurrency": {
                "1": {
                    "success_rate": 0.5,
                    "error_examples": ["private exception"],
                }
            },
            "scale": {"10": {"recall_at_3": 1.0}},
        }
    }

    safe = verify_agentmembench._safe_phases(result, arguments)

    assert "details" not in safe["retrieval"]
    assert "error_examples" not in safe["concurrency"]["1"]
    assert "secret" not in json.dumps(safe)
    assert "private exception" not in json.dumps(safe)

"""Artifact-level checks for scored MemoryAgentBench runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import duckdb
import pytest
import yaml

from benchmarks.integrations import memoryagentbench as adapter
from benchmarks.integrations import register_memoryagentbench as registrar
from benchmarks.integrations import verify_memoryagentbench as verifier
from prme.models.nodes import MemoryNode
from prme.models.relevance import make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.tokenization import count_tokens
from prme.types import NodeType, RepresentationLevel, Scope


PRME_REVISION = "1" * 40
PREPROCESSING = {
    "datasets": {"version": "test"},
    "nltk": {"version": "test", "punkt_tab_english_sha256": "a" * 64},
    "tiktoken": {
        "version": "test",
        "encoding": "o200k_base",
        "encoding_sha256": "b" * 64,
    },
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(verifier._canonical(value) + b"\n")


def fixture_run(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    prme_root = tmp_path / "prme"
    upstream_root = tmp_path / "upstream"
    source_root = prme_root / "benchmarks" / "integrations"
    source_root.mkdir(parents=True)
    for name, source in (
        ("memoryagentbench.py", Path(adapter.__file__)),
        ("install_memoryagentbench.py", Path(adapter.__file__)),
        ("register_memoryagentbench.py", Path(registrar.__file__)),
        ("verify_memoryagentbench.py", Path(verifier.__file__)),
    ):
        (source_root / name).write_bytes(source.read_bytes())
    installed_adapter = upstream_root / "methods" / "prme.py"
    installed_adapter.parent.mkdir(parents=True)
    installed_adapter.write_bytes(Path(adapter.__file__).read_bytes())
    upstream_names = (
        "main.py",
        "agent.py",
        "conversation_creator.py",
        "initialization.py",
        "utils/eval_data_utils.py",
        "utils/eval_other_utils.py",
    )
    for name in upstream_names:
        path = upstream_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture {name}\n", encoding="utf-8")

    def fake_git(root: Path, *args: str) -> str:
        if args[0] == "status":
            return ""
        return PRME_REVISION if root == prme_root else adapter.UPSTREAM_REVISION

    monkeypatch.setattr(verifier, "_git", fake_git)
    monkeypatch.setattr(
        verifier.registrar, "_preprocessing_identity", lambda: PREPROCESSING
    )

    agent_config = {
        "agent_name": "Agentic_memory_prme_rag",
        "model": "reader-model",
        "output_dir": "outputs/prme",
        "prme_token_budget": 4096,
        "reader_reasoning_effort": "none",
        "reader_seed": 42,
        "prme_run_id": "verified-arm",
    }
    dataset_config = {
        "dataset": "Accurate_Retrieval",
        "sub_dataset": "eventqa_65536",
    }
    agent_config_path = upstream_root / "agent.yaml"
    dataset_config_path = upstream_root / "dataset.yaml"
    agent_config_path.parent.mkdir(parents=True, exist_ok=True)
    agent_config_path.write_text(yaml.safe_dump(agent_config), encoding="utf-8")
    dataset_config_path.write_text(yaml.safe_dump(dataset_config), encoding="utf-8")

    identity = {
        "adapter_schema_version": adapter.ADAPTER_SCHEMA_VERSION,
        "upstream_revision": adapter.UPSTREAM_REVISION,
        "dataset_revision": adapter.DATASET_REVISION,
        "sub_dataset": dataset_config["sub_dataset"],
        "user_id": "memoryagentbench",
        "token_budget": 4096,
        "result_limit": 100,
        "max_chunk_chars": 6000,
        "embedding_provider": "fastembed",
        "embedding_model": "BAAI/bge-small-en-v1.5",
        "embedding_dimension": 384,
        "packing_policy": "balanced",
        "context_format": "auditable",
        "reader_reasoning_effort": "none",
        "reader_seed": 42,
        "run_id": "verified-arm",
    }
    manifest = {
        "schema_version": adapter.ADAPTER_SCHEMA_VERSION,
        "config": identity,
        "config_sha256": hashlib.sha256(verifier._canonical(identity)).hexdigest(),
        "status": "complete",
        "source_chunks": [{"index": 0, "sha256": "a" * 64, "piece_count": 2}],
        "stored_nodes": 2,
        "query_reference_time": "2026-09-14T12:00:00+00:00",
        "ingest_seconds": 1.5,
    }
    manifest_path = (
        upstream_root
        / "agents"
        / "prme_eventqa_65536_modelreader-model_runverified-arm"
        / "exp_0"
        / "prme_pack"
        / adapter._MANIFEST_NAME
    )
    write_json(manifest_path, manifest)

    query = "Question: What happened?"
    context = "## Memories\n- The event happened on Monday."
    request_id = "de305d54-75b4-431b-adb2-eb6b9e546014"
    reference_time = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)
    node = MemoryNode(
        id=UUID("de305d54-75b4-431b-adb2-eb6b9e546015"),
        user_id="memoryagentbench",
        scope=Scope.PROJECT,
        node_type=NodeType.FACT,
        content="The event happened on Monday.",
        created_at=reference_time,
        updated_at=reference_time,
        last_reinforced_at=reference_time,
    )
    candidates, _ = score_and_rank(
        [RetrievalCandidate(node=node, semantic_score=0.9)], now=reference_time
    )
    candidates[0].representation = RepresentationLevel.FULL
    candidates[0].token_cost = count_tokens(context)
    bundle = MemoryBundle(
        sections={"stable_facts": candidates},
        included_count=1,
        tokens_used=count_tokens(context),
        token_budget=4096,
        rendered_context=context,
        context_format="auditable",
    )
    receipt = make_receipt(
        request_id=UUID(request_id),
        user_id="memoryagentbench",
        query=query,
        reference_time=reference_time,
        scopes=(Scope.PROJECT,),
        scoring=ScoringWeights(),
        packing=PackingConfig(token_budget=4096, multipath_ordering="balanced"),
        candidates=candidates,
        bundle=bundle,
        result_limit=100,
        execution=RetrievalExecution(parameters={}, features={}),
    )
    database = manifest_path.parent / "memory.duckdb"
    connection = duckdb.connect(str(database))
    connection.execute(
        """
        CREATE TABLE operations (
            id VARCHAR PRIMARY KEY,
            op_type VARCHAR NOT NULL,
            target_id VARCHAR,
            payload JSON,
            actor_id VARCHAR,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    connection.execute(
        "INSERT INTO operations (id,op_type,target_id,payload,actor_id) "
        "VALUES (?,?,?,?,?)",
        [
            "receipt-operation",
            "RETRIEVAL_REQUEST",
            request_id,
            json.dumps(
                {
                    "receipt": receipt.model_dump_json(),
                    "receipt_checksum": receipt.checksum,
                }
            ),
            "memoryagentbench",
        ],
    )
    connection.close()
    capture = {
        "adapter_schema_version": adapter.ADAPTER_SCHEMA_VERSION,
        "upstream_revision": adapter.UPSTREAM_REVISION,
        "dataset_revision": adapter.DATASET_REVISION,
        "sub_dataset": dataset_config["sub_dataset"],
        "query_id": 0,
        "context_id": 0,
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "request_id": request_id,
        "receipt_persisted": True,
        "token_budget": 4096,
        "context_format": "auditable",
        "reader_reasoning_effort": "none",
        "reader_seed": 42,
        "run_id": "verified-arm",
        "context_token_count": count_tokens(context),
        "included_count": 1,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "context": context,
    }
    capture_path = (
        upstream_root
        / "outputs"
        / "prme"
        / "prme_retrievals"
        / "eventqa_65536"
        / "query_0_context_0.json"
    )
    write_json(capture_path, capture)

    result = {
        "agent_config": agent_config,
        "dataset_config": dataset_config,
        "data": [
            {
                "query_id": 0,
                "query": query,
                "answer": "Monday",
                "output": "Monday",
                "input_len": 25,
                "output_len": 1,
                "memory_construction_time": 1.5,
                "query_time_len": 0.25,
            }
        ],
        "metrics": {"accuracy": [True], "input_len": [25], "query_time_len": [0.25]},
        "averaged_metrics": {
            "accuracy": 100.0,
            "input_len": 25.0,
            "query_time_len": 0.25,
        },
        "time_cost": [1.75],
    }
    result_path = upstream_root / "result.json"
    write_json(result_path, result)
    source_names = (
        "memoryagentbench.py",
        "install_memoryagentbench.py",
        "register_memoryagentbench.py",
        "verify_memoryagentbench.py",
    )
    registration = {
        "schema_version": 1,
        "kind": "memoryagentbench-prme-registration",
        "registered_at": "2026-09-14T11:00:00+00:00",
        "source": {
            "prme_revision": PRME_REVISION,
            "upstream_revision": adapter.UPSTREAM_REVISION,
            "dataset_revision": adapter.DATASET_REVISION,
            "preprocessing": PREPROCESSING,
            "prme_files_sha256": {
                name: verifier._digest(source_root / name) for name in source_names
            },
            "upstream_files_sha256": {
                name: verifier._digest(upstream_root / name) for name in upstream_names
            },
            "installed_adapter_sha256": verifier._digest(installed_adapter),
        },
        "configuration": {
            "agent": agent_config,
            "agent_sha256": verifier._digest(agent_config_path),
            "dataset": dataset_config,
            "dataset_sha256": verifier._digest(dataset_config_path),
        },
        "task": {
            "dataset": dataset_config["dataset"],
            "sub_dataset": dataset_config["sub_dataset"],
            "context_count": 1,
            "query_count": 1,
            "query_limit": None,
            "contexts": [
                {
                    "context_id": 0,
                    "source_chunks": manifest["source_chunks"],
                    "queries": [
                        {
                            "query_id": 0,
                            "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                            "answer_sha256": hashlib.sha256(
                                verifier._canonical("Monday")
                            ).hexdigest(),
                            "qa_pair_id_sha256": hashlib.sha256(
                                verifier._canonical(None)
                            ).hexdigest(),
                        }
                    ],
                }
            ],
        },
    }
    registration_path = upstream_root / "registration.json"
    write_json(registration_path, registration)
    return {
        "prme_root": prme_root,
        "upstream_root": upstream_root,
        "agent_config": agent_config_path,
        "dataset_config": dataset_config_path,
        "result": result_path,
        "registration": registration_path,
        "capture": capture_path,
        "database": database,
    }


def run_verification(paths: dict[str, Path]) -> dict[str, object]:
    return verifier.verify(
        upstream_root=paths["upstream_root"],
        prme_root=paths["prme_root"],
        registration_path=paths["registration"],
        result_path=paths["result"],
        agent_config_path=paths["agent_config"],
        dataset_config_path=paths["dataset_config"],
    )


def test_verifier_binds_complete_run(tmp_path: Path, monkeypatch) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    report = run_verification(paths)
    assert report["status"] == "verified_complete"
    assert report["task"] == {
        "dataset": "Accurate_Retrieval",
        "sub_dataset": "eventqa_65536",
        "queries": 1,
        "contexts": 1,
        "source_chunks": 1,
        "stored_nodes": 2,
    }
    assert report["retrieval"]["context_tokens_max"] > 0
    assert report["source"]["result_sha256"] == verifier._digest(paths["result"])
    assert len(report["source"]["retrieval_receipts_sha256"]) == 64


def test_registrar_hashes_every_prepared_input() -> None:
    chunks = [["first source", "second source"], ["other context"]]
    queries = [
        [("Question: one?", "one", "qa-1")],
        [("Question: two?", ["two"], None)],
    ]
    contexts, query_count = registrar._registered_contexts(
        chunks, queries, max_chunk_chars=512
    )
    assert query_count == 2
    assert (
        contexts[0]["source_chunks"][1]["sha256"]
        == hashlib.sha256(b"second source").hexdigest()
    )
    assert contexts[1]["queries"][0]["query_id"] == 1
    assert (
        contexts[1]["queries"][0]["answer_sha256"]
        == hashlib.sha256(registrar._canonical(["two"])).hexdigest()
    )

    limited, limited_count = registrar._registered_contexts(
        chunks, queries, max_chunk_chars=512, max_queries=1
    )
    assert limited_count == 1
    assert len(limited) == 1
    assert limited[0]["queries"][0]["query_id"] == 0


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("receipt_persisted", False, "capture is inconsistent"),
        ("query_sha256", "0" * 64, "capture is inconsistent"),
        ("context_token_count", 4097, "accounting is invalid"),
        ("manifest_sha256", "0" * 64, "completed memory manifest"),
    ],
)
def test_verifier_rejects_unbound_retrievals(
    tmp_path: Path, monkeypatch, field: str, value: object, message: str
) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    capture = verifier._load_object(paths["capture"])
    capture[field] = value
    write_json(paths["capture"], capture)
    with pytest.raises(ValueError, match=message):
        run_verification(paths)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("missing", "durable receipt"),
        ("checksum", "durable receipt"),
        ("query", "differs from its retrieval capture"),
    ],
)
def test_verifier_authenticates_durable_receipt(
    tmp_path: Path, monkeypatch, change: str, message: str
) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    connection = duckdb.connect(str(paths["database"]))
    if change == "missing":
        connection.execute("DELETE FROM operations")
    else:
        payload = connection.execute("SELECT payload FROM operations").fetchone()[0]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if change == "checksum":
            payload["receipt_checksum"] = "0" * 64
        else:
            receipt_value = json.loads(payload["receipt"])
            receipt_value["query"] = "Question: A different question?"
            changed_receipt = verifier.RetrievalReceipt.model_validate(receipt_value)
            payload["receipt"] = changed_receipt.model_dump_json()
            payload["receipt_checksum"] = changed_receipt.checksum
        connection.execute(
            "UPDATE operations SET payload=?", [json.dumps(payload)]
        )
    connection.close()

    with pytest.raises(ValueError, match=message):
        run_verification(paths)


def test_verifier_binds_complete_manifest_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    capture = verifier._load_object(paths["capture"])
    manifest_path = next(paths["upstream_root"].rglob(adapter._MANIFEST_NAME))
    manifest = verifier._load_object(manifest_path)
    manifest["config"]["result_limit"] = 99
    manifest["config_sha256"] = hashlib.sha256(
        verifier._canonical(manifest["config"])
    ).hexdigest()
    write_json(manifest_path, manifest)
    capture["manifest_sha256"] = verifier._digest(manifest_path)
    write_json(paths["capture"], capture)

    with pytest.raises(ValueError, match="incompatible or incomplete manifest"):
        run_verification(paths)


def test_verifier_rejects_preprocessing_dependency_drift(
    tmp_path: Path, monkeypatch
) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    monkeypatch.setattr(
        verifier.registrar,
        "_preprocessing_identity",
        lambda: {**PREPROCESSING, "nltk": {"version": "changed"}},
    )
    with pytest.raises(ValueError, match="preprocessing dependencies differ"):
        run_verification(paths)

"""Artifact checks for the matched MemoryAgentBench BM25 baseline."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from benchmarks.integrations import memoryagentbench as adapter
from benchmarks.integrations import register_memoryagentbench_bm25 as registrar
from benchmarks.integrations import verify_memoryagentbench_bm25 as verifier


PRME_REVISION = "2" * 40
DEPENDENCIES = {
    "datasets": {"version": "test"},
    "langchain-community": {
        "version": "test",
        "bm25_source_sha256": "d" * 64,
    },
    "nltk": {"version": "test", "punkt_tab_english_sha256": "b" * 64},
    "numpy": {"version": "test"},
    "rank-bm25": {"version": "test", "source_sha256": "a" * 64},
    "tiktoken": {
        "version": "test",
        "encoding": "o200k_base",
        "encoding_sha256": "c" * 64,
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
    real_source_root = Path(registrar.__file__).parent
    for name in registrar.SOURCE_NAMES:
        (source_root / name).write_bytes((real_source_root / name).read_bytes())
    for name in registrar.UPSTREAM_SOURCE_NAMES:
        path = upstream_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# fixture {name}\n", encoding="utf-8")

    def fake_git(root: Path, *args: str) -> str:
        if args[0] == "status":
            return ""
        return PRME_REVISION if root == prme_root else adapter.UPSTREAM_REVISION

    monkeypatch.setattr(verifier, "_git", fake_git)
    monkeypatch.setattr(verifier, "_rank_indices", lambda documents, query, limit: [0])

    agent_config = {
        "agent_name": "Simple_rag_bm25",
        "model": "reader-model",
        "temperature": 0.0,
        "reader_reasoning_effort": "none",
        "reader_seed": 42,
        "retrieval_run_id": "bm25-test",
        "memory_timestamp": "2000-01-01 00:00:00",
        "input_length_limit": 10000,
        "buffer_length": 200,
        "output_dir": "outputs/bm25",
        "retrieve_num": 1,
    }
    dataset_config = {
        "dataset": "Accurate_Retrieval",
        "sub_dataset": "eventqa_65536",
        "chunk_size": 512,
        "generation_max_length": 40,
    }
    agent_path = upstream_root / "agent.yaml"
    dataset_path = upstream_root / "dataset.yaml"
    agent_path.parent.mkdir(parents=True, exist_ok=True)
    agent_path.write_text(yaml.safe_dump(agent_config), encoding="utf-8")
    dataset_path.write_text(yaml.safe_dump(dataset_config), encoding="utf-8")

    chunks = [["Alpha source"]]
    query = "Now Answer the Question: alpha?"
    queries = [[(query, "alpha", None)]]
    template = "At {time_stamp}: {context}"
    contexts, query_count = registrar._registered_task(
        chunks,
        queries,
        memorize_template=template,
        memory_timestamp=agent_config["memory_timestamp"],
        chunk_size=dataset_config["chunk_size"],
        input_length_limit=9760,
        max_queries=None,
    )
    registration = {
        "schema_version": 1,
        "kind": "memoryagentbench-bm25-registration",
        "registered_at": "2026-09-14T12:00:00+00:00",
        "source": {
            "prme_revision": PRME_REVISION,
            "upstream_revision": adapter.UPSTREAM_REVISION,
            "dataset_revision": adapter.DATASET_REVISION,
            "prme_files_sha256": {
                name: verifier._digest(source_root / name)
                for name in registrar.SOURCE_NAMES
            },
            "upstream_files_sha256": {
                name: verifier._digest(upstream_root / name)
                for name in registrar.UPSTREAM_SOURCE_NAMES
            },
            "dependencies": DEPENDENCIES,
        },
        "configuration": {
            "agent": agent_config,
            "agent_sha256": verifier._digest(agent_path),
            "dataset": dataset_config,
            "dataset_sha256": verifier._digest(dataset_path),
            "memorize_template_sha256": hashlib.sha256(template.encode()).hexdigest(),
        },
        "task": {
            "dataset": dataset_config["dataset"],
            "sub_dataset": dataset_config["sub_dataset"],
            "context_count": len(contexts),
            "query_count": query_count,
            "query_limit": None,
            "contexts": contexts,
        },
    }
    registration_path = upstream_root / "registration.json"
    write_json(registration_path, registration)

    result = {
        "agent_config": agent_config,
        "dataset_config": dataset_config,
        "data": [
            {
                "query_id": 0,
                "query": query,
                "answer": "alpha",
                "qa_pair_id": None,
                "output": "alpha",
                "input_len": 20,
                "output_len": 1,
                "memory_construction_time": 0.01,
                "query_time_len": 0.2,
            }
        ],
        "metrics": {"accuracy": [True], "input_len": [20], "query_time_len": [0.2]},
        "averaged_metrics": {
            "accuracy": 100.0,
            "input_len": 20.0,
            "query_time_len": 0.2,
        },
        "time_cost": [0.21],
    }
    result_path = upstream_root / "result.json"
    write_json(result_path, result)
    document = registrar._prepare_documents(
        chunks[0],
        memorize_template=template,
        memory_timestamp=agent_config["memory_timestamp"],
        chunk_size=dataset_config["chunk_size"],
        input_length_limit=9760,
    )[0]
    capture_path = (
        upstream_root
        / "outputs"
        / "rag_retrieved"
        / "Simple_rag_bm25"
        / "run_bm25-test"
        / "k_1"
        / "eventqa_65536"
        / "chunksize_512"
        / "query_0_context_0.json"
    )
    write_json(capture_path, [f"{document}\n"])
    return {
        "prme_root": prme_root,
        "upstream_root": upstream_root,
        "registration": registration_path,
        "result": result_path,
        "agent_config": agent_path,
        "dataset_config": dataset_path,
        "capture": capture_path,
        "chunks": chunks,
        "queries": queries,
        "template": template,
    }


def run_verification(paths: dict[str, object]) -> dict[str, object]:
    return verifier.verify(
        upstream_root=paths["upstream_root"],
        prme_root=paths["prme_root"],
        registration_path=paths["registration"],
        result_path=paths["result"],
        agent_config_path=paths["agent_config"],
        dataset_config_path=paths["dataset_config"],
        chunks=paths["chunks"],
        query_groups=paths["queries"],
        memorize_template=paths["template"],
        dependency_identity=DEPENDENCIES,
    )


def test_verifier_recomputes_complete_bm25_run(tmp_path: Path, monkeypatch) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    report = run_verification(paths)
    assert report["status"] == "verified_complete"
    assert report["task"]["queries"] == 1
    assert report["retrieval"]["documents_total"] == 1
    assert report["averaged_metrics"]["accuracy"] == 100.0


def test_bm25_verifier_rejects_unregistered_executing_code(
    tmp_path: Path, monkeypatch
) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    rogue = tmp_path / "rogue_bm25_verifier.py"
    rogue.write_text("# different verifier bytes\n", encoding="utf-8")
    monkeypatch.setattr(verifier, "__file__", str(rogue))
    with pytest.raises(ValueError, match="executing BM25 verifier differs"):
        run_verification(paths)


def test_verifier_rejects_changed_bm25_ranking(tmp_path: Path, monkeypatch) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    write_json(paths["capture"], ["wrong source\n"])
    with pytest.raises(ValueError, match="ranking differs"):
        run_verification(paths)


def test_verifier_rejects_dependency_drift(tmp_path: Path, monkeypatch) -> None:
    paths = fixture_run(tmp_path, monkeypatch)
    changed = {**DEPENDENCIES, "numpy": {"version": "changed"}}
    with pytest.raises(ValueError, match="dependencies differ"):
        verifier.verify(
            upstream_root=paths["upstream_root"],
            prme_root=paths["prme_root"],
            registration_path=paths["registration"],
            result_path=paths["result"],
            agent_config_path=paths["agent_config"],
            dataset_config_path=paths["dataset_config"],
            chunks=paths["chunks"],
            query_groups=paths["queries"],
            memorize_template=paths["template"],
            dependency_identity=changed,
        )


def test_registered_task_binds_formatted_documents_and_query() -> None:
    contexts, query_count = registrar._registered_task(
        [["first", "second"]],
        [[("Now Answer the Question: second?", "second")]],
        memorize_template="{time_stamp} {context}",
        memory_timestamp="fixed",
        chunk_size=10,
        input_length_limit=10,
        max_queries=None,
    )
    assert query_count == 1
    assert len(contexts[0]["source_chunks"]) == 2
    assert contexts[0]["bm25_documents"] == [
        {
            "index": 0,
            "sha256": hashlib.sha256(b"fixed second").hexdigest(),
        }
    ]
    assert contexts[0]["queries"][0]["retrieval_query_sha256"] == hashlib.sha256(
        b"second?"
    ).hexdigest()


def test_bm25_registration_uses_same_terminal_label_query_as_prme() -> None:
    prompt = (
        "Use the mapping to assign a label.\n\n"
        "Question:Where is my pending transfer? \n\n label:"
    )
    assert registrar._extract_retrieval_query(prompt) == "Where is my pending transfer?"

"""BEAM artifact validation rejects silent ingestion and model failures."""

import json

import duckdb
import pytest

from benchmarks.integrations import run_beam
from benchmarks.integrations.validate_beam import validate_run
from benchmarks.integrations import validate_beam


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _write_durable_work(
    execution_root,
    *,
    owner,
    materialization_status="complete",
    extraction_status="complete",
):
    database = execution_root / "prme-pack" / "memory.duckdb"
    database.parent.mkdir(parents=True, exist_ok=True)
    event_id = "00000000-0000-0000-0000-000000000001"
    connection = duckdb.connect(str(database))
    try:
        connection.execute("CREATE TABLE events (id UUID PRIMARY KEY, user_id VARCHAR)")
        connection.execute(
            "CREATE TABLE event_materializations (event_id UUID PRIMARY KEY, status VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE event_extractions (event_id UUID PRIMARY KEY, status VARCHAR)"
        )
        connection.execute("INSERT INTO events VALUES (?, ?)", [event_id, owner])
        connection.execute(
            "INSERT INTO event_materializations VALUES (?, ?)",
            [event_id, materialization_status],
        )
        if extraction_status is not None:
            connection.execute(
                "INSERT INTO event_extractions VALUES (?, ?)",
                [event_id, extraction_status],
            )
    finally:
        connection.close()


def _prediction(tmp_path, *, scored=False):
    owner = "beam_100K_0_registered"
    _write(
        tmp_path / "_ingestion_100K_0.json",
        {
            "chat_size": "100K",
            "conversation_idx": 0,
            "user_id": owner,
            "run_id": "registered",
            "chunk_size": 2,
            "total_chunks_processed": 7,
            "total_chunks_failed": 0,
        },
    )
    for index in range(2):
        question_id = f"100K_0_q{index}_abstention"
        row = {
            "question_id": question_id,
            "chat_size": "100K",
            "conversation_idx": 0,
            "question_type": "abstention",
            "question": f"Authored question {index}?",
            "rubric": ["Authored nugget"],
            "user_id": owner,
            "retrieval": {
                "search_query": f"Authored question {index}?",
                "search_results": [
                    {"id": f"node-{index}", "memory": "Authored memory", "score": 0.7}
                ],
                "search_latency_ms": 1.25,
                "total_results": 1,
            },
        }
        if scored:
            row["cutoff_results"] = {
                "top_50": {
                    "judgment": "PASS",
                    "score": 1.0,
                    "generated_answer": "Authored answer",
                    "memories_evaluated": 1,
                    "nugget_scores": [
                        {"nugget": "Authored nugget", "score": 1.0, "reason": "Supported"}
                    ],
                }
            }
        _write(tmp_path / f"{question_id}.json", row)


def test_validate_beam_accepts_complete_predict_only_artifacts(tmp_path):
    _prediction(tmp_path)
    report = validate_run(
        tmp_path,
        chat_sizes=("100K",),
        conversations=(0,),
        question_types=("abstention",),
    )
    assert report["complete"] is True
    assert report["errors"] == []
    assert report["coverage"]["questions"] == 2
    assert len(report["artifact_sha256"]) == 3


def test_validate_beam_accepts_complete_scored_artifacts(tmp_path):
    _prediction(tmp_path, scored=True)
    report = validate_run(
        tmp_path,
        chat_sizes=("100K",),
        conversations=(0,),
        question_types=("abstention",),
        scored=True,
        cutoffs=(50,),
    )
    assert report["complete"] is True


def test_validate_beam_rejects_failed_ingestion_and_empty_generation(tmp_path):
    _prediction(tmp_path, scored=True)
    ingestion = json.loads((tmp_path / "_ingestion_100K_0.json").read_text())
    ingestion["total_chunks_failed"] = 1
    _write(tmp_path / "_ingestion_100K_0.json", ingestion)
    question_path = tmp_path / "100K_0_q0_abstention.json"
    question = json.loads(question_path.read_text())
    question["cutoff_results"]["top_50"]["generated_answer"] = ""
    question["cutoff_results"]["top_50"]["nugget_scores"][0]["reason"] = (
        "Parse error: empty judge output"
    )
    _write(question_path, question)

    report = validate_run(
        tmp_path,
        chat_sizes=("100K",),
        conversations=(0,),
        question_types=("abstention",),
        scored=True,
        cutoffs=(50,),
    )
    assert report["complete"] is False
    assert any("failed chunks" in error for error in report["errors"])
    assert any("generated answer is empty" in error for error in report["errors"])
    assert any("invalid nugget verdict" in error for error in report["errors"])


def _registered_protocol():
    return {
        "profile": "raw",
        "chat_sizes": ["100K"],
        "conversations": [0],
        "question_types": list(validate_beam.QUESTION_TYPES),
        "top_k": 50,
        "top_k_cutoffs": [50],
        "predict_only": True,
        "project_name": run_beam.PROJECT_NAME,
        "run_id": run_beam.RUN_ID,
        "chunk_size": 2,
    }


def _scored_protocol():
    return {
        "profile": "raw",
        "chat_sizes": ["100K"],
        "conversations": [0],
        "question_types": list(validate_beam.QUESTION_TYPES),
        "top_k": 50,
        "top_k_cutoffs": [50],
        "predict_only": False,
        "project_name": "prme-beam-scored-test",
        "run_id": "prme-beam-scored-test",
        "chunk_size": 2,
        "max_workers": 1,
        "rpm": 60,
    }


def _scored_models():
    return {
        "answerer": {
            "provider": "openai",
            "model": "answerer",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_digest": "a" * 64,
        },
        "judge": {
            "provider": "openai",
            "model": "judge",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_digest": "b" * 64,
        },
    }


def test_scored_beam_protocol_requires_distinct_pinned_local_models(monkeypatch):
    registration = {
        "schema_version": 2,
        "kind": run_beam.SCORED_REGISTRATION_KIND,
        "protocol": _scored_protocol(),
        "models": _scored_models(),
    }
    assert run_beam._protocol(registration) == registration["protocol"]
    monkeypatch.setattr(
        run_beam,
        "_ollama_model_digests",
        lambda _url: {"answerer": "a" * 64, "judge": "b" * 64},
    )
    run_beam._verify_models(registration)

    registration["models"]["judge"]["model"] = "answerer"
    with pytest.raises(RuntimeError, match="must be distinct"):
        run_beam._protocol(registration)


@pytest.mark.parametrize(
    ("profile", "schema_version", "registration_kind", "execution_kind"),
    [
        (
            "raw",
            2,
            run_beam.SCORED_REGISTRATION_KIND,
            run_beam.SCORED_EXECUTION_KIND,
        ),
        (
            "extracted",
            3,
            run_beam.SCORED_REGISTRATION_KIND_V3,
            run_beam.SCORED_EXECUTION_KIND_V3,
        ),
        (
            "extracted",
            4,
            run_beam.SCORED_REGISTRATION_KIND_V3,
            run_beam.SCORED_EXECUTION_KIND_V3,
        ),
        (
            "extracted",
            5,
            run_beam.SCORED_REGISTRATION_KIND_V3,
            run_beam.SCORED_EXECUTION_KIND_V3,
        ),
        (
            "extracted",
            6,
            run_beam.SCORED_REGISTRATION_KIND_V3,
            run_beam.SCORED_EXECUTION_KIND_V3,
        ),
    ],
)
def test_registered_beam_validation_accepts_bound_scored_execution(
    tmp_path, profile, schema_version, registration_kind, execution_kind
):
    execution_root = tmp_path / "execution"
    prediction_dir = execution_root / "predictions"
    prediction_dir.mkdir(parents=True)
    protocol = _scored_protocol()
    protocol["profile"] = profile
    models = _scored_models()
    owner = f"beam_100K_0_{protocol['run_id']}"
    _write(
        prediction_dir / "_ingestion_100K_0.json",
        {
            "chat_size": "100K",
            "conversation_idx": 0,
            "user_id": owner,
            "run_id": protocol["run_id"],
            "chunk_size": 2,
            "total_chunks_processed": 10,
            "total_chunks_failed": 0,
        },
    )
    question_index = 0
    for question_type in validate_beam.QUESTION_TYPES:
        for _ in range(2):
            question_id = f"100K_0_q{question_index}_{question_type}"
            question = f"Question {question_index}?"
            _write(
                prediction_dir / f"{question_id}.json",
                {
                    "question_id": question_id,
                    "chat_size": "100K",
                    "conversation_idx": 0,
                    "question_type": question_type,
                    "question": question,
                    "rubric": ["Bound nugget"],
                    "user_id": owner,
                    "retrieval": {
                        "search_query": question,
                        "search_results": [
                            {"id": "node", "memory": "memory", "score": 0.5}
                        ],
                        "search_latency_ms": 1.0,
                        "total_results": 1,
                    },
                    "cutoff_results": {
                        "top_50": {
                            "judgment": "PASS",
                            "score": 1.0,
                            "generated_answer": "Bound answer",
                            "memories_evaluated": 1,
                            "nugget_scores": [
                                {
                                    "nugget": "Bound nugget",
                                    "score": 1.0,
                                    "reason": "Supported",
                                }
                            ],
                        }
                    },
                },
            )
            question_index += 1

    dataset = execution_root / "dataset" / run_beam.DATASET_FILENAME
    dataset.parent.mkdir(parents=True)
    dataset.write_text("[]")
    files = {"service_sha256": "c" * 64}
    extraction = None
    if profile == "extracted":
        extraction = {
            "provider": "ollama",
            "model": "extractor",
            "base_url": "http://127.0.0.1:11434/v1",
            "model_digest": "d" * 64,
            "reasoning_effort": "none",
            "temperature": 0.0,
            "timeout": 120.0,
            "lease_seconds": 60.0,
        }
        if schema_version >= 4:
            extraction["max_retries"] = 3
    registration = {
        "schema_version": schema_version,
        "kind": registration_kind,
        "source": {
            "prme_revision": "1" * 40,
            "upstream_revision": run_beam.UPSTREAM_COMMIT,
            "files": files,
        },
        "dataset": {
            "revision": run_beam.DATASET_REVISION,
            "cache_sha256": validate_beam._hash(dataset),
        },
        "protocol": protocol,
        "models": models,
        "system": {
            "id": "prme",
            "version": "0.11.0",
            "profile": profile,
            "embedding": {
                "provider": "fastembed",
                "model": "BAAI/bge-small-en-v1.5",
                "dimension": 384,
            },
            "adapter_schema": 4 if schema_version >= 5 else (3 if schema_version >= 4 else 2),
            "duckdb_threads": 1,
            "scoring_version": "scoring-v1",
            "packing": {"token_budget": 4096},
            "extraction": extraction,
            **(
                {
                    "retrieval": {
                        "max_per_source": 1,
                        "passage_time": "latest_evidence_event",
                    },
                    "admission": {
                        "raw_materialization_before_ack": True,
                        "extraction_before_ack": profile == "extracted",
                    },
                }
                if schema_version >= 5
                else {}
            ),
        },
    }
    registration_path = tmp_path / "registration.json"
    _write(registration_path, registration)
    _write(
        execution_root / run_beam.MANIFEST_FILENAME,
        {
            "schema_version": schema_version,
            "kind": execution_kind,
            "registration_sha256": validate_beam._hash(registration_path),
            "dataset_sha256": validate_beam._hash(dataset),
            "protocol": protocol,
            "models": models,
            "source": {
                "prme_revision": "1" * 40,
                "prme_worktree_changes": [],
                "upstream_revision": run_beam.UPSTREAM_COMMIT,
                "upstream_worktree_changes": [],
                "files": files,
            },
        },
    )
    _write(
        execution_root / "prme-pack" / "beam_adapter_manifest.json",
        {
            "profile": profile,
            "extraction": (
                None
                if extraction is None
                else {key: value for key, value in extraction.items() if key != "model_digest"}
            ),
            "upstream_commit": run_beam.UPSTREAM_COMMIT,
            "prme_version": "0.11.0",
            "adapter_source_sha256": "c" * 64,
            "embedding": registration["system"]["embedding"],
            "adapter_schema": 4 if schema_version >= 5 else (3 if schema_version >= 4 else 2),
            "duckdb_threads": 1,
            "scoring_version": "scoring-v1",
            "packing": {"token_budget": 4096},
            **(
                {
                    "retrieval": registration["system"]["retrieval"],
                    "admission": registration["system"]["admission"],
                }
                if schema_version >= 5
                else {}
            ),
        },
    )
    if schema_version >= 5:
        _write_durable_work(execution_root, owner=owner)

    report = validate_beam.validate_run(
        prediction_dir,
        chat_sizes=("100K",),
        conversations=(0,),
        scored=True,
        cutoffs=(50,),
    )
    validate_beam._registered_validation(
        report,
        registration_path=registration_path,
        execution_root=execution_root,
        chat_sizes=("100K",),
        conversations=(0,),
        question_types=validate_beam.QUESTION_TYPES,
        scored=True,
        cutoffs=(50,),
    )
    assert report["errors"] == []


def test_scored_beam_v4_and_v5_bind_extraction_and_admission(monkeypatch):
    protocol = {**_scored_protocol(), "profile": "extracted"}
    registration = {
        "schema_version": 4,
        "kind": run_beam.SCORED_REGISTRATION_KIND_V3,
        "protocol": protocol,
        "models": _scored_models(),
        "source": {
            "prme_revision": "1" * 40,
            "upstream_revision": run_beam.UPSTREAM_COMMIT,
            "files": {"service_sha256": "c" * 64},
        },
        "dataset": {
            "revision": run_beam.DATASET_REVISION,
            "cache_sha256": "e" * 64,
        },
        "system": {
            "id": "prme",
            "version": "0.11.0",
            "profile": "extracted",
            "adapter_schema": 3,
            "duckdb_threads": 1,
            "embedding": {
                "provider": "fastembed",
                "model": "BAAI/bge-small-en-v1.5",
                "dimension": 384,
            },
            "scoring_version": "scoring-v1",
            "packing": {"token_budget": 4096},
            "extraction": {
                "provider": "ollama",
                "model": "extractor",
                "base_url": "http://127.0.0.1:11434/v1",
                "model_digest": "d" * 64,
                "reasoning_effort": "none",
                "max_retries": 3,
                "temperature": 0.0,
                "timeout": 120.0,
                "lease_seconds": 60.0,
            },
        },
    }
    monkeypatch.setattr(
        run_beam,
        "_ollama_model_digests",
        lambda _url: {
            "answerer": "a" * 64,
            "judge": "b" * 64,
            "extractor": "d" * 64,
        },
    )
    run_beam.validate_registration(
        registration,
        project_revision="1" * 40,
        upstream_revision=run_beam.UPSTREAM_COMMIT,
        source_hashes={"service_sha256": "c" * 64},
        dataset_sha256="e" * 64,
    )
    registration["system"]["extraction"]["max_retries"] = 0
    with pytest.raises(RuntimeError, match="retries must be positive"):
        run_beam.validate_registration(
            registration,
            project_revision="1" * 40,
            upstream_revision=run_beam.UPSTREAM_COMMIT,
            source_hashes={"service_sha256": "c" * 64},
            dataset_sha256="e" * 64,
        )
    registration["system"]["extraction"]["max_retries"] = 3
    registration["system"]["extraction"]["model_digest"] = "f" * 64
    with pytest.raises(RuntimeError, match="extraction model digest"):
        run_beam._verify_models(registration)
    registration["system"]["extraction"]["model_digest"] = "d" * 64
    registration["schema_version"] = 5
    registration["system"].update(
        {
            "adapter_schema": 4,
            "retrieval": {
                "max_per_source": 1,
                "passage_time": "latest_evidence_event",
            },
            "admission": {
                "raw_materialization_before_ack": True,
                "extraction_before_ack": True,
            },
        }
    )
    run_beam.validate_registration(
        registration,
        project_revision="1" * 40,
        upstream_revision=run_beam.UPSTREAM_COMMIT,
        source_hashes={"service_sha256": "c" * 64},
        dataset_sha256="e" * 64,
    )
    registration["system"]["admission"]["raw_materialization_before_ack"] = False
    with pytest.raises(RuntimeError, match="complete admission work"):
        run_beam.validate_registration(
            registration,
            project_revision="1" * 40,
            upstream_revision=run_beam.UPSTREAM_COMMIT,
            source_hashes={"service_sha256": "c" * 64},
            dataset_sha256="e" * 64,
        )


def test_beam_schema_6_accepts_one_untouched_conversation():
    registration = {
        "schema_version": 6,
        "kind": run_beam.SCORED_REGISTRATION_KIND_V3,
        "protocol": {
            **_scored_protocol(),
            "profile": "extracted",
            "conversations": [1],
        },
        "models": _scored_models(),
    }

    assert run_beam._protocol(registration)["conversations"] == [1]

    registration["protocol"]["conversations"] = [1, 2]
    with pytest.raises(RuntimeError, match="one registered 100K conversation"):
        run_beam._protocol(registration)

    registration["protocol"]["conversations"] = [20]
    with pytest.raises(RuntimeError, match="one registered 100K conversation"):
        run_beam._protocol(registration)


def test_beam_durable_pack_validation_rejects_pending_materialization(tmp_path):
    execution_root = tmp_path / "execution"
    prediction_dir = execution_root / "predictions"
    owner = "beam_100K_0_pending"
    _write(
        prediction_dir / "_ingestion_100K_0.json",
        {"user_id": owner},
    )
    _write_durable_work(
        execution_root,
        owner=owner,
        materialization_status="pending",
    )
    report = {
        "errors": [],
        "artifact_sha256": {},
        "prediction_directory": str(prediction_dir),
    }

    validate_beam._validate_durable_pack(
        report,
        execution_root=execution_root,
        profile="extracted",
    )

    assert report["errors"] == [
        f"BEAM owner {owner} raw materialization is incomplete"
    ]
    assert report["durable_state"][owner] == {
        "events": 1,
        "materializations": {"pending": 1},
        "extractions": {"complete": 1},
    }


def test_registered_beam_validation_binds_source_dataset_and_adapter(tmp_path):
    execution_root = tmp_path / "execution"
    prediction_dir = execution_root / "predictions"
    prediction_dir.mkdir(parents=True)
    owner = f"beam_100K_0_{run_beam.RUN_ID}"
    _write(
        prediction_dir / "_ingestion_100K_0.json",
        {
            "chat_size": "100K",
            "conversation_idx": 0,
            "user_id": owner,
            "run_id": run_beam.RUN_ID,
            "chunk_size": 2,
            "total_chunks_processed": 10,
            "total_chunks_failed": 0,
        },
    )
    question_index = 0
    for question_type in validate_beam.QUESTION_TYPES:
        for _ in range(2):
            question_id = f"100K_0_q{question_index}_{question_type}"
            question = f"Question {question_index}?"
            _write(
                prediction_dir / f"{question_id}.json",
                {
                    "question_id": question_id,
                    "chat_size": "100K",
                    "conversation_idx": 0,
                    "question_type": question_type,
                    "question": question,
                    "user_id": owner,
                    "retrieval": {
                        "search_query": question,
                        "search_results": [
                            {"id": "node", "memory": "memory", "score": 0.5}
                        ],
                        "search_latency_ms": 1.0,
                        "total_results": 1,
                    },
                },
            )
            question_index += 1

    dataset = execution_root / "dataset" / run_beam.DATASET_FILENAME
    dataset.parent.mkdir(parents=True)
    dataset.write_text("[]")
    files = {"service_sha256": "a" * 64}
    registration = {
        "schema_version": 1,
        "kind": "beam-raw-predict-only-registration",
        "source": {
            "prme_revision": "1" * 40,
            "upstream_revision": run_beam.UPSTREAM_COMMIT,
            "files": files,
        },
        "dataset": {
            "revision": run_beam.DATASET_REVISION,
            "cache_sha256": validate_beam._hash(dataset),
        },
        "protocol": _registered_protocol(),
        "system": {
            "id": "prme",
            "version": "0.11.0",
            "profile": "raw",
            "embedding": {
                "provider": "fastembed",
                "model": "BAAI/bge-small-en-v1.5",
                "dimension": 384,
            },
        },
    }
    registration_path = tmp_path / "registration.json"
    _write(registration_path, registration)
    manifest = {
        "schema_version": 1,
        "kind": "beam-raw-predict-only-execution",
        "registration_sha256": validate_beam._hash(registration_path),
        "dataset_sha256": validate_beam._hash(dataset),
        "source": {
            "prme_revision": "1" * 40,
            "prme_worktree_changes": [],
            "upstream_revision": run_beam.UPSTREAM_COMMIT,
            "upstream_worktree_changes": [],
            "files": files,
        },
    }
    _write(execution_root / run_beam.MANIFEST_FILENAME, manifest)
    _write(
        execution_root / "prme-pack" / "beam_adapter_manifest.json",
        {
            "profile": "raw",
            "extraction": None,
            "upstream_commit": run_beam.UPSTREAM_COMMIT,
            "prme_version": "0.11.0",
            "adapter_source_sha256": "a" * 64,
            "embedding": registration["system"]["embedding"],
        },
    )

    report = validate_run(
        prediction_dir,
        chat_sizes=("100K",),
        conversations=(0,),
    )
    validate_beam._registered_validation(
        report,
        registration_path=registration_path,
        execution_root=execution_root,
        chat_sizes=("100K",),
        conversations=(0,),
        question_types=validate_beam.QUESTION_TYPES,
        scored=False,
        cutoffs=(50,),
    )
    assert report["errors"] == []
    assert report["registration_sha256"] == validate_beam._hash(registration_path)

    dataset.write_text("changed")
    changed = validate_run(
        prediction_dir,
        chat_sizes=("100K",),
        conversations=(0,),
    )
    validate_beam._registered_validation(
        changed,
        registration_path=registration_path,
        execution_root=execution_root,
        chat_sizes=("100K",),
        conversations=(0,),
        question_types=validate_beam.QUESTION_TYPES,
        scored=False,
        cutoffs=(50,),
    )
    assert "executed BEAM dataset hash differs" in changed["errors"]

"""BEAM artifact validation rejects silent ingestion and model failures."""

import json

from benchmarks.integrations.validate_beam import validate_run


def _write(path, value):
    path.write_text(json.dumps(value))


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

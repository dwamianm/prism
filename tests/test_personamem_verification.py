"""Authored completion, durable provenance and cluster-scoring guard checks."""

from copy import deepcopy
import csv
from io import StringIO
import json
from types import SimpleNamespace

import duckdb
import pytest

from benchmarks.diagnostics.hybrid_lexical import raw_config
from benchmarks.diagnostics.packing_reader import digest
from benchmarks.diagnostics.personamem_packing import ARMS, capture_persona
from benchmarks.diagnostics.verify_personamem import (
    cluster_comparison, complete_result, summarize, verify_pack,
)
from benchmarks.evidence import SourceTurn
from tests.test_durable_ingestion import MockEmbeddingProvider
from tests.test_personamem_adapter import row


@pytest.mark.parametrize("result", [
    {}, {"complete": False, "errors": 0, "process_exit_code": 0},
    {"complete": True, "errors": 1, "process_exit_code": 0},
    {"complete": True, "errors": 0, "process_exit_code": -11},
])
def test_rejects_incomplete_before_accessing_predictions(result):
    with pytest.raises(ValueError, match="native-exit-zero"):
        complete_result(result, {}, b"plan")


def test_rejects_missing_question_or_arm():
    ids = [str(i) for i in range(96)]
    plan = {"registered_at": "2026-01-01", "cohort": {"selected_question_ids": ids}}
    result = {"complete": True, "errors": 0, "process_exit_code": 0,
              "plan_sha256": digest(b"plan"), "started_at": "2026-01-02",
              "details": [{"question_id": qid, "arms": dict.fromkeys(ARMS)} for qid in ids]}
    complete_result(result, plan, b"plan")
    missing = deepcopy(result)
    missing["details"].pop()
    with pytest.raises(ValueError, match="Question coverage"):
        complete_result(missing, plan, b"plan")
    del result["details"][0]["arms"]["quarter"]
    with pytest.raises(ValueError, match="Arm coverage"):
        complete_result(result, plan, b"plan")


def test_bootstrap_keeps_four_correlated_questions_together():
    rows = [{"persona_id": owner, "correct": {"quarter": outcome, "density": not outcome}}
            for owner, outcome in [("a", True), ("b", False)] for _ in range(4)]
    stats = cluster_comparison(rows, "quarter", "density")
    assert stats["questions"] == 8 and stats["personas"] == 2
    assert stats["delta_accuracy"] == 0
    assert stats["ci95_percentile"] == [-1, 1]
    assert stats == cluster_comparison(list(reversed(rows)), "quarter", "density")


def test_invalid_predictions_count_in_denominator_and_every_category():
    rows = [{"persona_id": "a", "pref_type": "conditional", "who": "user", "updated": "False",
             "conversation_scenario": "travel", "answers": {arm: None for arm in ARMS},
             "correct": {arm: False for arm in ARMS}},
            {"persona_id": "b", "pref_type": "neutral", "who": "other", "updated": "True",
             "conversation_scenario": "work", "answers": dict.fromkeys(ARMS, "A"),
             "correct": {arm: True for arm in ARMS}}]
    result = summarize(rows)
    assert result["arms"]["quarter"] == {"correct": 1, "total": 2, "accuracy": .5, "invalid_answers": 1}
    assert set(result["categories"]) == {"pref_type", "who", "updated", "conversation_scenario"}
    assert result["categories"]["pref_type"]["conditional"]["quarter"]["accuracy"] == 0


def test_durable_pack_verification_detects_source_and_receipt_changes(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    turns = [SourceTurn("t00000", "", "user", "I like shaded routes only in summer.", ""),
             SourceTurn("t00001", "", "assistant", "The seasonal qualification matters.", "")]
    directory = tmp_path / "pack"
    capture = capture_persona(turns, [("authored", "Which summer route?")], directory, config=raw_config())
    path = directory / "memory.duckdb"
    original_hash = digest(path.read_bytes())
    assert verify_pack(path, turns, capture["captures"]) == original_hash
    assert digest(path.read_bytes()) == original_hash
    changed = deepcopy(capture["captures"])
    changed["authored"]["receipt"]["query"] = "Different request"
    with pytest.raises(ValueError, match="durable receipt"):
        verify_pack(path, turns, changed)
    with duckdb.connect(str(path)) as conn:
        conn.execute("UPDATE events SET content = 'lost qualification' WHERE role = 'user'")
    with pytest.raises(ValueError, match="event fidelity"):
        verify_pack(path, turns, capture["captures"])


def test_full_authored_run_reproduces_contexts_then_scores_all_cases(tmp_path, monkeypatch):
    from benchmarks.diagnostics import personamem_packing as runner
    from benchmarks.diagnostics import verify_personamem as verifier
    from benchmarks.diagnostics.packing_reader import write
    from benchmarks.personamem import cohort_identity

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    data = tmp_path / "data"
    csv_path = data / "benchmark/text/benchmark.csv"
    csv_path.parent.mkdir(parents=True)
    rows = [row(str(owner), f"Which quiet summer route {i} fits?") for owner in range(24) for i in range(4)]
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    csv_path.write_text(stream.getvalue())
    for owner in range(24):
        path = data / f"data/chat_history_32k/chat_persona{owner}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path, {"chat_history": [{"role": "system", "content": "Excluded oracle"},
                                     {"role": "user", "content": "Quiet shaded routes only in summer."},
                                     {"role": "assistant", "content": "The season matters."}]})
    write(data / "dataset-identity.json", {"authored": True})
    _, cohort = cohort_identity(csv_path, data)
    runtime = {"provenance": {"commit": "authored", "engine_config": raw_config().model_dump(mode="json")}}
    reader = {"model": "authored-model"}
    plan = {"registered_at": "2026-01-01", "runtime": runtime, "cohort": cohort,
            "dataset": {"authored": True}, "reader": reader, "embedding": {}, "limits": ["Authored test"]}
    args = SimpleNamespace(plan=tmp_path / "plan.json", data=data, base_url="unused", run_id="authored",
                           artifacts=tmp_path / "artifacts", output=tmp_path / "output.json")
    write(args.plan, plan)
    monkeypatch.setattr(runner, "runtime", lambda: runtime)
    monkeypatch.setattr(runner, "reader_identity", lambda *_: reader)
    async def embedding_identity():
        return {}
    monkeypatch.setattr(runner, "embedding_identity", embedding_identity)
    monkeypatch.setattr(runner, "request", lambda *_: {"model": "authored-model", "done": True, "done_reason": "stop",
        "prompt_eval_count": 100, "eval_count": 7, "message": {"content": '{"answer":"A"}'}})
    runner.run(args)
    output = json.loads(args.output.read_bytes())
    output["process_exit_code"] = 0  # Authored in-process fixture, never a benchmark result.
    write(args.output, output)
    monkeypatch.setattr(verifier, "verify_code", lambda _: None)
    result = verifier.verify(args.plan, args.output, data, args.artifacts)
    assert result["contexts_reproduced"] == 288 and result["raw_responses_verified"] == 384
    assert result["personas"] == 24 and result["questions"] == 96
    assert result["reader_repeats"] == {"total": 24, "disagreements": [], "invalid_answers": 0}
    references = json.loads((args.artifacts / "references.json").read_bytes())
    expected_correct = sum(ref["correct"] == "A" for ref in references)
    assert all(value["correct"] == expected_correct for value in result["comparison"]["arms"].values())
    assert result["comparison"]["primary"]["delta_accuracy"] == 0
    # Even if the top-level checksum is updated, a changed context must not be scored.
    path = args.artifacts / "prepared.json"
    prepared = json.loads(path.read_bytes())
    prepared[0]["contexts"]["quarter"]["context"] = "fabricated evidence"
    write(path, prepared)
    output["prepared_sha256"] = digest(path.read_bytes())
    write(args.output, output)
    with pytest.raises(ValueError, match="Prepared contexts"):
        verifier.verify(args.plan, args.output, data, args.artifacts)

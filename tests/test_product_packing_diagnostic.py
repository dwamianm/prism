"""Product evidence credit requires source text, not merely an included UUID."""
from copy import deepcopy
import hashlib
import json

import pytest

from benchmarks.diagnostics.product_packing import compare, measure
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import compute_str, pack_context


def make_candidate(sid, text, score):
    return RetrievalCandidate(
        node=MemoryNode(user_id="u", node_type="note", content=text, metadata={"source_turn": sid}),
        composite_score=score, path_count=2, paths=["VECTOR", "LEXICAL"],
    )


def fixture_report(tmp_path):
    relevant = make_candidate("s0:t0", "The project uses PostgreSQL. " * 100, .95)
    short = make_candidate("s1:t0", "Thanks.", .5)
    cfg = PackingConfig(token_budget=3000, overhead_tokens=0, min_fidelity="full")
    cfg.token_budget = pack_context([relevant], cfg).tokens_used
    candidates = [relevant, short]
    control = pack_context(candidates, cfg)
    snapshot = {"question_id": "q", "packing_config": cfg.model_dump(mode="json"),
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "control": {"context": control.render(), "tokens": control.tokens_used}}
    filename = hashlib.sha256(b"q").hexdigest() + ".json"
    raw = json.dumps(snapshot).encode()
    (tmp_path / filename).write_bytes(raw)
    return {"complete": True, "errors": 0, "process_exit_code": 0,
            "dataset": {"split": "dev", "selected_question_ids": ["q"]},
            "provenance": {"engine_config": {"packing": cfg.model_dump(mode="json")}},
            "budgets": [cfg.token_budget], "details": [
                {"question_id": "q", "category": "single-session-user", "evidence_source_ids": ["s0:t0"],
                 "candidate_snapshot": {"filename": filename, "sha256": hashlib.sha256(raw).hexdigest()}}]}


def test_frozen_comparison_changes_only_order_and_labels_do_not_control_packing(tmp_path):
    report = fixture_report(tmp_path)
    result = compare(report, tmp_path, samples=20)
    budget = str(report["budgets"][0])
    stats = result["summary"][budget]["evidence_recall"]
    assert stats["before"] == 0 and stats["after"] == 1
    changed = deepcopy(report)
    changed["details"][0]["evidence_source_ids"] = ["s1:t0"]
    other = compare(changed, tmp_path, samples=20)
    for name in ("density", "score"):
        old = result["details"][0]["variants"][name][budget]
        new = other["details"][0]["variants"][name][budget]
        assert old["context_sha256"] == new["context_sha256"]
        assert old["evidence_recall"] != new["evidence_recall"]
    from prme.retrieval import packing
    assert packing.compute_str is compute_str


def test_pointer_only_evidence_is_not_recall_and_unlabeled_is_null():
    source = make_candidate("s0:t0", "Critical evidence with exception. " * 1000, .9)
    cfg = PackingConfig(token_budget=300, overhead_tokens=0, min_fidelity="reference")
    bundle = pack_context([source], cfg)
    measured = measure(bundle, {"s0:t0"}, cfg)
    assert measured["pointer_source_ids"] == ["s0:t0"]
    assert measured["content_source_ids"] == []
    assert measured["evidence_recall"] == 0 and not measured["all_evidence_retained"]
    unlabeled = measure(bundle, set(), cfg)
    assert unlabeled["evidence_recall"] is None and unlabeled["all_evidence_retained"] is None


@pytest.mark.parametrize("mutation", ["exit", "split", "missing", "duplicate", "hash", "context", "tokens", "config"])
def test_invalid_or_unreproducible_captures_are_rejected(tmp_path, mutation):
    report = fixture_report(tmp_path)
    if mutation == "exit":
        report["process_exit_code"] = -6
    elif mutation == "split":
        report["dataset"]["split"] = "test"
    elif mutation == "missing":
        report["dataset"]["selected_question_ids"].append("missing")
    elif mutation == "duplicate":
        report["details"].append(deepcopy(report["details"][0]))
    elif mutation == "hash":
        report["details"][0]["candidate_snapshot"]["sha256"] = "changed"
    else:
        ref = report["details"][0]["candidate_snapshot"]
        path = tmp_path / ref["filename"]
        snapshot = json.loads(path.read_bytes())
        if mutation == "context":
            snapshot["control"]["context"] = "changed"
        elif mutation == "tokens":
            snapshot["control"]["tokens"] += 1
        else:
            snapshot["packing_config"]["overhead_tokens"] += 1
        raw = json.dumps(snapshot).encode()
        path.write_bytes(raw)
        ref["sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        compare(report, tmp_path, samples=20)


async def test_public_capture_reproduces_after_engine_close_and_excludes_labels(tmp_path, monkeypatch):
    from benchmarks.retrieval_eval import evaluate_question
    from prme import PRMEConfig
    from tests.test_evidence_evaluation import question
    from tests.test_durable_ingestion import MockEmbeddingProvider

    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    config = PRMEConfig(enable_qa_pairing=False, organizer={"opportunistic_enabled": False})
    row = await evaluate_question(question(), config, budgets=[1000], count_tokens=len, k=10,
                                  capture_candidates=tmp_path)
    raw = (tmp_path / row["candidate_snapshot"]["filename"]).read_bytes()
    assert b"secret-answer-label" not in raw and b"answer-labelled-session" not in raw
    assert b"has_answer" not in raw and b"answer_session_ids" not in raw
    report = {"complete": True, "errors": 0, "process_exit_code": 0,
              "dataset": {"split": "dev", "selected_question_ids": [row["question_id"]]},
              "provenance": {"engine_config": config.model_dump(mode="json")},
              "budgets": [1000], "details": [row]}
    result = compare(report, tmp_path, samples=20)
    assert result["baseline_reproduction_passed"]
    assert result["summary"]["1000"]["evidence_recall"]["before"] == 1


@pytest.mark.parametrize("content", ["", " \n"])
def test_blank_sources_are_accounted_without_positive_evidence_credit(content):
    source = make_candidate("blank", content, .9)
    cfg = PackingConfig(token_budget=1000, overhead_tokens=0)
    result = measure(pack_context([source], cfg), {"blank"}, cfg)
    assert result["blank_source_ids"] == ["blank"]
    assert result["content_source_ids"] == [] and result["pointer_source_ids"] == []
    assert result["evidence_recall"] == 0


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_separators_inside_source_are_not_json_record_boundaries(separator):
    source = make_candidate("unicode", "The source before" + separator + "and after.", .9)
    cfg = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity="full")
    result = measure(pack_context([source], cfg), {"unicode"}, cfg)
    assert result["content_source_ids"] == ["unicode"]
    assert result["evidence_recall"] == 1

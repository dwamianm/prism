"""Research gates must not turn incomplete or degraded runs into results."""
import asyncio
import json
import logging

import httpx
import pytest

from benchmarks.diagnostics import run_opt_in_interactions as study
from benchmarks.diagnostics.register_opt_in_interactions import clean_config, arms, write_new


@pytest.mark.parametrize("rows", [
    [{"question_id": "a", "status": "complete"}],
    [{"question_id": "a", "status": "complete"}, {"question_id": "a", "status": "complete"}],
    [{"question_id": "b", "status": "complete"}, {"question_id": "a", "status": "complete"}],
    [{"question_id": "a", "status": "complete"}, {"question_id": "b", "status": "failed"}],
])
def test_incomplete_or_replaced_cohort_cannot_be_scored(rows):
    with pytest.raises(study.ResearchFailure):
        study.validate_complete(["a", "b"], rows)


def test_unrelated_environment_cannot_enable_a_baseline_feature(monkeypatch):
    monkeypatch.setenv("PRME_ENABLE_QA_PAIRING", "true")
    monkeypatch.setenv("PRME_PACKING__EVIDENCE_AUGMENTATION_TOP_K", "10")
    matrix = {row["id"]: row for row in arms(clean_config())}
    assert matrix["baseline"]["config"]["enable_qa_pairing"] is False
    assert matrix["baseline"]["config"]["packing"]["evidence_augmentation_top_k"] == 0
    assert matrix["qa_balanced"]["config_sha256"] == matrix["qa_pairing"]["config_sha256"]
    assert matrix["full_feature_exploratory"]["config"]["packing"]["evidence_projection_top_k"] == 0


def test_failed_artifact_cannot_be_replaced(tmp_path):
    path = tmp_path / "result.json"
    write_new(path, {"status": "failed"})
    with pytest.raises(FileExistsError):
        write_new(path, {"status": "complete"})
    assert json.loads(path.read_text()) == {"status": "failed"}


def test_swallowed_feature_exception_is_still_a_research_failure():
    rows = []
    token = study.ERRORS.set(rows)
    try:
        try:
            raise TimeoutError("hidden provider details")
        except TimeoutError:
            import sys
            record = logging.LogRecord("prme.retrieval.reformulation", logging.WARNING,
                                       __file__, 1, "Query reformulation failed", (), sys.exc_info())
            study.FailureObserver().emit(record)
    finally:
        study.ERRORS.reset(token)
    assert rows[0]["exception_type"] == "TimeoutError"
    assert "hidden provider details" not in json.dumps(rows)


async def test_truncated_provider_output_is_retained_and_rejected(tmp_path):
    def respond(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": study.MODEL, "digest": study.DIGEST}]})
        return httpx.Response(200, json={"model": study.MODEL, "done": True, "done_reason": "length",
            "message": {"content": "yes"}, "prompt_eval_count": 10, "eval_count": 64})
    path = tmp_path / "judge.json"
    async with httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(study.ResearchFailure):
            await study.chat(client, asyncio.Semaphore(1), "authored control", 64, path)
    saved = json.loads(path.read_text())
    assert saved["status"] == "failed"
    assert saved["response"]["message"]["content"] == "yes"
    assert len(saved["attempts"]) == 1

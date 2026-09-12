"""Evaluation failures must remain visible without becoming accuracy scores."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks import llm_judge
from benchmarks.llm_judge import JUDGE_ERROR, LLMJudgeConfig
from benchmarks.locomo import LoCoMoRealBenchmark
from benchmarks.longmemeval import LongMemEvalRealBenchmark
from benchmarks.models import BenchmarkResult, QueryResult
from benchmarks.report import generate_json_report, print_summary


def make_result(name="test", scores=(1.0,)):
    details = [
        QueryResult(str(i), "test", "answer", "evidence", score == 1.0, score,
                    judge_error=llm_judge.is_judge_error(score))
        for i, score in enumerate(scores)
    ]
    scored = [d for d in details if not d.judge_error]
    return BenchmarkResult(
        name, sum(d.score for d in scored) / len(scored) if scored else 0.0,
        {}, len(details), sum(d.correct for d in scored),
        sum(not d.correct for d in scored), 0, 0.0, details,
    )


@pytest.fixture
def fake_engine(monkeypatch):
    from prme.storage.engine import MemoryEngine

    engine = SimpleNamespace(
        store=AsyncMock(), close=AsyncMock(),
        retrieve=AsyncMock(side_effect=[RuntimeError("private detail"), SimpleNamespace(results=[], bundle=SimpleNamespace(render=lambda: "related evidence"))]),
    )
    monkeypatch.setattr(MemoryEngine, "create", AsyncMock(return_value=engine))
    monkeypatch.setattr(llm_judge, "generate_answer", AsyncMock(return_value="oboe"))
    monkeypatch.setattr(llm_judge, "judge_answer", AsyncMock(return_value=1.0))
    return engine


@pytest.mark.parametrize("adapter", ["locomo", "lme-keyword", "lme-llm"])
async def test_question_exception_preserves_denominator_and_retry_identity(tmp_path, fake_engine, adapter):
    path = tmp_path / "dataset.json"
    questions = [{"question": f"Question {i}", "answer": "oboe", "category": 1}
                 for i in range(2)]
    if adapter == "locomo":
        path.write_text(json.dumps([{
            "sample_id": "test", "qa": questions,
            "conversation": {"session_1": [{"speaker": "Alice", "text": "Alice plays the oboe."}]},
        }]))
        result = await LoCoMoRealBenchmark(dataset_path=str(path)).run_with_llm(
            fake_engine, LLMJudgeConfig(enabled=True),
        )
    else:
        path.write_text(json.dumps([
            {**q, "question_id": str(i), "question_type": "single-session-user",
             "haystack_sessions": [[{"role": "user", "content": "Alice plays the oboe."}]]}
            for i, q in enumerate(questions)
        ]))
        bench = LongMemEvalRealBenchmark(dataset_path=str(path))
        if adapter == "lme-llm":
            result = await bench.run_with_llm(fake_engine, LLMJudgeConfig(enabled=True))
        else:
            result = await bench.run(fake_engine)

    assert result.total_queries == 2
    assert [d.query for d in result.details] == [q["question"] for q in questions]
    assert result.details[0].judge_error
    assert "private detail" not in result.details[0].actual
    assert result.correct + result.incorrect == 1
    data = result.to_dict()
    assert data["error_count"] == 1
    assert data["scored_queries"] == 1
    assert data["coverage"] == 0.5
    assert data["complete"] is False
    assert data["details"][0]["score"] is None


def test_summary_weights_only_measured_questions_and_exposes_coverage(capsys):
    results = [make_result("partial", (1.0, JUDGE_ERROR, JUDGE_ERROR)),
               make_result("complete", (0.0,))]
    summary = json.loads(generate_json_report(results))["summary"]
    assert summary["total_queries"] == 4
    assert summary["scored_queries"] == 2
    assert summary["error_count"] == 2
    assert summary["coverage"] == 0.5
    assert summary["overall_score"] == 0.5
    assert summary["complete"] is False
    print_summary(results)
    output = capsys.readouterr().out
    assert "Evaluation errors" in output
    assert "Coverage" in output
    assert "Failed queries (3" not in output


@pytest.mark.parametrize("parallel", [True, False])
async def test_whole_benchmark_failure_is_not_an_empty_success(monkeypatch, parallel):
    from benchmarks import runner

    monkeypatch.setattr(runner, "_run_single_benchmark", AsyncMock(side_effect=FileNotFoundError("dataset")))
    results = await runner.BenchmarkRunner().run(["locomo", "longmemeval"], parallel=parallel)
    for result in results:
        data = result.to_dict()
        assert data["benchmark_error"] == "FileNotFoundError"
        assert data["complete"] is False
    summary = json.loads(generate_json_report(results))["summary"]
    assert summary["failed_benchmarks"] == 2
    assert summary["complete"] is False


@pytest.mark.parametrize("failed_first", [True, False])
async def test_cli_rejects_incomplete_run_even_when_later_run_succeeds(monkeypatch, failed_first):
    from benchmarks import __main__ as cli

    incomplete = make_result(scores=(1.0, JUDGE_ERROR))
    runs = [[incomplete], [make_result()]] if failed_first else [[incomplete]]
    monkeypatch.setattr(cli.BenchmarkRunner, "run", AsyncMock(side_effect=runs))
    assert await cli._main(["locomo", "--runs", str(len(runs)), "--quiet"]) == 1


async def test_cli_rejects_whole_benchmark_failure(monkeypatch):
    from benchmarks import __main__ as cli, runner

    monkeypatch.setattr(runner, "_run_single_benchmark", AsyncMock(side_effect=FileNotFoundError()))
    assert await cli._main(["locomo", "--quiet"]) == 1


async def test_abstention_provider_failure_is_not_a_verdict(monkeypatch):
    from prme.retrieval import abstention

    client = SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("provider down")))
    monkeypatch.setattr(abstention, "_get_client", lambda _: client)
    # Applications keep the documented fallback; benchmark measurement is strict.
    assert await abstention.should_abstain("q", "context") is False
    with pytest.raises(RuntimeError):
        await llm_judge.check_abstention("q", "context", LLMJudgeConfig())


async def test_longmemeval_records_failed_abstention_check(tmp_path, fake_engine, monkeypatch):
    fake_engine.retrieve.side_effect = None
    fake_engine.retrieve.return_value = SimpleNamespace(
        results=[SimpleNamespace(composite_score=0.9)], bundle=SimpleNamespace(render=lambda: "related evidence"),
    )
    monkeypatch.setattr(llm_judge, "check_abstention", AsyncMock(side_effect=RuntimeError("outage")))
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps([{
        "question_id": "question_abs", "question_type": "single-session-user",
        "question": "What unknown instrument?", "answer": "", "haystack_sessions": [],
    }]))
    result = await LongMemEvalRealBenchmark(dataset_path=str(path)).run_with_llm(
        fake_engine, LLMJudgeConfig(enabled=True),
    )
    assert result.total_queries == 1
    assert result.correct == result.incorrect == result.abstained == 0
    assert result.category_scores == {}
    assert result.details[0].category == "abstention"
    llm_judge.check_abstention.assert_awaited_once_with("What unknown instrument?", "related evidence", LLMJudgeConfig(enabled=True))
    assert result.details[0].expected == "ABSTAIN"
    assert result.error_count == 1
    assert result.coverage == 0.0
    assert result.complete is False


async def test_cli_accepts_a_fully_measured_run(monkeypatch):
    from benchmarks import __main__ as cli

    monkeypatch.setattr(cli.BenchmarkRunner, "run", AsyncMock(return_value=[make_result()]))
    assert await cli._main(["locomo", "--quiet"]) == 0

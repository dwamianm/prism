"""Benchmark annotations must not become retrievable answer evidence."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from benchmarks import llm_judge
from benchmarks.llm_judge import LLMJudgeConfig
from benchmarks.locomo import LoCoMoRealBenchmark
from benchmarks.longmemeval import LongMemEvalRealBenchmark


@pytest.fixture
def benchmark_engine(monkeypatch):
    engine = SimpleNamespace(
        store=AsyncMock(),
        retrieve=AsyncMock(return_value=SimpleNamespace(results=[], bundle=SimpleNamespace(render=lambda: ""))),
        consolidate_knowledge=AsyncMock(return_value=0),
        close=AsyncMock(),
    )
    monkeypatch.setattr(llm_judge, "generate_answer", AsyncMock(return_value="I don't know"))
    monkeypatch.setattr(llm_judge, "judge_answer", AsyncMock(return_value=0.0))
    monkeypatch.setattr(
        llm_judge, "reformulate_query",
        AsyncMock(side_effect=AssertionError("Use product retrieval, not harness expansion")),
    )
    return engine


@pytest.mark.parametrize("with_llm", [False, True])
async def test_locomo_does_not_ingest_answer_annotations(tmp_path, benchmark_engine, with_llm):
    question = "What instrument does Alice play?"
    path = tmp_path / "locomo.json"
    path.write_text(json.dumps([{
        "sample_id": "test-conversation",
        "conversation": {
            "session_1": [{"speaker": "Alice", "text": "I enjoyed a quiet afternoon today."}],
        },
        "observation": {"session_1": {"Alice": [["Alice plays the secret instrument: oboe."]]}},
        "qa": [{"question": question, "answer": "oboe", "category": 1}],
    }]))
    benchmark = LoCoMoRealBenchmark(dataset_path=str(path))
    if with_llm:
        result = await benchmark.run_with_llm(benchmark_engine, LLMJudgeConfig(enabled=True))
    else:
        result = await benchmark.run(benchmark_engine)

    stored = [call.args[0] for call in benchmark_engine.store.await_args_list]
    assert stored == ["Alice: I enjoyed a quiet afternoon today."]
    assert result.total_queries == 1
    assert result.correct == 0
    benchmark_engine.retrieve.assert_awaited_once_with(
        question, user_id="bench-locomo-real-test-conversation",
    )


async def test_longmemeval_uses_one_public_retrieval(tmp_path, benchmark_engine, monkeypatch):
    from prme.storage.engine import MemoryEngine

    monkeypatch.setattr(MemoryEngine, "create", AsyncMock(return_value=benchmark_engine))
    question = "What instrument does Alice play?"
    path = tmp_path / "longmemeval.json"
    path.write_text(json.dumps([{
        "question_id": "test-question",
        "question_type": "single-session-user",
        "question": question,
        "answer": "oboe",
        "haystack_sessions": [[{"role": "user", "content": "Alice plays the oboe."}]],
    }]))
    benchmark = LongMemEvalRealBenchmark(dataset_path=str(path))
    result = await benchmark.run_with_llm(benchmark_engine, LLMJudgeConfig(enabled=True))
    assert result.total_queries == 1
    benchmark_engine.retrieve.assert_awaited_once_with(question, user_id="bench-lme-real")
    benchmark_engine.close.assert_awaited_once()


@pytest.mark.parametrize("abstention", [False, True])
async def test_longmemeval_judge_consumes_product_bundle_and_question_clock(
    tmp_path, benchmark_engine, monkeypatch, abstention,
):
    from datetime import datetime, timezone
    from prme.storage.engine import MemoryEngine

    benchmark_engine.retrieve.return_value = SimpleNamespace(
        results=[SimpleNamespace(composite_score=.2, node=SimpleNamespace(content="RAW-NOT-PACKED"))],
        bundle=SimpleNamespace(render=lambda: "PRODUCT-PACKED-CONTEXT"),
    )
    monkeypatch.setattr(MemoryEngine, "create", AsyncMock(return_value=benchmark_engine))
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(llm_judge, "check_abstention", check)
    path = tmp_path / "longmemeval.json"
    path.write_text(json.dumps([{
        "question_id": "example_abs" if abstention else "example",
        "question_type": "single-session-user", "question": "Which database?",
        "question_date": "2024/01/01 (Mon) 00:00", "answer": "PostgreSQL",
        "haystack_sessions": [[{"role": "user", "content": "A source turn."}]],
    }]))
    config = LLMJudgeConfig(enabled=True)
    result = await LongMemEvalRealBenchmark(dataset_path=str(path)).run_with_llm(benchmark_engine, config)
    assert result.error_count == 0
    benchmark_engine.retrieve.assert_awaited_once_with(
        "Which database?", user_id="bench-lme-real", reference_time=datetime(2024, 1, 1, tzinfo=timezone.utc))
    if abstention:
        check.assert_awaited_once_with("Which database?", "PRODUCT-PACKED-CONTEXT", config)
    else:
        llm_judge.generate_answer.assert_awaited_once_with("Which database?", "PRODUCT-PACKED-CONTEXT", config)


async def test_longmemeval_invalid_question_date_is_an_error_before_engine_or_model(
    tmp_path, benchmark_engine, monkeypatch,
):
    from prme.storage.engine import MemoryEngine
    create = AsyncMock(return_value=benchmark_engine)
    monkeypatch.setattr(MemoryEngine, "create", create)
    path = tmp_path / "longmemeval.json"
    path.write_text(json.dumps([{
        "question_id": "bad-date", "question_type": "temporal-reasoning", "question": "When?",
        "question_date": "not-a-date", "answer": "yesterday", "haystack_sessions": [],
    }]))
    result = await LongMemEvalRealBenchmark(dataset_path=str(path)).run_with_llm(
        benchmark_engine, LLMJudgeConfig(enabled=True))
    assert result.error_count == 1 and not result.complete
    create.assert_not_awaited()
    llm_judge.generate_answer.assert_not_awaited()


async def test_locomo_judge_consumes_exact_product_bundle(tmp_path, benchmark_engine):
    from prme.models import MemoryNode
    from prme.retrieval.models import RetrievalCandidate

    benchmark_engine.retrieve.return_value = SimpleNamespace(
        results=[RetrievalCandidate(node=MemoryNode(user_id="u", node_type="note", content="RAW-NOT-PACKED"), composite_score=.9)],
        bundle=SimpleNamespace(render=lambda: "PRODUCT-PACKED-CONTEXT"),
    )
    path = tmp_path / "locomo.json"
    path.write_text(json.dumps([{
        "sample_id": "bundle-case",
        "conversation": {"session_1": [{"speaker": "Alice", "text": "I play the oboe."}]},
        "qa": [{"question": "Which instrument?", "answer": "oboe", "category": 1}],
    }]))
    config = LLMJudgeConfig(enabled=True)
    result = await LoCoMoRealBenchmark(dataset_path=str(path)).run_with_llm(benchmark_engine, config)
    assert result.error_count == 0
    benchmark_engine.retrieve.assert_awaited_once_with("Which instrument?", user_id="bench-locomo-real-bundle-case")
    llm_judge.generate_answer.assert_awaited_once_with("Which instrument?", "PRODUCT-PACKED-CONTEXT", config)
    assert result.details[0].actual == "PRODUCT-PACKED-CONTEXT"

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
        retrieve=AsyncMock(return_value=SimpleNamespace(results=[])),
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

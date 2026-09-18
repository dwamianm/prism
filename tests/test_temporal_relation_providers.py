from datetime import datetime, timezone
import json
from uuid import UUID

import httpx
import pytest

from prme.retrieval.temporal_relation_providers import (
    JevTemporalRelationGate,
    OllamaTemporalResolver,
    TemporalRelationProviderError,
)
from prme.retrieval.temporal_relations import (
    CONFIRMED_RESOLVER_MODEL_DIGEST,
    EvidenceRecord,
    RawOperand,
    RawResolution,
    TemporalRelationConfig,
    compute_relation,
)


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def _records() -> tuple[EvidenceRecord, ...]:
    return (
        EvidenceRecord(
            id=UUID(int=1),
            event_time=datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
            text="I finished the frame today.",
        ),
        EvidenceRecord(
            id=UUID(int=2),
            event_time=datetime(2026, 8, 8, 9, tzinfo=timezone.utc),
            text="I hung the frame today.",
        ),
    )


def _resolution() -> RawResolution:
    return RawResolution(
        operation="elapsed_between",
        operands=[
            RawOperand(
                name="frame completion",
                evidence_id=UUID(int=1),
                quote="finished the frame today",
                time_expression="event_time",
                time_basis="event_time",
            ),
            RawOperand(
                name="frame hanging",
                evidence_id=UUID(int=2),
                quote="hung the frame today",
                time_expression="event_time",
                time_basis="event_time",
            ),
        ],
    )


def _ollama_response(content: str) -> dict:
    return {
        "model": "deepseek-v4.1-flash",
        "done": True,
        "done_reason": "stop",
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": 150,
        "eval_count": 30,
    }


def _identity_response(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == "/api/tags":
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "deepseek-v4.1-flash:cloud",
                        "digest": CONFIRMED_RESOLVER_MODEL_DIGEST,
                    }
                ]
            },
        )
    if request.url.path == "/api/version":
        return httpx.Response(200, json={"version": "0.34.2"})
    return None


@pytest.mark.asyncio
async def test_ollama_resolver_uses_answer_blind_contract() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        identity = _identity_response(request)
        if identity is not None:
            return identity
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json=_ollama_response(_resolution().model_dump_json()),
        )

    resolver = OllamaTemporalResolver(
        TemporalRelationConfig(enabled=True),
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve("How long passed?", NOW, _records())

    assert result.resolution == _resolution()
    assert result.audit.schema_repairs == 0
    assert result.audit.attempts == 1
    assert result.audit.model_digest == CONFIRMED_RESOLVER_MODEL_DIGEST
    assert result.audit.provider_version == "0.34.2"
    assert result.audit.input_tokens == 150
    assert seen[0]["format"] == "json"
    assert seen[0]["think"] is False
    system = seen[0]["messages"][0]["content"]
    assert "Do not answer the question" in system
    assert "do not calculate dates" in system
    state = json.loads(seen[0]["messages"][1]["content"])
    assert "answer" not in state
    assert state["question_date"] == "2026/09/18 12:00"


@pytest.mark.asyncio
async def test_ollama_resolver_repairs_schema_once() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        identity = _identity_response(request)
        if identity is not None:
            return identity
        body = json.loads(request.content)
        seen.append(body)
        content = (
            '{"operation":"order","operands":[{"evidence_id":"invented"}]}'
            if len(seen) == 1
            else _resolution().model_dump_json()
        )
        return httpx.Response(200, json=_ollama_response(content))

    resolver = OllamaTemporalResolver(
        TemporalRelationConfig(enabled=True),
        transport=httpx.MockTransport(handler),
    )
    result = await resolver.resolve("How long passed?", NOW, _records())

    assert result.audit.schema_repairs == 1
    assert result.audit.attempts == 2
    assert len(seen) == 2
    assert seen[1]["format"] == RawResolution.model_json_schema()
    assert seen[1]["messages"][-2]["role"] == "assistant"
    assert "did not match the required schema" in seen[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_ollama_resolver_rejects_changed_model_before_generation() -> None:
    chat_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_called
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "deepseek-v4.1-flash:cloud",
                            "digest": "0" * 64,
                        }
                    ]
                },
            )
        chat_called = True
        raise AssertionError("generation must not run with a changed model")

    resolver = OllamaTemporalResolver(
        TemporalRelationConfig(enabled=True),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(TemporalRelationProviderError, match="digest changed"):
        await resolver.resolve("How long passed?", NOW, _records())
    assert chat_called is False


def _relation():
    relation, errors = compute_relation(
        _resolution(), {record.id: record for record in _records()}, NOW
    )
    assert not errors and relation is not None
    return relation


@pytest.mark.asyncio
async def test_jev_gate_validates_all_operands_without_exposing_key() -> None:
    seen: list[tuple[dict, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((body, request.headers.get("authorization")))
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {
                    "operand_0": {"type": "noul", "noul": 0.91},
                    "operand_1": {"type": "noul", "noul": 0.87},
                },
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        )

    config = TemporalRelationConfig(enabled=True, gate_api_key="top-secret")
    gate = JevTemporalRelationGate(config, transport=httpx.MockTransport(handler))
    audit = await gate.assess("How long passed?", _relation())

    assert audit.probabilities == (0.91, 0.87)
    assert audit.minimum_probability == 0.87
    assert seen[0][1] == "Bearer top-secret"
    assert set(seen[0][0]["questions"]) == {"operand_0", "operand_1"}
    assert "top-secret" not in audit.model_dump_json()
    assert "top-secret" not in config.configuration_sha256


@pytest.mark.asyncio
async def test_jev_gate_fails_closed_on_incomplete_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"operand_0": {"type": "noul", "noul": 0.99}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    config = TemporalRelationConfig(enabled=True, gate_api_key="key")
    gate = JevTemporalRelationGate(config, transport=httpx.MockTransport(handler))

    with pytest.raises(TemporalRelationProviderError, match="answer set"):
        await gate.assess("How long passed?", _relation())

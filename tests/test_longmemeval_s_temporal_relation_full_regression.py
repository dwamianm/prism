from datetime import datetime, timezone
from uuid import uuid4

from benchmarks.diagnostics.longmemeval_s_temporal_relation_full_regression import (
    _ReplayOrUnsupportedResolver,
    _summary,
)
from prme.retrieval.temporal_relations import EvidenceRecord, TemporalRelationConfig


async def test_inert_full_regression_resolver_records_cost_without_model_calls() -> None:
    config = TemporalRelationConfig(enabled=True, gate_api_key="offline")
    resolver = _ReplayOrUnsupportedResolver(
        question_id="case",
        config=config,
        row=None,
        source="test",
    )
    result = await resolver.resolve(
        "How many days did it take?",
        datetime(2026, 9, 18, tzinfo=timezone.utc),
        (
            EvidenceRecord(
                id=uuid4(),
                event_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
                text="The package arrived today.",
            ),
        ),
    )

    assert result.resolution.operation == "unsupported"
    assert result.resolution.operands == []
    assert resolver.calls == 1
    assert resolver.request_bytes > 0
    assert resolver.prompt_tokens_estimate > 0
    assert resolver.record_count == 1
    assert resolver.record_characters == len("The package arrived today.")


def test_full_regression_summary_separates_routing_and_changes() -> None:
    base = {
        "question_type": "temporal-reasoning",
        "abstention": False,
        "frozen_source": "confirmation",
        "gate_called": False,
        "expected_accepted": False,
        "context_sha256": "a" * 64,
        "context_tokens": 10,
        "receipt_persisted": True,
        "confirmation_protocol_aligned": None,
        "resolver_request_bytes": 0,
        "resolver_prompt_cl100k_tokens_estimate": 0,
        "resolver_record_count": 0,
        "resolver_record_characters": 0,
        "gate_request_bytes": 0,
        "retrieval_seconds": 0.1,
        "operation": None,
        "clone_method": "clonefile",
    }
    rows = [
        {
            **base,
            "question_id": "a",
            "status": "not_routed",
            "resolver_called": False,
            "context_changed": False,
            "broad_context_route": True,
        },
        {
            **base,
            "question_id": "b",
            "status": "accepted",
            "resolver_called": True,
            "gate_called": True,
            "expected_accepted": True,
            "context_changed": True,
            "confirmation_protocol_aligned": True,
            "broad_context_route": True,
            "resolver_request_bytes": 100,
            "resolver_prompt_cl100k_tokens_estimate": 20,
            "gate_request_bytes": 30,
        },
    ]

    summary = _summary(rows)

    assert summary["questions"] == 2
    assert summary["routing"] == {
        "resolver_calls": 1,
        "gate_calls": 1,
        "not_routed": 1,
        "broad_context_route_calls": 2,
        "avoided_vs_broad_context_route": 1,
    }
    assert summary["contexts_changed"] == 1
    assert summary["resolver_payload"]["total_request_bytes"] == 100
    assert summary["gate_payload"]["total_request_bytes"] == 30

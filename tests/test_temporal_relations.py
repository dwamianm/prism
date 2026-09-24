from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from uuid import UUID

import pytest

from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.temporal_relations import (
    EvidenceRecord,
    GateAudit,
    RawOperand,
    RawResolution,
    ResolverAudit,
    ResolverResult,
    TemporalRelationConfig,
    TemporalRelationEnricher,
    compute_relation,
)
from prme.types import NodeType, RepresentationLevel


NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _record(number: int, text: str, when: datetime = NOW) -> EvidenceRecord:
    return EvidenceRecord(id=UUID(int=number), event_time=when, text=text)


def _operand(
    number: int,
    name: str,
    quote: str,
    expression: str = "event_time",
    basis: str = "event_time",
) -> RawOperand:
    return RawOperand(
        name=name,
        evidence_id=UUID(int=number),
        quote=quote,
        time_expression=expression,
        time_basis=basis,
    )


def test_relation_requires_verbatim_bundle_local_evidence() -> None:
    records = {UUID(int=1): _record(1, "I finished the repair today.")}
    relation, errors = compute_relation(
        RawResolution(
            operation="elapsed_since_question",
            operands=[_operand(1, "repair", "I finished it today.")],
        ),
        records,
        NOW,
    )

    assert relation is None
    assert errors == ("operand[0] quote is not verbatim",)


def test_relation_rejects_invented_order_for_equal_dates() -> None:
    records = {
        UUID(int=1): _record(1, "I ordered the lamp today."),
        UUID(int=2): _record(2, "The lamp arrived today."),
    }
    relation, errors = compute_relation(
        RawResolution(
            operation="order",
            operands=[
                _operand(1, "order", "ordered the lamp today"),
                _operand(2, "arrival", "lamp arrived today"),
            ],
        ),
        records,
        NOW,
    )

    assert relation is None
    assert errors == ("order is unresolved for events on the same date",)


def test_duration_arithmetic_uses_exact_decimal_values() -> None:
    records = {
        UUID(int=1): _record(1, "The first stage took 0.1 hours."),
        UUID(int=2): _record(2, "The second stage took 0.2 hours."),
    }
    relation, errors = compute_relation(
        RawResolution(
            operation="duration_from_text",
            operands=[
                _operand(1, "first", "0.1 hours", "0.1 hours", "duration"),
                _operand(2, "second", "0.2 hours", "0.2 hours", "duration"),
            ],
        ),
        records,
        NOW,
    )

    assert errors == ()
    assert relation is not None
    assert relation.value == "0.3 hours"
    assert relation.operands[0].duration_value == Decimal("0.1")


def _candidate(number: int, text: str, when: datetime) -> RetrievalCandidate:
    return RetrievalCandidate(
        node=MemoryNode(
            id=UUID(int=number),
            user_id="u",
            content=text,
            node_type=NodeType.NOTE,
            event_time=when,
            created_at=when,
        ),
        composite_score=1 - number / 100,
        path_count=2,
        paths=["VECTOR", "LEXICAL"],
    )


def _resolver_result(resolution: RawResolution) -> ResolverResult:
    return ResolverResult(
        resolution=resolution,
        audit=ResolverAudit(
            provider="fake",
            model="fake",
            request_sha256=_hash("request"),
            response_sha256=_hash("response"),
            attempts=1,
            schema_repairs=0,
            elapsed_ms=1,
        ),
    )


class _Resolver:
    def __init__(self, result: ResolverResult) -> None:
        self.result = result

    async def resolve(self, query, question_time, records):
        assert query
        assert question_time == NOW
        assert len(records) == 3
        return self.result


class _Gate:
    def __init__(self, probability: float) -> None:
        self.probability = probability

    async def assess(self, query, relation):
        assert query
        return GateAudit(
            provider="fake",
            model="fake",
            request_sha256=_hash("gate-request"),
            response_sha256=_hash("gate-response"),
            probabilities=(self.probability, self.probability),
            minimum_probability=self.probability,
            attempts=1,
            elapsed_ms=1,
        )


def _control() -> tuple[PackingConfig, MemoryBundle]:
    first = _candidate(
        1,
        "I finished the frame today. " + "frame context " * 30,
        datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
    )
    second = _candidate(
        2,
        "I hung the frame today. " + "hanging context " * 30,
        datetime(2026, 8, 8, 9, tzinfo=timezone.utc),
    )
    uncited = _candidate(3, "Unrelated detail. " * 100, NOW)
    roomy = PackingConfig(
        token_budget=10000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
        context_guidance_mode="off",
    )
    full = pack_context([first, second, uncited], roomy)
    config = roomy.model_copy(update={"token_budget": full.tokens_used})
    return config, pack_context([first, second, uncited], config)


@pytest.mark.asyncio
async def test_enricher_accepts_gated_relation_and_preserves_cited_records() -> None:
    config, control = _control()
    resolution = RawResolution(
        operation="elapsed_between",
        operands=[
            _operand(1, "frame completion", "finished the frame today"),
            _operand(2, "frame hanging", "hung the frame today"),
        ],
    )
    enricher = TemporalRelationEnricher(
        TemporalRelationConfig(enabled=True),
        _Resolver(_resolver_result(resolution)),
        _Gate(0.91),
    )

    result, metadata = await enricher.enrich(
        "How long passed between finishing and hanging the frame?",
        control,
        question_time=NOW,
        packing_config=config,
    )

    result_ids = {
        item.node.id for values in result.sections.values() for item in values
    }
    assert metadata.status == "accepted"
    assert metadata.value == "7 days"
    assert set(metadata.evidence_ids) == {UUID(int=1), UUID(int=2)}
    assert {UUID(int=1), UUID(int=2)} <= result_ids
    assert result.tokens_used <= result.token_budget == control.token_budget
    assert "Deterministic calendar-date difference: 7 days" in result.render()
    assert metadata.result_context_sha256 != metadata.control_context_sha256
    assert not set(metadata.dropped_record_ids) & set(metadata.evidence_ids)


@pytest.mark.asyncio
async def test_gate_rejection_returns_byte_identical_control() -> None:
    config, control = _control()
    resolution = RawResolution(
        operation="elapsed_between",
        operands=[
            _operand(1, "frame completion", "finished the frame today"),
            _operand(2, "frame hanging", "hung the frame today"),
        ],
    )
    enricher = TemporalRelationEnricher(
        TemporalRelationConfig(enabled=True),
        _Resolver(_resolver_result(resolution)),
        _Gate(0.84),
    )

    result, metadata = await enricher.enrich(
        "How long passed?", control, question_time=NOW, packing_config=config
    )

    assert metadata.status == "gate_rejected"
    assert result.render() == control.render()
    assert metadata.result_context_sha256 == metadata.control_context_sha256


@pytest.mark.parametrize("context_format", ["auditable", "reader"])
@pytest.mark.asyncio
async def test_repacking_retains_prior_exclusions_without_mutating_control(
    context_format,
) -> None:
    config, original = _control()
    config = config.model_copy(update={"context_format": context_format})
    candidates = [item for values in original.sections.values() for item in values]
    candidates += [
        _candidate(4, " ", NOW),
        _candidate(5, "Other frame detail. " * 10000, NOW),
    ]
    control = pack_context(candidates, config)
    if context_format == "reader":
        assert UUID(int=4) in control.excluded_ids
    assert UUID(int=5) in control.excluded_ids
    expected_prior = list(control.excluded_ids)
    control.excluded_ids += control.excluded_ids[:1]
    before = control.model_dump(mode="json")
    resolution = RawResolution(
        operation="elapsed_between",
        operands=[
            _operand(1, "frame completion", "finished the frame today"),
            _operand(2, "frame hanging", "hung the frame today"),
        ],
    )
    enricher = TemporalRelationEnricher(
        TemporalRelationConfig(enabled=True),
        _Resolver(_resolver_result(resolution)),
        _Gate(0.91),
    )
    result, metadata = await enricher.enrich(
        "How long passed between finishing and hanging the frame?",
        control,
        question_time=NOW,
        packing_config=config,
    )
    assert metadata.status == "accepted"
    assert result.excluded_ids[: len(expected_prior)] == expected_prior
    assert set(metadata.dropped_record_ids) <= set(result.excluded_ids)
    assert len(result.excluded_ids) == len(set(result.excluded_ids))
    assert control.model_dump(mode="json") == before
    assert result.tokens_used <= result.token_budget == control.token_budget


@pytest.mark.asyncio
async def test_provider_failure_is_explicit_and_falls_back() -> None:
    config, control = _control()

    class FailingResolver:
        async def resolve(self, query, question_time, records):
            raise TimeoutError("provider details are not public metadata")

    enricher = TemporalRelationEnricher(
        TemporalRelationConfig(enabled=True), FailingResolver(), _Gate(1)
    )
    result, metadata = await enricher.enrich(
        "When?", control, question_time=NOW, packing_config=config
    )

    assert result.render() == control.render()
    assert metadata.status == "provider_error"
    assert metadata.error_stage == "resolver"
    assert metadata.error_type == "TimeoutError"

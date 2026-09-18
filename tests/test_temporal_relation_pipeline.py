from datetime import datetime, timezone
import hashlib

from prme import MemoryEngine, PRMEConfig
from prme.retrieval.context_formatter import (
    _detect_context_type,
    is_temporal_reasoning_query,
)
from prme.retrieval.query_analysis import analyze_query
from prme.retrieval.temporal_relation_models import GateAudit, ResolverAudit
from prme.retrieval.temporal_relations import (
    RawOperand,
    RawResolution,
    ResolverResult,
    TemporalRelationConfig,
    TemporalRelationEnricher,
)
from tests.test_durable_ingestion import config, user  # noqa: F401


REFERENCE_TIME = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _Resolver:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, query, question_time, records):
        self.calls += 1
        by_text = {record.text: record for record in records}
        first = next(
            record for text, record in by_text.items() if "finished the frame" in text
        )
        second = next(
            record for text, record in by_text.items() if "hung the frame" in text
        )
        return ResolverResult(
            resolution=RawResolution(
                operation="elapsed_between",
                operands=[
                    RawOperand(
                        name="frame completion",
                        evidence_id=first.id,
                        quote="finished the frame today",
                        time_expression="event_time",
                        time_basis="event_time",
                    ),
                    RawOperand(
                        name="frame hanging",
                        evidence_id=second.id,
                        quote="hung the frame today",
                        time_expression="event_time",
                        time_basis="event_time",
                    ),
                ],
            ),
            audit=ResolverAudit(
                provider="test",
                model="test",
                request_sha256=_hash("request"),
                response_sha256=_hash("response"),
                attempts=1,
                schema_repairs=0,
                elapsed_ms=1,
            ),
        )


class _Gate:
    async def assess(self, query, relation):
        return GateAudit(
            provider="test",
            model="test",
            request_sha256=_hash("gate-request"),
            response_sha256=_hash("gate-response"),
            probabilities=(0.94, 0.93),
            minimum_probability=0.93,
            attempts=1,
            elapsed_ms=1,
        )


async def test_public_retrieval_enriches_temporal_context_and_receipt(
    config, user  # noqa: F811
) -> None:
    relation_config = TemporalRelationConfig(enabled=True)
    configured = config.model_copy(
        update={"temporal_relation": relation_config}
    )
    resolver = _Resolver()
    async with MemoryEngine.open(configured) as engine:
        await engine.ingest_fast(
            "I finished the frame today.",
            user_id=user,
            event_time=datetime(2026, 8, 1, 9, tzinfo=timezone.utc),
        )
        await engine.ingest_fast(
            "I hung the frame today.",
            user_id=user,
            event_time=datetime(2026, 8, 8, 9, tzinfo=timezone.utc),
        )
        await engine.process_pending(user_id=user)
        engine._retrieval_pipeline._temporal_relation_enricher = (  # type: ignore[union-attr]
            TemporalRelationEnricher(relation_config, resolver, _Gate())
        )

        response = await engine.retrieve(
            "How long passed between finishing and hanging the frame?",
            user_id=user,
            min_score=0,
            reference_time=REFERENCE_TIME,
            include_cross_scope=False,
        )

        metadata = response.metadata.temporal_relation
        assert metadata is not None and metadata.status == "accepted"
        assert metadata.value == "7 days"
        assert len(metadata.evidence_ids) == 2
        assert "Deterministic calendar-date difference: 7 days" in response.bundle.render()
        assert response.bundle.tokens_used <= response.bundle.token_budget
        receipt = await engine.get_retrieval_receipt(
            str(response.metadata.request_id), user_id=user
        )
        assert receipt is not None and receipt.execution is not None
        recorded = receipt.execution.parameters["temporal_relation"]
        assert isinstance(recorded, dict) and recorded["status"] == "accepted"
        assert recorded["evidence_ids"] == [str(item) for item in metadata.evidence_ids]
        assert receipt.context_sha256 == hashlib.sha256(
            response.bundle.render().encode()
        ).hexdigest()
        feature = receipt.execution.features["temporal_relation"]
        assert isinstance(feature, dict) and feature["enabled"] is True

        ordinary = await engine.retrieve(
            "What material did I use for the frame?",
            user_id=user,
            min_score=0,
            reference_time=REFERENCE_TIME,
            include_cross_scope=False,
        )
        assert ordinary.metadata.temporal_relation is None
        assert resolver.calls == 1


async def test_temporal_relation_routing_covers_duration_arithmetic_without_changing_base_guidance() -> None:
    for query in (
        "How many weeks have I been taking sculpting classes when I invested in tools?",
        "How many days did it take for me to find a house after starting with Rachel?",
        "How many days did I spend on my camping trip?",
    ):
        analysis = await analyze_query(query, reference_time=REFERENCE_TIME)
        assert _detect_context_type(query, analysis) == "aggregation"
        assert is_temporal_reasoning_query(query, analysis) is True

    count_query = "How many days did I visit the gym this month?"
    count_analysis = await analyze_query(count_query, reference_time=REFERENCE_TIME)
    assert _detect_context_type(count_query, count_analysis) == "aggregation"
    assert is_temporal_reasoning_query(count_query, count_analysis) is False


def test_temporal_relation_configuration_is_opt_in_and_env_addressable(
    monkeypatch,
) -> None:
    assert PRMEConfig(_env_file=None).temporal_relation.enabled is False
    monkeypatch.setenv("PRME_TEMPORAL_RELATION__ENABLED", "true")
    monkeypatch.setenv(
        "PRME_TEMPORAL_RELATION__RESOLVER_MODEL", "deepseek-v4.1-flash:cloud"
    )

    loaded = PRMEConfig(_env_file=None)

    assert loaded.temporal_relation.enabled is True
    assert loaded.temporal_relation.confirmation_protocol_aligned is True
    assert "gate_api_key" not in loaded.temporal_relation.model_dump(
        mode="json", exclude={"gate_api_key"}
    )

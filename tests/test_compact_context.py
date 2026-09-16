"""Compact packed context keeps evidence semantics while spending fewer tokens."""

import json
from datetime import datetime, timezone

import pytest
import tiktoken

from prme.models import MemoryNode
from prme.models.relevance import make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.models import MemoryBundle, RetrievalCandidate, ScoreProvenance, ScoreTrace
from prme.retrieval.packing import pack_context
from prme.types import EpistemicType, LifecycleState, Scope, SourceType


NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)


def candidate(index: int, *, content: str | None = None) -> RetrievalCandidate:
    trace = ScoreTrace(semantic_similarity=.8, epistemic_weight=1,
                       node_type_boost=1, composite_score=.8)
    node = MemoryNode(
        id=f"00000000-0000-0000-0000-{index:012d}",
        user_id="owner",
        node_type="note",
        scope=Scope.PROJECT,
        epistemic_type=EpistemicType.OBSERVED,
        lifecycle_state=LifecycleState.STABLE,
        source_type=SourceType.EXTERNAL_DOCUMENT,
        event_time=NOW,
        valid_from=NOW,
        content=content or (f"Evidence {index}: " + "supporting detail " * 45),
    )
    result = RetrievalCandidate(node=node, composite_score=.8, score_trace=trace)
    result.score_provenance = ScoreProvenance(
        base_node_id=node.id, trace=trace, weights=ScoringWeights(
            w_semantic=1, w_lexical=0, w_graph=0, w_recency=0,
            w_salience=0, w_confidence=0,
        )
    )
    return result


def test_compact_context_preserves_semantics_and_resolvable_identity():
    source = candidate(1, content='A pipe | and newline\nremain safely encoded.')
    config = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity="full",
                           context_format="compact", context_guidance_mode="off")
    bundle = pack_context([source], config)
    lines = bundle.render().splitlines()
    fields = lines[0].split("[", 1)[1].split("]", 1)[0].split(",")
    row = dict(zip(fields, json.loads(lines[2]), strict=True))

    assert row == {
        "ref": "m1", "type": "note", "scope": "project",
        "epistemic": "observed", "memory_lifecycle": "stable",
        "source_type": "external_document", "representation": "full",
        "event_time": NOW.isoformat(), "valid_from": NOW.isoformat(),
        "valid_to": None, "text": source.node.content,
    }
    assert bundle.resolve_context_ref("m1") == source.node.id
    assert bundle.context_references == {"m1": source.node.id}
    assert str(source.node.id) not in bundle.render()
    assert bundle.tokens_used == len(
        tiktoken.get_encoding(config.tokenizer).encode(bundle.render())
    )
    with pytest.raises(ValueError, match="Unknown context reference"):
        bundle.resolve_context_ref("m2")


def test_compact_context_fits_more_whole_sources_at_the_same_budget():
    values = [candidate(index) for index in range(1, 41)]
    base = PackingConfig(token_budget=4096, overhead_tokens=0, min_fidelity="full",
                         multipath_ordering="score", context_guidance_mode="off")
    auditable = pack_context(values, base)
    compact = pack_context(values, base.model_copy(update={"context_format": "compact"}))

    assert compact.included_count > auditable.included_count
    assert compact.tokens_used <= compact.token_budget
    assert auditable.tokens_used <= auditable.token_budget
    assert set(compact.context_references.values()) == {
        item.node.id for group in compact.sections.values() for item in group
    }
    assert auditable.context_references == {}
    assert all(item.rendered_text == item.node.content
               for group in compact.sections.values() for item in group)


def test_compact_context_receipt_is_versioned_and_binds_exact_output():
    source = candidate(1)
    config = PackingConfig(token_budget=1000, overhead_tokens=0,
                           context_format="compact", context_guidance_mode="off")
    bundle = pack_context([source], config)
    receipt = make_receipt(
        request_id="00000000-0000-0000-0000-000000000099",
        user_id="owner", query="evidence", reference_time=NOW,
        scopes=(Scope.PROJECT,), scoring=ScoringWeights(), packing=config,
        candidates=[source], bundle=bundle,
        execution=RetrievalExecution(features={"test": True}, parameters={}),
    )

    assert receipt.schema_version == 12
    assert receipt.packing.context_format == "compact"
    assert receipt.context_sha256
    with pytest.raises(ValueError, match="execution descriptor"):
        make_receipt(
            request_id="00000000-0000-0000-0000-000000000098",
            user_id="owner", query="evidence", reference_time=NOW,
            scopes=(Scope.PROJECT,), scoring=ScoringWeights(), packing=config,
            candidates=[source], bundle=bundle,
        )


def test_empty_bundle_context_reference_defaults_remain_compatible():
    bundle = MemoryBundle()
    assert bundle.context_format == "auditable"
    assert bundle.context_references == {}

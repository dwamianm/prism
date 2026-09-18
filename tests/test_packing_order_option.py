"""The public packing policy reproduces both tested multi-path orderings."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import PRMEConfig
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens
from prme.types import NodeType, RepresentationLevel


def candidate(number, text, score, **kwargs):
    return RetrievalCandidate(
        node=MemoryNode(
            id=UUID(int=number),
            user_id="u",
            content=text,
            node_type=kwargs.pop("node_type", NodeType.NOTE),
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            **kwargs,
        ),
        composite_score=score,
        path_count=2,
        paths=["VECTOR", "LEXICAL"],
    )


def test_score_option_preserves_long_relevant_source_instead_of_short_lower_score():
    long = candidate(
        1, "A lengthy source with context. " * 80 + "Only if approval is granted.", 0.9
    )
    short = candidate(2, "A lower-scoring short source.", 0.3)
    before = [c.model_dump(mode="json") for c in [short, long]]
    roomy = PackingConfig(
        token_budget=10000, overhead_tokens=0, min_fidelity=RepresentationLevel.FULL
    )
    budget = pack_context([long], roomy).tokens_used
    contexts = {}
    for ordering, expected in [("density", short), ("score", long), ("balanced", long)]:
        cfg = PackingConfig(
            token_budget=budget,
            overhead_tokens=0,
            min_fidelity=RepresentationLevel.FULL,
            multipath_ordering=ordering,
        )
        bundle = pack_context([short, long], cfg)
        assert bundle.included_count == 1
        assert expected.node.content in bundle.render()
        assert (
            bundle.tokens_used == count_tokens(bundle.render(), cfg.tokenizer) <= budget
        )
        contexts[ordering] = bundle.render()
        assert pack_context([long, short], cfg).render() == bundle.render()
    assert contexts["density"] != contexts["score"]
    assert [c.model_dump(mode="json") for c in [short, long]] == before


@pytest.mark.parametrize("ordering", ["density", "score", "balanced"])
@pytest.mark.parametrize(
    "priority",
    [
        {"pinned": True},
        {"node_type": NodeType.TASK},
        {"node_type": NodeType.INSTRUCTION},
    ],
)
def test_ordering_does_not_override_priority_or_budget(ordering, priority):
    first = candidate(1, "Priority source.", 0.01, **priority)
    other = candidate(2, "Other source.", 1)
    cfg = PackingConfig(
        token_budget=10000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
        multipath_ordering=ordering,
    )
    budget = pack_context([first], cfg).tokens_used
    bundle = pack_context(
        [other, first], cfg.model_copy(update={"token_budget": budget})
    )
    assert bundle.included_count == 1 and first.node.content in bundle.render()
    assert bundle.tokens_used <= budget


def test_default_and_environment_selection_are_explicit(monkeypatch):
    assert PackingConfig().multipath_ordering == "balanced"
    assert PackingConfig().context_guidance_mode == "temporal"
    assert PackingConfig().context_format == "auditable"
    monkeypatch.setenv("PRME_PACKING__MULTIPATH_ORDERING", "score")
    assert PRMEConfig(_env_file=None).packing.multipath_ordering == "score"
    monkeypatch.setenv("PRME_PACKING__MULTIPATH_ORDERING", "balanced")
    assert PRMEConfig(_env_file=None).packing.multipath_ordering == "balanced"
    monkeypatch.setenv("PRME_PACKING__CONTEXT_GUIDANCE_MODE", "off")
    assert PRMEConfig(_env_file=None).packing.context_guidance_mode == "off"
    with pytest.raises(ValidationError):
        PackingConfig(multipath_ordering="typo")
    with pytest.raises(ValidationError):
        PackingConfig(context_guidance_mode="typo")
    with pytest.raises(ValidationError):
        PackingConfig(context_format="typo")


def test_experimental_session_marginal_ordering_diversifies_ordinary_evidence():
    first = candidate(1, "Session A first source.", 0.9, session_id="a")
    second = candidate(2, "Session A second fact.", 0.8, session_id="a")
    other = candidate(3, "Session B useful fact.", 0.6, session_id="b")
    roomy = PackingConfig(
        token_budget=10_000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
    )
    budget = pack_context([first, second], roomy).tokens_used
    config = roomy.model_copy(update={"token_budget": budget})

    control = pack_context([first, second, other], config)
    diversified = pack_context(
        [first, second, other],
        config,
        _session_marginal_free_slots=1,
        _session_marginal_decay=0.5,
    )

    assert "Session A first source." in control.render()
    assert "Session A second fact." in control.render()
    assert "Session B useful fact." not in control.render()
    assert "Session A first source." in diversified.render()
    assert "Session A second fact." not in diversified.render()
    assert "Session B useful fact." in diversified.render()
    assert diversified.tokens_used <= budget


def test_session_slot_is_consumed_only_after_a_record_fits():
    oversized = candidate(1, "Too large. " * 500, 0.9, session_id="a")
    same_session = candidate(2, "Session A compact fact.", 0.8, session_id="a")
    other = candidate(3, "Session B compact fact.", 0.6, session_id="b")
    roomy = PackingConfig(
        token_budget=10_000,
        overhead_tokens=0,
        min_fidelity=RepresentationLevel.FULL,
    )
    budget = pack_context([same_session], roomy).tokens_used
    bundle = pack_context(
        [oversized, same_session, other],
        roomy.model_copy(update={"token_budget": budget}),
        _session_marginal_free_slots=1,
        _session_marginal_decay=0.5,
    )

    assert "Too large." not in bundle.render()
    assert "Session A compact fact." in bundle.render()
    assert "Session B compact fact." not in bundle.render()


def test_session_marginal_hook_validates_parameters_and_decay_one_is_identity():
    rows = [
        candidate(1, "First.", 0.9, session_id="a"),
        candidate(2, "Second.", 0.8, session_id="a"),
        candidate(3, "Third.", 0.7, session_id="b"),
    ]
    config = PackingConfig(token_budget=10_000)
    control = pack_context(rows, config)
    identity = pack_context(
        rows,
        config,
        _session_marginal_free_slots=1,
        _session_marginal_decay=1.0,
    )
    assert identity.model_dump(mode="json") == control.model_dump(mode="json")

    with pytest.raises(ValueError, match="set together"):
        pack_context(rows, config, _session_marginal_free_slots=1)
    with pytest.raises(ValueError, match="at least one"):
        pack_context(
            rows,
            config,
            _session_marginal_free_slots=0,
            _session_marginal_decay=0.5,
        )
    with pytest.raises(ValueError, match="in \\(0, 1\\]"):
        pack_context(
            rows,
            config,
            _session_marginal_free_slots=1,
            _session_marginal_decay=0.0,
        )

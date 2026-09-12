"""Actual rendered context must fit without losing source qualifiers."""

import tiktoken
import pytest

from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.context_formatter import format_for_llm
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import _render_representation, pack_context
from prme.types import NodeType, RepresentationLevel


def candidate(content, *, score=1, pinned=False, node_type=NodeType.NOTE):
    return RetrievalCandidate(
        node=MemoryNode(user_id="u", content=content, node_type=node_type, pinned=pinned),
        composite_score=score,
    )


def tokens(text, encoding="cl100k_base"):
    return len(tiktoken.get_encoding(encoding).encode(text, disallowed_special=()))


@pytest.mark.parametrize("level", [RepresentationLevel.PROSE, RepresentationLevel.STRUCTURED])
def test_representations_preserve_trailing_qualifiers(level):
    source = candidate("Use blue. " * 100 + "Never apply this to suspended accounts.")
    assert source.node.content in _render_representation(source, level)


@pytest.mark.parametrize("encoding", ["cl100k_base", "o200k_base"])
def test_bundle_counts_the_complete_rendered_context(encoding):
    source = candidate("原文 <|endoftext|> " * 100 + "unless access is revoked")
    small = candidate("A short independent fact", score=.5)
    bundle = pack_context([source, small, small], PackingConfig(
        token_budget=250, overhead_tokens=20, tokenizer=encoding, min_fidelity=RepresentationLevel.FULL,
    ))
    assert bundle.tokens_used == tokens(bundle.render(), encoding)
    assert bundle.tokens_used + 20 <= bundle.token_budget
    assert bundle.budget_remaining == 230 - bundle.tokens_used
    assert "short independent fact" in bundle.render()
    assert "原文" not in bundle.render()
    assert bundle.included_count == 1
    assert source.representation is None and small.representation is None


def test_tiny_budgets_do_not_force_a_pinned_memory_or_negative_remaining():
    source = candidate("Pinned but too large", pinned=True)
    for budget in (0, 1, 5, 50):
        bundle = pack_context([source], PackingConfig(token_budget=budget))
        assert bundle.tokens_used <= budget
        assert bundle.budget_remaining >= 0
        assert bundle.render() == ""


def test_pinned_flag_takes_priority_even_with_low_salience():
    pin = candidate("Preserve this pinned note", score=.01, pinned=True)
    other = candidate("A distracting unpinned note", score=1)
    pin.node.salience = .1
    full = PackingConfig(token_budget=1000, overhead_tokens=0, min_fidelity=RepresentationLevel.FULL)
    one_cost = pack_context([pin], full).tokens_used
    bundle = pack_context([other, pin], full.model_copy(update={"token_budget": one_cost}))
    assert bundle.included_count == 1
    assert "Preserve this pinned note" in bundle.render()


@pytest.mark.parametrize("profile", [True, False])
@pytest.mark.parametrize("hint", ["default", "temporal", "knowledge_update", "aggregation"])
def test_formatter_counts_profiles_headers_and_full_sources(profile, hint):
    giant = candidate("Deployment preference. " * 100 + "Except when the account is suspended.", node_type=NodeType.PREFERENCE)
    short = candidate("A complete small note.")
    budget = tokens(format_for_llm([short], "What do I know?", include_profile=profile, context_hint=hint))
    text = format_for_llm(
        [giant, short], "What do I know?", include_profile=profile,
        context_hint=hint, token_budget=budget,
    )
    assert tokens(text) <= budget
    assert "Deployment preference" not in text
    assert "A complete small note." in text
    assert format_for_llm([giant], "test", token_budget=0) == ""


def test_formatter_supports_the_consumers_tokenizer():
    sources = [candidate("Hello 世界 " * 10), candidate("Tiny complete fact.")]
    text = format_for_llm(sources, "test", include_profile=False, token_budget=60, token_counter=len)
    assert len(text) <= 60
    assert "Tiny complete fact." in text


@pytest.mark.parametrize("source", ["The team decided to migrate to GraphQL.",
                                    "The team tentatively agreed to consider GraphQL; no final decision exists."])
def test_lifecycle_metadata_does_not_rewrite_the_reported_decision(source):
    import json
    result = pack_context([candidate(source, node_type=NodeType.DECISION)], PackingConfig(token_budget=1000))
    entry = json.loads(next(line for line in result.render().splitlines() if line.startswith("{")))
    assert entry["memory_lifecycle"] == "tentative"
    assert "state" not in entry  # Avoid ambiguity with the decision's own state.
    assert entry["text"] == source
    assert result.tokens_used == tokens(result.render())

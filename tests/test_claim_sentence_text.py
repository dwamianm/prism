"""A claim's own sentences can be its text instead of the whole paragraph (enable_claim_sentence_text)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from prme import MemoryEngine
from prme.ingestion.grounding import claim_sentences
from prme.ingestion.schema import ExtractionResult
from prme.types import NodeType
from tests import test_durable_ingestion
from tests.test_fact_text_resolution import CAROLINE_TURN, RESOLVED, extractor, went

config = test_durable_ingestion.config
user = test_durable_ingestion.user

OFFER = ("Really well. If Brightpath makes an offer, I'll probably take it. "
         "Their team uses Python for everything, which I love.")


class TestClaimSentences:
    @pytest.mark.parametrize(("subject", "obj", "expected"), [
        ("I", "Python", "Their team uses Python for everything, which I love."),
        ("I", "Brightpath", "If Brightpath makes an offer, I'll probably take it."),
    ])
    def test_the_sentence_that_mentions_both(self, subject, obj, expected):
        assert claim_sentences(OFFER, subject, obj) == expected

    def test_a_claim_across_sentences_keeps_both(self):
        passage = "Hi! Sam is my partner. He works nights as a nurse."
        assert claim_sentences(passage, "Sam", "nurse") == "Sam is my partner. He works nights as a nurse."

    def test_the_shortest_run_wins(self):
        passage = "Sam likes tea. Sam is a nurse at Denver Health, and nurses work hard."
        assert claim_sentences(passage, "Sam", "nurse") == "Sam is a nurse at Denver Health, and nurses work hard."

    def test_a_following_qualifier_stays(self):
        passage = "I eat meat. Only if it is fish, though. Mornings are rough."
        assert claim_sentences(passage, "I", "meat") == "I eat meat. Only if it is fish, though."

    def test_a_paragraph_break_ends_a_sentence(self):
        passage = "Biscuit is our golden retriever\n\nSam walks him every night."
        assert claim_sentences(passage, "Biscuit", "golden retriever") == "Biscuit is our golden retriever"

    def test_no_run_mentions_both(self):
        assert claim_sentences(OFFER, "Sam", "Python") is None


def _offer_claims(base):
    async def extract(content, *, role=None, **_):
        return ExtractionResult.model_validate({
            "entities": [{"name": "Brightpath", "entity_type": "organization"},
                         {"name": "Python", "entity_type": "technology"}],
            "facts": [{"subject": "I", "predicate": "likes", "object": "Python", "object_entity_type": "technology",
                       "fact_type": "preference", "polarity": "positive", "evidence_quote": content},
                      {"subject": "I", "predicate": "would_take_offer_from", "object": "Brightpath",
                       "object_entity_type": "organization", "epistemic_type": "conditional",
                       "condition": "If Brightpath makes an offer", "polarity": "positive",
                       "evidence_quote": content}]})
    return AsyncMock(side_effect=extract)


async def _claims(config, user, *, enabled):
    settings = config.model_copy(update={"enable_claim_sentence_text": enabled})
    async with MemoryEngine.open(settings) as engine:
        engine._pipeline._extraction_provider.extract = _offer_claims(settings)
        await engine.ingest(OFFER, user_id=user, session_id="s1", role="user",
                            event_time=datetime(2026, 3, 19, tzinfo=timezone.utc), wait_for_extraction=True)
        nodes = await engine.query_nodes(user_id=user)
    return {node.metadata["object"]: node for node in nodes
            if node.node_type in (NodeType.FACT, NodeType.PREFERENCE)}


async def test_off_by_default_every_claim_shows_the_paragraph(config, user):
    claims = await _claims(config, user, enabled=False)
    assert {node.content for node in claims.values()} == {OFFER}
    assert {node.metadata["grounding_method"] for node in claims.values()} == {"source_passage_v1"}


async def test_each_claim_shows_its_own_sentence_and_keeps_the_paragraph_as_evidence(config, user):
    claims = await _claims(config, user, enabled=True)
    assert claims["Python"].content == "Their team uses Python for everything, which I love."
    assert claims["Brightpath"].content == "If Brightpath makes an offer, I'll probably take it."
    for node in claims.values():
        assert node.metadata["evidence_quote"] == OFFER
        assert node.metadata["grounding_method"] == "claim_sentences_v1"


async def test_an_accepted_resolution_takes_precedence(config, user):
    both = config.model_copy(update={"enable_claim_sentence_text": True, "enable_fact_text_resolution": True})
    async with MemoryEngine.open(both) as engine:
        engine._pipeline._extraction_provider.extract = extractor([], [went(resolved_text=RESOLVED)])
        await engine.ingest(CAROLINE_TURN, user_id=user, session_id="s1", role="user", speaker="Caroline",
                            event_time=datetime(2023, 5, 8, 13, 56, tzinfo=timezone.utc), wait_for_extraction=True)
        [node] = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
    assert node.content == RESOLVED and node.metadata["grounding_method"] == "resolved_text_v1"

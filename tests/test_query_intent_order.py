"""Opt-in temporal-first query intent (issue #85).

By default a question that names a person, place or organization is an entity
lookup before its temporal wording is checked, so "When did Caroline go to the
support group?" never gets temporal scoring. ``PRMEConfig.query_intent_order =
"temporal_first"`` checks temporal wording and dates first. The default is
unchanged, and so are the receipts it writes.
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.retrieval.pipeline import RetrievalPipeline
from prme.types import Scope
from tests import test_durable_ingestion

config = test_durable_ingestion.config
user = test_durable_ingestion.user

REFERENCE = datetime(2023, 6, 1, 12, tzinfo=timezone.utc)
QUESTION = "What did Caroline do last week?"
MEMORIES = (
    ("Caroline went to the support group with a friend.", REFERENCE - timedelta(days=7)),
    ("Caroline painted a sunset over the lake.", REFERENCE - timedelta(days=200)),
)


def test_the_default_is_entity_first_and_the_environment_can_change_it(monkeypatch):
    monkeypatch.delenv("PRME_QUERY_INTENT_ORDER", raising=False)
    assert PRMEConfig(_env_file=None).query_intent_order == "entity_first"
    with pytest.raises(ValidationError):
        PRMEConfig(_env_file=None, query_intent_order="temporal")
    # The pipeline refuses an unknown order before it touches its stores.
    with pytest.raises(ValueError, match="Unknown query intent order: 'temporal'"):
        RetrievalPipeline(None, None, None, None, query_intent_order="temporal")
    monkeypatch.setenv("PRME_QUERY_INTENT_ORDER", "temporal_first")
    assert PRMEConfig(_env_file=None).query_intent_order == "temporal_first"


async def _retrieve(config, user, order):
    """Store MEMORIES for a user of this order's own and ask QUESTION, with the order set in the config."""
    owner = f"{user}-{order}"
    async with MemoryEngine.open(config.model_copy(update={"query_intent_order": order})) as engine:
        for text, event_time in MEMORIES:
            await engine.store(text, user_id=owner, scope=Scope.PROJECT, event_time=event_time)
        response = await engine.retrieve(QUESTION, user_id=owner, scope=Scope.PROJECT, min_score=0,
                                         include_cross_scope=False, reference_time=REFERENCE)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=owner)
    assert receipt.replay_ranking() == tuple(item.node.id for item in response.results)
    affinity = {item.node.content: item.score_trace.temporal_affinity for item in response.results}
    assert sorted(affinity) == sorted(text for text, _ in MEMORIES) and len(response.results) == len(MEMORIES)
    return affinity, receipt


async def test_a_temporal_question_that_names_a_person_gets_temporal_scoring(config, user):
    affinity, receipt = await _retrieve(config, user, "entity_first")
    assert set(affinity.values()) == {0.0}
    assert "query_intent_classification" not in receipt.execution.features
    assert "query_intent_order" not in receipt.execution.parameters

    affinity, receipt = await _retrieve(config, user, "temporal_first")
    near, far = (affinity[text] for text, _ in MEMORIES)
    assert near > far > 0
    assert receipt.execution.features["query_intent_classification"] == {"order": "temporal_first", "version": 1}
    assert receipt.execution.parameters["query_intent_order"] == "temporal_first"

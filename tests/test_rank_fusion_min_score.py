"""Under rank fusion, min_score gates semantic relevance, not the fused rank (issue #110)."""

import hashlib
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import ValidationError

from prme import MemoryEngine
from prme.config import MCPConfig
from prme.mcp.server import create_mcp_server
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, PackingConfig
from prme.retrieval.episode_context import expand_episode_context
from prme.retrieval.evidence_context import augment_evidence_context, project_evidence_context
from prme.retrieval.models import MemoryBundle, rank_fusion_relevance
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.selection import select_candidates, with_rank_fusion_relevance
from prme.retrieval.session_context import expand_session_context
from prme.types import RetrievalMode, Scope
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_rank_fusion import EXECUTION, NOW, RRF, candidate, receipt

config = test_durable_ingestion.config
user = test_durable_ingestion.user

FIXTURES = Path(__file__).parent / "fixtures/relevance"
# Written by make_receipt on main before this change: rank fusion, a session
# neighbor and a min_score of 0.5 applied to the fused score.
V15_CHECKSUM = "1d9553a5732fe5f9d70de18f2444239236fcf0b211bbb6b5db86381d99a232fe"

UNRELATED = (
    "The telescope is blue.",
    "The telescope lens is cracked.",
    "We bought bread yesterday.",
    "Anna plays tennis on Sundays.",
    "The quarterly budget review moved to Friday.",
)


class KeywordEmbedding:
    """Bag-of-words vectors: texts sharing no content word have cosine near zero."""

    model_name = "keyword-test"
    model_version = "1"
    dimension = 384
    _stop = {"the", "a", "an", "is", "of", "on", "in", "to", "we", "what", "and", "at"}

    def __init__(self):
        self._vocabulary: dict[str, int] = {}

    async def embed(self, texts):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimension
            vector[0] = 0.05  # never a zero vector
            for word in re.findall(r"[a-z]+", text.lower()):
                if word not in self._stop:
                    index = self._vocabulary.setdefault(word, 1 + len(self._vocabulary))
                    vector[index] += 1.0
            vectors.append(vector)
        return vectors


@pytest.fixture
def keyword_config(config, monkeypatch):
    embedding = KeywordEmbedding()
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: embedding)
    return config.model_copy(update={"scoring": RRF})


# --- Relevance ----------------------------------------------------------------


def test_relevance_is_the_candidates_cosine_floored_at_zero():
    ranked, _ = score_and_rank([candidate(1, semantic=.62, lexical=.9), candidate(2, semantic=.2)],
                               RRF, now=NOW)
    assert [rank_fusion_relevance(c) for c in ranked] == [.62, .2]
    assert rank_fusion_relevance(candidate(3, semantic=.3)) == .3  # no provenance yet
    assert rank_fusion_relevance(candidate(4, semantic=-.4)) == 0
    assert rank_fusion_relevance(candidate(5, lexical=1.0)) == 0  # not a vector hit


async def test_session_context_keeps_the_relevance_of_the_memory_that_pulled_it_in():
    trigger = candidate(1, semantic=.62, lexical=.5, days=1, session="conversation")
    # Already found, and its own fused score stays above the trigger's share.
    answer = candidate(2, semantic=.15, lexical=1.0, days=0, session="conversation")
    stronger = candidate(3, semantic=.8, days=3, session="conversation")
    weaker = candidate(4, semantic=.3, days=4, session="conversation")
    unrelated = candidate(5, semantic=.1, days=2)
    added = candidate(6, days=5, session="conversation")
    ranked, _ = score_and_rank([trigger, answer, stronger, weaker, unrelated], RRF, now=NOW)
    assert ranked[0].node.id == trigger.node.id
    graph = Mock(query_nodes=AsyncMock(return_value=[
        c.node for c in (trigger, answer, stronger, weaker, added)
    ]))
    expanded = await expand_session_context(
        ranked, graph, "owner", PackingConfig(session_context_top_k=1), [Scope.PROJECT],
    )
    by_id = {c.node.id.int: c for c in expanded}
    assert by_id[2].score_provenance.base_node_id == answer.node.id
    assert by_id[3].score_provenance.base_node_id == trigger.node.id
    assert {number: rank_fusion_relevance(c) for number, c in by_id.items()} == {
        1: .62, 2: .62, 3: .8, 4: .62, 5: .1, 6: .62,
    }
    selected, excluded = select_candidates(
        with_rank_fusion_relevance(expanded), min_score=.5, limit=None,
    )
    assert {c.node.id.int for c in selected} == {1, 2, 3, 4, 6}
    assert [e.node_id.int for e in excluded] == [5]
    assert all("context_relevance" not in c.model_dump(mode="json") for c in selected)


def test_episode_context_keeps_the_anchors_relevance():
    anchor = candidate(1, semantic=.6, lexical=1.0, session="episode")
    # Its own fused score stays above the anchor's share, so it keeps it.
    member = candidate(2, semantic=.05, lexical=.9, session="episode")
    ranked, _ = score_and_rank([anchor, member], RRF, now=NOW)
    expanded = expand_episode_context(ranked, "telescope", PackingConfig(episode_context_top_k=1))
    kept = next(c for c in expanded if c.node.id == member.node.id)
    assert "EPISODE_CONTEXT" in kept.paths
    assert kept.score_provenance.base_node_id == member.node.id
    assert rank_fusion_relevance(kept) == .6


def _evidence_candidate(number, *, semantic=None, lexical=None, evidence=()):
    item = candidate(number, semantic=semantic, lexical=lexical)
    return item.model_copy(update={"node": item.node.model_copy(update={"evidence_refs": list(evidence)})})


@pytest.mark.parametrize("policy", ["projection", "augmentation"])
@pytest.mark.parametrize("retrieved,decay,expected", [
    (None, .5, .7),
    # A keyword-only hit on the source, fused above the group's share.
    ({"lexical": 1.0}, .5, .7),
    # A vector hit whose own cosine beats the group's, while the group's
    # inherited score replaces its own.
    ({"semantic": .75}, 1.0, .75),
])
async def test_evidence_context_keeps_its_groups_relevance(policy, retrieved, decay, expected):
    source_id = UUID(int=9)
    derived = _evidence_candidate(1, semantic=.7, lexical=.3, evidence=(source_id,))
    sibling = _evidence_candidate(2, semantic=.4, evidence=(source_id,))
    pool = [derived, sibling]
    if retrieved is not None:
        pool.append(_evidence_candidate(9, **retrieved))
    ranked, _ = score_and_rank(pool, RRF, now=NOW)
    source = candidate(9).node

    async def get_nodes(_ids):
        return [source]

    options = dict(graph_store=SimpleNamespace(get_nodes=get_nodes), user_id="owner",
                   scopes=[Scope.PROJECT], retrieval_mode=RetrievalMode.DEFAULT,
                   unverified_confidence_threshold=None)
    if policy == "projection":
        expanded = await project_evidence_context(
            ranked, config=PackingConfig(evidence_projection_top_k=1,
                                         evidence_projection_score_decay=decay),
            **options,
        )
    else:
        expanded = await augment_evidence_context(
            ranked, config=PackingConfig(evidence_augmentation_top_k=1,
                                         evidence_augmentation_score_decay=decay),
            **options,
        )
    direct = next(c for c in expanded if c.node.id == source_id)
    assert "EVIDENCE_CONTEXT" in direct.paths
    assert rank_fusion_relevance(direct) == expected


def test_min_score_compares_relevance_under_rank_fusion_and_the_score_otherwise():
    ranked, _ = score_and_rank([candidate(1, semantic=.7), candidate(2, semantic=.2, lexical=1.0),
                                candidate(3, lexical=.4)], RRF, now=NOW)
    # The top keyword hit fuses near 1.0 although its cosine is weak.
    assert [c.node.id.int for c in ranked] == [2, 1, 3]
    assert ranked[0].composite_score > .99 and ranked[0].semantic_score == .2
    scored = with_rank_fusion_relevance(ranked)
    assert [c.composite_score for c in scored] == [c.composite_score for c in ranked]
    assert [c.semantic_relevance for c in scored] == [.2, .7, 0.0]
    assert all(c.semantic_relevance is None for c in ranked)  # inputs are not mutated

    selected, excluded = select_candidates(scored, min_score=.5, limit=None)
    assert [c.node.id.int for c in selected] == [1]
    assert {e.node_id.int: (e.semantic_relevance, e.composite_score) for e in excluded} == {
        2: (.2, ranked[0].composite_score), 3: (0.0, ranked[2].composite_score),
    }
    # Zero stays a no-op: the relevance is never negative.
    assert select_candidates(scored, min_score=0, limit=None)[0] == scored

    weighted, _ = score_and_rank([candidate(1, semantic=.2, lexical=1.0), candidate(2, semantic=.7)],
                                 now=NOW)
    floor = min(c.composite_score for c in weighted)
    kept, dropped = select_candidates(weighted, min_score=floor + 1e-9, limit=None)
    assert len(kept) == 1 and dropped[0].semantic_relevance is None
    assert dropped[0].model_dump(mode="json") == {
        "node_id": str(dropped[0].node_id), "reason": "below_threshold",
        "composite_score": dropped[0].composite_score,
    }
    assert "semantic_relevance" not in weighted[0].model_dump(mode="json")


# --- Receipts -----------------------------------------------------------------


def test_saved_version_15_rank_fusion_receipts_keep_their_bytes_and_replay():
    raw = (FIXTURES / "receipt-v15-rrf.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V15_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 15 and restored.min_score == .5
    assert restored.model_dump_json() == raw and restored.checksum == V15_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)
    assert all(c.semantic_relevance is None for c in restored.candidates)


def test_rank_fusion_receipts_record_the_relevance_min_score_compares():
    ranked, _ = score_and_rank([candidate(1, semantic=.9, lexical=.2), candidate(2, semantic=.6)],
                               RRF, now=NOW)
    scored = with_rank_fusion_relevance(ranked)
    saved = make_receipt(request_id=UUID(int=110), user_id="owner", query="telescope",
                         reference_time=NOW, scopes=(Scope.PROJECT,), scoring=RRF,
                         packing=PackingConfig(), candidates=scored, bundle=MemoryBundle(),
                         min_score=.6, execution=EXECUTION)
    assert saved.schema_version == 16
    assert [c.semantic_relevance for c in saved.candidates] == [c.semantic_relevance for c in scored]
    assert [c.score for c in saved.candidates] == [c.composite_score for c in ranked]
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored == saved and restored.checksum == saved.checksum
    # A direct caller that never ran the pipeline's selection records the same values.
    assert [c.semantic_relevance for c in receipt(ranked).candidates] == [.9, .6]


@pytest.mark.parametrize("change", ["older_version", "missing", "below_floor", "below_cosine",
                                    "not_selected"])
def test_receipt_relevance_is_validated(change):
    ranked, _ = score_and_rank([candidate(1, semantic=.9), candidate(2, semantic=.6)], RRF, now=NOW)
    options = dict(request_id=UUID(int=111), user_id="owner", query="telescope", reference_time=NOW,
                   scopes=(Scope.PROJECT,), scoring=RRF, packing=PackingConfig(),
                   candidates=ranked, bundle=MemoryBundle(), execution=EXECUTION)
    if change == "not_selected":
        # A receipt cannot claim a floor that its candidates did not pass.
        with pytest.raises(ValidationError, match="min_score selection"):
            make_receipt(**options, min_score=.7)
        return
    data = json.loads(make_receipt(**options, min_score=.6).model_dump_json())
    if change == "older_version":
        data["schema_version"] = 15
        match = "version 16"
    elif change == "missing":
        data["candidates"][1].pop("semantic_relevance")
        match = "every candidate"
    elif change == "below_floor":
        data["candidates"][1]["semantic_relevance"] = .5
        match = "min_score selection"
    else:
        data["min_score"] = None
        data["candidates"][0]["semantic_relevance"] = .8
        match = "below a cosine"
    with pytest.raises(ValidationError, match=match):
        RetrievalReceipt.model_validate(data)


# --- Retrieval ----------------------------------------------------------------


async def test_min_score_filters_unrelated_memories_under_rank_fusion(keyword_config, user):
    async with MemoryEngine.open(keyword_config) as engine:
        for text in UNRELATED:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        kwargs = {"user_id": user, "scope": Scope.PROJECT, "include_cross_scope": False,
                  "reference_time": NOW}
        query = "What is the boiling point of mercury in kelvin?"

        unfiltered = await engine.retrieve(query, **kwargs)
        assert unfiltered.results
        top = unfiltered.results[0]
        # The fused score ranks within the pool: the best of nothing still scores high.
        assert top.composite_score >= .45 and top.semantic_relevance < .05
        assert all(c.semantic_relevance is not None for c in unfiltered.results)
        zero = await engine.retrieve(query, min_score=0, **kwargs)
        assert [c.node.id for c in zero.results] == [c.node.id for c in unfiltered.results]

        floored = await engine.retrieve(query, min_score=.3, **kwargs)
        assert floored.results == [] and floored.bundle.included_count == 0
        dropped = {e.node_id: e for e in floored.excluded if e.reason == "below_threshold"}
        assert set(dropped) == {c.node.id for c in unfiltered.results}
        assert dropped[top.node.id].composite_score == top.composite_score
        assert dropped[top.node.id].semantic_relevance == top.semantic_relevance

        relevant = await engine.retrieve("telescope lens", min_score=.3, **kwargs)
        assert [c.node.content for c in relevant.results] == ["The telescope lens is cracked.",
                                                            "The telescope is blue."]
        assert all(c.semantic_relevance >= .3 for c in relevant.results)
        saved = await engine.get_retrieval_receipt(str(relevant.metadata.request_id), user_id=user)
        assert saved.schema_version == 16 and saved.min_score == .3
        assert [c.semantic_relevance for c in saved.candidates] == [
            c.semantic_relevance for c in relevant.results
        ]
        assert [c.score for c in saved.candidates] == [c.composite_score for c in relevant.results]


async def test_a_per_request_rank_fusion_is_gated_and_its_receipt_is_readable(keyword_config, user):
    settings = keyword_config.model_copy(update={"scoring": DEFAULT_SCORING_WEIGHTS})
    async with MemoryEngine.open(settings) as engine:
        for text in UNRELATED:
            await engine.store(text, user_id=user, scope=Scope.PROJECT)
        response = await engine.retrieve("telescope lens", user_id=user, weights=RRF, min_score=.3,
                                         include_cross_scope=False)
        assert [c.node.content for c in response.results] == ["The telescope lens is cracked.",
                                                            "The telescope is blue."]
        request_id = str(response.metadata.request_id)
        async with client_for(app_for(settings, engine, user)) as client:
            saved = await client.get(f"/v1/retrievals/{request_id}")
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["schema_version"] == 16 and body["min_score"] == .3
        assert [c["semantic_relevance"] for c in body["candidates"]] == [
            c.semantic_relevance for c in response.results
        ]


async def test_cross_scope_hints_are_gated_on_relevance(keyword_config, user):
    async with MemoryEngine.open(keyword_config) as engine:
        await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
        await engine.store("The telescope lens is cracked.", user_id=user, scope=Scope.PERSONAL)
        await engine.store("We bought bread yesterday.", user_id=user, scope=Scope.PERSONAL)
        kwargs = {"user_id": user, "scope": Scope.PROJECT, "reference_time": NOW}

        hints = (await engine.retrieve("telescope lens", **kwargs)).cross_scope_hints
        bread = next(c for c in hints if c.node.content.startswith("We bought"))
        assert bread.composite_score >= .3 > bread.semantic_relevance

        gated = (await engine.retrieve("telescope lens", min_score=.3, **kwargs)).cross_scope_hints
        assert [c.node.content for c in gated] == ["The telescope lens is cracked."]


async def test_weighted_retrieval_is_unchanged(config, user):
    async with MemoryEngine.open(config) as engine:
        await engine.store("The telescope is blue.", user_id=user, scope=Scope.PROJECT)
        await engine.store("We bought bread.", user_id=user, scope=Scope.PROJECT)
        response = await engine.retrieve("telescope", user_id=user, reference_time=NOW)
        assert response.results
        assert all(c.semantic_relevance is None for c in response.results)
        assert all("semantic_relevance" not in c.model_dump(mode="json") for c in response.results)
        best = response.results[0].composite_score
        floored = await engine.retrieve("telescope", user_id=user, reference_time=NOW, min_score=best)
        assert [c.node.id for c in floored.results] == [response.results[0].node.id]
        saved = await engine.get_retrieval_receipt(str(floored.metadata.request_id), user_id=user)
        assert saved.schema_version == 12
        assert all(c.semantic_relevance is None for c in saved.candidates)


async def test_http_and_mcp_report_relevance_only_under_rank_fusion(keyword_config, user):
    for scoring in (RRF, DEFAULT_SCORING_WEIGHTS):
        settings = keyword_config.model_copy(update={"scoring": scoring, "mcp": MCPConfig(user_id=user)})
        async with MemoryEngine.open(settings) as engine:
            if scoring is RRF:
                # The weighted pass reopens the same store and reads these.
                for text in UNRELATED:
                    await engine.store(text, user_id=user, scope=Scope.PROJECT)
            body = {"query": "telescope lens", "min_score": .3}

            async with client_for(app_for(settings, engine, user)) as client:
                response = await client.post("/v1/retrieve", json=body)
                assert response.status_code == 200, response.text
                items = response.json()["results"]

            @asynccontextmanager
            async def lifespan(server):
                yield {"engine": engine}

            server = create_mcp_server(settings, lifespan=lifespan)
            async with create_connected_server_and_client_session(
                server._mcp_server, raise_exceptions=True,
            ) as session:
                await session.initialize()
                reply = await session.call_tool("memory_retrieve", body)
                tool_items = json.loads(reply.content[0].text)["results"]

        if scoring is RRF:
            assert [item["content"] for item in items] == ["The telescope lens is cracked.",
                                                         "The telescope is blue."]
            assert all(item["semantic_relevance"] >= .3 for item in items + tool_items)
            assert [item["node_id"] for item in tool_items] == [item["node_id"] for item in items]
        else:
            assert items and tool_items
            assert all("semantic_relevance" not in item for item in items + tool_items)

"""Graph fusion: the cities of the recorded dev run reach the top 3 (prism-monorepo issues 259 and 260).

The hosted dev service (prme 0.16.0a3) ingested the four-turn Japan chat and ranked the note and the claim that
answer "which cities will Alex and Jordan visit in Japan?" 11th and 12th of 21 results. Three options, each off by
default, move them:

- ``scoring.rrf_graph_weight``: the graph path becomes a third rank fusion list, ranked by hop count.
- ``enable_owner_group_links``: a user's "we" claim also links to the owner's entity.
- ``packing.session_context_skip_entities``: an entity takes no part in session context expansion.

The replay tests ingest the saved extraction without a model call and embed every text at the cosine the dev
service's model gave it (``trip_dev_a3_cosines.json``). The rest test each option on its own. Every test runs on
DuckDB and, with PRME_TEST_DATABASE_URL set, on PostgreSQL. The two differ for the question without any option:
PostgreSQL's English text search drops "and" as a stop word and Tantivy keeps it, so only DuckDB gives the cities
claim a lexical score and ranks it 2nd.
"""

import json
import math
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.ingestion.group_links import GROUP_LINK_METADATA, is_group_link, link_group_claims
from prme.ingestion.schema import ExtractionResult
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import PackingConfig, ScoringWeights
from prme.retrieval.execution import RetrievalExecution
from prme.retrieval.models import MemoryBundle, RankFusion, RetrievalCandidate, ScoreProvenance
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import EdgeType, NodeType, Scope
from tests import test_durable_ingestion
from tests.test_mention_links import claim, person, place
from tests.test_owner_carry_batch import RECORDED, T0, hosted, replay_dev_run
from tests.test_owner_name_carry import on

config = test_durable_ingestion.config
user = test_durable_ingestion.user

COSINES = json.loads((Path(__file__).parent / "fixtures" / "extraction" / "trip_dev_a3_cosines.json").read_text())
QUESTIONS = ("which cities will Alex and Jordan visit in Japan?", "which cities will Alex and jordan visit in Japan?")
CITIES = "We chose Tokyo, Kyoto, Kanazawa, and Osaka."
WEIGHT = 0.25
ALL = {"graph": WEIGHT, "group_links": True, "skip_entities": True}
RECORDS = {turn["content"]: entry for turn, entry in zip(RECORDED["turns"], RECORDED["records"])}
INTRODUCTION, REPLY, CITIES_TURN, TRAINS = (turn["content"] for turn in RECORDED["turns"])


class RecordedCosines:
    """Embeds each text of the recorded run at its recorded cosine with the question.

    The question is the first axis. A text is that axis scaled by its cosine plus a unit axis of its own, so the
    question's cosine with it is the recorded one and the texts are otherwise unrelated. A text the recording lacks
    fails the test, so a change to what ingestion embeds cannot pass unnoticed.
    """

    model_name = "recorded-cosines"
    model_version = "1"
    dimension = 384

    def __init__(self):
        self._slot = {text: place for place, text in enumerate(sorted(COSINES["cosines"]), start=1)}

    async def embed(self, texts):
        return [self._vector(text) for text in texts]

    def _vector(self, text):
        vector = [0.0] * self.dimension
        if text in QUESTIONS:
            vector[0] = 1.0
            return vector
        cosine = COSINES["cosines"][text]
        vector[0] = cosine
        vector[self._slot[text]] = math.sqrt(1 - cosine * cosine)
        return vector


@pytest.fixture
def recorded(config, monkeypatch):
    """The test configuration, with every text embedded at its recorded cosine."""
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: RecordedCosines())
    return config


def on_postgres(base):
    return base.database_url is not None


def dev_settings(base, *, graph=None, group_links=False, skip_entities=False):
    """What the dev service ran (batched extraction, the structure options), plus the options under test."""
    scoring = base.scoring
    if graph is not None:
        scoring = ScoringWeights.model_validate({**scoring.model_dump(), "rrf_graph_weight": graph})
    packing = base.packing.model_copy(update={
        "graph_seed_words": True, "folded_records_keep_rank": True, "session_context_skip_entities": skip_entities,
    })
    return hosted(base, scoring=scoring, packing=packing, enable_owner_group_links=group_links)


async def places(base, owner, **options):
    """Replay the run once; the place of the cities claim and of the cities note for each spelling of the question."""
    found = {}
    async with MemoryEngine.open(dev_settings(base, **options)) as engine:
        await replay_dev_run(engine, owner)
        await engine.process_extractions(user_id=owner)
        for question in QUESTIONS:
            found[question] = {}
            for place, result in enumerate((await engine.retrieve(question, user_id=owner)).results, start=1):
                if result.node.content.startswith("We chose Tokyo, Kyoto"):
                    found[question]["note" if result.node.node_type == NodeType.NOTE else "claim"] = place
    return found


class TestRecordedDevRun:
    async def test_without_an_option_the_cities_claim_ranks_low_on_the_hosted_backend(self, recorded, user):
        found = await places(recorded, user)
        for question in QUESTIONS:
            if on_postgres(recorded):
                # The dev table: the note 10th or 11th, tied with two other notes, and the claim 12th.
                assert found[question]["claim"] == 12 and 9 <= found[question]["note"] <= 11
            else:
                # Tantivy keeps "and", which the claim holds.
                assert found[question]["claim"] == 2

    @pytest.mark.parametrize("weight", [0.1, WEIGHT, 0.5, 1.0])
    async def test_the_three_options_put_the_cities_claim_in_the_top_three_for_both_spellings(self, recorded, user, weight):
        found = await places(recorded, user, graph=weight, group_links=True, skip_entities=True)
        assert [found[question]["claim"] <= 3 for question in QUESTIONS] == [True, True]

    @pytest.mark.parametrize("options", [
        {"graph": WEIGHT}, {"group_links": True}, {"skip_entities": True},
        {"graph": WEIGHT, "group_links": True}, {"graph": WEIGHT, "skip_entities": True},
        {"group_links": True, "skip_entities": True},
    ])
    async def test_no_smaller_set_of_options_does_it(self, recorded, user, options):
        if not on_postgres(recorded):
            pytest.skip("DuckDB's lexical index ranks the cities claim 2nd without any option")
        found = await places(recorded, user, **options)
        assert [found[question]["claim"] > 3 for question in QUESTIONS] == [True, True]

    async def test_the_receipt_of_a_retrieval_with_the_options_records_them_and_replays_its_ranking(self, recorded, user):
        async with MemoryEngine.open(dev_settings(recorded, **ALL)) as engine:
            await replay_dev_run(engine, user)
            await engine.process_extractions(user_id=user)
            response = await engine.retrieve(QUESTIONS[0], user_id=user)
            saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        assert saved.scoring.rrf_graph_weight == WEIGHT and saved.packing.session_context_skip_entities is True
        assert saved.replay_ranking() == tuple(item.node.id for item in response.results)
        graph_ranks = {p.rank_fusion.graph_rank for p in saved.score_provenance.values() if p.rank_fusion}
        assert {1, 2} <= graph_ranks

    async def test_the_options_keep_the_two_spellings_alike(self, recorded, user):
        found = await places(recorded, user, **ALL)
        assert found[QUESTIONS[0]] == found[QUESTIONS[1]]


# --- Option 1: the graph list in rank fusion ------------------------------------------------------------------


NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
RRF = ScoringWeights(fusion="rrf")
GRAPH = ScoringWeights(fusion="rrf", rrf_graph_weight=WEIGHT)
EXECUTION = RetrievalExecution(features={"test": True}, parameters={})


def candidate(number, *, semantic=None, graph=0.0, node_type=NodeType.FACT, session=None):
    """A candidate on the vector path when it has a cosine and on the graph path when it has a proximity."""
    paths = [name for name, value in (("VECTOR", semantic), ("GRAPH", graph or None)) if value is not None]
    return RetrievalCandidate(node=MemoryNode(
        id=UUID(int=number), user_id="owner", scope=Scope.PROJECT, node_type=node_type,
        content=f"memory {number}", created_at=NOW, updated_at=NOW, last_reinforced_at=NOW, event_time=NOW,
        session_id=session, confidence_base=.8, salience_base=.5,
    ), semantic_score=semantic or 0.0, lexical_score=0.0, graph_proximity=graph, paths=paths, path_count=len(paths))


def fused(semantic_rank, graph_rank=None, weight=WEIGHT, k=60):
    total = (1 / (k + semantic_rank) if semantic_rank else 0) + (weight / (k + graph_rank) if graph_rank else 0)
    return total * (k + 1) / 2


def scores(pool, weights=GRAPH):
    ranked, _ = score_and_rank(pool, weights, now=NOW)
    return {item.node.id.int: item for item in ranked}


class TestGraphList:
    def test_a_graph_candidate_gains_the_weighted_reciprocal_rank_of_its_hop_count(self):
        pool = [candidate(1, semantic=.9, graph=1.0), candidate(2, semantic=.8, graph=0.7),
                candidate(3, semantic=.7, graph=0.4), candidate(4, semantic=.6)]
        found = scores(pool)
        assert [found[number].composite_score for number in (1, 2, 3, 4)] == pytest.approx(
            [fused(1, 1), fused(2, 2), fused(3, 3), fused(4)], abs=1e-9)
        # Each candidate records its place in the list, and a candidate the graph did not return has none.
        assert [found[number].score_provenance.rank_fusion.graph_rank for number in (1, 2, 3, 4)] == [1, 2, 3, None]

    def test_candidates_at_one_hop_count_share_a_rank_and_the_next_count_is_the_next_rank(self):
        pool = [candidate(number, semantic=1 - number / 100, graph=1.0) for number in range(1, 41)]
        pool.append(candidate(41, semantic=.1, graph=0.4))
        found = scores(pool)
        assert {found[number].score_provenance.rank_fusion.graph_rank for number in range(1, 41)} == {1}
        # Competition ranks would give the farther candidate rank 41, whatever the size of the neighborhood.
        assert found[41].score_provenance.rank_fusion.graph_rank == 2

    def test_a_graph_hit_can_lift_a_candidate_over_a_better_match(self):
        pool = [candidate(1, semantic=.9), candidate(2, semantic=.8, graph=1.0)]
        found = scores(pool, RRF)
        assert found[1].composite_score > found[2].composite_score
        found = scores(pool, ScoringWeights(fusion="rrf", rrf_graph_weight=1.0))
        assert found[2].composite_score > found[1].composite_score

    def test_a_candidate_only_the_graph_found_scores_above_zero_only_with_the_weight(self):
        pool = [candidate(1, semantic=.9), candidate(2, graph=1.0)]
        assert scores(pool, RRF)[2].composite_score == 0
        found = scores(pool)
        assert found[2].composite_score == pytest.approx(fused(None, 1), abs=1e-9)
        assert 0 < found[2].composite_score < found[1].composite_score

    def test_unset_the_graph_proximity_changes_nothing(self):
        near = [candidate(1, semantic=.9, graph=1.0), candidate(2, semantic=.8)]
        far = [candidate(1, semantic=.9), candidate(2, semantic=.8, graph=0.4)]
        assert [item.composite_score for item in scores(near, RRF).values()] == [
            item.composite_score for item in scores(far, RRF).values()]
        provenance = scores(near, RRF)[1].score_provenance
        assert provenance.rank_fusion.graph_rank is None
        saved = json.loads(provenance.model_dump_json())
        assert "graph_rank" not in saved["rank_fusion"] and "rrf_graph_weight" not in saved["weights"]

    def test_the_score_replays_from_the_saved_ranks_and_the_weight_alone(self):
        found = scores([candidate(1, semantic=.9, graph=1.0), candidate(2, semantic=.5, graph=0.7),
                        candidate(3, semantic=.2)])
        for item in found.values():
            provenance = item.score_provenance
            assert provenance.replay_score() == item.composite_score
            restored = ScoreProvenance.model_validate_json(provenance.model_dump_json())
            assert restored == provenance and restored.replay_score() == item.composite_score

    def test_provenance_with_a_graph_rank_and_no_weight_is_rejected(self):
        item = scores([candidate(1, semantic=.9, graph=1.0)])[1]
        data = json.loads(item.score_provenance.model_dump_json())
        data["weights"].pop("rrf_graph_weight")
        with pytest.raises(ValidationError, match="requires rrf_graph_weight"):
            ScoreProvenance.model_validate(data)
        with pytest.raises(ValueError, match="needs rrf_graph_weight"):
            RankFusion(semantic_rank=1, graph_rank=1, epistemic_factor=1, node_type_factor=1,
                       temporal_factor=1).score(60)

    def test_a_receipt_records_the_weight_as_a_version_19_opt_in_and_replays_its_ranking(self):
        ranked, _ = score_and_rank([candidate(1, semantic=.9, graph=1.0), candidate(2, semantic=.8),
                                    candidate(3, semantic=.7, graph=.4)], GRAPH, now=NOW)
        saved = make_receipt(request_id=UUID(int=100), user_id="owner", query="telescope", reference_time=NOW,
                             scopes=(Scope.PROJECT,), scoring=GRAPH, packing=PackingConfig(), candidates=ranked,
                             bundle=MemoryBundle(), ranking_policy="score_path_id", execution=EXECUTION)
        assert saved.schema_version == 19
        assert saved.replay_ranking() == tuple(item.node.id for item in ranked)
        assert json.loads(saved.model_dump_json())["scoring"]["rrf_graph_weight"] == WEIGHT
        assert RetrievalReceipt.model_validate_json(saved.model_dump_json()).checksum == saved.checksum

    def test_the_setting_is_validated_serialized_and_part_of_the_version(self):
        assert RRF.rrf_graph_weight is None and "rrf_graph_weight" not in RRF.model_dump(mode="json")
        assert ScoringWeights.model_validate_json(GRAPH.model_dump_json()) == GRAPH
        assert GRAPH.rank_fusion_opt_ins == {"rrf_graph_weight": WEIGHT}
        assert len({RRF.version_id, GRAPH.version_id, ScoringWeights(fusion="rrf", rrf_graph_weight=.5).version_id}) == 3
        for bad in (0, -1, 4.5):
            with pytest.raises(ValidationError):
                ScoringWeights(fusion="rrf", rrf_graph_weight=bad)
        with pytest.warns(UserWarning, match="rrf_graph_weight applies only"):
            assert ScoringWeights(rrf_graph_weight=WEIGHT) == ScoringWeights()

    def test_the_product_defaults_rank_the_graph_list_by_seed_count_not_by_hop_count(self, monkeypatch):
        from prme import PRMEConfig
        from prme.retrieval.config import RRF_GRAPH_SEED_COUNT_WEIGHT

        # tests/conftest.py turns the group links, the entity skip and the seed count list off for every other test.
        monkeypatch.delenv("PRME_ENABLE_OWNER_GROUP_LINKS")
        monkeypatch.delenv("PRME_PACKING__SESSION_CONTEXT_SKIP_ENTITIES")
        monkeypatch.delenv("PRME_SCORING__RRF_GRAPH_SEED_COUNT")
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            config = PRMEConfig(_env_file=None)
        # The graph weight is the seed count list's; the hop count list is never the default.
        assert config.scoring.rrf_graph_seed_count is True
        assert config.scoring.rrf_graph_weight == RRF_GRAPH_SEED_COUNT_WEIGHT
        # On in the alpha channel: the entity skip since 0.16.0a4, the group links since prism-monorepo issue 275,
        # the seed count list since prism-monorepo issue 276.
        assert config.enable_owner_group_links is True
        assert config.packing.session_context_skip_entities is True


# --- Option 2: a "we" claim links to the owner ---------------------------------------------------------------


async def ingest_messages(engine, owner, messages):
    """Ingest the recorded turns of ``messages`` as one batched request."""
    async def extract_batch(turns):
        return {"turns": [{**RECORDS[turn.content], "turn": turn.index} for turn in turns]}

    engine._pipeline._extraction_provider.extract_batch = extract_batch
    prepared = [{"event_time": T0 + timedelta(minutes=index), **message} for index, message in enumerate(messages)]
    await engine.ingest_batch(prepared, user_id=owner, session_id="s1", wait_for_extraction=True)


async def group_links(engine, owner):
    """(claim text, entity name) for every group link of the owner's memory."""
    nodes = {node.id: node for node in await engine.query_nodes(user_id=owner, limit=500)}
    edges = await engine._graph_store.get_edges(node_ids=[str(node_id) for node_id in nodes])
    return sorted((nodes[edge.source_id].content, nodes[edge.target_id].content) for edge in edges if is_group_link(edge))


def linked(base, **updates):
    """The dev service's settings with the group links on, and ``updates`` over them."""
    return hosted(base).model_copy(update={"enable_owner_group_links": True, **updates})


OWNER = "Alex Rivera"
BOTH_WE_CLAIMS = [("We chose Tokyo, Kyoto, Kanazawa, and Osaka.", OWNER), ("We dropped Hiroshima to save time.", OWNER)]
# The introduction says "My partner Jordan and I", so the session's we is Alex and Jordan (prism-monorepo issue 275).
MEMBER_LINKS = [(text, "Jordan") for text, _ in BOTH_WE_CLAIMS]


class TestOwnerGroupLinks:
    async def test_the_owners_we_claims_link_to_the_owner_and_keep_their_group_subject(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await ingest_messages(engine, user, [
                {"content": INTRODUCTION, "role": "user"}, {"content": REPLY, "role": "assistant"},
                {"content": CITIES_TURN, "role": "user"}, {"content": TRAINS, "role": "user"},
            ])
            found = await group_links(engine, user)
            [cities] = [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
                        if node.content == CITIES]
            edges = await engine._graph_store.get_edges(node_ids=[str(cities.id)])
            [introduction] = [event for event in await engine.get_events(user) if event.content == INTRODUCTION]
        assert found == sorted(BOTH_WE_CLAIMS + MEMBER_LINKS)
        # The subject link to the group stays, and the extra links name their basis and their source turn.
        subjects = {edge.source_id for edge in edges if edge.edge_type == EdgeType.HAS_FACT}
        assert len(subjects) == 1 and cities.metadata["subject"] == "We"
        links = [edge for edge in edges if is_group_link(edge)]
        [link] = [edge for edge in links if "named_in" not in edge.metadata]
        assert (link.edge_type, link.metadata, link.source_id, link.provenance_event_id) == (
            EdgeType.MENTIONS, GROUP_LINK_METADATA, cities.id, cities.evidence_refs[0])
        # The member link also names the turn that named the group.
        [member] = [edge for edge in links if edge is not link]
        assert member.metadata == {**GROUP_LINK_METADATA, "named_in": str(introduction.id)}
        assert not {edge.target_id for edge in links} & subjects

    async def test_the_owners_own_claims_get_no_group_link(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await ingest_messages(engine, user, [{"content": INTRODUCTION, "role": "user"},
                                                 {"content": TRAINS, "role": "user"}])
            assert await group_links(engine, user) == []

    async def test_off_it_makes_no_link(self, config, user):
        async with MemoryEngine.open(linked(config, enable_owner_group_links=False)) as engine:
            await ingest_messages(engine, user, [{"content": INTRODUCTION, "role": "user"},
                                                 {"content": CITIES_TURN, "role": "user"}])
            assert await group_links(engine, user) == []

    async def test_it_needs_the_name_the_carry_remembers(self, config, user):
        async with MemoryEngine.open(linked(config, enable_owner_name_carry=False)) as engine:
            await ingest_messages(engine, user, [{"content": INTRODUCTION, "role": "user"},
                                                 {"content": CITIES_TURN, "role": "user"}])
            assert await group_links(engine, user) == []

    async def test_a_name_no_turn_gave_is_not_invented(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await ingest_messages(engine, user, [{"content": CITIES_TURN, "role": "user"}])
            assert await group_links(engine, user) == []
            names = {node.content for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)}
        assert not names & {OWNER, "Dana"}

    @pytest.mark.parametrize("role", ["assistant", "participant"])
    async def test_only_a_user_turn_speaks_for_the_owner(self, config, user, role):
        async with MemoryEngine.open(linked(config)) as engine:
            await ingest_messages(engine, user, [{"content": INTRODUCTION, "role": "user"},
                                                 {"content": CITIES_TURN, "role": role}])
            assert await group_links(engine, user) == []

    async def test_a_callers_speaker_names_the_owner_for_its_own_turn(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await ingest_messages(engine, user, [{"content": INTRODUCTION, "role": "user"},
                                                 {"content": CITIES_TURN, "role": "user", "speaker": "Dana"}])
            found = await group_links(engine, user)
        assert found == sorted((text, "Dana") for text, _ in BOTH_WE_CLAIMS)

    async def test_it_links_a_turn_ingested_alone(self, config, user):
        async with MemoryEngine.open(linked(config, extraction_batch_target_tokens=0)) as engine:
            engine._pipeline._extraction_provider.extract = AsyncMock(
                side_effect=lambda content, **_: ExtractionResult.model_validate(RECORDS[content]))
            for content in (INTRODUCTION, CITIES_TURN):
                await engine.ingest(content, user_id=user, role="user", session_id="s1", wait_for_extraction=True)
            assert await group_links(engine, user) == sorted(BOTH_WE_CLAIMS + MEMBER_LINKS)

    async def test_a_second_pass_makes_no_more_links(self, config, user):
        async with MemoryEngine.open(linked(config)) as engine:
            await ingest_messages(engine, user, [{"content": INTRODUCTION, "role": "user"},
                                                 {"content": CITIES_TURN, "role": "user"}])
            claims = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
            [event_id] = {str(ref) for node in claims if node.content == CITIES for ref in node.evidence_refs}
            again = await link_group_claims(
                event_id, claims, user_id=user, graph_store=engine._graph_store,
                event_store=engine._event_store, create_edge=engine._graph_store.create_edge)
            assert again == 0 and len(await group_links(engine, user)) == 4

    async def test_a_group_link_does_not_stop_a_repeated_claim_from_merging(self, config, user):
        def extract(content, **_):
            return ExtractionResult.model_validate({
                "entities": [person("Alex"), place("Paris")],
                "facts": [claim("Alex", "lives_in", "Paris", content, object_entity_type="location")],
            })

        async with MemoryEngine.open(on(config)) as engine:
            engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=extract)
            await engine.ingest("Alex lives in Paris.", user_id=user, wait_for_extraction=True)
            [first] = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
            [alex] = [node for node in await engine.query_nodes(user_id=user, node_type=NodeType.ENTITY)
                      if node.content == "Alex"]
            await engine._graph_store.create_edge(MemoryEdge(
                source_id=first.id, target_id=alex.id, edge_type=EdgeType.MENTIONS, user_id=user,
                provenance_event_id=first.evidence_refs[0], metadata=dict(GROUP_LINK_METADATA)))
            await engine.ingest("Alex still lives in Paris.", user_id=user, wait_for_extraction=True)
            [current] = await engine.query_nodes(user_id=user, node_type=NodeType.FACT)
        assert len(current.metadata["merged_claim_ids"]) == 1


# --- Option 3: entities take no part in session context ------------------------------------------------------


def record(minute, kind, session="s1"):
    when = datetime(2026, 10, 19, 12, minute, tzinfo=timezone.utc)
    return MemoryNode(
        user_id="owner", scope=Scope.PERSONAL, node_type=kind, content=f"{kind.value} {minute}", session_id=session,
        created_at=when, updated_at=when, last_reinforced_at=when, confidence_base=.8, salience_base=.5,
    )


def scored(node, score):
    return RetrievalCandidate(node=node, paths=["VECTOR"], path_count=1, semantic_score=.5,
                              lexical_score=0.0, graph_proximity=0.0, composite_score=score)


class SessionStore:
    """Answers the compatibility query with every node of the sessions asked for."""

    def __init__(self, nodes):
        self._nodes = nodes

    async def query_nodes(self, **kwargs):
        return [node for node in self._nodes if node.session_id in kwargs["session_ids"]]


async def expanded(nodes, candidates, **settings):
    config = PackingConfig(session_context_window=3, session_context_score_decay=.5, **settings)
    result = await expand_session_context(candidates, SessionStore(nodes), "owner", config)
    return {item.node.content: item.composite_score for item in result}


class TestSessionContextSkipsEntities:
    def session(self):
        kinds = (NodeType.NOTE, NodeType.ENTITY, NodeType.ENTITY, NodeType.FACT, NodeType.ENTITY, NodeType.NOTE,
                 NodeType.FACT)
        return [record(minute, kind) for minute, kind in enumerate(kinds)]

    async def test_an_entity_in_the_window_takes_no_inherited_score(self):
        nodes = self.session()
        candidates = [scored(nodes[3], .8)]
        off = await expanded(nodes, candidates)
        on = await expanded(nodes, candidates, session_context_skip_entities=True)
        assert off == {f"{node.node_type.value} {node.created_at.minute}": (.8 if node is nodes[3] else .4)
                       for node in nodes}
        # The window still counts the entities' places, so no record moves into it.
        assert on == {"note 0": .4, "fact 3": .8, "note 5": .4, "fact 6": .4}

    async def test_an_entity_keeps_its_own_score(self):
        nodes = self.session()
        candidates = [scored(nodes[3], .8), scored(nodes[1], .1)]
        assert (await expanded(nodes, candidates))["entity 1"] == .4
        assert (await expanded(nodes, candidates, session_context_skip_entities=True))["entity 1"] == .1

    async def test_an_entity_does_not_start_an_expansion(self):
        nodes = [*self.session(), record(0, NodeType.ENTITY, "other"), record(1, NodeType.NOTE, "other")]
        candidates = [scored(nodes[3], .6), scored(nodes[7], .9)]
        assert "note 1" in await expanded(nodes, candidates)
        assert "note 1" not in await expanded(nodes, candidates, session_context_skip_entities=True)

    async def test_entities_do_not_use_up_the_top_k(self):
        nodes = [*self.session(), record(0, NodeType.ENTITY, "other"), record(1, NodeType.NOTE, "other")]
        candidates = [scored(nodes[7], .9), scored(nodes[3], .6)]
        # With one expansion allowed, the entity takes it unless entities are kept out.
        off = await expanded(nodes, candidates, session_context_top_k=1)
        on = await expanded(nodes, candidates, session_context_top_k=1, session_context_skip_entities=True)
        assert "note 5" not in off and "note 1" in off
        assert "note 5" in on and "note 1" not in on

    def test_the_setting_is_left_out_of_a_configuration_that_does_not_use_it(self):
        assert "session_context_skip_entities" not in PackingConfig().model_dump(mode="json")
        on_config = PackingConfig(session_context_skip_entities=True)
        assert on_config.model_dump(mode="json")["session_context_skip_entities"] is True
        assert PackingConfig.model_validate_json(on_config.model_dump_json()) == on_config

"""The graph list ranked by seed count (``scoring.rrf_graph_seed_count``, prism-monorepo issue 276).

Under rank fusion a graph link changed no score unless ``scoring.rrf_graph_weight`` was set, and that list ranks
every graph candidate by hop count, so the owner's own claims, all one hop from the owner, rise together. The seed
count list holds only a record that two or more of the question's seeds reach without passing through the owner's
entity, one of them by a direct link, and that the semantic channel ranks in its top 10; it ranks more seeds first,
then fewer total hops. A record that session, episode or evidence augmentation context pulls in inherits its trigger's score without that
graph term; evidence projection, which replaces a claim with its source, keeps it.
"""

import json
import warnings
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.ingestion.group_links import GROUP_LINK_METADATA
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
import prme.retrieval.candidates as candidates_module
import prme.retrieval.config as config_module
from prme.retrieval.candidates import _seed_reach
from prme.retrieval.config import (
    RRF_GRAPH_SEED_COUNT_SEMANTIC_TOP, RRF_GRAPH_SEED_COUNT_WEIGHT, PackingConfig, ScoringWeights,
)
from prme.retrieval.episode_context import expand_episode_context
from prme.retrieval.evidence_context import augment_evidence_context, project_evidence_context
from prme.retrieval.models import MemoryBundle, RetrievalCandidate, ScoreProvenance
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import EdgeType, EpistemicType, LifecycleState, NodeType, RetrievalMode, Scope
from tests import conftest, test_durable_ingestion, test_graph_fusion
from tests.test_graph_fusion import (
    EXECUTION, NOW, QUESTIONS, RRF, SessionStore, candidate, dev_settings, fused, record, scores,
)
from tests.test_owner_carry_batch import replay_dev_run

config = test_durable_ingestion.config
user = test_durable_ingestion.user
recorded = test_graph_fusion.recorded

SEEDS = ScoringWeights(fusion="rrf", rrf_graph_seed_count=True)
WEIGHT = RRF_GRAPH_SEED_COUNT_WEIGHT


def reached(number, reach, *, semantic=.5, node_type=NodeType.FACT):
    """A graph candidate that ``reach`` = (seeds, total hops, fewest hops) of the question's seeds reach."""
    return candidate(number, semantic=semantic, graph=1.0, node_type=node_type).model_copy(
        update={"graph_seed_reach": reach})


def graph_ranks(found, numbers):
    return [found[number].score_provenance.rank_fusion.graph_rank for number in numbers]


class TestTheList:
    def test_more_seeds_rank_first_then_fewer_total_hops(self):
        pool = [reached(1, (2, 4, 1)), reached(2, (3, 7, 1)), reached(3, (2, 2, 1)), reached(4, (2, 2, 1))]
        assert graph_ranks(scores(pool, SEEDS), (1, 2, 3, 4)) == [3, 1, 2, 2]

    def test_only_a_record_two_seeds_reach_and_one_links_to_directly_joins(self):
        pool = [reached(1, (1, 1, 1)), reached(2, (2, 6, 3)), reached(3, (2, 2, 1), node_type=NodeType.ENTITY),
                reached(4, (2, 2, 1)), candidate(5, semantic=.5, graph=1.0)]
        # Near one seed, only between two, an entity, and a graph hit that was not measured: none joins.
        assert graph_ranks(scores(pool, SEEDS), (1, 2, 3, 4, 5)) == [None, None, None, 1, None]

    def test_only_a_record_the_semantic_channel_ranks_in_its_top_ten_joins(self):
        top = RRF_GRAPH_SEED_COUNT_SEMANTIC_TOP
        pool = [candidate(number, semantic=1 - number / 100) for number in range(1, top)]
        pool += [reached(top, (2, 2, 1), semantic=1 - top / 100), reached(top + 1, (3, 3, 1), semantic=.5),
                 reached(top + 2, (3, 3, 1), semantic=None)]
        found = scores(pool, SEEDS)
        # Tenth by meaning joins; eleventh, and a record only the graph found, do not, however many seeds reach them.
        assert graph_ranks(found, (top, top + 1, top + 2)) == [1, None, None]

    def test_a_record_on_the_list_gains_the_weighted_reciprocal_rank_and_the_score_replays(self):
        found = scores([reached(1, (2, 2, 1), semantic=.9), reached(2, (2, 4, 1), semantic=.8),
                        candidate(3, semantic=.7)], SEEDS)
        assert [found[number].composite_score for number in (1, 2, 3)] == pytest.approx(
            [fused(1, 1, WEIGHT), fused(2, 2, WEIGHT), fused(3)], abs=1e-9)
        for item in found.values():
            restored = ScoreProvenance.model_validate_json(item.score_provenance.model_dump_json())
            assert restored == item.score_provenance and restored.replay_score() == item.composite_score

    def test_the_hop_count_list_ignores_the_seed_count(self):
        hops = ScoringWeights(fusion="rrf", rrf_graph_weight=WEIGHT)
        assert graph_ranks(scores([reached(1, (1, 1, 1)), reached(2, (3, 3, 1))], hops), (1, 2)) == [1, 1]

    def test_off_the_reach_changes_nothing(self):
        pool = [reached(1, (3, 3, 1), semantic=.9), reached(2, (2, 2, 1), semantic=.8)]
        plain = [candidate(1, semantic=.9, graph=1.0), candidate(2, semantic=.8, graph=1.0)]
        assert [item.composite_score for item in scores(pool, RRF).values()] == [
            item.composite_score for item in scores(plain, RRF).values()]

    def test_a_receipt_records_the_list_and_replays_its_ranking(self):
        ranked, _ = score_and_rank([reached(1, (2, 2, 1), semantic=.9), candidate(2, semantic=.8),
                                    reached(3, (3, 5, 1), semantic=.7)], SEEDS, now=NOW)
        saved = make_receipt(request_id=UUID(int=101), user_id="owner", query="trip", reference_time=NOW,
                             scopes=(Scope.PROJECT,), scoring=SEEDS, packing=PackingConfig(), candidates=ranked,
                             bundle=MemoryBundle(), ranking_policy="score_path_id", execution=EXECUTION)
        assert saved.schema_version == 19
        assert saved.replay_ranking() == tuple(item.node.id for item in ranked)
        scoring = json.loads(saved.model_dump_json())["scoring"]
        assert (scoring["rrf_graph_seed_count"], scoring["rrf_graph_weight"]) == (True, WEIGHT)
        assert RetrievalReceipt.model_validate_json(saved.model_dump_json()).checksum == saved.checksum


# --- Which seeds reach a record ---------------------------------------------------------------------------------


class EdgeStore:
    """Answers get_edges(node_ids=...) from a fixed list of links, as the graph stores do."""

    def __init__(self, links):
        self.edges = [MemoryEdge(source_id=source.id, target_id=target.id, edge_type=kind, user_id="owner",
                                 metadata=metadata or {}) for source, target, kind, metadata in links]

    async def get_edges(self, *, node_ids=None, **_):
        wanted = set(node_ids or ())
        return [edge for edge in self.edges if str(edge.source_id) in wanted or str(edge.target_id) in wanted]


def entity(name, **metadata):
    return MemoryNode(user_id="owner", scope=Scope.PERSONAL, node_type=NodeType.ENTITY, content=name,
                      metadata={"entity_type": "person", **metadata})


def fact(text):
    return MemoryNode(user_id="owner", scope=Scope.PERSONAL, node_type=NodeType.FACT, content=text)


OWNER_MARKS = {
    "named owner": {"owner_name": {"event_id": "e1", "basis": "self_introduction"}},
    "owner reference": {"identity_status": "owner_reference"},
}


def trip_graph(owner_marks):
    """The defects chat's trip in miniature, with the #275 links of the cities claim to Alex and Jordan."""
    nodes = {"alex": entity("Alex Rivera", **owner_marks), "jordan": entity("Jordan"), "japan": entity("Japan"),
             "rust": entity("Rust"), "trip": fact("Jordan and I are planning a trip to Japan."),
             "cities": fact("We chose Tokyo and Kyoto."), "allergy": fact("Jordan is allergic to shellfish."),
             "learning": fact("I am learning Rust.")}
    n = nodes
    links = [(n["jordan"], n["trip"], EdgeType.HAS_FACT, None), (n["trip"], n["japan"], EdgeType.MENTIONS, None),
             (n["trip"], n["alex"], EdgeType.MENTIONS, None),
             (n["cities"], n["alex"], EdgeType.MENTIONS, GROUP_LINK_METADATA),
             (n["cities"], n["jordan"], EdgeType.MENTIONS, GROUP_LINK_METADATA),
             (n["jordan"], n["allergy"], EdgeType.HAS_FACT, None),
             (n["alex"], n["learning"], EdgeType.HAS_FACT, None), (n["learning"], n["rust"], EdgeType.MENTIONS, None)]
    return nodes, EdgeStore(links)


def record_reads(store):
    """The node IDs whose links the walk reads from ``store``."""
    asked = []
    read = store.get_edges

    async def get_edges(*, node_ids=None, **kwargs):
        asked.extend(node_ids or ())
        return await read(node_ids=node_ids, **kwargs)

    store.get_edges = get_edges
    return asked


async def reach_of(nodes, store, seeds, max_hops=3):
    reach = await _seed_reach(store, [nodes[name] for name in seeds], {str(node.id): node for node in nodes.values()},
                              max_hops)
    return {name: reach.get(str(node.id)) for name, node in nodes.items()}


class TestSeedReach:
    @pytest.mark.parametrize("marks", OWNER_MARKS.values(), ids=OWNER_MARKS)
    async def test_a_seed_reaches_through_a_person_but_never_through_the_owner(self, marks):
        nodes, store = trip_graph(marks)
        reach = await reach_of(nodes, store, ("alex", "japan"))
        # The trip links to both; the cities claim links to Alex, and Japan reaches it through the trip and Jordan.
        assert (reach["trip"], reach["cities"]) == ((2, 2, 1), (2, 4, 1))
        # Japan reaches the owner's own claim only through Alex, so one seed reaches it.
        assert reach["learning"] == (1, 1, 1)
        # Both reach the allergy only three hops away, through Jordan.
        assert reach["allergy"] == (2, 6, 3)

    async def test_an_unmarked_hub_is_passed_through(self):
        nodes, store = trip_graph({})
        reach = await reach_of(nodes, store, ("alex", "japan"))
        assert reach["learning"] == (2, 4, 1)

    async def test_the_walk_stops_at_the_hop_limit(self):
        nodes, store = trip_graph(OWNER_MARKS["named owner"])
        reach = await reach_of(nodes, store, ("alex", "japan"), max_hops=1)
        assert (reach["trip"], reach["cities"], reach["allergy"]) == ((2, 2, 1), (1, 1, 1), None)

    async def test_the_links_of_an_owner_the_question_does_not_name_are_not_read(self):
        nodes, store = trip_graph(OWNER_MARKS["named owner"])
        asked = record_reads(store)
        reach = await reach_of(nodes, store, ("japan", "jordan"))
        assert str(nodes["alex"].id) not in asked
        # The owner is still reached through the records linked to it, and the owner's own claim is not.
        assert (reach["alex"], reach["learning"], reach["cities"]) == ((2, 4, 2), None, (2, 4, 1))

    async def test_a_walk_past_the_node_limit_reads_nothing(self, monkeypatch):
        nodes, store = trip_graph(OWNER_MARKS["named owner"])
        asked = record_reads(store)
        monkeypatch.setattr(candidates_module, "SEED_REACH_MAX_NODES", 3)
        known = {str(node.id): node for node in nodes.values()}
        assert await _seed_reach(store, [nodes["japan"], nodes["jordan"]], known, 3) is None
        assert asked == []

    async def test_an_alias_no_one_accepted_is_not_a_path(self):
        nodes, store = trip_graph(OWNER_MARKS["named owner"])
        store.edges.append(MemoryEdge(
            source_id=nodes["japan"].id, target_id=nodes["rust"].id, edge_type=EdgeType.RELATES_TO, user_id="owner",
            metadata={"relation": "alias", "identity_verified": False}))
        reach = await reach_of(nodes, store, ("japan", "rust"))
        assert reach["learning"] == (1, 1, 1)
        store.edges[-1] = store.edges[-1].model_copy(update={"metadata": {"relation": "alias", "identity_verified": True}})
        assert (await reach_of(nodes, store, ("japan", "rust")))["learning"] == (2, 3, 1)


# --- Session context ---------------------------------------------------------------------------------------------


class TestSessionContext:
    @pytest.mark.parametrize("weights, inherits_graph_term", [
        (SEEDS, False), (ScoringWeights(fusion="rrf", rrf_graph_weight=WEIGHT), True),
    ], ids=["seed count", "hop count"])
    async def test_a_neighbor_inherits_its_triggers_score_without_the_seed_count_graph_term(
        self, weights, inherits_graph_term,
    ):
        nodes = [record(minute, kind) for minute, kind in enumerate((NodeType.NOTE, NodeType.FACT, NodeType.NOTE))]
        trigger = RetrievalCandidate(node=nodes[1], paths=["VECTOR", "GRAPH"], path_count=2, semantic_score=.9,
                                     graph_proximity=1.0, graph_seed_reach=(2, 2, 1))
        ranked, _ = score_and_rank([trigger], weights, now=NOW)
        packing = PackingConfig(session_context_window=1, session_context_rank_fusion_score_decay=.6)
        result = {item.node.id: item for item in await expand_session_context(ranked, SessionStore(nodes), "owner",
                                                                               packing)}
        base = fused(1, 1, WEIGHT) if inherits_graph_term else fused(1)
        for neighbor in (result[nodes[0].id], result[nodes[2].id]):
            assert neighbor.composite_score == pytest.approx(.6 * base, abs=1e-9)
            assert neighbor.score_provenance.replay_score() == neighbor.composite_score
        # The trigger keeps its own graph term.
        assert result[nodes[1].id].composite_score == pytest.approx(fused(1, 1, WEIGHT), abs=1e-9)


    async def test_a_receipt_with_a_stripped_neighbor_round_trips_and_replays(self):
        nodes = [record(minute, kind) for minute, kind in enumerate((NodeType.NOTE, NodeType.FACT, NodeType.NOTE))]
        trigger = RetrievalCandidate(node=nodes[1], paths=["VECTOR", "GRAPH"], path_count=2, semantic_score=.9,
                                     graph_proximity=1.0, graph_seed_reach=(2, 2, 1))
        ranked, _ = score_and_rank([trigger], SEEDS, now=NOW)
        packing = PackingConfig(session_context_window=1, session_context_rank_fusion_score_decay=.6)
        expanded = await expand_session_context(ranked, SessionStore(nodes), "owner", packing)
        neighbor = next(item for item in expanded if item.node.id == nodes[0].id)
        assert neighbor.score_provenance.rank_fusion.graph_rank is None
        saved = make_receipt(request_id=UUID(int=102), user_id="owner", query="trip", reference_time=NOW,
                             scopes=(Scope.PERSONAL,), scoring=SEEDS, packing=packing, candidates=expanded,
                             bundle=MemoryBundle(), ranking_policy="score_path_id", execution=EXECUTION)
        restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
        assert restored == saved and restored.checksum == saved.checksum
        assert restored.replay_ranking() == tuple(item.node.id for item in expanded)
        assert restored.score_provenance[neighbor.node.id].replay_score() == neighbor.composite_score


# --- Episode and evidence context --------------------------------------------------------------------------------


def note(content, *, session=None, evidence=(), node_type=NodeType.NOTE):
    return MemoryNode(
        id=uuid4(), user_id="owner", session_id=session, node_type=node_type, content=content, scope=Scope.PROJECT,
        evidence_refs=evidence, epistemic_type=EpistemicType.OBSERVED, lifecycle_state=LifecycleState.STABLE,
        confidence=.8, confidence_base=.8, salience=.5, salience_base=.5, created_at=NOW, updated_at=NOW,
        last_reinforced_at=NOW, event_time=NOW,
    )


def listed(node, semantic):
    """A candidate on the vector path that two seeds reach, one directly, so it joins the seed count list."""
    return RetrievalCandidate(node=node, paths=["VECTOR", "GRAPH"], path_count=2, semantic_score=semantic,
                              graph_proximity=1.0, graph_seed_reach=(2, 2, 1))


class TestOtherContext:
    def test_an_episode_member_inherits_its_anchors_score_without_the_graph_term(self):
        anchor = listed(note("The launch discussion covered status updates.", session="launch"), .95)
        member = RetrievalCandidate(node=note("Maya supplied the launch approval.", session="launch"),
                                    paths=["VECTOR"], path_count=1, semantic_score=.05)
        ranked, _ = score_and_rank([anchor, member], SEEDS, now=NOW)
        expanded = {item.node.id: item for item in expand_episode_context(
            ranked, "launch approval", PackingConfig(episode_context_top_k=1, episode_context_score_decay=1.0))}
        promoted = expanded[member.node.id]
        assert "EPISODE_CONTEXT" in promoted.paths
        assert promoted.composite_score == pytest.approx(fused(1), abs=1e-9)
        assert promoted.score_provenance.rank_fusion.graph_rank is None
        assert promoted.score_provenance.replay_score() == promoted.composite_score
        assert expanded[anchor.node.id].composite_score == pytest.approx(fused(1, 1, WEIGHT), abs=1e-9)

    async def test_an_added_source_inherits_without_the_graph_term_and_a_projected_one_keeps_it(self):
        source_id = uuid4()
        source = note("The complete schedule ends transaction work January 15.", evidence=(source_id,))
        source = source.model_copy(update={"id": source_id})
        claim = listed(note("Transaction work ends January 15.", evidence=(source_id,), node_type=NodeType.FACT), .9)
        ranked, _ = score_and_rank([claim], SEEDS, now=NOW)
        assert ranked[0].composite_score == pytest.approx(fused(1, 1, WEIGHT), abs=1e-9)

        async def get_nodes(_ids):
            return [source]

        common = {"graph_store": SimpleNamespace(get_nodes=get_nodes), "user_id": "owner", "scopes": [Scope.PROJECT],
                  "retrieval_mode": RetrievalMode.DEFAULT, "unverified_confidence_threshold": None}
        augmented = await augment_evidence_context(
            ranked, config=PackingConfig(evidence_augmentation_top_k=1, evidence_augmentation_score_decay=.9), **common)
        added = next(item for item in augmented if item.node.id == source_id)
        assert added.composite_score == pytest.approx(.9 * fused(1), abs=1e-9)
        assert added.score_provenance.rank_fusion.graph_rank is None
        assert added.score_provenance.replay_score() == added.composite_score
        # Projection replaces the claim with its source, which takes the claim's place and its whole score.
        projected = await project_evidence_context(
            ranked, config=PackingConfig(evidence_projection_top_k=1, evidence_projection_score_decay=.9), **common)
        [replacement] = projected
        assert replacement.node.id == source_id
        assert replacement.composite_score == pytest.approx(.9 * fused(1, 1, WEIGHT), abs=1e-9)
        assert replacement.score_provenance.rank_fusion.graph_rank == 1
        assert replacement.score_provenance.replay_score() == replacement.composite_score


# --- The setting ---------------------------------------------------------------------------------------------------


class TestTheSetting:
    def test_on_it_sets_the_default_weight_and_an_explicit_weight_wins(self):
        assert SEEDS.rrf_graph_weight == WEIGHT
        assert ScoringWeights(fusion="rrf", rrf_graph_seed_count=True, rrf_graph_weight=.25).rrf_graph_weight == .25
        assert ScoringWeights.model_validate({"fusion": "rrf", "rrf_graph_seed_count": "true"}) == SEEDS

    def test_it_is_serialized_only_when_on_and_is_part_of_the_version(self):
        off = ScoringWeights(fusion="rrf", rrf_graph_seed_count=False)
        assert off == RRF and "rrf_graph_seed_count" not in off.model_dump(mode="json")
        assert off.rank_fusion_opt_ins == {}
        assert ScoringWeights.model_validate_json(SEEDS.model_dump_json()) == SEEDS
        assert SEEDS.rank_fusion_opt_ins == {"rrf_graph_weight": WEIGHT, "rrf_graph_seed_count": True}
        hops = ScoringWeights(fusion="rrf", rrf_graph_weight=WEIGHT)
        assert len({RRF.version_id, hops.version_id, SEEDS.version_id}) == 3

    def test_the_version_names_the_rules_constant(self, monkeypatch):
        before = SEEDS.version_id
        monkeypatch.setattr(config_module, "RRF_GRAPH_SEED_COUNT_SEMANTIC_TOP", RRF_GRAPH_SEED_COUNT_SEMANTIC_TOP + 1)
        assert SEEDS.version_id != before

    def test_weighted_fusion_drops_it_with_a_warning_and_off_is_no_stray_setting(self):
        with pytest.warns(UserWarning, match="rrf_graph_seed_count applies only"):
            assert ScoringWeights(rrf_graph_seed_count=True) == ScoringWeights()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert ScoringWeights(rrf_graph_seed_count=False) == ScoringWeights()
            # As the environment gives it.
            assert ScoringWeights.model_validate({"rrf_graph_seed_count": "false"}) == ScoringWeights()

    def test_the_environment_turns_it_on_and_off(self, monkeypatch):
        monkeypatch.setenv("PRME_SCORING__RRF_GRAPH_SEED_COUNT", "true")
        scoring = PRMEConfig(_env_file=None).scoring
        assert (scoring.rrf_graph_seed_count, scoring.rrf_graph_weight) == (True, WEIGHT)
        monkeypatch.setenv("PRME_SCORING__RRF_GRAPH_SEED_COUNT", "false")
        scoring = PRMEConfig(_env_file=None).scoring
        assert (scoring.rrf_graph_seed_count, scoring.rrf_graph_weight) == (False, None)

    def test_the_product_defaults_turn_it_on_and_false_restores_the_scoring_before(self, monkeypatch):
        # tests/conftest.py keeps the alpha defaults off for every other test.
        conftest.product_defaults(monkeypatch)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            scoring = PRMEConfig(_env_file=None).scoring
        assert (scoring.rrf_graph_seed_count, scoring.rrf_graph_weight) == (True, WEIGHT)
        dumped = scoring.model_dump(mode="json")
        assert (dumped["rrf_graph_seed_count"], dumped["rrf_graph_weight"]) == (True, WEIGHT)
        # A ScoringWeights built in code keeps it off, and the environment variable restores the scoring before.
        assert ScoringWeights(fusion="rrf").rrf_graph_seed_count is False
        monkeypatch.setenv("PRME_SCORING__RRF_GRAPH_SEED_COUNT", "false")
        before = PRMEConfig(_env_file=None).scoring
        assert before == ScoringWeights(fusion="rrf", rrf_recency_boost=.25, rrf_tie_break="event_time")


# --- A retrieval ---------------------------------------------------------------------------------------------------


def seed_count(settings):
    return settings.model_copy(update={"scoring": ScoringWeights.model_validate(
        {**settings.scoring.model_dump(), "rrf_graph_seed_count": True})})


class TestRetrieval:
    async def test_a_retrieval_ranks_the_records_that_connect_the_names_and_replays_its_receipt(self, recorded, user):
        async with MemoryEngine.open(seed_count(dev_settings(recorded, group_links=True))) as engine:
            await replay_dev_run(engine, user)
            await engine.process_extractions(user_id=user)
            response = await engine.retrieve(QUESTIONS[0], user_id=user)
            saved = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
        listed = [item for item in response.results if item.score_provenance.rank_fusion.graph_rank is not None]
        # The introduction names Alex, Jordan and Japan, so its sentence and its note lead the list.
        assert {item.node.node_type for item in listed} <= {NodeType.FACT, NodeType.NOTE, NodeType.DECISION}
        assert any(item.node.content.startswith("My partner Jordan and I") and
                   item.score_provenance.rank_fusion.graph_rank == 1 for item in listed)
        assert saved.scoring.rrf_graph_seed_count is True and saved.scoring.rrf_graph_weight == WEIGHT
        assert saved.replay_ranking() == tuple(item.node.id for item in response.results)

    @pytest.mark.parametrize("group_links", [True, False])
    async def test_the_cities_claim_joins_the_list_through_its_member_links(self, recorded, user, group_links):
        async with MemoryEngine.open(seed_count(dev_settings(recorded, group_links=group_links))) as engine:
            await replay_dev_run(engine, user)
            await engine.process_extractions(user_id=user)
            results = (await engine.retrieve(QUESTIONS[0], user_id=user)).results
        [cities] = [item for item in results if item.node.content.startswith("We chose Tokyo, Kyoto")
                    and item.node.node_type != NodeType.NOTE]
        # The group links (prism-monorepo#275) tie it to Alex and Jordan, so Japan reaches it through Jordan;
        # without them it hangs on its "We" entity alone and no seed reaches it.
        assert (cities.score_provenance.rank_fusion.graph_rank is not None) is group_links

    @pytest.mark.parametrize("trouble, status", [("failure", "backend_error"), ("limit", "neighborhood_limit")])
    async def test_a_walk_that_does_not_run_keeps_the_graph_candidates_and_says_why(
        self, recorded, user, monkeypatch, trouble, status,
    ):
        if trouble == "failure":
            async def broken(*_args, **_kwargs):
                raise RuntimeError("edge read failed")
            monkeypatch.setattr(candidates_module, "_seed_reach", broken)
        else:
            monkeypatch.setattr(candidates_module, "SEED_REACH_MAX_NODES", 1)
        async with MemoryEngine.open(seed_count(dev_settings(recorded, group_links=True))) as engine:
            await replay_dev_run(engine, user)
            await engine.process_extractions(user_id=user)
            response = await engine.retrieve(QUESTIONS[0], user_id=user)
        assert response.metadata.graph_seed_reach == status and response.metadata.backend_failures == {}
        assert any("GRAPH" in item.paths for item in response.results)
        assert all(item.score_provenance.rank_fusion.graph_rank is None for item in response.results)

    async def test_off_no_record_has_a_graph_rank(self, recorded, user):
        async with MemoryEngine.open(dev_settings(recorded, group_links=True)) as engine:
            await replay_dev_run(engine, user)
            await engine.process_extractions(user_id=user)
            response = await engine.retrieve(QUESTIONS[0], user_id=user)
        assert all(item.score_provenance.rank_fusion.graph_rank is None for item in response.results)

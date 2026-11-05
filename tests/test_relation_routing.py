"""Relation routing: a question that asks for a kind of relation is answered from the graph (dwamianm/prism#399).

"What are the names of Maya's dependents?" shares no word with "My husband is Theo and my child is Nico.", and the
claims behind that sentence carry open predicates (``is_husband_of``, ``is_child_of``). With
``enable_relation_routing`` (off by default) the predicate normalizer maps those to ``partner_of`` and ``kin_of``,
the router maps "dependents" to the same relations, and the walk from Maya over those claims adds a weighted rank
fusion list. These tests cover the vocabulary, the normalizer, the concepts, the router, the walk's statement on
DuckDB and PostgreSQL, the route list's scores and their replay, and the retrievals the option leaves unchanged.
No model call.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from uuid import UUID

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.client import config_from_directory
from prme.ingestion.owner_name import OWNER_NAME_KEY, owner_name_marker
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import RRF_ROUTE_WEIGHT, PackingConfig, ScoringWeights
from prme.retrieval.models import MemoryBundle, ScoreProvenance, inherited_score
from prme.ingestion.mention_links import TEXT_LINK_METADATA
from prme.retrieval.relation_walk import (
    MAX_SEEDS,
    ROUTE_PATH,
    SEED_CAP,
    owner_entities,
    walk_route,
    walk_statement,
)
from prme.retrieval.relations import (
    CORE_CONCEPTS,
    DERIVED_CUES,
    ClaimRelation,
    RelationStep,
    concept_map,
    coverage,
    derive_cues,
    normalize_concepts,
    normalize_predicate,
    route_question,
)
from prme.retrieval.scoring import score_and_rank
from prme.types import EdgeType, LifecycleState, NodeType, Scope
from tests import test_durable_ingestion
from tests.test_concurrency import MockExtractionProvider
from tests.test_durable_ingestion import MockEmbeddingProvider
from tests.test_graph_fusion import EXECUTION, NOW, RRF, candidate

config = test_durable_ingestion.config
user = test_durable_ingestion.user

USER = "member"
ASKED_AT = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
DEPENDENTS = "What are the names of Maya's dependents?"
FIRST_PERSON = "Do you remember the names of my dependents?"
UNROUTED = "What is the deductible of the plan?"
ROUTED = ScoringWeights(fusion="rrf", rrf_route_weight=RRF_ROUTE_WEIGHT)


# --- the vocabulary and the normalizer ---------------------------------------------------------------------------


def test_the_derived_cues_follow_the_graph_oracle_rule():
    from benchmarks.graph_oracle.gold import derive_cues as oracle_cues

    assert DERIVED_CUES == derive_cues() == oracle_cues()


@pytest.mark.parametrize("predicate, expected", [
    ("has_husband", ClaimRelation("partner_of")),
    ("is_husband_of", ClaimRelation("partner_of")),
    ("is_married_to", ClaimRelation("partner_of")),
    ("partner_of", ClaimRelation("partner_of")),
    ("has_child", ClaimRelation("kin_of", "child", "object")),
    ("is_child_of", ClaimRelation("kin_of", "child", "subject")),
    ("is_sister_of", ClaimRelation("kin_of", "sister", "subject")),
    ("kin_of", ClaimRelation("kin_of")),
    ("lives_in", ClaimRelation("lives_in")),
    ("resides_in", ClaimRelation("lives_in")),
    ("worksAt", ClaimRelation("works_at")),
    ("planning_trip_to", ClaimRelation("visits")),
    ("dislikes", ClaimRelation("prefers", negative=True)),
    ("is_allergic_to", ClaimRelation("allergic_to")),
])
def test_a_predicate_maps_by_its_words(predicate, expected):
    assert normalize_predicate(predicate) == expected


@pytest.mark.parametrize("predicate", [
    "has_dependents", "diagnosed_with", "coworker_of", "is_separate_plan_with_own_premium", "is_a", "has", "",
    None, "amount",
])
def test_a_predicate_with_no_relation_stays_unmapped(predicate):
    assert normalize_predicate(predicate) is None


def test_coverage_counts_mapped_claims_and_unmapped_predicates():
    report = coverage(["is_husband_of", "is_child_of", "has_dependents", "has_dependents", "amount", None])
    assert report == {"claims": 6, "mapped": 2, "by_relation": {"kin_of": 1, "partner_of": 1},
                      "unmapped": {"has_dependents": 2, "": 1, "amount": 1}}


# --- concepts and routes -----------------------------------------------------------------------------------------


CHILD = RelationStep("kin_of", ("child", "daughter", "kid", "son", "stepchild", "stepdaughter", "stepson"))


@pytest.mark.parametrize("question", [DEPENDENTS, FIRST_PERSON])
def test_dependents_route_to_partner_and_kin(question):
    assert route_question(question) == ((CHILD, RelationStep("partner_of")),)


def test_a_people_word_that_belongs_to_someone_starts_a_two_step_route():
    assert route_question("Where does Dana's sister live?") == (
        (RelationStep("kin_of", ("sister",)),), (RelationStep("lives_in"),))


@pytest.mark.parametrize("question", ["Who is my sister's husband?", "Who is the husband of my sister?"])
def test_a_chain_of_people_words_is_walked_in_order(question):
    assert route_question(question) == ((RelationStep("kin_of", ("sister",)),), (RelationStep("partner_of"),))


@pytest.mark.parametrize("question, relations", [
    ("Who is Maya's husband?", ["partner_of"]),
    ("What are Dana's allergies?", ["allergic_to"]),
    ("What does Alex dislike?", ["prefers"]),
])
def test_a_people_concept_or_cue_word_routes(question, relations):
    route = route_question(question)
    assert route is not None and len(route) == 1 and [step.relation for step in route[0]] == relations


def test_a_question_about_dislikes_asks_for_negative_preferences():
    assert route_question("What does Alex dislike?") == ((RelationStep("prefers", polarity="negative"),),)


@pytest.mark.parametrize("question", [
    UNROUTED, "What did I tell you about my health?", "When did Dana start the new job?",
    "How long has Dana lived in Denver?", "", "?",
])
def test_a_question_no_word_routes_or_a_question_about_when_is_unrouted(question):
    assert route_question(question) is None


def test_a_concept_extension_adds_a_word_and_can_replace_a_built_in_one():
    assert route_question("Who are Maya's beneficiaries?") is None
    extension = {"beneficiaries": ["partner_of", "kin_of:child"], "home": ["visits"]}
    assert route_question("Who are Maya's beneficiaries?", extension) == ((CHILD, RelationStep("partner_of")),)
    assert route_question("Where is Maya's home?", extension) == ((RelationStep("visits"),),)
    assert set(concept_map()) == {"dependent", "family", "spouse", "child", "home", "job", "allergy"}
    assert len(CORE_CONCEPTS) == 7
    assert normalize_concepts(extension) == {"beneficiaries": ["kin_of:child", "partner_of"], "home": ["visits"]}
    assert normalize_concepts({}) is None


@pytest.mark.parametrize("extension", [
    {"dependents": ["spouse_of"]}, {"dependents": []}, {"dependents": "partner_of"}, {"two words": ["partner_of"]},
    {"": ["partner_of"]}, {"dependents": ["has"]}, {"dependents": [3]},
    {"home": ["works_at"], "Home": ["lives_in"]}, {"child": ["kin_of"], "children": ["kin_of:child"]},
])
def test_a_bad_concept_extension_is_refused(extension):
    with pytest.raises(ValueError):
        concept_map(extension)


# --- the route list ----------------------------------------------------------------------------------------------


def routed(number, score, *, semantic=None):
    return candidate(number, semantic=semantic).model_copy(update={"route_score": score})


def test_the_route_list_ranks_by_step_then_names_then_meaning_and_replays():
    pool = [routed(1, (2, 1), semantic=.9), routed(2, (2, 2), semantic=.1), routed(3, (1, 3), semantic=.8),
            candidate(4, semantic=.95)]
    ranked, _ = score_and_rank(pool, ROUTED, now=NOW)
    found = {item.node.id.int: item for item in ranked}
    assert [found[number].score_provenance.rank_fusion.route_rank for number in (1, 2, 3, 4)] == [2, 1, 3, None]
    for item in ranked:
        restored = ScoreProvenance.model_validate_json(item.score_provenance.model_dump_json())
        assert restored == item.score_provenance and restored.replay_score() == item.composite_score
    saved = make_receipt(request_id=UUID(int=7), user_id="owner", query="dependents", reference_time=NOW,
                         scopes=(Scope.PROJECT,), scoring=ROUTED, packing=PackingConfig(), candidates=ranked,
                         bundle=MemoryBundle(), ranking_policy="score_path_id", execution=EXECUTION)
    assert saved.replay_ranking() == tuple(item.node.id for item in ranked)
    assert json.loads(saved.model_dump_json())["scoring"]["rrf_route_weight"] == RRF_ROUTE_WEIGHT
    assert RetrievalReceipt.model_validate_json(saved.model_dump_json()).checksum == saved.checksum


def test_without_the_weight_the_route_scores_change_nothing():
    pool = [routed(1, (2, 2), semantic=.5), candidate(2, semantic=.9)]
    plain = [candidate(1, semantic=.5), candidate(2, semantic=.9)]
    assert ([item.composite_score for item in score_and_rank(pool, RRF, now=NOW)[0]]
            == [item.composite_score for item in score_and_rank(plain, RRF, now=NOW)[0]])


def test_a_route_rank_needs_the_route_weight():
    ranked, _ = score_and_rank([routed(1, (2, 1), semantic=.5)], ROUTED, now=NOW)
    provenance = ranked[0].score_provenance
    with pytest.raises(ValueError, match="rrf_route_weight"):
        ScoreProvenance.model_validate({**provenance.model_dump(), "weights": RRF.model_dump()})


def test_context_inherits_the_score_without_the_route_term():
    ranked, _ = score_and_rank([routed(1, (2, 1), semantic=.5), candidate(2, semantic=.9)], ROUTED, now=NOW)
    top = next(item for item in ranked if item.node.id.int == 1)
    score, provenance = inherited_score(top)
    assert provenance.rank_fusion.route_rank is None and score < top.composite_score
    assert provenance.replay_score() == score


def test_the_route_weight_is_a_rank_fusion_setting():
    assert ROUTED.rank_fusion_opt_ins == {"rrf_route_weight": RRF_ROUTE_WEIGHT}
    assert ROUTED.version_id != RRF.version_id
    with pytest.warns(UserWarning, match="rrf_route_weight"):
        assert ScoringWeights(rrf_route_weight=1.0).rrf_route_weight is None


# --- the walk on both backends -----------------------------------------------------------------------------------


async def family_graph(engine, owner: str, *, scope: Scope = Scope.PERSONAL, indexed: bool = False,
                       ) -> dict[str, MemoryNode]:
    """Maya's household as ingest() stores it: claims are fact nodes, HAS_FACT from the subject, MENTIONS to the object.

    With ``indexed`` the claims are stored with ``store()``, so retrieval's search finds them too; the superseded
    claim is then left out, since ``store()`` writes a current one.
    """
    store = engine._graph_store
    made: dict[str, MemoryNode] = {}

    async def node(key, node_type, content, metadata=None, state=LifecycleState.TENTATIVE):
        if indexed and node_type == NodeType.FACT:
            event_id = await engine.store(content, user_id=owner, node_type=NodeType.FACT, scope=scope,
                                          metadata=metadata)
            made[key] = (await engine.get_event_nodes(event_id, user_id=owner))[0]
            return
        made[key] = MemoryNode(user_id=owner, node_type=node_type, content=content, scope=scope,
                               metadata=metadata or {}, lifecycle_state=state)
        await store.create_node(made[key])

    async def link(source, target, kind, metadata=None):
        await store.create_edge(MemoryEdge(source_id=made[source].id, target_id=made[target].id, edge_type=kind,
                                           user_id=owner, metadata=metadata or {}))

    await node("maya", NodeType.ENTITY, "Maya Okafor", {OWNER_NAME_KEY: owner_name_marker("e1", "speaker")})
    for key, name in (("theo", "Theo"), ("nico", "Nico"), ("ruth", "Ruth"), ("ravi", "Ravi"), ("lisbon", "Lisbon"),
                      ("dana", "Dana")):
        await node(key, NodeType.ENTITY, name)
    claims = (
        ("husband", "My husband is Theo and my child is Nico.", "is_husband_of", "theo", "maya", "positive", None),
        ("child", "Nico is child of Maya Okafor.", "is_child_of", "nico", "maya", "positive", None),
        ("mother", "Maya Okafor is child of Ruth.", "is_child_of", "maya", "ruth", "positive", None),
        ("asked", "Do you remember the names of my dependents?", "has_dependents", "maya", None, "positive", None),
        ("divorced", "Maya is not married to Ravi.", "married_to", "maya", "ravi", "negative", None),
        ("old", "Maya was married to Ravi.", "married_to", "maya", "ravi", "positive", LifecycleState.SUPERSEDED),
        ("home", "Maya lives in Lisbon.", "lives_in", "maya", "lisbon", "positive", None),
        ("dana_home", "Dana lives in Lisbon too.", "lives_in", "dana", "lisbon", "positive", None),
    )
    for key, text, predicate, subject, obj, polarity, state in claims:
        if indexed and state is not None:
            continue
        await node(key, NodeType.FACT, text, {"predicate": predicate, "polarity": polarity},
                   state or LifecycleState.TENTATIVE)
        await link(subject, key, EdgeType.HAS_FACT)
        if obj is not None:
            await link(key, obj, EdgeType.MENTIONS)
    return made


def contents(walk) -> dict[str, tuple[int, int]]:
    return {walk.nodes[node_id].content: score for node_id, score in walk.scores.items()}


class TestTheWalk:
    async def test_dependents_reach_the_partner_and_the_child_but_not_the_parent(self, config, user):
        async with MemoryEngine.open(config) as engine:
            made = await family_graph(engine, user)
            walk = await walk_route(engine._graph_store, route_question(DEPENDENTS), user_id=user,
                                    seed_ids=[str(made["maya"].id)])
        # The sentence that names both dependents first; the parent, the question with the word, the negative and
        # the superseded marriage are not reached.
        assert contents(walk) == {"My husband is Theo and my child is Nico.": (2, 2),
                                  "Nico is child of Maya Okafor.": (2, 1)}
        assert {(claim["relation"], claim.get("role")) for claim in walk.claims} == {
            ("partner_of", None), ("kin_of", "child")}

    async def test_a_two_step_route_walks_through_the_first_steps_people(self, config, user):
        async with MemoryEngine.open(config) as engine:
            made = await family_graph(engine, user)
            walk = await walk_route(engine._graph_store, route_question("Where does Theo's wife live?"),
                                    user_id=user, seed_ids=[str(made["theo"].id)])
        assert contents(walk) == {"Maya lives in Lisbon.": (2, 1), "My husband is Theo and my child is Nico.": (1, 0)}

    async def test_a_text_mention_is_neither_end_of_a_claim(self, config, user):
        # enable_mention_links links "Maya lives in Lisbon." to Dana when its text names her; it is not Dana's claim.
        async with MemoryEngine.open(config) as engine:
            made = await family_graph(engine, user)
            await engine._graph_store.create_edge(MemoryEdge(
                source_id=made["home"].id, target_id=made["dana"].id, edge_type=EdgeType.MENTIONS, user_id=user,
                metadata=dict(TEXT_LINK_METADATA)))
            walk = await walk_route(engine._graph_store, route_question("Where does Dana live?"), user_id=user,
                                    seed_ids=[str(made["dana"].id)])
        assert contents(walk) == {"Dana lives in Lisbon too.": (2, 1)}

    async def test_a_negative_step_reads_only_negative_claims(self, config, user):
        async with MemoryEngine.open(config) as engine:
            made = await family_graph(engine, user)
            walk = await walk_route(engine._graph_store, ((RelationStep("partner_of", polarity="negative"),),),
                                    user_id=user, seed_ids=[str(made["maya"].id)])
        assert contents(walk) == {"Maya is not married to Ravi.": (2, 1)}

    async def test_another_owner_and_another_scope_are_never_read(self, config, user):
        async with MemoryEngine.open(config) as engine:
            made = await family_graph(engine, user)
            other = await family_graph(engine, user + "-other")
            store = engine._graph_store
            route = route_question(DEPENDENTS)
            assert (await walk_route(store, route, user_id=user, seed_ids=[str(other["maya"].id)])).scores == {}
            scoped = await walk_route(store, route, user_id=user, seed_ids=[str(made["maya"].id)],
                                      scopes=[Scope.PROJECT])
            assert scoped.scores == {}

    async def test_the_owner_is_the_marked_name_or_the_speaker(self, config, user):
        async with MemoryEngine.open(config) as engine:
            made = await family_graph(engine, user)
            store = engine._graph_store
            assert await owner_entities(store, user_id=user, scopes=None) == [str(made["maya"].id)]
            assert await owner_entities(store, user_id=user, scopes=None, speaker="theo") == [str(made["theo"].id)]
            # Two names given to the owner: nobody in particular is asking.
            await store.create_node(MemoryNode(user_id=user, node_type=NodeType.ENTITY, content="Sam",
                                               metadata={OWNER_NAME_KEY: owner_name_marker("e2", "speaker")}))
            assert await owner_entities(store, user_id=user, scopes=None) == []

    def test_the_statement_holds_only_parsed_ids_and_caps_each_seed(self):
        statement = walk_statement([str(UUID(int=1))], steps=1)
        assert f"rn <= {SEED_CAP}" in statement and "$1" in statement and "c1" not in statement
        assert "l1 AS" in walk_statement([str(UUID(int=1))], steps=2, scopes=[Scope.PROJECT])
        with pytest.raises(ValueError):
            walk_statement(["1'); DROP TABLE nodes; --"], steps=1)
        with pytest.raises(ValueError):
            walk_statement([], steps=1)


# --- retrieval ---------------------------------------------------------------------------------------------------


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    monkeypatch.setattr("prme.ingestion.extraction.create_extraction_provider", lambda _: MockExtractionProvider())
    monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_ENABLED", "false")
    for name in ("PRME_DATABASE_URL", "PRME_PACK_PATH", "PRME_ENCRYPTION_ENABLED", "PRME_ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)
    import structlog

    saved = structlog.get_config()
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
                        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    yield
    structlog.configure(**saved)


def pack_config(pack: Path, on: bool) -> PRMEConfig:
    return config_from_directory(str(pack)).model_copy(update={"enable_relation_routing": on})


async def build(pack: Path) -> None:
    """The household claims, indexed for retrieval, and a note that is about neither."""
    async with MemoryEngine.open(pack_config(pack, False)) as engine:
        await family_graph(engine, USER, indexed=True)
        await engine.store("The deductible of the plan is $1,500.", user_id=USER)


def receipt_bytes(receipt: RetrievalReceipt) -> dict:
    data = receipt.model_dump(mode="json")
    data.pop("request_id")
    return data


async def owner_ids(pack: Path) -> list[str]:
    async with MemoryEngine.open(pack_config(pack, False)) as engine:
        return await owner_entities(engine._graph_store, user_id=USER, scopes=None)


async def ask(pack: Path, on: bool, question: str, **options):
    async with MemoryEngine.open(pack_config(pack, on)) as engine:
        response = await engine.retrieve(question, user_id=USER, reference_time=ASKED_AT, **options)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
    return response, receipt


@pytest.mark.usefixtures("isolated")
class TestRetrieval:
    @pytest.mark.parametrize("question", [DEPENDENTS, FIRST_PERSON])
    async def test_a_routed_question_ranks_the_family_claim_and_records_the_route(self, tmp_path, question):
        await build(tmp_path / "pack")
        on, receipt = await ask(tmp_path / "pack", True, question)
        texts = [result.node.content for result in on.results]
        family = texts.index("My husband is Theo and my child is Nico.")
        assert ROUTE_PATH in on.results[family].paths
        assert family < texts.index("Do you remember the names of my dependents?") or family == 0
        record = receipt.execution.features["relation_routing"]
        assert record["route"] == [[CHILD.as_dict(), {"relation": "partner_of"}]]
        assert {item["step"] for item in record["candidates"]} == {2}
        assert len(record["candidates"]) == 2 and len(record["claims"]) == 2
        assert receipt.scoring.rrf_route_weight == RRF_ROUTE_WEIGHT
        restored = RetrievalReceipt.model_validate_json(receipt.model_dump_json())
        assert restored.replay_ranking() == receipt.replay_ranking()

    async def test_the_route_moves_the_family_claim_up(self, tmp_path):
        await build(tmp_path / "pack")
        off, _ = await ask(tmp_path / "pack", False, DEPENDENTS)
        on, _ = await ask(tmp_path / "pack", True, DEPENDENTS)

        def place(response):
            return [result.node.content for result in response.results].index(
                "My husband is Theo and my child is Nico.")

        assert place(on) < place(off)

    @pytest.mark.parametrize("question", [UNROUTED, "When did Maya marry Theo?", "Where does Zed's sister live?"])
    async def test_other_retrievals_are_unchanged_byte_for_byte(self, tmp_path, question):
        # No word routes, a question about when, and a route whose walk has no seed.
        await build(tmp_path / "pack")
        off, off_receipt = await ask(tmp_path / "pack", False, question)
        on, receipt = await ask(tmp_path / "pack", True, question)
        assert [result.model_dump(mode="json") for result in on.results] == [
            result.model_dump(mode="json") for result in off.results]
        assert on.bundle.render() == off.bundle.render()
        assert receipt_bytes(receipt) == receipt_bytes(off_receipt)
        assert "relation_routing" not in receipt.execution.features

    async def test_a_configured_route_weight_is_recorded_only_when_it_acts(self, tmp_path):
        await build(tmp_path / "pack")
        base = pack_config(tmp_path / "pack", True)
        weighted = base.model_copy(update={"scoring": ScoringWeights.model_validate(
            {**base.scoring.model_dump(), "rrf_route_weight": 0.5})})
        async with MemoryEngine.open(weighted) as engine:
            plain = await engine.retrieve(UNROUTED, user_id=USER, reference_time=ASKED_AT)
            routed_response = await engine.retrieve(DEPENDENTS, user_id=USER, reference_time=ASKED_AT)
            plain_receipt = await engine.get_retrieval_receipt(str(plain.metadata.request_id), user_id=USER)
            routed_receipt = await engine.get_retrieval_receipt(
                str(routed_response.metadata.request_id), user_id=USER)
        assert plain_receipt.scoring.rrf_route_weight is None
        assert routed_receipt.scoring.rrf_route_weight == 0.5

    async def test_concepts_extend_the_route_for_one_request(self, tmp_path):
        await build(tmp_path / "pack")
        question = "Who are Maya's beneficiaries?"
        _, plain = await ask(tmp_path / "pack", True, question)
        assert "relation_routing" not in plain.execution.features
        on, receipt = await ask(tmp_path / "pack", True, question,
                                concepts={"beneficiaries": ["partner_of", "kin_of:child"]})
        record = receipt.execution.features["relation_routing"]
        assert record["concepts"] == {"beneficiaries": ["kin_of:child", "partner_of"]}
        assert any(ROUTE_PATH in result.paths for result in on.results)

    async def test_a_bad_concept_is_refused_with_the_option_off_too(self, tmp_path):
        await build(tmp_path / "pack")
        with pytest.raises(ValueError):
            await ask(tmp_path / "pack", False, DEPENDENTS, concepts={"dependents": ["spouse_of"]})

    async def test_weighted_scoring_is_never_routed(self, tmp_path):
        await build(tmp_path / "pack")
        weighted = pack_config(tmp_path / "pack", True).model_copy(update={"scoring": ScoringWeights()})
        async with MemoryEngine.open(weighted) as engine:
            response = await engine.retrieve(DEPENDENTS, user_id=USER, reference_time=ASKED_AT)
        assert not any(ROUTE_PATH in result.paths for result in response.results)

    async def test_a_failed_walk_leaves_the_retrieval_as_it_was(self, tmp_path, monkeypatch):
        await build(tmp_path / "pack")
        off, off_receipt = await ask(tmp_path / "pack", False, DEPENDENTS)

        async def broken(*args, **kwargs):
            raise RuntimeError("walk failed")

        monkeypatch.setattr("prme.retrieval.pipeline.walk_route", broken)
        on, receipt = await ask(tmp_path / "pack", True, DEPENDENTS)
        assert [result.node.id for result in on.results] == [result.node.id for result in off.results]
        assert on.metadata.backend_failures.get("ROUTE") == "backend_error"
        assert "relation_routing" not in receipt.execution.features
        # The ranking, scores and settings are the option-off receipt's.
        failed, plain = receipt_bytes(receipt), receipt_bytes(off_receipt)
        assert failed["execution"]["features"] == plain["execution"]["features"]
        assert {key: value for key, value in failed.items() if key != "execution"} == {
            key: value for key, value in plain.items() if key != "execution"}

    async def test_the_owner_seeds_come_first_under_the_seed_cap(self, tmp_path, monkeypatch):
        await build(tmp_path / "pack")
        seen = {}

        async def many(*args, **kwargs):
            # Twenty named entities ahead of the cap, all sorting before the owner's id.
            return [MemoryNode(id=UUID(int=number), user_id=USER, node_type=NodeType.ENTITY, content=f"x{number}")
                    for number in range(1, 21)]

        async def record(store, route, *, user_id, seed_ids, scopes=None):
            seen["seeds"] = seed_ids
            return await walk_route(store, route, user_id=user_id, seed_ids=seed_ids, scopes=scopes)

        monkeypatch.setattr("prme.retrieval.pipeline._find_seed_entities", many)
        monkeypatch.setattr("prme.retrieval.pipeline.walk_route", record)
        on, receipt = await ask(tmp_path / "pack", True, FIRST_PERSON)
        assert len(seen["seeds"]) == MAX_SEEDS
        owner = (await owner_ids(tmp_path / "pack"))[0]
        assert owner in seen["seeds"]
        assert receipt.execution.features["relation_routing"]["seeds"] == seen["seeds"]
        assert any(ROUTE_PATH in result.paths for result in on.results)

    def test_the_http_request_validates_concepts(self):
        from pydantic import ValidationError

        from prme.api.models import RetrieveRequest

        assert RetrieveRequest(query="q", concepts={"beneficiaries": ["partner_of"]}).concepts == {
            "beneficiaries": ["partner_of"]}
        with pytest.raises(ValidationError):
            RetrieveRequest(query="q", concepts={"beneficiaries": ["spouse_of"]})

"""Address-first memory: the belief ledger and the other options memory_mode="address_first" turns on.

``memory_mode="address_first"`` (memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md, off by default) turns on typed
extraction and relation routing, and with them: the belief ledger (``enable_belief_state``,
``prme.retrieval.beliefs``), which reads the claims at an address and says which one holds now, with its catalog
lookup for a question no word routes (``relation_walk.catalog_walk``, pipeline stage 5.5f2);
``packing.session_context_direct_first``; and ``scoring.rrf_recency_order``. The options clock_window_filter,
collapse_identical_text and second_round have their own module, ``tests/test_address_first_repairs.py``. These tests
cover each with small in-memory fixtures. No model call.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.client import config_from_directory
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.beliefs import BELIEF_PATH, Belief, is_single_valued, place_beliefs, read_ledger
from prme.retrieval.config import RRF_RECENCY_ORDER_SEMANTIC_TOP, PackingConfig, ScoringWeights
from prme.retrieval.models import MemoryBundle, ScoreAdjustment
from prme.retrieval.pipeline import _asks_for_history
from prme.retrieval.relation_walk import _role_names_subject, _typed, attribute_key, catalog_walk
from prme.retrieval.relations import ROUTING_VERSION, ClaimRelation, RelationStep, concept_map, route_question
from prme.retrieval.scoring import _order_recency, score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import EdgeType, NodeType, Scope
from tests import test_durable_ingestion
from tests.test_rank_fusion import EXECUTION, RRF, candidate, receipt
from tests.test_rank_fusion import NOW as SCORED_AT
from tests.test_rank_fusion_session_context import _graph, _session_decays
from tests.test_relation_routing import isolated, receipt_bytes  # noqa: F401 (fixture)

config = test_durable_ingestion.config
user = test_durable_ingestion.user

NOW = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)


def node_id(number: int) -> str:
    return str(UUID(int=number))


# --- read_ledger -------------------------------------------------------------------------------------------------


def claim(number, *, days, obj="the clinic", status="current", polarity="positive", valid_to=None) -> MemoryNode:
    """A claim stated ``days`` before NOW (a negative number: after NOW), with its typed fields in metadata."""
    metadata = {"object": obj, "polarity": polarity, **({"status": status} if status is not None else {})}
    return MemoryNode(id=UUID(int=number), user_id="owner", node_type=NodeType.FACT, content=f"claim {number}",
                      event_time=NOW - timedelta(days=days), valid_to=valid_to, metadata=metadata)


def ledger(*members, relation="owns", history=False, last_step=0):
    """``read_ledger`` over claims all read on one pivot. A member is a node, or (node, role), or
    (node, role, relation)."""
    nodes, claims, pivots = {}, [], {}
    for member in members:
        node, role, rel = (*member, relation)[:3] if isinstance(member, tuple) else (member, None, relation)
        nodes[str(node.id)] = node
        pivots[str(node.id)] = "sam"
        claims.append({"node_id": str(node.id), "step": 0, "relation": rel, **({"role": role} if role else {})})
    return read_ledger(nodes, claims, pivots, last_step=last_step, now=NOW, history=history)


def states(beliefs) -> list[tuple[int, str]]:
    return [(UUID(belief.node_id).int, belief.state) for belief in beliefs]


@pytest.mark.parametrize("options, state", [
    ({"days": 3}, "current"),
    ({"days": 3, "status": None}, "current"),
    ({"days": 3, "status": "done"}, "current"),
    ({"days": -5, "status": "planned"}, "planned"),
    ({"days": 5, "status": "planned"}, "current"),
    ({"days": 3, "status": "past"}, "ended"),
    ({"days": 3, "status": "superseded"}, "ended"),
    ({"days": 3, "polarity": "negative"}, "negated"),
    ({"days": 3, "valid_to": NOW - timedelta(days=1)}, "ended"),
    ({"days": 3, "valid_to": NOW + timedelta(days=1)}, "current"),
])
def test_a_claim_alone_has_the_state_it_records(options, state):
    assert states(ledger(claim(1, **options))) == [(1, state)]


def test_an_end_closes_the_earlier_claims_with_the_same_value_and_no_others():
    beliefs = ledger(
        claim(1, days=30, obj="a greyhound"),
        claim(2, days=20, obj="a beagle"),
        claim(3, days=10, obj="the greyhound", status="past"),
        claim(4, days=5, obj="greyhound"),  # stated after the end: it holds again
    )
    assert states(beliefs) == [(4, "current"), (2, "current"), (3, "ended"), (1, "ended")]


def test_a_negated_claim_closes_an_earlier_claim_with_the_same_value():
    beliefs = ledger(claim(1, days=10, obj="a cat"), claim(2, days=2, obj="a cat", polarity="negative"))
    assert states(beliefs) == [(1, "ended"), (2, "negated")]


def test_at_a_single_valued_relation_an_end_closes_every_earlier_claim():
    # "I quit" need not name the place it ends.
    beliefs = ledger(
        claim(1, days=60, obj="St. Mary's hospital"),
        claim(2, days=25, obj="my job", status="past"),
        claim(3, days=7, obj="a pediatric clinic"),
        relation="works_at",
    )
    assert states(beliefs) == [(3, "current"), (2, "ended"), (1, "ended")]


def test_at_a_single_valued_relation_a_negation_closes_only_the_same_value():
    beliefs = ledger(claim(1, days=60, obj="the hospital"), claim(2, days=10, obj="the bakery", polarity="negative"),
                     relation="works_at")
    assert states(beliefs) == [(1, "current"), (2, "negated")]


def test_at_a_single_valued_relation_the_newest_holder_is_current_and_the_older_ones_earlier():
    beliefs = ledger(claim(1, days=60, obj="the hospital"), claim(2, days=30, obj="a school"),
                     claim(3, days=7, obj="a clinic"), relation="works_at")
    assert states(beliefs) == [(3, "current"), (2, "earlier"), (1, "earlier")]


def test_a_set_valued_relation_keeps_every_value_current():
    assert states(ledger(claim(1, days=30, obj="a beagle"), claim(2, days=10, obj="a cat"))) == [
        (2, "current"), (1, "current")]


@pytest.mark.parametrize("relation, single", [
    ("works_at", True), ("lives_in", True), ("partner_of", True), ("located_in", True), ("has:diet", True),
    ("owns", False), ("kin_of", False), ("allergic_to", False), ("prefers", False),
])
def test_which_relations_hold_one_value(relation, single):
    assert is_single_valued(relation) is single


def test_the_role_splits_an_address_at_kin_of():
    # Priya the sister and Priya the mother are two addresses: the end of one does not close the other.
    split = ledger((claim(1, days=30, obj="Priya"), "sister", "kin_of"),
                   (claim(2, days=10, obj="Priya", status="past"), "mother", "kin_of"))
    assert states(split) == [(1, "current"), (2, "ended")]
    assert {UUID(belief.node_id).int: belief.role for belief in split} == {1: "sister", 2: "mother"}
    same = ledger((claim(1, days=30, obj="Priya"), "sister", "kin_of"),
                  (claim(2, days=10, obj="Priya", status="past"), "sister", "kin_of"))
    assert states(same) == [(2, "ended"), (1, "ended")]


def test_the_role_does_not_split_an_address_at_works_at():
    beliefs = ledger((claim(1, days=60, obj="the hospital"), "nurse", "works_at"),
                     (claim(2, days=7, obj="a clinic"), "pediatric nurse", "works_at"))
    assert states(beliefs) == [(2, "current"), (1, "earlier")]
    assert {belief.role for belief in beliefs} == {None}


def test_for_now_the_order_is_current_planned_earlier_ended_negated():
    beliefs = ledger(
        claim(1, days=3, obj="a beagle"),
        claim(2, days=-2, obj="a kitten", status="planned"),
        claim(3, days=-9, obj="a parrot", status="planned"),
        claim(4, days=1, obj="a hamster", status="past"),
        claim(5, days=1, obj="a snake", polarity="negative"),
        (claim(6, days=40, obj="the hospital"), None, "works_at"),
        (claim(7, days=8, obj="a clinic"), None, "works_at"),
    )
    assert states(beliefs) == [(1, "current"), (7, "current"), (2, "planned"), (3, "planned"), (6, "earlier"),
                               (4, "ended"), (5, "negated")]


def test_history_reads_the_ledger_in_time_order():
    beliefs = ledger(claim(3, days=7, obj="a clinic"), claim(1, days=60, obj="the hospital"),
                     claim(2, days=25, obj="my job", status="past"), relation="works_at", history=True)
    assert states(beliefs) == [(1, "ended"), (2, "ended"), (3, "current")]


def said(number, text, *, days, obj, status="current") -> MemoryNode:
    """A claim with its own text, stated ``days`` before NOW."""
    node = claim(number, days=days, obj=obj, status=status)
    return node.model_copy(update={"content": text})


@pytest.mark.parametrize("history", [False, True])
def test_claims_stated_at_one_time_are_ordered_by_their_text(history):
    beliefs = ledger(said(3, "We have a cat.", days=3, obj="a cat"),
                     said(5, "We have a beagle.", days=3, obj="a beagle"), history=history)
    assert [UUID(belief.node_id).int for belief in beliefs] == [5, 3]


@pytest.mark.parametrize("history", [False, True])
def test_equal_time_and_text_fall_back_on_node_id(history):
    beliefs = ledger(said(5, "We have a cat.", days=3, obj="a cat"), said(3, "We have a cat.", days=3, obj="a cat"),
                     history=history)
    assert [UUID(belief.node_id).int for belief in beliefs] == [3, 5]


def test_two_claims_stated_at_one_time_at_works_at_are_both_current():
    beliefs = ledger(said(1, "I work as a nurse at St. Mary's hospital.", days=60, obj="St. Mary's hospital"),
                     said(2, "I start at a pediatric clinic on November 2.", days=20, obj="a pediatric clinic"),
                     said(3, "Sam works day shifts only.", days=20, obj="day shifts only"),
                     relation="works_at")
    assert {UUID(belief.node_id).int: belief.state for belief in beliefs} == {1: "earlier", 2: "current", 3: "current"}


# One chat turn as a build stores it: "Big news: I quit the hospital. I start at a pediatric clinic on November 2,
# day shifts only." Its claims share a stated time; the nurse claim is weeks older.
TURN = (
    ("I work as a nurse at St. Mary's hospital.", 60, "St. Mary's hospital", "current"),
    ("Big news: I quit the hospital.", 20, "the hospital", "past"),
    ("I start at a pediatric clinic on November 2.", 20, "a pediatric clinic", "current"),
    ("Sam works day shifts only.", 20, "day shifts only", "current"),
)


def built(ids: tuple[int, ...], order: tuple[int, ...]):
    """The turn's claims with the node ids a build gave them, listed in ``order``: (text, state) in ledger order."""
    nodes = [said(ids[index], text, days=days, obj=obj, status=status)
             for index, (text, days, obj, status) in enumerate(TURN)]
    beliefs = ledger(*[nodes[index] for index in order], relation="works_at")
    by_id = {str(node.id): node.content for node in nodes}
    return [(by_id[belief.node_id], belief.state) for belief in beliefs]


@pytest.mark.parametrize("ids, order", [
    ((1, 2, 3, 4), (0, 1, 2, 3)),
    ((4, 3, 2, 1), (3, 2, 1, 0)),
    ((2, 4, 1, 3), (2, 0, 3, 1)),
])
def test_the_same_claims_give_the_same_ledger_whatever_ids_and_order_a_build_gives_them(ids, order):
    assert built(ids, order) == built((1, 2, 3, 4), (0, 1, 2, 3))


def test_only_the_last_steps_claims_with_a_node_and_a_pivot_are_read():
    nodes = {node_id(1): claim(1, days=3), node_id(2): claim(2, days=3), node_id(4): claim(4, days=3)}
    claims = [{"node_id": node_id(number), "step": step, "relation": "owns"}
              for number, step in ((1, 1), (2, 0), (3, 1), (4, 1))]
    pivots = {node_id(1): "sam", node_id(2): "sam", node_id(3): "sam"}
    # 2 is on another step, 3 has no node and 4 no pivot.
    assert states(read_ledger(nodes, claims, pivots, last_step=1, now=NOW)) == [(1, "current")]


@pytest.mark.parametrize("question, names, history", [
    ("Where does Sam work now?", (), False),
    ("What pet does Sam have?", (), False),
    ("Where did Sam work before the clinic?", (), True),
    ("When did Sam start at the clinic?", (), True),
    ("Where did Sam use to work?", ("Sam", "the clinic"), True),
    ("List every job Sam has had.", (), True),
    # A word inside a name the ledger's claims use is not a cue.
    ("Where is the first aid kit now?", ("first aid kit", "bedroom nightstand"), False),
    ("Where was the first aid kit before the move?", ("first aid kit", "bedroom nightstand"), True),
    ("Since when does the Every Day Cafe open early?", ("Every Day Cafe",), True),
])
def test_a_question_about_history_is_read_in_time_order(question, names, history):
    assert _asks_for_history(question, names) is history


def test_without_the_names_a_word_inside_a_name_still_reads_as_a_cue():
    # The shared current-state check reads "first" in "first aid kit" as a history word too (reported, unchanged).
    assert _asks_for_history("Where is the first aid kit now?") is True


# --- place_beliefs -------------------------------------------------------------------------------------------------


def scored_pool(*semantics):
    """Candidates 1, 2, ... ranked by rank fusion, the first the best."""
    ranked, _ = score_and_rank([candidate(number, semantic=value) for number, value in enumerate(semantics, 1)],
                               RRF, now=SCORED_AT)
    return ranked


def belief(number, state="current") -> Belief:
    return Belief(node_id=node_id(number), state=state, relation="works_at", role=None, pivot="sam", when=NOW)


def order(results) -> list[int]:
    return [result.node.id.int for result in results]


def test_claims_trade_the_places_they_hold_in_ledger_order_and_their_scores_replay():
    # The ledger orders the address's claims among their own places; it does not lift them.
    scored = scored_pool(.9, .7, .5, .3)
    before = {result.node.id.int: result.composite_score for result in scored}
    results, placed = place_beliefs(scored, [belief(3), belief(2, "ended")])
    assert placed == 2 and order(results) == [1, 3, 2, 4]
    for result, place, state in ((results[1], 2, "current"), (results[2], 3, "ended")):
        assert BELIEF_PATH in result.paths and result.belief_state == state
        # The member's own score times the coefficient: the place's score, to the last bit or two.
        assert math.isclose(result.composite_score, before[place], rel_tol=1e-12)
        adjustment = result.score_provenance.adjustments[-1]
        assert (adjustment.kind, adjustment.source_node_id) == ("belief", UUID(int=place))
        assert math.isclose(adjustment.coefficient, before[place] / scored[result.node.id.int - 1].composite_score)
        assert result.score_provenance.replay_score() == result.composite_score
    assert {item.node.id.int: item.composite_score for item in (results[0], results[3])} == {1: before[1],
                                                                                            4: before[4]}
    assert results[0].belief_state is None


def test_a_claim_that_was_not_retrieved_is_not_added():
    # Version 3: a claim the search did not return, or that an earlier stage left out, stays out.
    scored = scored_pool(.9, .5, .3)
    results, placed = place_beliefs(scored, [belief(2), belief(5, "earlier")])
    assert placed == 1 and order(results) == [1, 2, 3]
    assert [result.composite_score for result in results] == [result.composite_score for result in scored]
    assert results[1].belief_state == "current" and BELIEF_PATH in results[1].paths
    assert results[1].score_provenance == scored[1].score_provenance


def test_every_other_candidate_keeps_its_rank_and_its_score():
    scored = scored_pool(.95, .9, .8, .7, .6, .5, .4, .3)
    # The ledger names the claims at ranks 7, 2 and 5, in that order, and one claim that was not retrieved.
    results, placed = place_beliefs(scored, [belief(7), belief(9), belief(2, "earlier"), belief(5, "ended")])
    assert placed == 3 and order(results) == [1, 7, 3, 4, 2, 6, 5, 8]
    for rank in (0, 2, 3, 5, 7):
        assert results[rank] == scored[rank]
    assert all(math.isclose(results[rank].composite_score, scored[rank].composite_score, rel_tol=1e-12)
               for rank in (1, 4, 6))
    assert [results[rank].belief_state for rank in (1, 4, 6)] == ["current", "earlier", "ended"]
    # The group's best relevance reaches every member, and a member keeps its own when that is higher.
    assert all(results[rank].context_relevance >= scored[1].semantic_score for rank in (1, 4, 6))


def test_the_placed_results_replay_from_a_receipt():
    scored = scored_pool(.95, .9, .8, .7, .6, .5, .4, .3)
    results, _placed = place_beliefs(scored, [belief(7), belief(2, "earlier"), belief(5, "ended")])
    saved = receipt(results, policy="score_id")
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.replay_ranking() == saved.replay_ranking() == tuple(result.node.id for result in results)
    assert all(result.score_provenance.replay_score() == result.composite_score for result in results)


@pytest.mark.parametrize("ledger_order, moved", [([3, 2], True), ([2, 3], False)])
def test_two_claims_with_equal_scores_keep_the_ledger_order(ledger_order, moved):
    # Candidates 2 and 3 tie on every channel, so they share a score; node id alone would put 2 first.
    scored = scored_pool(.9, .5, .5, .3)
    assert scored[1].composite_score == scored[2].composite_score
    results, placed = place_beliefs(scored, [belief(number) for number in ledger_order])
    assert placed == 2 and order(results) == [1, *ledger_order, 4]
    assert math.isclose(results[1].composite_score, scored[1].composite_score, rel_tol=1e-12)
    assert (results[2].composite_score < results[1].composite_score) is moved
    assert (results[0], results[3]) == (scored[0], scored[3])
    saved = receipt(results, policy="score_id")
    assert saved.replay_ranking() == tuple(result.node.id for result in results)


@pytest.mark.parametrize("coefficient, valid", [(.5, True), (1.0, True), (3.0, True), (0.0, False), (-1.0, False)])
def test_a_belief_coefficient_is_any_positive_number(coefficient, valid):
    def make():
        return ScoreAdjustment(kind="belief", coefficient=coefficient, source_node_id=UUID(int=1))
    if valid:
        assert make().coefficient == coefficient
    else:
        with pytest.raises(ValidationError, match="above zero"):
            make()


def test_nothing_to_place_leaves_the_results_as_they_were():
    scored = scored_pool(.9, .5)
    assert place_beliefs([], [belief(1)]) == ([], 0)
    unchanged, placed = place_beliefs(scored, [])
    assert unchanged is scored and placed == 0
    # None of the claims retrieved: nothing to reorder.
    unchanged, placed = place_beliefs(scored, [belief(9)])
    assert unchanged is scored and placed == 0


# --- the catalog ---------------------------------------------------------------------------------------------------


JOB_CLAIMS = (
    # key, text, typed fields, object entity, days before NOW
    ("hospital", "I work as a nurse at St. Mary's hospital.",
     {"relation": "works_at", "predicate": "works_at", "object": "St. Mary's hospital"}, "hospital_place", 60),
    ("clinic", "I start at a pediatric clinic on November 2.",
     {"relation": "works_at", "predicate": "starts_at", "object": "pediatric clinic"}, "clinic_place", 20),
)
OTHER_CLAIMS = (
    ("beagle", "We adopted a beagle called Miso.",
     {"relation": "owns", "predicate": "adopted", "object": "Miso"}, "miso", 20),
    ("vegetarian", "I'm vegetarian.", {"relation": "has", "predicate": "diet", "object": "vegetarian"}, None, 60),
    ("fish", "I'm not strictly vegetarian anymore.",
     {"relation": "has", "predicate": "is_diet", "object": "not strictly vegetarian"}, None, 20),
)


async def sam_graph(engine, owner: str, claims, *, indexed: bool) -> dict[str, MemoryNode]:
    """Sam's claims as typed extraction stores them: claim nodes with HAS_FACT from Sam and MENTIONS to the object.

    With ``indexed`` the claims are stored with ``store()``, so retrieval's search finds them too.
    """
    store = engine._graph_store
    made: dict[str, MemoryNode] = {}
    for key, name in (("sam", "Sam"), ("hospital_place", "St. Mary's hospital"), ("clinic_place", "pediatric clinic"),
                      ("miso", "Miso")):
        made[key] = MemoryNode(user_id=owner, node_type=NodeType.ENTITY, content=name)
        await store.create_node(made[key])
    for key, text, typed, target, days in claims:
        metadata = {"subject": "Sam", "polarity": "positive", "status": "current", **typed}
        when = NOW - timedelta(days=days)
        if indexed:
            event_id = await engine.store(text, user_id=owner, node_type=NodeType.FACT, metadata=metadata,
                                          event_time=when)
            made[key] = (await engine.get_event_nodes(event_id, user_id=owner))[0]
        else:
            made[key] = MemoryNode(user_id=owner, node_type=NodeType.FACT, content=text, metadata=metadata,
                                   event_time=when)
            await store.create_node(made[key])
        await store.create_edge(MemoryEdge(source_id=made["sam"].id, target_id=made[key].id,
                                           edge_type=EdgeType.HAS_FACT, user_id=owner))
        if target is not None:
            await store.create_edge(MemoryEdge(source_id=made[key].id, target_id=made[target].id,
                                               edge_type=EdgeType.MENTIONS, user_id=owner))
    return made


@pytest.mark.parametrize("predicate, key", [("is_diet", "diet"), ("has_diet", "diet"), ("diet", "diet"),
                                            ("Diet Type", "diet_type"), ("is", "value"), ("", "value")])
def test_an_attribute_key_drops_copulas(predicate, key):
    assert attribute_key(predicate) == key


class TestCatalogWalk:
    async def walk(self, config, user, best: str, *, floor=.5):
        async with MemoryEngine.open(config) as engine:
            made = await sam_graph(engine, user, JOB_CLAIMS + OTHER_CLAIMS, indexed=False)
            semantic = {str(made[key].id): .3 for key, *_ in JOB_CLAIMS + OTHER_CLAIMS}
            semantic[str(made[best].id)] = .6
            walk = await catalog_walk(engine._graph_store, user_id=user, seed_ids=[str(made["sam"].id)],
                                      semantic=semantic, floor=floor)
        return made, walk

    async def test_the_best_matching_claim_names_the_address_and_every_claim_there_is_read(self, config, user):
        made, walk = await self.walk(config, user, "hospital")
        assert set(walk.nodes) == {str(made["hospital"].id), str(made["clinic"].id)}
        assert {claim["relation"] for claim in walk.claims} == {"works_at"}
        assert {claim["step"] for claim in walk.claims} == {0}
        assert set(walk.pivots.values()) == {str(made["sam"].id)}
        assert set(walk.scores.values()) == {(2, 0)}

    async def test_a_literal_attribute_is_one_address_by_its_predicates_words(self, config, user):
        made, walk = await self.walk(config, user, "fish")
        assert set(walk.nodes) == {str(made["vegetarian"].id), str(made["fish"].id)}
        assert {claim["relation"] for claim in walk.claims} == {"has:diet"}

    async def test_a_set_valued_address_holds_only_its_relation(self, config, user):
        made, walk = await self.walk(config, user, "beagle")
        assert set(walk.nodes) == {str(made["beagle"].id)} and walk.claims[0]["relation"] == "owns"

    async def test_nothing_is_read_below_the_floor_or_without_seeds(self, config, user):
        _, walk = await self.walk(config, user, "hospital", floor=.9)
        assert walk is None
        async with MemoryEngine.open(config) as engine:
            made = await sam_graph(engine, user, JOB_CLAIMS, indexed=False)
            store = engine._graph_store
            assert await catalog_walk(store, user_id=user, seed_ids=[], semantic={"x": 1.0}, floor=0) is None
            assert await catalog_walk(store, user_id=user, seed_ids=[str(made["sam"].id)], semantic={},
                                      floor=0) is None
            # Another owner's claims are never read.
            assert await catalog_walk(store, user_id=user + "-other", seed_ids=[str(made["sam"].id)],
                                      semantic={str(made["hospital"].id): .9}, floor=0) is None


# --- the pipeline stage --------------------------------------------------------------------------------------------


UNROUTED_ABOUT_SAM = "What is Sam up to these days?"
ABOUT_NOBODY = "What is the deductible of the plan?"


def belief_config(pack: Path, on: bool | None) -> PRMEConfig:
    """The pack's configuration with enable_belief_state on, off, or not set (None)."""
    base = config_from_directory(str(pack))
    return base if on is None else base.model_copy(update={"enable_belief_state": on})


async def build_jobs(pack: Path) -> None:
    async with MemoryEngine.open(belief_config(pack, None)) as engine:
        await sam_graph(engine, "member", JOB_CLAIMS, indexed=True)
        await engine.store("The deductible of the plan is $1,500.", user_id="member")


async def ask(pack: Path, on: bool | None, question: str):
    async with MemoryEngine.open(belief_config(pack, on)) as engine:
        response = await engine.retrieve(question, user_id="member", reference_time=NOW)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id="member")
    return response, receipt


@pytest.mark.usefixtures("isolated")
class TestTheStage:
    async def test_a_question_no_word_routes_groups_the_claims_at_its_best_address(self, tmp_path):
        assert route_question(UNROUTED_ABOUT_SAM) is None
        await build_jobs(tmp_path / "pack")
        response, receipt = await ask(tmp_path / "pack", True, UNROUTED_ABOUT_SAM)
        placed = [(rank, result.belief_state, result.node.content) for rank, result in enumerate(response.results)
                  if BELIEF_PATH in result.paths]
        assert [(state, text) for _rank, state, text in placed] == [
            ("current", "I start at a pediatric clinic on November 2."),
            ("earlier", "I work as a nurse at St. Mary's hospital.")]
        assert placed[1][0] == placed[0][0] + 1
        record = receipt.execution.features["belief_state"]
        assert record["source"] == "catalog" and [claim["state"] for claim in record["claims"]] == [
            "current", "earlier"]
        restored = RetrievalReceipt.model_validate_json(receipt.model_dump_json())
        assert restored.replay_ranking() == receipt.replay_ranking()
        assert all(result.score_provenance.replay_score() == result.composite_score
                   for result in response.results if BELIEF_PATH in result.paths)

    async def test_off_is_exactly_the_retrieval_without_the_option(self, tmp_path):
        await build_jobs(tmp_path / "pack")
        absent, absent_receipt = await ask(tmp_path / "pack", None, UNROUTED_ABOUT_SAM)
        off, off_receipt = await ask(tmp_path / "pack", False, UNROUTED_ABOUT_SAM)
        assert [result.model_dump(mode="json") for result in off.results] == [
            result.model_dump(mode="json") for result in absent.results]
        assert off.bundle.render() == absent.bundle.render()
        assert receipt_bytes(off_receipt) == receipt_bytes(absent_receipt)
        assert "belief_state" not in off_receipt.execution.features

    async def test_on_with_nothing_to_place_keeps_the_bytes(self, tmp_path):
        # The question names no entity, so the catalog has no seed and nothing is placed.
        await build_jobs(tmp_path / "pack")
        off, off_receipt = await ask(tmp_path / "pack", None, ABOUT_NOBODY)
        on, on_receipt = await ask(tmp_path / "pack", True, ABOUT_NOBODY)
        assert [result.model_dump(mode="json") for result in on.results] == [
            result.model_dump(mode="json") for result in off.results]
        assert on.bundle.render() == off.bundle.render()
        assert receipt_bytes(on_receipt) == receipt_bytes(off_receipt)
        assert "belief_state" not in on_receipt.execution.features


# --- session_context_direct_first ----------------------------------------------------------------------------------


DECAY = .6
# Under rank fusion a candidate ranked r on both channels scores 61 / (60 + r) of the first, so 0.6 of the first
# place lands near place 42: with sixty candidates, a direct_first above that is what the cap has to hold.
POOL = 60


def session_pool():
    """Sixty candidates ranked 1 to 60 on both channels; the first has a session neighbor, 99."""
    pool = [candidate(number, semantic=1 - number / 100, lexical=1 - number / 100,
                      session="conversation" if number == 1 else None) for number in range(1, POOL + 1)]
    ranked, _ = score_and_rank(pool, RRF, now=SCORED_AT)
    return pool, ranked, candidate(999, session="conversation")


def session_packing(direct_first=None) -> PackingConfig:
    return PackingConfig(session_context_rank_fusion_score_decay=DECAY,
                         **({"session_context_direct_first": direct_first} if direct_first else {}))


async def expand(direct_first=None):
    pool, ranked, neighbor = session_pool()
    expanded = await expand_session_context(ranked, _graph(pool[0], neighbor), "owner", session_packing(direct_first))
    return ranked, expanded, next(item for item in expanded if item.node.id == neighbor.node.id)


async def test_unbounded_the_neighbor_outranks_direct_matches_from_about_place_42():
    ranked, expanded, added = await expand()
    assert _session_decays(added) == [DECAY]
    assert 40 < [item.node.id for item in expanded].index(added.node.id) + 1 < 45


@pytest.mark.parametrize("direct_first", [45, 50, POOL])
async def test_a_neighbor_never_outranks_the_nth_direct_match(direct_first):
    ranked, expanded, added = await expand(direct_first)
    assert added.composite_score < ranked[direct_first - 1].composite_score
    assert [item.node.id for item in expanded[:direct_first]] == [item.node.id for item in ranked[:direct_first]]
    assert 0 < _session_decays(added)[0] < DECAY
    assert added.score_provenance.replay_score() == added.composite_score


@pytest.mark.parametrize("direct_first", [10, POOL + 1])
async def test_a_neighbor_already_below_the_nth_or_too_few_candidates_keep_the_decay(direct_first):
    # Place 10 is above the neighbor's own place, and with fewer than N candidates nothing is capped.
    _ranked, _expanded, added = await expand(direct_first)
    assert _session_decays(added) == [DECAY]


async def test_a_capped_decay_validates_only_in_a_receipt_that_records_the_option():
    packing = session_packing(50)
    _ranked, expanded, added = await expand(50)
    assert _session_decays(added)[0] < DECAY

    def receipt(settings):
        return make_receipt(request_id=UUID(int=7), user_id="owner", query="telescope", reference_time=SCORED_AT,
                            scopes=(Scope.PROJECT,), scoring=RRF, packing=settings, candidates=expanded,
                            bundle=MemoryBundle(), ranking_policy="score_path_id", execution=EXECUTION)

    saved = receipt(packing)
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.checksum == saved.checksum and restored.replay_ranking() == saved.replay_ranking()
    with pytest.raises(ValidationError, match="decay"):
        receipt(session_packing())


@pytest.mark.parametrize("value", [0, -1])
def test_session_context_direct_first_is_a_positive_count(value):
    with pytest.raises(ValidationError):
        PackingConfig(session_context_direct_first=value)


# --- rrf_recency_order ---------------------------------------------------------------------------------------------


def dated(*days):
    return [candidate(number, semantic=.5, days=value) for number, value in enumerate(days, 1)]


def test_the_newest_match_has_recency_one_and_the_oldest_decays_by_the_span():
    recency = _order_recency(dated(10, 5, 0), [3, 2, 1])
    assert recency[2] == 1.0
    assert recency[1] == pytest.approx(math.exp(-2.5)) and recency[0] == pytest.approx(math.exp(-5))


def test_the_span_is_the_scale_whether_it_is_an_hour_or_a_year():
    hours = _order_recency(dated(1 / 24, 0.5 / 24, 0), [1, 2, 3])
    year = _order_recency(dated(365, 182.5, 0), [1, 2, 3])
    assert hours == pytest.approx(year)


def test_matches_that_share_one_time_have_no_recency():
    assert _order_recency(dated(4, 4, 4), [1, 2, 3]) == [0.0, 0.0, 0.0]
    assert _order_recency([], []) == []


def test_candidates_outside_the_best_semantic_matches_have_no_recency_and_set_no_scale():
    top = RRF_RECENCY_ORDER_SEMANTIC_TOP
    # The two newest are outside the top: the scale is the span of the first two only.
    recency = _order_recency(dated(10, 0, -5, -3), [1, top, top + 1, None])
    assert recency[1] == 1.0 and recency[0] == pytest.approx(math.exp(-5))
    assert recency[2:] == [0.0, 0.0]


def test_rrf_recency_order_is_a_rank_fusion_switch_recorded_only_when_on():
    on = ScoringWeights(fusion="rrf", rrf_recency_order=True)
    assert on.version_id != RRF.version_id and "rrf_recency_order" not in RRF.model_dump(mode="json")
    assert on.model_dump(mode="json")["rrf_recency_order"] is True


# --- memory_mode ---------------------------------------------------------------------------------------------------


def settings(**fields) -> PRMEConfig:
    return PRMEConfig(_env_file=None, **fields)


def address_first_options(config: PRMEConfig) -> dict:
    return {
        "enable_relation_routing": config.enable_relation_routing,
        "enable_belief_state": config.enable_belief_state,
        "session_context_direct_first": config.packing.session_context_direct_first,
        "clock_window_filter": config.packing.clock_window_filter,
        "collapse_identical_text": config.packing.collapse_identical_text,
        "rrf_recency_order": config.scoring.rrf_recency_order,
    }


ALL_ON = {"enable_relation_routing": True, "enable_belief_state": True, "session_context_direct_first": 10,
          "clock_window_filter": True, "collapse_identical_text": True, "rrf_recency_order": True}


def test_address_first_turns_each_option_on():
    assert address_first_options(settings(memory_mode="address_first")) == ALL_ON


def test_address_first_turns_on_nothing_that_acts_when_memories_are_stored():
    # Typed extraction, corrections and the second round lost answers or evidence on 2026-11-11; they stay apart.
    config = settings(memory_mode="address_first")
    assert not (config.enable_typed_extraction or config.enable_extraction_corrections or config.packing.second_round)


def test_address_first_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("PRME_MEMORY_MODE", "address_first")
    assert address_first_options(settings()) == ALL_ON


@pytest.mark.parametrize("fields, kept", [
    ({"enable_belief_state": False}, {"enable_belief_state": False}),
    ({"enable_relation_routing": False}, {"enable_relation_routing": False}),
    ({"packing": {"collapse_identical_text": False}}, {"collapse_identical_text": False}),
    ({"packing": {"session_context_direct_first": 3}}, {"session_context_direct_first": 3}),
])
def test_an_option_the_configuration_sets_itself_is_kept(fields, kept):
    assert address_first_options(settings(memory_mode="address_first", **fields)) == {**ALL_ON, **kept}


def test_an_explicit_rrf_recency_order_false_is_kept():
    config = settings(memory_mode="address_first", scoring={"fusion": "rrf", "rrf_recency_order": False})
    assert config.scoring.rrf_recency_order is False and config.enable_belief_state


def test_under_weighted_scoring_address_first_leaves_rrf_recency_order_off():
    config = settings(memory_mode="address_first", scoring={"fusion": "weighted"})
    assert config.scoring.fusion == "weighted" and config.scoring.rrf_recency_order is False


def test_classic_changes_nothing():
    assert settings(memory_mode="classic").model_dump(mode="json") == settings().model_dump(mode="json")
    assert address_first_options(settings()) == {
        "enable_relation_routing": False, "enable_belief_state": False, "session_context_direct_first": None,
        "clock_window_filter": False, "collapse_identical_text": False, "rrf_recency_order": False}


def test_an_unknown_memory_mode_is_refused():
    with pytest.raises(ValidationError):
        settings(memory_mode="sideways")


# --- relation_walk: the role's end, and the pet concept ------------------------------------------------------------


@pytest.mark.parametrize("quote, subject, target, names_subject", [
    ("My sister Priya is visiting on Friday.", "Priya", "Sam", True),
    ("Priya is my sister.", "Priya", "Sam", True),
    ("Priya was my sister.", "Priya", "Sam", True),
    ("Priya was my older sister.", "Priya", "Sam", True),        # up to two words between was and the role
    ("Priya was my much older sister.", "Priya", "Sam", False),  # three words: the quote does not decide
    ("My sister, Priya, is visiting.", "Priya", "Sam", True),
    ("Sam visits his sister Priya.", "Sam", "Priya", False),     # the role is beside the object
    ("My sister Priya and my sister Ana.", "Priya", "Ana", False),  # beside both: the quote does not decide
    ("Priya is visiting.", "Priya", "Sam", False),
    (None, "Priya", "Sam", False),
    ("My sister Priya.", "", "Sam", False),
])
def test_the_quote_decides_the_roles_end_only_when_the_role_is_beside_the_subject(quote, subject, target,
                                                                                   names_subject):
    metadata = {"evidence_quote": quote, "subject": subject, "object": target}
    assert _role_names_subject("sister", metadata) is names_subject


def test_a_typed_claim_whose_quote_puts_the_role_on_its_subject_reads_it_there():
    metadata = {"relation": "kin_of", "role": "sister", "predicate": "kin_of", "subject": "Priya", "object": "I",
                "evidence_quote": "My sister Priya is visiting on Friday."}
    assert _typed(metadata) == ClaimRelation("kin_of", "sister", "subject")
    assert _typed({**metadata, "evidence_quote": "Priya is visiting on Friday."}) == ClaimRelation(
        "kin_of", "sister", "object")


@pytest.mark.parametrize("question", ["What pet does Sam have?", "What pets do I have?"])
def test_the_pet_concept_routes_to_owns(question):
    assert route_question(question) == ((RelationStep("owns"),),)


def test_the_pet_concept_and_the_routing_version():
    assert concept_map()["pet"] == (RelationStep("owns"),)
    assert ROUTING_VERSION == 3

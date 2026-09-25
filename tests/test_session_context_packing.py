"""Opt-in session context packing (issue #86).

Session expansion gave a neighbor it added one path and never counted
SESSION_CONTEXT on a neighbor another path had found, so single-path neighbors
waited behind every multi-path candidate and were never packed. With
PackingConfig.session_context_packing set, SESSION_CONTEXT counts as a path,
an added neighbor takes its trigger's tier, and "adjacent" packs a trigger's
neighbors beside it. Unset, nothing changes.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.models import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, PackingConfig, ScoringWeights
from prme.retrieval.models import MemoryBundle, RetrievalCandidate, SessionContextLink
from prme.retrieval.packing import pack_context
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import NodeType, RepresentationLevel, Scope
from tests import test_durable_ingestion
from tests.previous_defaults import previous_defaults
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_rank_fusion import EXECUTION, NOW, PRODUCT, RRF, candidate

config = test_durable_ingestion.config
user = test_durable_ingestion.user

FIELD = "session_context_packing"
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
TRIGGER_SCORE = 1.0
FIXTURES = Path(__file__).parent / "fixtures/relevance"
# Written by make_receipt on main before this change: weighted scoring with
# event-time recency and the reader format (tests/test_event_time_recency.py).
V20_CHECKSUM = "cf2d3dc04020a6a846991e69e37d457459e40814c73820189bb5d0ead58cde0f"


def node(number, text, *, session=None, minute=0, node_type=NodeType.EVENT):
    at = START + timedelta(minutes=minute)
    return MemoryNode(id=UUID(int=number), user_id="u", content=text, node_type=node_type,
                      scope=Scope.PERSONAL, session_id=session, created_at=at, updated_at=at,
                      last_reinforced_at=at)


def found(item, score, *, paths=("VECTOR", "LEXICAL")):
    return RetrievalCandidate(node=item, composite_score=score, paths=list(paths), path_count=len(paths))


# One conversation: a question (the trigger), the answer after it and a turn before it.
BEFORE = node(10, "Anna: We went to the observatory on Friday.", session="s1", minute=0)
QUESTION = node(11, "Ben: What color was the telescope?", session="s1", minute=1)
ANSWER = node(12, "Anna: It was bright blue.", session="s1", minute=2)
# Unrelated multi-path records that outscore the answer at the default decay.
FILLERS = [node(20 + n, f"Filler note number {n:02d} about something else.") for n in range(12)]


def _graph(*nodes):
    return Mock(query_nodes=AsyncMock(return_value=list(nodes)))


async def _expanded(setting, *, answer_found=False, filler_paths=("VECTOR", "LEXICAL")):
    """The trigger, the fillers and, optionally, the answer found by vector search alone."""
    scored = [found(QUESTION, TRIGGER_SCORE)]
    scored += [found(item, .95 - n / 100, paths=filler_paths) for n, item in enumerate(FILLERS)]
    if answer_found:
        scored.append(found(ANSWER, .1, paths=("VECTOR",)))
    scored.sort(key=lambda item: (-item.composite_score, str(item.node.id)))
    # Only the question and fillers without a session are triggers; the answer ranks below them.
    packing = PackingConfig(context_format="reader", multipath_ordering="score", overhead_tokens=0,
                            session_context_window=1, session_context_top_k=5, **{FIELD: setting})
    expanded = await expand_session_context(scored, _graph(BEFORE, QUESTION, ANSWER), "u", packing)
    return expanded, packing


def _budget_for(records: int) -> int:
    """Room for the trigger and this many short records in the reader format."""
    probe = PackingConfig(context_format="reader", token_budget=100_000, overhead_tokens=0)
    fits = [found(QUESTION, 1.0), *[found(item, .5) for item in FILLERS[:records]]]
    return pack_context(fits, probe).tokens_used + 2


def _packed_ids(bundle):
    return [item.node.id for group in bundle.sections.values() for item in group]


def _link(trigger, offset):
    return SessionContextLink(trigger_id=trigger.id, offset=offset)


def _config(setting, **kwargs):
    values = dict(context_format="reader", multipath_ordering="score", overhead_tokens=0,
                  session_context_window=1)
    return PackingConfig(**{**values, **kwargs, FIELD: setting})


# --- Expansion ------------------------------------------------------------------


async def test_unset_expansion_keeps_the_path_count_and_links_nothing():
    expanded, _ = await _expanded(None, answer_found=True)
    by_id = {item.node.id: item for item in expanded}

    assert by_id[ANSWER.id].paths == ["VECTOR", "SESSION_CONTEXT"]
    assert by_id[ANSWER.id].path_count == 1
    assert by_id[BEFORE.id].paths == ["SESSION_CONTEXT"] and by_id[BEFORE.id].path_count == 1
    assert all(item.session_context_link is None for item in expanded)


@pytest.mark.parametrize("setting", ["trigger_tier", "adjacent"])
async def test_expansion_counts_the_session_path_and_links_neighbors_to_their_trigger(setting):
    expanded, _ = await _expanded(setting, answer_found=True)
    by_id = {item.node.id: item for item in expanded}

    # An existing single-path neighbor now has two paths, like episode and evidence context.
    assert by_id[ANSWER.id].paths == ["VECTOR", "SESSION_CONTEXT"]
    assert by_id[ANSWER.id].path_count == 2
    assert by_id[ANSWER.id].session_context_link == _link(QUESTION, 1)
    # A neighbor that expansion added keeps one path and records its trigger.
    assert by_id[BEFORE.id].paths == ["SESSION_CONTEXT"] and by_id[BEFORE.id].path_count == 1
    assert by_id[BEFORE.id].session_context_link == _link(QUESTION, -1)
    # The link is serialized only when set, so a saved candidate packs the same way again.
    assert by_id[BEFORE.id].model_dump(mode="json")["session_context_link"] == {
        "trigger_id": str(QUESTION.id), "offset": -1}
    assert "session_context_link" not in by_id[QUESTION.id].model_dump(mode="json")
    assert by_id[QUESTION.id].session_context_link is None


async def test_a_neighbor_belongs_to_the_highest_ranked_trigger_and_a_trigger_follows_nobody():
    turns = [node(40 + n, f"turn {n}", session="s9", minute=n) for n in range(6)]
    # Turns 1 and 3 are both triggers and each sits in the other's window; turn 2 sits
    # in both windows.
    scored = [found(turns[3], .9), found(turns[1], .8)]
    packing = PackingConfig(session_context_window=2, **{FIELD: "adjacent"})
    expanded = await expand_session_context(scored, _graph(*turns), "u", packing)
    by_id = {item.node.id: item for item in expanded}

    assert by_id[turns[2].id].session_context_link == _link(turns[3], -1)
    assert by_id[turns[4].id].session_context_link == _link(turns[3], 1)
    assert by_id[turns[5].id].session_context_link == _link(turns[3], 2)
    assert by_id[turns[0].id].session_context_link == _link(turns[1], -1)
    # Each trigger gains the session path and a path count but keeps its own place.
    for trigger in (turns[1], turns[3]):
        assert by_id[trigger.id].session_context_link is None
        assert by_id[trigger.id].paths == ["VECTOR", "LEXICAL", "SESSION_CONTEXT"]
        assert by_id[trigger.id].path_count == 3
    # A path is counted once however many windows hold the record.
    assert by_id[turns[2].id].path_count == 1


def test_the_setting_is_omitted_unset_and_read_from_the_environment(monkeypatch):
    assert FIELD not in PackingConfig().model_dump(mode="json")
    assert FIELD not in PRMEConfig().packing.model_dump(mode="json")
    assert PackingConfig(**{FIELD: "adjacent"}).model_dump(mode="json")[FIELD] == "adjacent"
    with pytest.raises(ValidationError):
        PackingConfig(**{FIELD: "nearby"})
    monkeypatch.setenv("PRME_PACKING__SESSION_CONTEXT_PACKING", "trigger_tier")
    settings = PRMEConfig().packing
    assert getattr(settings, FIELD) == "trigger_tier"
    # The other product defaults stay.
    assert (settings.context_format, settings.multipath_ordering) == ("reader", "balanced")


# --- Packing --------------------------------------------------------------------


async def _crowded(setting, *, answer_found=False, ordering="score"):
    """The question as the only trigger, with multi-path records that score below the
    score its neighbors inherit, as in the LoCoMo pools the issue measured."""
    scored = [found(QUESTION, TRIGGER_SCORE), *[found(item, .5 - n / 100) for n, item in enumerate(FILLERS)]]
    if answer_found:
        scored.append(found(ANSWER, .1, paths=("VECTOR",)))
    scored.sort(key=lambda item: (-item.composite_score, str(item.node.id)))
    packing = _config(setting, session_context_top_k=1, multipath_ordering=ordering)
    expanded = await expand_session_context(scored, _graph(BEFORE, QUESTION, ANSWER), "u", packing)
    return expanded, packing


@pytest.mark.parametrize("setting", ["trigger_tier", "adjacent"])
@pytest.mark.parametrize("answer_found", [False, True], ids=["new neighbor", "existing single-path neighbor"])
async def test_a_trigger_and_its_neighbors_are_packed_when_the_budget_allows(answer_found, setting):
    budget = _budget_for(3)

    unset, packing = await _crowded(None, answer_found=answer_found)
    control = pack_context(unset, packing.model_copy(update={"token_budget": budget}))
    # Unset, lower-scored multi-path records fill the context before either neighbor.
    assert _packed_ids(control) == [QUESTION.id, *[item.id for item in FILLERS[:3]]]

    expanded, packing = await _crowded(setting, answer_found=answer_found)
    bundle = pack_context(expanded, packing.model_copy(update={"token_budget": budget}))
    packed = _packed_ids(bundle)
    assert {QUESTION.id, BEFORE.id, ANSWER.id} <= set(packed)
    assert bundle.tokens_used <= budget
    assert len(bundle.excluded_ids) == len(set(bundle.excluded_ids))
    assert set(bundle.excluded_ids).isdisjoint(packed)
    if setting == "adjacent":
        # Beside the trigger, in session order.
        start = packed.index(BEFORE.id)
        assert packed[start:start + 3] == [BEFORE.id, QUESTION.id, ANSWER.id]
        lines = bundle.rendered_context.splitlines()
        first = next(i for i, line in enumerate(lines) if "observatory" in line)
        assert "telescope" in lines[first + 1] and "bright blue" in lines[first + 2]


@pytest.mark.parametrize("ordering", ["score", "balanced", "density"])
async def test_adjacent_packs_the_same_records_as_trigger_tier(ordering):
    for budget in (0, _budget_for(0), _budget_for(1), _budget_for(2), _budget_for(4), 100_000):
        packed = {}
        for setting in ("trigger_tier", "adjacent"):
            expanded, packing = await _crowded(setting, answer_found=True, ordering=ordering)
            bundle = pack_context(expanded, packing.model_copy(update={"token_budget": budget}))
            packed[setting] = (set(_packed_ids(bundle)), set(bundle.excluded_ids))
        assert packed["adjacent"] == packed["trigger_tier"]


async def test_a_neighbor_packed_before_its_trigger_still_sits_beside_it_in_session_order():
    # Balanced order packs short records first; the long question comes later but is
    # placed between the turns around it.
    long_question = node(11, "Ben: " + "What color was the telescope we saw that night? " * 12,
                         session="s1", minute=1)
    head = found(FILLERS[0], 1.0)
    scored = [head, found(long_question, .99), *[found(item, .2) for item in FILLERS[1:4]]]
    packing = _config("adjacent", multipath_ordering="balanced", token_budget=100_000,
                      session_context_top_k=2)
    expanded = await expand_session_context(scored, _graph(BEFORE, long_question, ANSWER), "u", packing)
    bundle = pack_context(expanded, packing)
    packed = _packed_ids(bundle)
    start = packed.index(BEFORE.id)
    assert packed[start:start + 3] == [BEFORE.id, long_question.id, ANSWER.id]
    # "trigger_tier" packs the same records in their own order: the short turns first.
    tiered = _packed_ids(pack_context(expanded, packing.model_copy(update={FIELD: "trigger_tier"})))
    assert set(tiered) == set(packed)
    assert max(tiered.index(BEFORE.id), tiered.index(ANSWER.id)) < tiered.index(long_question.id)


async def test_trigger_tier_packs_added_neighbors_before_single_path_candidates():
    budget = _budget_for(3)
    # Single-path fillers outscore the neighbors, so unset they fill the context first.
    unset, packing = await _expanded(None, filler_paths=("VECTOR",))
    control = pack_context(unset, packing.model_copy(update={"token_budget": budget}))
    assert ANSWER.id not in _packed_ids(control)

    tiered, packing = await _expanded("trigger_tier", filler_paths=("VECTOR",))
    bundle = pack_context(tiered, packing.model_copy(update={"token_budget": budget}))
    packed = _packed_ids(bundle)
    assert {QUESTION.id, ANSWER.id, BEFORE.id} <= set(packed)
    assert packed[0] == QUESTION.id


async def test_trigger_tier_does_not_lift_neighbors_of_a_single_path_trigger():
    scored = [found(QUESTION, TRIGGER_SCORE, paths=("VECTOR",)),
              found(FILLERS[0], .9, paths=("VECTOR",))]
    packing = _config("trigger_tier", token_budget=100_000)
    expanded = await expand_session_context(scored, _graph(BEFORE, QUESTION, ANSWER), "u", packing)
    bundle = pack_context(expanded, packing)
    # Everything fits, and the neighbors (equal scores, so by node ID) stay behind the
    # higher-scored single-path filler.
    assert _packed_ids(bundle) == [QUESTION.id, FILLERS[0].id, BEFORE.id, ANSWER.id]


async def test_a_neighbor_whose_trigger_was_dropped_keeps_its_own_tier():
    expanded, packing = await _crowded("adjacent")
    # Selection (a limit or min_score) can drop the trigger and keep its neighbors.
    without_trigger = [item for item in expanded if item.node.id != QUESTION.id]
    bundle = pack_context(without_trigger, packing.model_copy(update={"token_budget": _budget_for(2)}))
    assert ANSWER.id not in _packed_ids(bundle) and BEFORE.id not in _packed_ids(bundle)


async def test_unset_packing_ignores_links_and_matches_the_default_bytes():
    linked, packing = await _crowded("adjacent", answer_found=True)
    unset_packing = packing.model_copy(update={FIELD: None, "token_budget": _budget_for(3)})
    plain = [item.model_copy(update={"session_context_link": None}) for item in linked]
    with_links = pack_context(linked, unset_packing)
    without_links = pack_context(plain, unset_packing)
    assert with_links.rendered_context == without_links.rendered_context
    assert with_links.excluded_ids == without_links.excluded_ids


async def test_repacking_with_a_reserved_trigger_keeps_its_window_together():
    expanded, packing = await _crowded("adjacent")
    packing = packing.model_copy(update={"token_budget": _budget_for(3)})
    bundle = pack_context(expanded, packing)
    packed = [item for group in bundle.sections.values() for item in group]
    assert {BEFORE.id, QUESTION.id, ANSWER.id} <= {item.node.id for item in packed}
    # The temporal relation enricher repacks the packed records with a cited one reserved.
    again = pack_context(packed, packing, _required=((ANSWER.id, RepresentationLevel.FULL),))
    assert again.rendered_context == bundle.rendered_context


async def test_saved_candidates_pack_the_same_way_again():
    expanded, packing = await _crowded("adjacent", answer_found=True, ordering="balanced")
    packing = packing.model_copy(update={"token_budget": _budget_for(3)})
    saved = json.loads(json.dumps([item.model_dump(mode="json") for item in expanded]))
    restored = [RetrievalCandidate.model_validate(item) for item in saved]
    assert pack_context(restored, packing).rendered_context == pack_context(expanded, packing).rendered_context


async def test_neighbors_in_another_section_are_packed_but_not_placed_in_the_trigger_section():
    fact = node(13, "Anna: The telescope cost 300 dollars.", session="s1", minute=3,
                node_type=NodeType.FACT)
    scored = [found(QUESTION, TRIGGER_SCORE)]
    packing = _config("adjacent", session_context_window=2, token_budget=100_000)
    expanded = await expand_session_context(scored, _graph(BEFORE, QUESTION, ANSWER, fact), "u", packing)
    bundle = pack_context(expanded, packing)
    assert [item.node.id for item in bundle.sections["stable_facts"]] == [fact.id]
    assert [item.node.id for item in bundle.sections["provenance_refs"]] == [BEFORE.id, QUESTION.id, ANSWER.id]


# --- Receipts -------------------------------------------------------------------


async def _neighbor_retrieval(config, user, setting, **packing):
    config = config.model_copy(update={"packing": config.packing.model_copy(update={FIELD: setting, **packing})})
    async with MemoryEngine.open(config) as engine:
        for text in ("The telescope is blue.", "We bought bread.", "The weather was mild."):
            await engine.store(text, user_id=user, scope=Scope.PROJECT, session_id="s1")
        response = await engine.retrieve("telescope", user_id=user, scope=Scope.PROJECT, min_score=0,
                                         include_cross_scope=False, reference_time=NOW)
        request_id = str(response.metadata.request_id)
        saved = await engine.get_retrieval_receipt(request_id, user_id=user)
        async with client_for(app_for(config, engine, user)) as client:
            served = await client.get(f"/v1/retrievals/{request_id}")
    assert served.status_code == 200, served.text
    assert served.json() == json.loads(saved.model_dump_json())
    return saved, response


@pytest.mark.parametrize("setting", ["trigger_tier", "adjacent"])
async def test_rank_fused_retrieval_records_the_setting_in_version_21(config, user, setting):
    saved, response = await _neighbor_retrieval(config, user, setting)

    assert saved.schema_version == 21 and saved.scoring.fusion == "rrf"
    assert json.loads(saved.model_dump_json())["packing"][FIELD] == setting
    assert saved.replay_ranking() == tuple(item.node.id for item in response.results)
    restored = RetrievalReceipt.model_validate_json(saved.model_dump_json())
    assert restored.checksum == saved.checksum
    assert any("SESSION_CONTEXT" in item.paths for item in response.results)


async def test_weighted_retrieval_records_the_setting_in_version_21(config, user):
    saved, response = await _neighbor_retrieval(previous_defaults(config), user, "adjacent")

    assert saved.schema_version == 21 and saved.scoring.fusion == "weighted"
    assert all(item.semantic_relevance is None for item in saved.candidates)
    assert saved.replay_ranking() == tuple(item.node.id for item in response.results)


async def test_unset_retrieval_keeps_its_receipt_version_and_bytes(config, user):
    saved, _ = await _neighbor_retrieval(config, user, None)
    assert saved.schema_version == 19
    assert FIELD not in json.loads(saved.model_dump_json())["packing"]
    weighted, _ = await _neighbor_retrieval(previous_defaults(config), user, None)
    assert weighted.schema_version == 12


async def test_the_setting_without_session_expansion_changes_nothing_and_is_not_recorded(config, user):
    saved, response = await _neighbor_retrieval(config, user, "adjacent", session_context_window=0)
    assert saved.schema_version == 19
    assert FIELD not in json.loads(saved.model_dump_json())["packing"]
    assert all("SESSION_CONTEXT" not in item.paths for item in response.results)


async def test_the_built_in_neighbor_query_links_neighbors_to_their_trigger(config, user):
    # One trigger, so the other two turns are its neighbors (DuckDB and PostgreSQL windows).
    _, response = await _neighbor_retrieval(config, user, "adjacent", session_context_top_k=1)
    trigger, *rest = response.results
    assert trigger.session_context_link is None and "telescope" in trigger.node.content
    linked = {item.node.content: item for item in rest}
    assert linked["We bought bread."].session_context_link == SessionContextLink(
        trigger_id=trigger.node.id, offset=1)
    assert linked["The weather was mild."].session_context_link == SessionContextLink(
        trigger_id=trigger.node.id, offset=2)
    # Every record matched "telescope" on no channel or one, so the session path adds one.
    for item in rest:
        assert "SESSION_CONTEXT" in item.paths and item.path_count == len(item.paths)


async def _session_receipt(scoring=RRF, setting="adjacent"):
    trigger = candidate(1, semantic=.9, lexical=.9, session="conversation")
    neighbor = candidate(2, session="conversation")
    ranked, _ = score_and_rank([trigger], scoring, now=NOW)
    packing = PackingConfig(**{FIELD: setting})
    expanded = await expand_session_context(ranked, _graph(trigger.node, neighbor.node), "owner", packing)
    return make_receipt(request_id=UUID(int=86), user_id="owner", query="telescope", reference_time=NOW,
                        scopes=(Scope.PROJECT,), scoring=scoring, packing=packing, candidates=expanded,
                        bundle=MemoryBundle(), ranking_policy="score_id", execution=EXECUTION)


@pytest.mark.parametrize("scoring", [RRF, DEFAULT_SCORING_WEIGHTS], ids=["rank fusion", "weighted"])
async def test_version_21_round_trips_and_requires_the_setting(scoring):
    saved = await _session_receipt(scoring)
    raw = saved.model_dump_json()
    data = json.loads(raw)
    assert data["schema_version"] == 21 and data["packing"][FIELD] == "adjacent"
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.model_dump_json() == raw and restored.checksum == saved.checksum

    missing = json.loads(raw)
    missing["packing"].pop(FIELD)
    with pytest.raises(ValidationError, match="Version 21 records session context packing"):
        RetrievalReceipt.model_validate(missing)


@pytest.mark.parametrize("version, scoring", [
    (14, DEFAULT_SCORING_WEIGHTS),
    (16, RRF),
    (19, ScoringWeights(**PRODUCT)),
    (20, ScoringWeights(recency_time="event_time")),
])
async def test_earlier_versions_cannot_record_the_setting(version, scoring):
    data = json.loads((await _session_receipt(scoring)).model_dump_json())
    data["schema_version"] = version
    with pytest.raises(ValidationError, match="Session context packing requires a version 21 receipt"):
        RetrievalReceipt.model_validate(data)


@pytest.mark.parametrize("scoring, decay", [
    (ScoringWeights(**PRODUCT), .6),
    (ScoringWeights(recency_time="event_time"), None),
], ids=["rank fusion defaults", "weighted event-time recency"])
async def test_version_21_admits_the_earlier_features_of_its_formula(scoring, decay):
    trigger = candidate(1, semantic=.9, lexical=.9, session="conversation")
    neighbor = candidate(2, session="conversation")
    ranked, _ = score_and_rank([trigger], scoring, now=NOW)
    packing = PackingConfig(session_context_rank_fusion_score_decay=decay, **{FIELD: "trigger_tier"})
    expanded = await expand_session_context(ranked, _graph(trigger.node, neighbor.node), "owner", packing)
    saved = make_receipt(request_id=UUID(int=88), user_id="owner", query="telescope", reference_time=NOW,
                         scopes=(Scope.PROJECT,), scoring=scoring, packing=packing, candidates=expanded,
                         bundle=MemoryBundle(), ranking_policy="score_id", execution=EXECUTION)
    raw = saved.model_dump_json()
    assert saved.schema_version == 21 and saved.scoring == scoring
    assert saved.packing.session_context_rank_fusion_score_decay == decay
    assert RetrievalReceipt.model_validate_json(raw).model_dump_json() == raw


def test_saved_version_20_receipts_keep_their_bytes():
    # Version selection and the weighted receipt rules were rewritten to add version 21.
    raw = (FIXTURES / "receipt-v20-weighted.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V20_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 20 and restored.scoring.recency_time == "event_time"
    assert restored.packing.session_context_packing is None
    assert restored.model_dump_json() == raw and restored.checksum == V20_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)


async def test_rank_fused_version_21_keeps_the_rank_fusion_rules():
    data = json.loads((await _session_receipt(RRF)).model_dump_json())
    for item in data["candidates"]:
        item.pop("semantic_relevance")
    with pytest.raises(ValidationError, match="record every candidate's rank fusion relevance"):
        RetrievalReceipt.model_validate(data)


async def test_weighted_version_21_records_no_rank_fusion_features():
    data = json.loads((await _session_receipt(DEFAULT_SCORING_WEIGHTS)).model_dump_json())
    data["packing"]["session_context_rank_fusion_score_decay"] = .6
    with pytest.raises(ValidationError, match="Version 21 records no rank fusion session decay"):
        RetrievalReceipt.model_validate(data)


def test_a_receipt_without_an_execution_descriptor_cannot_record_the_setting():
    # Score order and the auditable format need no descriptor themselves.
    packing = PackingConfig(multipath_ordering="score", **{FIELD: "adjacent"})
    with pytest.raises(ValueError, match="Session context packing receipts require an execution descriptor"):
        make_receipt(request_id=UUID(int=87), user_id="owner", query="telescope", reference_time=NOW,
                     scopes=(Scope.PROJECT,), scoring=DEFAULT_SCORING_WEIGHTS, packing=packing,
                     candidates=[], bundle=MemoryBundle(), ranking_policy="score_id")

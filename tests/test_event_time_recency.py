"""Opt-in event-time recency for the weighted formula (issue #83).

Unset, the weighted formula measures recency on questions that are not about
the current state from ``updated_at or created_at`` against the request's
reference time. History imported with a past event time and retrieved at a past
reference time then has negative ages, which clamp to zero, so every memory
looks brand new. ``ScoringWeights.recency_time="event_time"`` dates every
memory by when it was stated, its event time or else when it was stored, the
clock rank fusion's recency boost and tie-break already use. Rank fusion drops
the setting.
"""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.models.learning import RankingMultipliers
from prme.models.nodes import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.quality.feedback import FeedbackSignal, FeedbackSignalType
from prme.quality.tuner import WeightTuner
from prme.retrieval.config import (
    DEFAULT_SCORING_WEIGHTS,
    PackingConfig,
    ScoringWeights,
    default_packing_config,
    default_scoring_weights,
)
from prme.retrieval.models import MemoryBundle, QueryAnalysis, RetrievalCandidate, ScoreProvenance
from prme.retrieval.ranking_adjustments import adjusted_weights
from prme.retrieval.scoring import compute_composite_score, score_and_rank
from prme.retrieval.selection import with_rank_fusion_relevance
from prme.types import NodeType, QueryIntent, Scope
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_rank_fusion import EXECUTION
from tests.test_rank_fusion_recency import CURRENT, UPDATE, _copies

config = test_durable_ingestion.config
user = test_durable_ingestion.user

FIXTURES = Path(__file__).parent / "fixtures/relevance"
# Written by make_receipt on main before this change (see the fixture tests below).
V14_CHECKSUM = "77a92af0e90f3638b6cfcec6fd2a3b8a51a1c1eb97c4682ba1f628ec75e835c4"
V19_CHECKSUM = "95dc1bf4c3034676699bd83509e8ad97e2f33ec32e2518385961b3523162c254"
EVENT = ScoringWeights(recency_time="event_time")
# The saved benchmark packs were ingested in 2026; each question asks at its
# conversation's last session time.
INGESTED = datetime(2026, 9, 23, tzinfo=timezone.utc)
REFERENCE = datetime(2023, 6, 1, tzinfo=timezone.utc)
HISTORICAL = QueryAnalysis(query="What did Caroline say about the painting?", intent=QueryIntent.FACTUAL)


def _node(number, *, content, stored, event_time=None, valid_from=None, updated=None):
    return MemoryNode(
        id=UUID(int=number), user_id="owner", scope=Scope.PROJECT, node_type=NodeType.FACT,
        content=content, created_at=stored, updated_at=updated or stored, last_reinforced_at=stored,
        valid_from=valid_from or stored, event_time=event_time,
    )


def _imported(number, days, *, semantic=.6, lexical=.5, content="Caroline talked about her painting."):
    """A turn stated ``days`` before the reference time and ingested today."""
    return RetrievalCandidate(
        node=_node(number, content=content, stored=INGESTED, event_time=REFERENCE - timedelta(days=days)),
        semantic_score=semantic, lexical_score=lexical, paths=["VECTOR", "LEXICAL"], path_count=2,
    )


def _recency(ranked):
    return {c.node.id.int: c.score_trace.recency_factor for c in ranked}


def _adjustments(ranked):
    return {c.node.id.int: [op.kind for op in c.score_provenance.adjustments] for c in ranked}


# --- Configuration -----------------------------------------------------------


def test_unset_keeps_every_configurations_bytes_and_version():
    assert "recency_time" not in DEFAULT_SCORING_WEIGHTS.model_dump(mode="json")
    # The product default, recorded on main before the setting existed.
    product = default_scoring_weights()
    assert "recency_time" not in product.model_dump(mode="json")
    assert product.version_id == "50b6b46ae105"
    assert hashlib.sha256(product.model_dump_json().encode()).hexdigest() == (
        "6cb073c2c541c92fc31efdcf6d96399a25820ab2a69a545c371e147103ca0dfd"
    )


def test_the_setting_is_serialized_versioned_and_validated():
    assert EVENT.model_dump(mode="json")["recency_time"] == "event_time"
    assert ScoringWeights.model_validate_json(EVENT.model_dump_json()) == EVENT
    assert EVENT.version_id not in {DEFAULT_SCORING_WEIGHTS.version_id, default_scoring_weights().version_id}
    with pytest.raises(ValidationError):
        ScoringWeights(recency_time="valid_from")


def test_rank_fusion_drops_the_setting_with_a_warning(monkeypatch):
    with pytest.warns(UserWarning, match="recency_time applies only when fusion is 'weighted' and is ignored"):
        assert ScoringWeights(fusion="rrf", recency_time="event_time") == ScoringWeights(fusion="rrf")
    # Rank fusion is PRMEConfig's default, so the setting alone is dropped and
    # the product defaults stay as they are.
    monkeypatch.setenv("PRME_SCORING__RECENCY_TIME", "event_time")
    with pytest.warns(UserWarning, match="recency_time applies only"):
        assert PRMEConfig(_env_file=None).scoring == default_scoring_weights()


def test_the_setting_is_read_from_the_environment_with_weighted_fusion(monkeypatch):
    monkeypatch.setenv("PRME_SCORING__FUSION", "weighted")
    monkeypatch.setenv("PRME_SCORING__RECENCY_TIME", "event_time")
    assert PRMEConfig(_env_file=None).scoring == EVENT


@pytest.mark.parametrize("weights,match", [
    # Validation drops or rejects these; model_copy skips it.
    (ScoringWeights(fusion="rrf").model_copy(update={"recency_time": "event_time"}),
     "recency_time applies only to fusion='weighted'"),
    (DEFAULT_SCORING_WEIGHTS.model_copy(update={"recency_time": "updated_at"}),
     "Unknown recency_time 'updated_at'"),
])
def test_copied_settings_the_scorer_would_ignore_are_rejected(weights, match):
    with pytest.raises(ValueError, match=match):
        score_and_rank([_imported(1, 0)], weights, now=REFERENCE)


def test_query_shifts_learned_multipliers_and_feedback_tuning_keep_the_setting():
    assert adjusted_weights(EVENT, RankingMultipliers(recency=2.0)).recency_time == "event_time"
    tuned = WeightTuner(EVENT).update([FeedbackSignal(
        query="painting", surfaced_node_ids=["n1"], signal_type=FeedbackSignalType.USED,
    )])
    assert tuned.recency_time == "event_time"
    ranked, _ = score_and_rank([_imported(1, 0)], EVENT, now=REFERENCE, query_analysis=CURRENT)
    assert ranked[0].score_provenance.weights.recency_time == "event_time"


# --- Imported history ----------------------------------------------------------


def test_imported_history_looks_brand_new_without_the_setting():
    items = [_imported(1, 150), _imported(2, 30), _imported(3, 0)]
    ranked, _ = score_and_rank(_copies(items), DEFAULT_SCORING_WEIGHTS, now=REFERENCE,
                               query_analysis=HISTORICAL)
    # Ingested after the reference time, so every age clamps to zero.
    assert set(_recency(ranked).values()) == {1.0}
    assert len({c.composite_score for c in ranked}) == 1


def test_event_time_recency_measures_imported_history_from_the_reference_time():
    items = [_imported(1, 150), _imported(2, 30), _imported(3, 0)]
    ranked, _ = score_and_rank(_copies(items), EVENT, now=REFERENCE, query_analysis=HISTORICAL)
    assert _recency(ranked) == pytest.approx({1: math.exp(-.02 * 150), 2: math.exp(-.02 * 30), 3: 1.0})
    # About 0.05 for a turn 150 days before the reference time, as the issue estimated.
    assert _recency(ranked)[1] == pytest.approx(.0498, abs=1e-4)
    assert [c.node.id.int for c in ranked] == [3, 2, 1]
    for item in ranked:
        restored = ScoreProvenance.model_validate_json(item.score_provenance.model_dump_json())
        assert restored.replay_score() == item.composite_score


@pytest.mark.parametrize("query", [None, QueryAnalysis(query="What did Caroline say recently?",
                                                        intent=QueryIntent.FACTUAL)])
def test_no_query_analysis_and_recent_episode_questions_use_event_time_too(query):
    items = [_imported(1, 150), _imported(2, 0)]
    plain, _ = score_and_rank(_copies(items), DEFAULT_SCORING_WEIGHTS, now=REFERENCE, query_analysis=query)
    event, _ = score_and_rank(_copies(items), EVENT, now=REFERENCE, query_analysis=query)
    assert set(_recency(plain).values()) == {1.0}
    assert _recency(event) == pytest.approx({1: math.exp(-.02 * 150), 2: 1.0})
    # The recent-episode shift raises recency's weight to 0.20.
    expected_weight = .10 if query is None else .20
    assert event[0].score_provenance.weights.w_recency == pytest.approx(expected_weight)


def test_a_memory_stated_after_the_reference_time_counts_as_no_time_ago():
    later = _imported(1, -10)
    ranked, _ = score_and_rank([later], EVENT, now=REFERENCE, query_analysis=HISTORICAL)
    assert ranked[0].score_trace.recency_factor == 1.0


def test_a_memory_without_an_event_time_is_dated_when_stored():
    # Entity, consolidation and profile nodes carry no event time, so against a
    # past reference time they keep full recency while imported turns decay.
    derived = RetrievalCandidate(node=_node(2, content="Caroline paints.", stored=INGESTED),
                                 semantic_score=.6, lexical_score=.5)
    ranked, _ = score_and_rank([_imported(1, 150), derived], EVENT, now=REFERENCE, query_analysis=HISTORICAL)
    assert _recency(ranked) == pytest.approx({1: math.exp(-.02 * 150), 2: 1.0})
    stored = RetrievalCandidate(node=_node(3, content="Caroline paints.", stored=REFERENCE - timedelta(days=20)),
                                semantic_score=.6, lexical_score=.5)
    trace = compute_composite_score(stored, EVENT, now=REFERENCE)
    assert trace.recency_factor == pytest.approx(math.exp(-.02 * 20))


@pytest.mark.parametrize("days", [-400, 400])
def test_a_validity_start_is_not_when_a_memory_was_stated(days):
    # "Valid since last year" or "valid from next year", stated 20 days ago.
    stated = REFERENCE - timedelta(days=20)
    node = _node(1, content="The office is in Berlin.", stored=stated, valid_from=REFERENCE + timedelta(days=days))
    trace = compute_composite_score(RetrievalCandidate(node=node, semantic_score=.6, lexical_score=.5),
                                    EVENT, now=REFERENCE)
    assert trace.recency_factor == pytest.approx(math.exp(-.02 * 20))


def test_a_future_validity_start_does_not_become_the_current_state_anchor():
    stated = REFERENCE - timedelta(days=10)
    planned = RetrievalCandidate(node=_node(
        1, content="We switched to Oracle.", stored=stated - timedelta(days=20),
        valid_from=REFERENCE + timedelta(days=90),
    ), semantic_score=.8, lexical_score=.6)
    current = RetrievalCandidate(node=_node(2, content=UPDATE, stored=stated), semantic_score=.8, lexical_score=.6)
    ranked, _ = score_and_rank([planned, current], EVENT, now=REFERENCE, query_analysis=CURRENT)
    assert _adjustments(ranked) == {1: [], 2: ["current_update"]}
    assert _recency(ranked)[2] == 1.0


def test_the_event_time_wins_and_a_time_without_a_zone_is_read_as_utc():
    item = _imported(1, 40)
    item.node.created_at = REFERENCE - timedelta(days=5)
    item.node.event_time = item.node.event_time.replace(tzinfo=None)
    trace = compute_composite_score(item, EVENT, now=REFERENCE)
    assert trace.recency_factor == pytest.approx(math.exp(-.02 * 40))


def test_an_update_to_the_record_does_not_make_it_recent():
    # A lifecycle change such as an organizer promotion resets updated_at.
    stored = REFERENCE - timedelta(days=60)
    promoted = RetrievalCandidate(node=_node(1, content="Caroline paints.", stored=stored, updated=REFERENCE),
                                  semantic_score=.6, lexical_score=.5)
    plain = compute_composite_score(promoted, DEFAULT_SCORING_WEIGHTS, now=REFERENCE)
    event = compute_composite_score(promoted, EVENT, now=REFERENCE)
    assert plain.recency_factor == 1.0
    assert event.recency_factor == pytest.approx(math.exp(-.02 * 60))


# --- Records stored and retrieved at wall-clock time ----------------------------


def test_a_record_without_an_event_time_scores_as_before():
    # Stored at one moment and never updated: every timestamp is the same.
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    items = [RetrievalCandidate(node=_node(n, content=f"Note {n}", stored=now - timedelta(days=days)),
                                semantic_score=semantic, lexical_score=.4)
             for n, days, semantic in ((1, 40, .7), (2, 3, .6), (3, 0, .5))]
    for query in (None, HISTORICAL, CURRENT):
        plain, plain_traces = score_and_rank(_copies(items), DEFAULT_SCORING_WEIGHTS, now=now, query_analysis=query)
        event, event_traces = score_and_rank(_copies(items), EVENT, now=now, query_analysis=query)
        assert [c.node.id for c in event] == [c.node.id for c in plain]
        assert event_traces == plain_traces


async def _retrieve_both(config, user, reference_time, texts):
    """Store ``texts`` (text, event time) and retrieve with and without the setting."""
    async with MemoryEngine.open(config) as engine:
        for text, event_time in texts:
            await engine.store(text, user_id=user, scope=Scope.PROJECT, event_time=event_time)
        reference = reference_time or datetime.now(timezone.utc)
        responses, receipts = {}, {}
        for name, weights in (("plain", DEFAULT_SCORING_WEIGHTS), ("event", EVENT)):
            response = await engine.retrieve(
                "How does billing work?", user_id=user, scope=Scope.PROJECT, min_score=0,
                include_cross_scope=False, reference_time=reference, weights=weights,
            )
            responses[name] = response.results
            receipts[name] = await engine.get_retrieval_receipt(
                str(response.metadata.request_id), user_id=user,
            )
        request_id = str(receipts["event"].request_id)
        async with client_for(app_for(config, engine, user)) as client:
            served = await client.get(f"/v1/retrievals/{request_id}")
    assert served.status_code == 200, served.text
    assert served.json() == json.loads(receipts["event"].model_dump_json())
    assert receipts["event"].schema_version == 20 and receipts["event"].scoring == EVENT
    assert receipts["event"].replay_ranking() == tuple(item.node.id for item in responses["event"])
    # The default packing is the reader format, so the same request without the setting is version 14.
    assert receipts["plain"].schema_version == 14 and receipts["plain"].scoring == DEFAULT_SCORING_WEIGHTS
    return responses


async def test_retrieval_at_wall_clock_time_keeps_the_order(config, user):
    texts = [("The team uses PostgreSQL for billing.", None), ("Billing runs every Monday.", None),
             ("The office plants were watered.", None)]
    responses = await _retrieve_both(config, user, None, texts)
    plain, event = responses["plain"], responses["event"]
    assert [item.node.id for item in event] == [item.node.id for item in plain]
    # created_at and updated_at are set microseconds apart when a memory is stored.
    for before, after in zip(plain, event):
        assert after.score_trace.recency_factor == pytest.approx(before.score_trace.recency_factor, abs=1e-9)


async def test_retrieval_of_imported_history_at_a_past_reference_time(config, user):
    texts = [("Billing moved to a monthly cycle.", REFERENCE - timedelta(days=150)),
             ("Billing runs every Monday.", REFERENCE - timedelta(days=2))]
    responses = await _retrieve_both(config, user, REFERENCE, texts)
    plain = {item.node.content: item.score_trace.recency_factor for item in responses["plain"]}
    event = {item.node.content: item.score_trace.recency_factor for item in responses["event"]}
    assert set(plain.values()) == {1.0}
    assert event == pytest.approx({"Billing moved to a monthly cycle.": math.exp(-.02 * 150),
                                   "Billing runs every Monday.": math.exp(-.02 * 2)})


# --- Current-state questions -----------------------------------------------------


def test_current_state_questions_keep_their_anchor_and_read_the_same_time():
    # Stored without an event time; the older update was promoted after the newer one was stored.
    older = RetrievalCandidate(node=_node(
        1, content="We switched to MySQL.", stored=REFERENCE - timedelta(days=30), updated=REFERENCE,
    ), semantic_score=.8, lexical_score=.6)
    newer = RetrievalCandidate(node=_node(
        2, content=UPDATE, stored=REFERENCE - timedelta(days=5),
    ), semantic_score=.8, lexical_score=.6)
    plain, _ = score_and_rank(_copies([older, newer]), DEFAULT_SCORING_WEIGHTS, now=REFERENCE,
                              query_analysis=CURRENT)
    event, _ = score_and_rank(_copies([older, newer]), EVENT, now=REFERENCE, query_analysis=CURRENT)
    # Without the setting the promotion makes the older update look newest.
    assert _adjustments(plain) == {1: ["current_update"], 2: []}
    assert _adjustments(event) == {1: [], 2: ["current_update"]}
    assert event[0].node.id.int == 2
    # Recency is still measured back from the newest candidate, not the reference time.
    assert _recency(event)[2] == 1.0
    assert _recency(event)[1] == pytest.approx(min(1.0, 2 * math.exp(-.05 * 25)))


# --- Receipts ---------------------------------------------------------------------


def _receipt(items, weights, *, packing=None, execution=EXECUTION):
    return make_receipt(request_id=UUID(int=83), user_id="owner", query=HISTORICAL.query,
                        reference_time=REFERENCE, scopes=(Scope.PROJECT,), scoring=weights,
                        packing=packing or PackingConfig(), candidates=items,
                        bundle=MemoryBundle(), execution=execution)


def _ranked(weights=EVENT):
    ranked, _ = score_and_rank([_imported(1, 150), _imported(2, 30), _imported(3, 0)], weights,
                               now=REFERENCE, query_analysis=HISTORICAL)
    return ranked


def _data(weights=EVENT, packing=None):
    return json.loads(_receipt(_ranked(weights), weights, packing=packing).model_dump_json())


@pytest.mark.parametrize("context_format", ["auditable", "compact", "reader"])
def test_version_20_records_the_setting_in_every_context_format_and_replays(context_format):
    ranked = _ranked()
    saved = _receipt(ranked, EVENT, packing=PackingConfig(context_format=context_format))
    raw = saved.model_dump_json()
    assert saved.schema_version == 20 and saved.scoring == EVENT
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.model_dump_json() == raw and restored.checksum == saved.checksum
    assert restored.replay_ranking() == tuple(c.node.id for c in ranked)


@pytest.mark.parametrize("version", [12, 13, 14])
def test_earlier_weighted_versions_cannot_record_the_setting(version):
    data = _data()
    data["schema_version"] = version
    with pytest.raises(ValidationError, match="Event-time recency requires a version 20 receipt"):
        RetrievalReceipt.model_validate(data)


def test_version_20_requires_the_setting_and_matching_provenance():
    # Version 14 records every packing setting that version 20 does.
    data = _data(DEFAULT_SCORING_WEIGHTS, PackingConfig(context_format="reader"))
    data["schema_version"] = 20
    with pytest.raises(ValidationError, match="Version 20 records event-time recency"):
        RetrievalReceipt.model_validate(data)

    data = _data()
    data["scoring"].pop("recency_time")
    with pytest.raises(ValidationError, match="does not match the recorded recency time"):
        RetrievalReceipt.model_validate(data)


def test_version_20_records_weighted_scoring_only():
    rank_fused, _ = score_and_rank([_imported(1, 0)], ScoringWeights(fusion="rrf"), now=REFERENCE)
    data = json.loads(_receipt(with_rank_fusion_relevance(rank_fused), ScoringWeights(fusion="rrf")).model_dump_json())
    assert data["schema_version"] == 16
    data["schema_version"] = 20
    with pytest.raises(ValidationError, match="Version 20 records weighted scoring only"):
        RetrievalReceipt.model_validate(data)


@pytest.mark.parametrize("change,match", [
    ("relevance", "Version 20 records no rank fusion relevance"),
    ("skipped_floor", "Only rank fusion skips min_score"),
    ("session_decay", "Version 20 records no rank fusion session decay"),
])
def test_version_20_cannot_record_rank_fusion_features(change, match):
    data = _data()
    if change == "relevance":
        candidate = data["candidates"][0]
        candidate["semantic_relevance"] = candidate["trace"]["semantic_similarity"]
    elif change == "skipped_floor":
        data.update(min_score=.3, min_score_skipped=True)
    else:
        data["packing"]["session_context_rank_fusion_score_decay"] = .6
    with pytest.raises(ValidationError, match=match):
        RetrievalReceipt.model_validate(data)


def test_version_20_cannot_carry_copied_rank_fusion_settings():
    # model_copy skips validation, but the receipt validates its scoring again.
    copied = EVENT.model_copy(update={"rrf_tie_break": "event_time"})
    with pytest.raises(ValidationError, match="rrf_tie_break apply only when fusion is 'rrf'"):
        _receipt(_ranked(), copied)


def test_a_receipt_without_an_execution_descriptor_cannot_record_the_setting():
    with pytest.raises(ValueError, match="Event-time recency receipts require an execution descriptor"):
        _receipt(_ranked(), EVENT, packing=PackingConfig(multipath_ordering="density"), execution=None)


def test_saved_weighted_receipts_keep_their_bytes():
    # Weighted scoring, the reader format and a current-state question. Version
    # selection and validation were rewritten to add version 20.
    raw = (FIXTURES / "receipt-v14-weighted.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V14_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 14 and restored.scoring == DEFAULT_SCORING_WEIGHTS
    assert restored.model_dump_json() == raw and restored.checksum == V14_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)
    assert [c.score for c in restored.candidates] == [
        restored.score_provenance[c.node_id].replay_score() for c in restored.candidates
    ]


def test_saved_default_receipts_keep_their_bytes():
    # PRMEConfig's default scoring and packing on a current-state question. The
    # rank fusion receipt rules were rewritten to add version 20.
    raw = (FIXTURES / "receipt-v19-rrf.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V19_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 19 and restored.scoring == default_scoring_weights()
    assert restored.packing.session_context_rank_fusion_score_decay == default_packing_config(
    ).session_context_rank_fusion_score_decay
    assert restored.model_dump_json() == raw and restored.checksum == V19_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)

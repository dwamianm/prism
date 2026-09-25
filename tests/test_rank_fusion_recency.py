"""Opt-in recency and tie-break for rank fusion (issue #168).

Rank fusion orders candidates by their semantic and lexical ranks alone, so on
a current-state question the older of two conflicting memories can rank above
the newer one, and equal fused scores fall back to node ID order.
``rrf_recency_boost`` applies the weighted formula's current-state recency
factor to the fused score, and ``rrf_tie_break="event_time"`` orders equal
fused scores newest first. ``ScoringWeights()`` leaves both unset, and
``PRMEConfig`` sets both by default (0.25 and "event_time").
"""

import hashlib
import json
import random
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine, PRMEConfig
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.quality.feedback import FeedbackSignal, FeedbackSignalType
from prme.quality.tuner import WeightTuner
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, PackingConfig, ScoringWeights
from prme.retrieval.models import MemoryBundle, QueryAnalysis, ScoreProvenance
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.selection import with_rank_fusion_relevance
from prme.types import EpistemicType, QueryIntent, Scope
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_rank_fusion import EXECUTION, NOW, RRF, candidate

config = test_durable_ingestion.config
user = test_durable_ingestion.user

FIXTURES = Path(__file__).parent / "fixtures/relevance"
# Written by make_receipt on main before this change: rank fusion, a skipped
# min_score of 0.3 and a session neighbor at a rank fusion session decay of 0.7.
V18_CHECKSUM = "96811d5f26304d8e8a93f3b29499d49ed7f40352b84f63eb9e5f2a684a24e118"
BOOST = ScoringWeights(fusion="rrf", rrf_recency_boost=.25)
TIE = ScoringWeights(fusion="rrf", rrf_tie_break="event_time")
BOTH = ScoringWeights(fusion="rrf", rrf_recency_boost=.25, rrf_tie_break="event_time")
CURRENT = QueryAnalysis(query="What database do we currently use?", intent=QueryIntent.FACTUAL)
UPDATE = "We switched to PostgreSQL; MySQL is no longer used."


def _copies(items):
    return [item.model_copy(deep=True) for item in items]


def _conflict():
    # The older memory ranks first on both channels. The newest memory is not
    # the update, so the current-update multiplier does not apply.
    return [candidate(1, semantic=.9, lexical=1.0, days=30, content="Our database is MySQL."),
            candidate(2, semantic=.8, lexical=.6, days=5, content=UPDATE),
            candidate(3, semantic=.7, lexical=.2, days=1, content="The database has nightly backups.")]


# --- Configuration -----------------------------------------------------------


def test_unset_settings_keep_the_rank_fusion_bytes_and_version():
    # Recorded on main before these settings existed.
    assert RRF.version_id == "8a71d4bd2988"
    assert hashlib.sha256(RRF.model_dump_json().encode()).hexdigest() == (
        "fd77d89d7683898d87df81973d4401faf0c7fbe99793d686b121ac9f1279a15e"
    )
    assert not {"rrf_recency_boost", "rrf_tie_break"} & RRF.model_dump(mode="json").keys()


def test_settings_are_serialized_versioned_and_validated():
    data = BOTH.model_dump(mode="json")
    assert (data["rrf_recency_boost"], data["rrf_tie_break"]) == (.25, "event_time")
    assert ScoringWeights.model_validate_json(BOTH.model_dump_json()) == BOTH
    versions = {RRF.version_id, BOOST.version_id, TIE.version_id, BOTH.version_id,
                ScoringWeights(fusion="rrf", rrf_recency_boost=1.0).version_id}
    assert len(versions) == 5
    for bad in (0, -.1, 4.01, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            ScoringWeights(fusion="rrf", rrf_recency_boost=bad)
    with pytest.raises(ValidationError):
        ScoringWeights(fusion="rrf", rrf_tie_break="node_id")


def test_weighted_scoring_drops_the_settings_with_a_warning(monkeypatch):
    with pytest.warns(UserWarning, match="rrf_recency_boost applies only when fusion is 'rrf' and is ignored"):
        assert ScoringWeights(rrf_recency_boost=.25) == DEFAULT_SCORING_WEIGHTS
    with pytest.warns(UserWarning, match="rrf_recency_boost, rrf_tie_break apply only .* are ignored"):
        assert ScoringWeights(rrf_recency_boost=.25, rrf_tie_break="event_time") == DEFAULT_SCORING_WEIGHTS
    # Rank fusion is the default, so an environment that names only the
    # tie-break keeps it; a weighted fusion set beside it drops it with a warning.
    monkeypatch.setenv("PRME_SCORING__FUSION", "weighted")
    monkeypatch.setenv("PRME_SCORING__RRF_TIE_BREAK", "event_time")
    with pytest.warns(UserWarning, match="rrf_tie_break applies only"):
        assert PRMEConfig(_env_file=None).scoring == DEFAULT_SCORING_WEIGHTS


def test_prme_config_sets_both_by_default(monkeypatch):
    assert PRMEConfig(_env_file=None).scoring == BOTH
    monkeypatch.setenv("PRME_SCORING__RRF_RECENCY_BOOST", "0.5")
    assert PRMEConfig(_env_file=None).scoring == ScoringWeights(
        fusion="rrf", rrf_recency_boost=.5, rrf_tie_break="event_time")


def test_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("PRME_SCORING__FUSION", "rrf")
    monkeypatch.setenv("PRME_SCORING__RRF_RECENCY_BOOST", "0.25")
    monkeypatch.setenv("PRME_SCORING__RRF_TIE_BREAK", "event_time")
    assert PRMEConfig(_env_file=None).scoring == BOTH


# --- Recency ------------------------------------------------------------------


def test_without_the_boost_the_older_conflicting_memory_ranks_first():
    ranked, _ = score_and_rank(_conflict(), RRF, now=NOW, query_analysis=CURRENT)
    assert [c.node.id.int for c in ranked][:2] == [1, 2]
    assert all(c.score_provenance.rank_fusion.recency_boost_factor is None for c in ranked)


def test_the_boost_ranks_the_newer_conflicting_memory_first_and_replays():
    ranked, _ = score_and_rank(_conflict(), BOOST, now=NOW, query_analysis=CURRENT)
    assert [c.node.id.int for c in ranked] == [2, 3, 1]
    assert all(item.score_provenance.adjustments == () for item in ranked)
    fusion = {c.node.id.int: c.score_provenance.rank_fusion for c in ranked}
    # The update's wording doubles its recency to the newest memory's.
    assert fusion[2].recency_boost_factor == fusion[3].recency_boost_factor == 1.0
    assert 0 < fusion[1].recency_boost_factor < 1
    for item in ranked:
        restored = ScoreProvenance.model_validate_json(item.score_provenance.model_dump_json())
        assert restored.replay_score() == item.composite_score


def test_the_recency_is_the_weighted_formulas_current_state_recency():
    items = _conflict()
    weighted, _ = score_and_rank(_copies(items), DEFAULT_SCORING_WEIGHTS, now=NOW, query_analysis=CURRENT)
    fused, _ = score_and_rank(_copies(items), BOOST, now=NOW, query_analysis=CURRENT)
    assert {c.node.id: c.score_trace.recency_factor for c in fused} == {
        c.node.id: c.score_trace.recency_factor for c in weighted
    }
    # Pool-relative: the most recent candidate's factor is 1.0.
    boost = {c.node.id: 1 + .25 * c.score_trace.recency_factor for c in fused}
    assert {c.node.id: c.score_provenance.rank_fusion.recency_boost_factor for c in fused} == pytest.approx(
        {node_id: value / max(boost.values()) for node_id, value in boost.items()}
    )


def test_a_promoted_older_memory_does_not_look_newest():
    # Stored without an event time; an organizer promotion reset the older
    # memory's updated_at after the update was stored.
    items = _conflict()
    for item, stored_days, touched_days in zip(items, (30, 5, 10), (1, 5, 10)):
        item.node.event_time = None
        item.node.created_at = NOW - timedelta(days=stored_days)
        item.node.updated_at = NOW - timedelta(days=touched_days)
    ranked, _ = score_and_rank(items, BOTH, now=NOW, query_analysis=CURRENT)
    assert ranked[0].node.content == UPDATE
    fusion = {c.node.id.int: c.score_provenance.rank_fusion for c in ranked}
    assert fusion[1].recency_boost_factor < fusion[2].recency_boost_factor == 1.0
    assert (fusion[2].tie_break, fusion[3].tie_break, fusion[1].tie_break) == (0.0, 1 / 3, 2 / 3)


def test_the_boost_applies_only_to_current_state_questions():
    historical = QueryAnalysis(query="Which database did we use before?", intent=QueryIntent.FACTUAL)
    for query in (None, historical):
        plain, _ = score_and_rank(_conflict(), RRF, now=NOW, query_analysis=query)
        boosted, _ = score_and_rank(_conflict(), BOOST, now=NOW, query_analysis=query)
        assert [(c.node.id, c.composite_score) for c in boosted] == [
            (c.node.id, c.composite_score) for c in plain
        ]
        assert all(c.score_provenance.rank_fusion.recency_boost_factor is None for c in boosted)
        assert all(c.score_trace.recency_factor == 0.0 for c in boosted)


def test_no_boosted_score_exceeds_first_place_on_both_channels():
    ranked, _ = score_and_rank(_conflict(), ScoringWeights(fusion="rrf", rrf_recency_boost=4,
                                                           current_update_multiplier=1.0),
                               now=NOW, query_analysis=CURRENT)
    assert all(0 < c.composite_score <= 1.0 for c in ranked)


def test_an_implicit_current_state_question_without_an_update_takes_no_boost():
    # "What X does Y ..." implies current state only when an update is in the pool.
    implicit = QueryAnalysis(query="What database does the team use?", intent=QueryIntent.FACTUAL)
    items = [item for item in _conflict() if item.node.content != UPDATE]
    ranked, _ = score_and_rank(items, BOOST, now=NOW, query_analysis=implicit)
    assert all(c.score_provenance.rank_fusion.recency_boost_factor is None for c in ranked)

    ranked, _ = score_and_rank(_conflict(), BOOST, now=NOW, query_analysis=implicit)
    assert all(c.score_provenance.rank_fusion.recency_boost_factor is not None for c in ranked)


def test_the_boost_and_the_current_update_multiplier_combine_and_replay():
    # The update is now the newest memory, so it also takes the current-update multiplier.
    items = [item for item in _conflict() if item.node.id.int != 3]
    ranked, _ = score_and_rank(items, BOOST, now=NOW, query_analysis=CURRENT)
    update = ranked[0]
    assert update.node.content == UPDATE
    assert [op.kind for op in update.score_provenance.adjustments] == ["current_update"]
    assert update.score_provenance.rank_fusion.recency_boost_factor == 1.0
    assert update.composite_score == pytest.approx(update.score_trace.composite_score * 1.3)
    restored = ScoreProvenance.model_validate_json(update.score_provenance.model_dump_json())
    assert restored.replay_score() == update.composite_score


def test_weighted_settings_copied_with_the_terms_are_rejected():
    # Validation drops the terms from weighted scoring; model_copy skips it.
    copied = DEFAULT_SCORING_WEIGHTS.model_copy(update={"rrf_tie_break": "event_time"})
    with pytest.raises(ValueError, match="rrf_tie_break apply only to fusion='rrf'"):
        score_and_rank(_conflict(), copied, now=NOW)


def test_feedback_tuning_keeps_the_terms():
    tuned = WeightTuner(BOTH).update([FeedbackSignal(
        query="database", surfaced_node_ids=["n1"], signal_type=FeedbackSignalType.USED,
    )])
    assert tuned.rank_fusion_opt_ins == BOTH.rank_fusion_opt_ins


# --- Tie-break ----------------------------------------------------------------


@pytest.mark.parametrize("old_id,new_id", [(1, 2), (2, 1)])
def test_equal_fused_scores_put_the_newer_memory_first_whatever_the_node_ids(old_id, new_id):
    # Ranks (1, 2) and (2, 1) fuse to the same score.
    old = candidate(old_id, semantic=.9, lexical=.5, days=30)
    new = candidate(new_id, semantic=.8, lexical=.9, days=1)
    plain, _ = score_and_rank(_copies([old, new]), RRF, now=NOW)
    assert plain[0].composite_score == plain[1].composite_score
    assert [c.node.id.int for c in plain] == [1, 2]

    ranked, _ = score_and_rank([old, new], TIE, now=NOW)
    assert [c.node.id.int for c in ranked] == [new_id, old_id]
    assert ranked[0].composite_score == plain[0].composite_score
    assert 0 < ranked[0].composite_score - ranked[1].composite_score < 1e-11
    assert [c.score_provenance.rank_fusion.tie_break for c in ranked] == [0.0, .5]


def test_the_tie_break_only_orders_scores_that_are_equal():
    rng = random.Random(168)
    items = [candidate(i, semantic=round(rng.random(), 2) if i % 3 else None,
                       lexical=round(rng.random(), 1) if i % 4 else None, days=rng.randrange(30))
             for i in range(1, 90)]
    plain, _ = score_and_rank(_copies(items), RRF, now=NOW)
    broken, _ = score_and_rank(_copies(items), TIE, now=NOW)
    base = {c.node.id: c.composite_score for c in plain}
    assert all(0 <= base[c.node.id] - c.composite_score < 1e-11 for c in broken)
    assert len(set(base.values())) < len(base)  # the pool has ties to break
    days = {c.node.id: (NOW - c.node.event_time).days for c in items}
    for higher, lower in zip(broken, broken[1:]):
        if base[higher.node.id] == base[lower.node.id] > 0:
            assert days[higher.node.id] <= days[lower.node.id]
        else:
            assert base[higher.node.id] >= base[lower.node.id]  # equal only at 0


def test_a_zero_score_takes_no_tie_break():
    # An epistemic weight of 0 zeroes a ranked candidate's score.
    older = candidate(1, semantic=.9, days=5)
    newer = candidate(2, semantic=.8, days=1, epistemic=EpistemicType.HYPOTHETICAL)
    ranked, _ = score_and_rank([older, newer], TIE, now=NOW,
                               epistemic_weights={"asserted": 0.0, "hypothetical": .5})
    zero = next(c for c in ranked if c.node.id.int == 1)
    assert zero.score_provenance.rank_fusion.tie_break == .5
    assert zero.composite_score == 0.0


def test_times_without_a_zone_are_read_as_utc():
    aware = [candidate(1, semantic=.9, lexical=.5, days=3), candidate(2, semantic=.8, lexical=.9, days=1)]
    naive = [item.model_copy(deep=True) for item in aware]
    for item in naive:
        item.node.event_time = item.node.event_time.replace(tzinfo=None)
    places = [
        {c.node.id: c.score_provenance.rank_fusion.tie_break
         for c in score_and_rank(items, TIE, now=NOW)[0]}
        for items in (aware, naive)
    ]
    assert places[0] == places[1]


def test_candidates_on_neither_channel_still_score_zero():
    ranked, _ = score_and_rank([candidate(1, semantic=.5, days=3), candidate(2, graph=1.0)], TIE, now=NOW)
    assert [c.composite_score for c in ranked] == [.5, 0.0]
    assert ranked[1].score_provenance.rank_fusion.tie_break == 0.0


# --- Provenance ---------------------------------------------------------------


@pytest.mark.parametrize("change,match", [
    ("recency_without_boost", "recency boost factor requires rrf_recency_boost"),
    ("tie_break_without_setting", "exactly when rrf_tie_break is set"),
    ("setting_without_tie_break", "exactly when rrf_tie_break is set"),
])
def test_inconsistent_provenance_is_rejected(change, match):
    ranked, _ = score_and_rank(_conflict(), BOTH, now=NOW, query_analysis=CURRENT)
    data = json.loads(ranked[0].score_provenance.model_dump_json())
    if change == "recency_without_boost":
        data["weights"].pop("rrf_recency_boost")
    elif change == "tie_break_without_setting":
        data["weights"].pop("rrf_tie_break")
    else:
        data["rank_fusion"].pop("tie_break")
    with pytest.raises(ValidationError, match=match):
        ScoreProvenance.model_validate(data)


def test_provenance_without_the_settings_keeps_its_bytes():
    ranked, _ = score_and_rank(_conflict(), RRF, now=NOW, query_analysis=CURRENT)
    data = json.loads(ranked[0].score_provenance.model_dump_json())
    assert not {"recency_boost_factor", "tie_break"} & data["rank_fusion"].keys()


# --- Receipts -----------------------------------------------------------------


def _receipt(items, weights, *, packing=None, min_score=None, skipped=False):
    return make_receipt(request_id=UUID(int=168), user_id="owner", query=CURRENT.query,
                        reference_time=NOW, scopes=(Scope.PROJECT,), scoring=weights,
                        packing=packing or PackingConfig(), candidates=with_rank_fusion_relevance(items),
                        bundle=MemoryBundle(), min_score=min_score, execution=EXECUTION,
                        min_score_skipped=skipped)


@pytest.mark.parametrize("weights", [BOOST, TIE, BOTH])
def test_version_19_records_the_settings_and_replays(weights):
    ranked, _ = score_and_rank(_conflict(), weights, now=NOW, query_analysis=CURRENT)
    saved = _receipt(ranked, weights)
    raw = saved.model_dump_json()
    assert saved.schema_version == 19 and saved.scoring == weights
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.model_dump_json() == raw and restored.checksum == saved.checksum
    assert restored.replay_ranking() == tuple(c.node.id for c in ranked)


def test_rank_fusion_without_the_settings_stays_at_version_16():
    ranked, _ = score_and_rank(_conflict(), RRF, now=NOW, query_analysis=CURRENT)
    assert _receipt(ranked, RRF).schema_version == 16


def _lexical_only(weights):
    ranked, _ = score_and_rank([candidate(1, lexical=1.0, days=5), candidate(2, lexical=.4)],
                               weights, now=NOW, query_analysis=CURRENT)
    return ranked


@pytest.mark.parametrize("version", [16, 17, 18])
def test_earlier_versions_cannot_record_the_settings(version):
    packing = PackingConfig(session_context_rank_fusion_score_decay=.7) if version == 17 else None
    skipped = version == 18
    saved = _receipt(_lexical_only(BOTH), BOTH, packing=packing, min_score=.3 if skipped else None,
                     skipped=skipped)
    assert saved.schema_version == 19
    data = json.loads(saved.model_dump_json())
    data["schema_version"] = version
    with pytest.raises(ValidationError, match="require a version 19 receipt"):
        RetrievalReceipt.model_validate(data)


def test_version_19_admits_a_skipped_floor_and_a_session_decay():
    packing = PackingConfig(session_context_rank_fusion_score_decay=.7)
    saved = _receipt(_lexical_only(TIE), TIE, packing=packing, min_score=.3, skipped=True)
    assert saved.schema_version == 19 and saved.min_score_skipped
    assert RetrievalReceipt.model_validate_json(saved.model_dump_json()).checksum == saved.checksum


def test_version_19_requires_a_setting_and_matching_provenance():
    ranked, _ = score_and_rank(_conflict(), RRF, now=NOW, query_analysis=CURRENT)
    data = json.loads(_receipt(ranked, RRF).model_dump_json())
    data["schema_version"] = 19
    with pytest.raises(ValidationError, match="Version 19 records a rank fusion recency boost or tie-break"):
        RetrievalReceipt.model_validate(data)

    ranked, _ = score_and_rank(_conflict(), BOTH, now=NOW, query_analysis=CURRENT)
    data = json.loads(_receipt(ranked, BOTH).model_dump_json())
    data["scoring"].pop("rrf_tie_break")
    with pytest.raises(ValidationError, match="does not match the recorded rank fusion recency"):
        RetrievalReceipt.model_validate(data)


def test_the_boost_applies_to_every_score_in_a_receipt_or_to_none():
    ranked, _ = score_and_rank(_conflict(), BOOST, now=NOW, query_analysis=CURRENT)
    data = json.loads(_receipt(ranked, BOOST).model_dump_json())
    # A factor of 1.0 changes no score, so only the all-or-none rule catches its removal.
    provenance = data["score_provenance"][str(UUID(int=3))]
    assert provenance["rank_fusion"].pop("recency_boost_factor") == 1.0
    with pytest.raises(ValidationError, match="every score in a pool or to none"):
        RetrievalReceipt.model_validate(data)


def test_saved_version_18_receipts_keep_their_bytes():
    # Version selection was rewritten to add version 19.
    raw = (FIXTURES / "receipt-v18-rrf.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V18_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 18 and restored.min_score_skipped
    assert (restored.scoring.rrf_recency_boost, restored.scoring.rrf_tie_break) == (None, None)
    assert restored.model_dump_json() == raw and restored.checksum == V18_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)
    assert [c.score for c in restored.candidates] == [.5, 0.4919354839, .35]


# --- Retrieval ----------------------------------------------------------------


OLD_FACT = "What database does the team use? The team uses MySQL."


async def _conflict_retrieval(config, user, scoring):
    config = config.model_copy(update={"scoring": scoring})
    async with MemoryEngine.open(config) as engine:
        # The newest memory is not the update, so the current-update
        # multiplier does not apply. The update contains every query word:
        # PostgreSQL's lexical index (plainto_tsquery) requires all of them,
        # while Tantivy on DuckDB matches any, so the update has a lexical rank
        # on both backends.
        for text, days in ((OLD_FACT, 30),
                           ("The team switched to a PostgreSQL database, so MySQL is no longer used.", 5),
                           ("The office plants were watered.", 1)):
            await engine.store(text, user_id=user, scope=Scope.PROJECT,
                               event_time=NOW - timedelta(days=days))
        response = await engine.retrieve("What database does the team use?", user_id=user,
                                         scope=Scope.PROJECT, min_score=0, include_cross_scope=False,
                                         reference_time=NOW)
        request_id = str(response.metadata.request_id)
        saved = await engine.get_retrieval_receipt(request_id, user_id=user)
        async with client_for(app_for(config, engine, user)) as client:
            served = await client.get(f"/v1/retrievals/{request_id}")
    assert served.status_code == 200, served.text
    assert served.json() == json.loads(saved.model_dump_json())
    assert saved.replay_ranking() == tuple(item.node.id for item in response.results)
    # The older memory ranks first on both channels, so only recency can move it.
    old = next(item for item in response.results if item.node.content == OLD_FACT)
    fusion = saved.score_provenance[old.node.id].rank_fusion
    assert (fusion.semantic_rank, fusion.lexical_rank) == (1, 1)
    return [item.node.content for item in response.results], saved


def _position(contents, prefix):
    return next(index for index, content in enumerate(contents) if content.startswith(prefix))


async def test_retrieval_applies_and_records_the_settings(config, user):
    # Separate users keep the two stores apart in the shared database.
    plain, before = await _conflict_retrieval(config, f"{user}-plain", RRF)
    assert _position(plain, "What database") < _position(plain, "The team switched")
    # Version 17: the default packing also records the rank fusion session decay.
    assert before.schema_version == 17

    # BOTH is PRMEConfig's default scoring, so this retrieval runs the default settings.
    assert config.scoring == BOTH
    boosted, saved = await _conflict_retrieval(config, f"{user}-boosted", BOTH)
    assert _position(boosted, "The team switched") < _position(boosted, "What database")
    assert saved.schema_version == 19 and saved.scoring == BOTH
    assert (saved.packing.context_format, saved.packing.multipath_ordering,
            saved.packing.session_context_rank_fusion_score_decay) == ("reader", "balanced", .6)
    fusions = [provenance.rank_fusion for provenance in saved.score_provenance.values()]
    assert all(f.recency_boost_factor is not None and f.tie_break is not None for f in fusions)

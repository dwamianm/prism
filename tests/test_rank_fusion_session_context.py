"""Opt-in session decay for rank-fused triggers (issue #111).

A rank-fused score is compressed: 0.85 of a first-place score outranks every
candidate from about twelfth place down on both channels, so session
neighbors can crowd primary evidence out of the context. Unset, rank fusion
keeps session_context_score_decay.
"""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, PackingConfig
from prme.retrieval.models import MemoryBundle
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.session_context import expand_session_context
from prme.types import Scope
from tests import test_durable_ingestion
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_rank_fusion import EXECUTION, NOW, RRF, candidate

config = test_durable_ingestion.config
user = test_durable_ingestion.user

FIXTURES = Path(__file__).parent / "fixtures/relevance"
# Written by make_receipt on main before this change: rank fusion, a session
# neighbor at session_context_score_decay (0.85) and a min_score of 0.5.
V16_CHECKSUM = "d880fac17e48ddec94d0b54392584e9ac9c48d52d3224b758b8f68f2c6749522"
DECAY_FIELD = "session_context_rank_fusion_score_decay"
# Distinct from both defaults, so a test cannot pass on a default by accident.
WEIGHTED, RANK_FUSED = .5, .7


def _graph(*candidates):
    return Mock(query_nodes=AsyncMock(return_value=[item.node for item in candidates]))


def _session_decays(candidate):
    return [item.coefficient for item in candidate.score_provenance.adjustments
            if item.kind == "session_decay"]


async def _expand(weights, packing):
    trigger = candidate(1, semantic=.9, lexical=.9, session="conversation")
    neighbor = candidate(2, session="conversation")
    ranked, _ = score_and_rank([trigger, candidate(3, semantic=.5, lexical=.5)], weights, now=NOW)
    expanded = await expand_session_context(ranked, _graph(trigger, neighbor), "owner", packing,
                                            [Scope.PROJECT])
    added = next(c for c in expanded if c.node.id == neighbor.node.id)
    return ranked[0].composite_score, added


# --- Expansion ----------------------------------------------------------------


async def test_a_rank_fused_trigger_takes_the_rank_fusion_decay_when_it_is_set():
    packing = PackingConfig(session_context_score_decay=WEIGHTED, **{DECAY_FIELD: RANK_FUSED})
    trigger_score, added = await _expand(RRF, packing)

    assert added.composite_score == trigger_score * RANK_FUSED
    assert _session_decays(added) == [RANK_FUSED]
    assert added.score_provenance.replay_score() == added.composite_score


async def test_unset_rank_fusion_keeps_the_weighted_decay():
    trigger_score, added = await _expand(RRF, PackingConfig(session_context_score_decay=WEIGHTED))

    assert added.composite_score == trigger_score * WEIGHTED
    assert _session_decays(added) == [WEIGHTED]


async def test_a_weighted_trigger_ignores_the_rank_fusion_decay():
    packing = PackingConfig(session_context_score_decay=WEIGHTED, **{DECAY_FIELD: RANK_FUSED})
    trigger_score, added = await _expand(DEFAULT_SCORING_WEIGHTS, packing)

    assert added.composite_score == trigger_score * WEIGHTED
    assert _session_decays(added) == [WEIGHTED]


async def test_a_top_neighbor_at_0_6_no_longer_outranks_primary_evidence_near_twelfth_place():
    # Thirteen candidates ranked 1 to 13 on both channels; the first has a neighbor.
    pool = [candidate(number, semantic=1 - number / 100, lexical=1 - number / 100,
                      session="conversation" if number == 1 else None)
            for number in range(1, 14)]
    neighbor = candidate(99, session="conversation")
    ranked, _ = score_and_rank(pool, RRF, now=NOW)
    assert ranked[11].composite_score == pytest.approx(61 / 72)  # twelfth place: 0.847

    async def neighbor_position(packing):
        expanded = await expand_session_context(ranked, _graph(pool[0], neighbor), "owner", packing)
        return [c.node.id for c in expanded].index(neighbor.node.id) + 1

    # 0.85 of first place beats twelfth and thirteenth place; 0.6 beats none of them.
    assert await neighbor_position(PackingConfig()) == 12
    assert await neighbor_position(PackingConfig(**{DECAY_FIELD: .6})) == 14


@pytest.mark.parametrize("value", [0, -.1, 1.01, float("nan"), float("inf")])
def test_the_rank_fusion_decay_is_a_fraction(value):
    with pytest.raises(ValidationError) as exc:
        PackingConfig(**{DECAY_FIELD: value})
    assert exc.value.errors()[0]["loc"] == (DECAY_FIELD,)


def test_the_rank_fusion_decay_is_read_from_the_environment(monkeypatch):
    from prme import PRMEConfig

    monkeypatch.setenv("PRME_PACKING__SESSION_CONTEXT_RANK_FUSION_SCORE_DECAY", "0.6")
    assert PRMEConfig(_env_file=None).packing.session_context_rank_fusion_score_decay == .6


def test_an_unset_rank_fusion_decay_keeps_the_packing_bytes():
    assert DECAY_FIELD not in PackingConfig().model_dump(mode="json")
    assert PackingConfig(**{DECAY_FIELD: .6}).model_dump(mode="json")[DECAY_FIELD] == .6


# --- Retrieval ----------------------------------------------------------------


async def _neighbor_retrieval(config, user, **kwargs):
    async with MemoryEngine.open(config) as engine:
        for text in ("The telescope is blue.", "We bought bread.", "The weather was mild."):
            await engine.store(text, user_id=user, scope=Scope.PROJECT, session_id="s1")
        await engine.store("Anna plays tennis.", user_id=user, scope=Scope.PROJECT, session_id="s2")
        response = await engine.retrieve("telescope", user_id=user, scope=Scope.PROJECT, min_score=0,
                                         include_cross_scope=False, reference_time=NOW, **kwargs)
        request_id = str(response.metadata.request_id)
        saved = await engine.get_retrieval_receipt(request_id, user_id=user)
        async with client_for(app_for(config, engine, user)) as client:
            served = await client.get(f"/v1/retrievals/{request_id}")
    assert served.status_code == 200, served.text
    assert served.json() == json.loads(saved.model_dump_json())
    decays = {decay for item in response.results for decay in _session_decays(item)}
    return decays, saved, response


def _with_decay(config, scoring, decay=.55):
    packing = config.packing.model_copy(update={DECAY_FIELD: decay})
    return config.model_copy(update={"scoring": scoring, "packing": packing})


async def test_rank_fused_retrieval_applies_and_records_the_rank_fusion_decay(config, user):
    decays, saved, response = await _neighbor_retrieval(_with_decay(config, RRF), user)

    assert decays == {.55}
    assert saved.schema_version == 17
    assert json.loads(saved.model_dump_json())["packing"][DECAY_FIELD] == .55
    assert saved.replay_ranking() == tuple(item.node.id for item in response.results)


async def test_rank_fused_retrieval_without_the_decay_is_unchanged(config, user):
    decays, saved, _ = await _neighbor_retrieval(config.model_copy(update={"scoring": RRF}), user)

    assert decays == {.85}
    assert saved.schema_version == 16
    assert DECAY_FIELD not in json.loads(saved.model_dump_json())["packing"]


async def test_a_per_request_rank_fusion_takes_the_rank_fusion_decay(config, user):
    settings = _with_decay(config, DEFAULT_SCORING_WEIGHTS)
    decays, saved, _ = await _neighbor_retrieval(settings, user, weights=RRF)

    assert decays == {.55}
    assert saved.schema_version == 17


async def test_weighted_retrieval_ignores_and_omits_the_rank_fusion_decay(config, user):
    decays, saved, _ = await _neighbor_retrieval(_with_decay(config, DEFAULT_SCORING_WEIGHTS), user)

    assert decays == {.85}
    assert saved.schema_version == 12
    assert saved.packing == config.packing
    assert DECAY_FIELD not in json.loads(saved.model_dump_json())["packing"]


# --- Receipts -----------------------------------------------------------------


def test_saved_version_16_receipts_keep_their_bytes():
    raw = (FIXTURES / "receipt-v16-rrf.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V16_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 16
    assert restored.model_dump_json() == raw and restored.checksum == V16_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)
    # Its neighbor took session_context_score_decay, which is what an unset decay means.
    assert getattr(restored.packing, DECAY_FIELD) is None
    assert [c.score for c in restored.candidates] == [1.0, 0.9838709677, .85]


async def _rank_fused_receipt(decay=RANK_FUSED):
    trigger = candidate(1, semantic=.9, lexical=.9, session="conversation")
    neighbor = candidate(2, session="conversation")
    ranked, _ = score_and_rank([trigger], RRF, now=NOW)
    packing = PackingConfig(**{DECAY_FIELD: decay})
    expanded = await expand_session_context(ranked, _graph(trigger, neighbor), "owner", packing)
    return make_receipt(request_id=UUID(int=111), user_id="owner", query="telescope",
                         reference_time=NOW, scopes=(Scope.PROJECT,), scoring=RRF, packing=packing,
                         candidates=expanded, bundle=MemoryBundle(), ranking_policy="score_id",
                         execution=EXECUTION)


async def test_version_17_records_the_decay_its_neighbors_took():
    saved = await _rank_fused_receipt()
    raw = saved.model_dump_json()
    data = json.loads(raw)
    assert data["schema_version"] == 17
    assert data["packing"][DECAY_FIELD] == RANK_FUSED
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.model_dump_json() == raw and restored.checksum == saved.checksum

    missing = json.loads(json.dumps(data))
    missing["packing"].pop(DECAY_FIELD)
    with pytest.raises(ValidationError, match="Version 17 records a rank fusion session decay"):
        RetrievalReceipt.model_validate(missing)

    contradicted = json.loads(json.dumps(data))
    contradicted["packing"][DECAY_FIELD] = .65
    with pytest.raises(ValidationError, match="does not match the recorded rank fusion session decay"):
        RetrievalReceipt.model_validate(contradicted)


@pytest.mark.parametrize("version", [15, 16])
async def test_earlier_rank_fusion_receipts_cannot_record_the_decay(version):
    data = json.loads((await _rank_fused_receipt()).model_dump_json())
    data["schema_version"] = version
    if version == 15:
        for item in data["candidates"]:
            item.pop("semantic_relevance")
    with pytest.raises(ValidationError, match="A rank fusion session decay requires a version 17"):
        RetrievalReceipt.model_validate(data)

"""Each source text is packed once (packing.fold_repeated_text)."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme import MemoryEngine
from prme.ingestion.schema import ExtractionResult
from prme.models import MemoryNode
from prme.models.relevance import RetrievalReceipt, make_receipt
from prme.models.speaker import SPEAKER_METADATA_KEY
from prme.retrieval.config import DEFAULT_SCORING_WEIGHTS, PackingConfig, ScoringWeights
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.scoring import score_and_rank
from prme.retrieval.tokenization import count_tokens
from prme.types import EpistemicType, LifecycleState, NodeType, Scope
from tests import test_durable_ingestion
from tests.previous_defaults import previous_defaults
from tests.test_http_write_fidelity import app_for, client_for
from tests.test_rank_fusion import EXECUTION, NOW, PRODUCT, RRF, candidate

config = test_durable_ingestion.config
user = test_durable_ingestion.user

EVENT = datetime(2026, 2, 3, 19, 4, tzinfo=timezone.utc)
TURN = ("Mostly work stuff and planning. I'm a data analyst at Northwind, and my manager Priya keeps "
        "asking me for weekly dashboards.")
SENTENCE = "I'm a data analyst at Northwind, and my manager Priya keeps asking me for weekly dashboards."
OTHER = "We use Tableau at Northwind."


def record(number: int, text: str, score: float, *, source: int = 1, **fields) -> RetrievalCandidate:
    """A record of source event ``source``; record 1 is that event's own turn."""
    fields.setdefault("event_time", EVENT)
    node = MemoryNode(id=UUID(int=number), user_id="owner", node_type=fields.pop("node_type", NodeType.FACT),
                      content=text, evidence_refs=[UUID(int=source)], **fields)
    return RetrievalCandidate(node=node, composite_score=score, paths=["VECTOR", "LEXICAL"], path_count=2)


def turn(score: float, **fields) -> RetrievalCandidate:
    return record(1, TURN, score, node_type=NodeType.NOTE, metadata={SPEAKER_METADATA_KEY: "Dana"}, **fields)


def claims(*scores: float) -> list[RetrievalCandidate]:
    return [record(10 + index, SENTENCE, score) for index, score in enumerate(scores)]


def reader(**updates) -> PackingConfig:
    return PackingConfig(**{"context_format": "reader", "token_budget": 4096, "overhead_tokens": 0,
                            "min_fidelity": "full", **updates})


def lines(bundle) -> list[str]:
    return [line for line in bundle.rendered_context.splitlines() if line.startswith("- ")]


def packed_ids(bundle) -> set[int]:
    return {candidate.node.id.int for values in bundle.sections.values() for candidate in values}


def test_off_by_default_a_sentence_shows_once_per_record():
    assert PackingConfig().fold_repeated_text is False
    bundle = pack_context([turn(.9), *claims(.8, .7, .6, .5)], reader())
    assert len(lines(bundle)) == 5
    assert bundle.rendered_context.count(SENTENCE) == 5


def test_claims_inside_their_packed_turn_are_left_out():
    bundle = pack_context([turn(.9), *claims(.8, .7, .6, .5)], reader(fold_repeated_text=True))
    assert packed_ids(bundle) == {1}
    assert bundle.rendered_context.count(SENTENCE) == 1
    assert {node_id.int for node_id in bundle.excluded_ids} == {10, 11, 12, 13}
    assert bundle.tokens_used == count_tokens(bundle.rendered_context, "cl100k_base")


def test_a_turn_packed_after_its_claims_replaces_them():
    bundle = pack_context([*claims(.9, .8), turn(.7)], reader(fold_repeated_text=True))
    # The second claim repeated the first; the turn then took the first's place.
    assert packed_ids(bundle) == {1}
    assert bundle.rendered_context.count(SENTENCE) == 1
    assert bundle.tokens_used == count_tokens(bundle.rendered_context, "cl100k_base")


def test_identical_claims_without_their_turn_pack_once():
    bundle = pack_context(claims(.9, .8, .7), reader(fold_repeated_text=True))
    assert packed_ids(bundle) == {10}


@pytest.mark.parametrize("fields", [
    {"lifecycle_state": LifecycleState.SUPERSEDED},
    {"epistemic_type": EpistemicType.HYPOTHETICAL},
    {"event_time": EVENT + timedelta(days=14)},
    {"metadata": {SPEAKER_METADATA_KEY: "Sam"}},
    {"source": 2},
])
def test_a_record_whose_line_shows_something_else_stays(fields):
    other = record(10, SENTENCE, .8, **fields)
    bundle = pack_context([turn(.9), other], reader(fold_repeated_text=True))
    assert packed_ids(bundle) == {1, 10}


def test_a_turn_that_states_its_date_still_shows_the_claims_time():
    dated = "(7:04 pm on 3 February, 2026) Dana: " + TURN
    bundle = pack_context([record(1, dated, .9, node_type=NodeType.NOTE), *claims(.8)],
                          reader(fold_repeated_text=True))
    # The turn's line drops its time label because its text states the date; the time is the same.
    assert "[2026-02-03" not in lines(bundle)[0]
    assert packed_ids(bundle) == {1}


def test_the_freed_budget_packs_another_record():
    other = record(20, OTHER, .6, source=2)
    candidates = [*claims(.95, .9, .85, .8), turn(.7), other]
    # Room for one claim, the turn and the other record, but not for four copies of the claim.
    budget = pack_context([claims(.95)[0], turn(.7), other], reader()).tokens_used
    without = pack_context(candidates, reader(token_budget=budget))
    folded = pack_context(candidates, reader(token_budget=budget, fold_repeated_text=True))
    # Without folding, copies of the claim fill the room: neither the turn nor the other record fits.
    assert packed_ids(without) <= {10, 11, 12, 13} and len(packed_ids(without)) > 1
    assert packed_ids(folded) == {1, 20}
    assert folded.rendered_context.count(SENTENCE) == 1


def test_required_records_are_never_folded():
    candidates = [turn(.9), *claims(.8)]
    bundle = pack_context(candidates, reader(fold_repeated_text=True),
                          _required=[(UUID(int=10), "full"), (UUID(int=1), "full")])
    assert packed_ids(bundle) == {1, 10}


def test_only_the_reader_format_folds():
    with pytest.raises(ValidationError, match="fold_repeated_text applies only"):
        PackingConfig(fold_repeated_text=True)
    with pytest.raises(ValidationError, match="fold_repeated_text applies only"):
        PackingConfig(context_format="compact", fold_repeated_text=True)
    # Off, the field stays out of serialized configurations and receipts.
    assert "fold_repeated_text" not in PackingConfig().model_dump()
    assert reader(fold_repeated_text=True).model_dump()["fold_repeated_text"] is True


def test_a_copied_config_that_folds_another_format_packs_as_without_it():
    # model_copy skips the validator that refuses other formats.
    auditable = PackingConfig(token_budget=4096, overhead_tokens=0, min_fidelity="full")
    copied = auditable.model_copy(update={"fold_repeated_text": True})
    candidates = [turn(.9), *claims(.8, .7)]
    assert packed_ids(pack_context(candidates, copied)) == {1, 10, 11}
    assert pack_context(candidates, copied).rendered_context == pack_context(candidates, auditable).rendered_context


# --- Receipts ----------------------------------------------------------------------------------------

FIELD = "fold_repeated_text"
FIXTURES = Path(__file__).parent / "fixtures/relevance"
# Written by make_receipt before version 22: rank fusion with session context
# packing (_session_receipt in tests/test_session_context_packing.py).
V21_CHECKSUM = "61cf781f9a0f831bbcf6af1721bf743ab9ec30b5e97b6eed6466f0056b1efec7"


def _ranked(scoring=RRF) -> list[RetrievalCandidate]:
    return score_and_rank([candidate(1, semantic=.9, lexical=.9), candidate(2, semantic=.5)], scoring, now=NOW)[0]


def _receipt(scoring=RRF, packing: PackingConfig | None = None, ranked=None) -> RetrievalReceipt:
    packing = packing or PackingConfig(context_format="reader", **{FIELD: True})
    ranked = ranked or _ranked(scoring)
    return make_receipt(request_id=UUID(int=22), user_id="owner", query="telescope", reference_time=NOW,
                        scopes=(Scope.PROJECT,), scoring=scoring, packing=packing, candidates=ranked,
                        bundle=pack_context(ranked, packing), ranking_policy="score_id", execution=EXECUTION)


@pytest.mark.parametrize("scoring", [RRF, ScoringWeights(**PRODUCT), DEFAULT_SCORING_WEIGHTS],
                         ids=["rank fusion", "rank fusion defaults", "weighted"])
def test_version_22_records_folding_and_round_trips(scoring):
    saved = _receipt(scoring)
    raw = saved.model_dump_json()
    data = json.loads(raw)
    assert data["schema_version"] == 22 and data["packing"][FIELD] is True
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.model_dump_json() == raw and restored.checksum == saved.checksum
    assert restored.replay_ranking() == tuple(item.node_id for item in saved.candidates)

    missing = json.loads(raw)
    missing["packing"].pop(FIELD)
    with pytest.raises(ValidationError, match="Version 22 records folded repeated text"):
        RetrievalReceipt.model_validate(missing)


def test_version_22_admits_session_context_packing():
    packing = PackingConfig(context_format="reader", session_context_packing="adjacent", **{FIELD: True})
    saved = _receipt(RRF, packing)
    raw = saved.model_dump_json()
    assert saved.schema_version == 22 and saved.packing.session_context_packing == "adjacent"
    assert RetrievalReceipt.model_validate_json(raw).model_dump_json() == raw


@pytest.mark.parametrize("version, scoring, session", [
    (14, DEFAULT_SCORING_WEIGHTS, None),
    (16, RRF, None),
    (19, ScoringWeights(**PRODUCT), None),
    (20, ScoringWeights(recency_time="event_time"), None),
    (21, RRF, "adjacent"),
])
def test_earlier_versions_cannot_record_folding(version, scoring, session):
    packing = PackingConfig(context_format="reader", session_context_packing=session, **{FIELD: True})
    data = json.loads(_receipt(scoring, packing).model_dump_json())
    data["schema_version"] = version
    with pytest.raises(ValidationError, match="Folded repeated text requires a version 22 receipt"):
        RetrievalReceipt.model_validate(data)


def test_a_receipt_cannot_fold_another_format():
    data = json.loads(_receipt().model_dump_json())
    data["packing"]["context_format"] = "auditable"
    with pytest.raises(ValidationError, match="fold_repeated_text applies only to context_format='reader'"):
        RetrievalReceipt.model_validate(data)


def test_a_copied_config_that_folds_another_format_is_not_recorded():
    # model_copy skips the validator that refuses other formats, and packing then ignores the setting.
    auditable, ranked = PackingConfig(), _ranked()
    copied = _receipt(RRF, auditable.model_copy(update={FIELD: True}), ranked)
    assert copied.schema_version == 16 and FIELD not in json.loads(copied.model_dump_json())["packing"]
    assert copied.model_dump_json() == _receipt(RRF, auditable, ranked).model_dump_json()


def test_saved_version_21_receipts_keep_their_bytes():
    # The version 21 receipt rules were rewritten to add version 22.
    raw = (FIXTURES / "receipt-v21-rrf.json").read_text()
    assert hashlib.sha256(raw.encode()).hexdigest() == V21_CHECKSUM
    restored = RetrievalReceipt.model_validate_json(raw)
    assert restored.schema_version == 21 and restored.packing.session_context_packing == "adjacent"
    assert restored.packing.fold_repeated_text is False
    assert restored.model_dump_json() == raw and restored.checksum == V21_CHECKSUM
    assert restored.replay_ranking() == tuple(c.node_id for c in restored.candidates)


# --- Through ingest() and retrieve() ---------------------------------------------------------------

async def _extract(content, *, role="user", **_):
    facts = [{"subject": "I", "predicate": predicate, "object": obj, "polarity": "positive", "evidence_quote": SENTENCE}
             for predicate, obj in (("works_at", "Northwind"), ("job_title", "data analyst"), ("has_manager", "Priya"))]
    return ExtractionResult.model_validate({"entities": [{"name": "Northwind", "entity_type": "organization"},
                                                         {"name": "Priya", "entity_type": "person"}],
                                            "facts": facts})


async def _retrieve(base, user, *, fold: bool) -> tuple[str, RetrievalReceipt]:
    settings = base.model_copy(update={
        "enable_claim_sentence_text": True,
        "packing": base.packing.model_copy(update={"context_format": "reader", FIELD: fold}),
    })
    async with MemoryEngine.open(settings) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=_extract)
        await engine.ingest(TURN, user_id=user, session_id="s1", role="user", speaker="Dana", event_time=EVENT,
                            wait_for_extraction=True)
        response = await engine.retrieve("Where does Dana work, and who is the manager?", user_id=user)
        request_id = str(response.metadata.request_id)
        receipt = await engine.get_retrieval_receipt(request_id, user_id=user)
        async with client_for(app_for(settings, engine, user)) as client:
            served = await client.get(f"/v1/retrievals/{request_id}")
    assert served.status_code == 200, served.text
    assert served.json() == json.loads(receipt.model_dump_json())
    context = response.bundle.render()
    packed = {candidate.node.id for values in response.bundle.sections.values() for candidate in values}
    # The receipt describes exactly the folded context.
    assert receipt.context_sha256 == hashlib.sha256(context.encode()).hexdigest()
    assert {candidate.node_id for candidate in receipt.candidates if candidate.in_context} == packed
    return context, receipt


async def test_ingest_and_retrieve_show_the_sentence_once(config, user):
    context, receipt = await _retrieve(config, user, fold=False)
    assert context.count(SENTENCE) == 4
    assert receipt.schema_version == 19 and FIELD not in json.loads(receipt.model_dump_json())["packing"]
    context, receipt = await _retrieve(config, user + "-folded", fold=True)
    assert context.count(SENTENCE) == 1
    assert receipt.schema_version == 22 and receipt.packing.fold_repeated_text is True


async def test_a_weighted_retrieval_records_folding_in_version_22(config, user):
    context, receipt = await _retrieve(previous_defaults(config), user, fold=True)
    assert context.count(SENTENCE) == 1
    assert receipt.schema_version == 22 and receipt.scoring.fusion == "weighted"
    _, unfolded = await _retrieve(previous_defaults(config), user + "-unfolded", fold=False)
    assert unfolded.schema_version == 14


async def test_store_records_have_nothing_to_fold(config, user):
    # store() keeps one record per message and extracts nothing, so no two records share a source.
    contexts = []
    for fold in (False, True):
        settings = config.model_copy(update={"packing": config.packing.model_copy(update={FIELD: fold})})
        async with MemoryEngine.open(settings) as engine:
            if not fold:
                for minute, text in enumerate((TURN, SENTENCE, OTHER)):
                    await engine.store(text, user_id=user, session_id="s1", speaker="Dana",
                                       event_time=EVENT + timedelta(minutes=minute))
            response = await engine.retrieve("Who is Dana's manager?", user_id=user, reference_time=NOW)
        contexts.append(response.bundle.render())
    # Both turns show the sentence, but they are different messages, so both stay.
    assert contexts[0] == contexts[1] and contexts[0].count(SENTENCE) == 2

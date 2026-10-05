"""Each source text is packed once (packing.fold_repeated_text)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from prme.models import MemoryNode
from prme.models.speaker import SPEAKER_METADATA_KEY
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.tokenization import count_tokens
from prme.types import EpistemicType, LifecycleState, NodeType

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


# --- Through ingest() and retrieve() ---------------------------------------------------------------

from unittest.mock import AsyncMock  # noqa: E402
import hashlib  # noqa: E402

from prme import MemoryEngine  # noqa: E402
from prme.ingestion.schema import ExtractionResult  # noqa: E402
from tests import test_durable_ingestion  # noqa: E402

config = test_durable_ingestion.config
user = test_durable_ingestion.user


async def _extract(content, *, role="user", **_):
    facts = [{"subject": "I", "predicate": predicate, "object": obj, "polarity": "positive", "evidence_quote": SENTENCE}
             for predicate, obj in (("works_at", "Northwind"), ("job_title", "data analyst"), ("has_manager", "Priya"))]
    return ExtractionResult.model_validate({"entities": [{"name": "Northwind", "entity_type": "organization"},
                                                         {"name": "Priya", "entity_type": "person"}],
                                            "facts": facts})


async def _context(base, user, *, fold: bool):
    settings = base.model_copy(update={
        "enable_claim_sentence_text": True,
        "packing": base.packing.model_copy(update={"context_format": "reader", "fold_repeated_text": fold}),
    })
    async with MemoryEngine.open(settings) as engine:
        engine._pipeline._extraction_provider.extract = AsyncMock(side_effect=_extract)
        await engine.ingest(TURN, user_id=user, session_id="s1", role="user", speaker="Dana", event_time=EVENT,
                            wait_for_extraction=True)
        response = await engine.retrieve("Where does Dana work, and who is the manager?", user_id=user)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=user)
    context = response.bundle.render()
    packed = {candidate.node.id for values in response.bundle.sections.values() for candidate in values}
    # The receipt describes exactly the folded context.
    assert receipt.context_sha256 == hashlib.sha256(context.encode()).hexdigest()
    assert {candidate.node_id for candidate in receipt.candidates if candidate.in_context} == packed
    return context


async def test_ingest_and_retrieve_show_the_sentence_once(config, user):
    assert (await _context(config, user, fold=False)).count(SENTENCE) == 4
    assert (await _context(config, user + "-folded", fold=True)).count(SENTENCE) == 1

"""packing.stable_tie_break orders equal records by what they hold, not by random node IDs."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from prme import PRMEConfig
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate, tie_key
from prme.retrieval.packing import pack_context
from prme.types import NodeType

BASE = datetime(2026, 3, 19, 18, 30, tzinfo=timezone.utc)


def candidate(number, text, minutes, score=0.5, **kwargs):
    return RetrievalCandidate(
        node=MemoryNode(
            id=UUID(int=number),
            user_id="u",
            content=text,
            node_type=kwargs.pop("node_type", NodeType.NOTE),
            created_at=BASE + timedelta(minutes=minutes),
            event_time=BASE + timedelta(minutes=minutes),
            valid_from=BASE,
            **kwargs,
        ),
        composite_score=score,
        path_count=1,
        paths=["VECTOR"],
    )


def records(bundle):
    return [item.node.content for group in bundle.sections.values() for item in group]


def test_off_orders_equal_scores_by_node_id():
    older = candidate(1, "A girl named Ivy.", 0)
    newer = candidate(2, "Congratulations, Aunt Dana!", 2)
    config = PackingConfig(token_budget=10000)
    # The record with the lower node ID comes first, whatever it holds.
    assert records(pack_context([newer, older], config)) == ["A girl named Ivy.", "Congratulations, Aunt Dana!"]
    swapped = [candidate(2, "A girl named Ivy.", 0), candidate(1, "Congratulations, Aunt Dana!", 2)]
    assert records(pack_context(swapped, config)) == ["Congratulations, Aunt Dana!", "A girl named Ivy."]


def test_on_gives_the_same_order_whatever_ids_a_build_drew():
    config = PackingConfig(token_budget=10000, stable_tie_break=True)
    first = [candidate(1, "A girl named Ivy.", 0), candidate(2, "Congratulations, Aunt Dana!", 2)]
    second = [candidate(2, "A girl named Ivy.", 0), candidate(1, "Congratulations, Aunt Dana!", 2)]
    assert records(pack_context(first, config)) == ["Congratulations, Aunt Dana!", "A girl named Ivy."]
    # The reader format shows no node IDs, so the whole context is the same.
    reader = config.model_copy(update={"context_format": "reader"})
    assert pack_context(first, reader).render() == pack_context(second, reader).render()


def test_on_breaks_a_same_time_tie_by_text_then_stored_time():
    config = PackingConfig(token_budget=10000, stable_tie_break=True)
    b = candidate(1, "Sam is a nurse.", 0)
    a = candidate(2, "sam IS a   nurse at Denver.", 0)
    # Same stated time: the text decides, compared without case or spacing differences.
    assert tie_key(a.node, True)[1] == "sam is a nurse at denver."
    assert records(pack_context([b, a], config)) == ["sam IS a   nurse at Denver.", "Sam is a nurse."]
    # Same time and text: the record stored first comes first, before any node ID.
    stored_later = candidate(0, "Sam is a nurse.", 0).node.model_copy(
        update={"created_at": BASE + timedelta(seconds=1)})
    assert tie_key(b.node, True) < tie_key(stored_later, True)


def test_on_never_moves_a_record_of_higher_priority():
    config = PackingConfig(token_budget=10000, stable_tie_break=True)
    high = candidate(9, "Older but scored higher.", 0, score=0.9)
    low = candidate(1, "Newer and scored lower.", 60, score=0.4)
    assert records(pack_context([low, high], config)) == ["Older but scored higher.", "Newer and scored lower."]


def test_a_naive_time_is_read_as_utc():
    aware = candidate(1, "x", 0).node
    naive = aware.model_copy(update={"event_time": aware.event_time.replace(tzinfo=None),
                                     "created_at": aware.created_at.replace(tzinfo=None)})
    assert tie_key(aware, True) == tie_key(naive, True)


def test_off_by_default_and_omitted_from_dumps():
    assert PRMEConfig().packing.stable_tie_break is False
    assert PackingConfig().stable_tie_break is False
    assert "stable_tie_break" not in PackingConfig().model_dump()
    assert PackingConfig(stable_tie_break=True).model_dump()["stable_tie_break"] is True
    assert tie_key(candidate(5, "x", 0).node, False) == (str(UUID(int=5)),)

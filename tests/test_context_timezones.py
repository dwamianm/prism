"""Equivalent instants produce identical packed evidence across host timezones."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from prme import MemoryEngine
from prme.models import MemoryNode
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context
from prme.retrieval.context_formatter import format_for_llm, format_days_ago
from tests.test_fresh_pack_paths import fresh_config  # noqa: F401


INSTANT = datetime(2024, 3, 10, 0, 30, tzinfo=timezone.utc)


def candidates():
    original = MemoryNode(
        user_id="alice", node_type="note", content="The migration completed.",
        event_time=INSTANT, created_at=INSTANT, updated_at=INSTANT,
        valid_from=INSTANT, valid_to=INSTANT + timedelta(days=1),
    )
    shifted = original.model_copy(update={
        name: getattr(original, name).astimezone(ZoneInfo("America/Chicago"))
        for name in ("event_time", "created_at", "updated_at", "valid_from", "valid_to")
    })
    return [RetrievalCandidate(node=n, composite_score=.9) for n in (original, shifted)]


def test_product_context_and_budget_ignore_offset_representation():
    left, right = candidates()
    config = PackingConfig(token_budget=1024)
    expected = pack_context([left], config)
    actual = pack_context([right], config)
    assert expected.render() == actual.render()
    assert expected.tokens_used == actual.tokens_used
    assert right.node.event_time.utcoffset() != timedelta(0)  # Inputs untouched.


@pytest.mark.parametrize("hint", ["default", "temporal", "knowledge_update", "aggregation"])
def test_reader_context_uses_same_day_for_equivalent_instants(hint):
    left, right = candidates()
    question = INSTANT + timedelta(days=1)
    original = format_for_llm([left], "When did the migration complete?", include_profile=False,
                              context_hint=hint, question_date=question)
    shifted = format_for_llm([right], "When did the migration complete?", include_profile=False,
                             context_hint=hint, question_date=question.astimezone(ZoneInfo("America/Chicago")))
    assert original == shifted
    assert "2024-03-10" in original
    assert format_days_ago(right.node.event_time, question) == "yesterday"


async def test_local_pack_reopen_uses_utc_despite_host_connection_default(fresh_config, monkeypatch):  # noqa: F811
    import prme.storage.engine as module
    native_connect = module.duckdb.connect
    host_zone = "America/Chicago"

    def connect(*args, **kwargs):
        conn = native_connect(*args, **kwargs)
        conn.execute("SET TimeZone = ?", [host_zone])
        return conn

    monkeypatch.setattr(module.duckdb, "connect", connect)
    async with MemoryEngine.open(fresh_config) as engine:
        event_id = await engine.store("Same source across machines", user_id="alice", event_time=INSTANT)
        event = await engine.get_event(event_id, user_id="alice")
        assert event.event_time.isoformat() == INSTANT.isoformat()
    host_zone = "Asia/Tokyo"
    async with MemoryEngine.open(fresh_config) as engine:
        event = await engine.get_event(event_id, user_id="alice")
        assert event.event_time.isoformat() == INSTANT.isoformat()

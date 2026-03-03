"""Tests for PgEventStore against a real PostgreSQL instance.

Requires PRME_TEST_DATABASE_URL environment variable.
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("PRME_TEST_DATABASE_URL"),
        reason="PRME_TEST_DATABASE_URL not set",
    ),
]


@pytest.fixture
async def pg_pool():
    import asyncpg

    url = os.environ["PRME_TEST_DATABASE_URL"]
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    yield pool
    await pool.close()


@pytest.fixture
async def pg_event_store(pg_pool):
    from prme.storage.pg.schema import initialize_pg_database
    from prme.storage.pg.event_store import PgEventStore

    await initialize_pg_database(pg_pool)
    return PgEventStore(pg_pool)


async def test_append_and_get(pg_event_store):
    from prme.models import Event

    event = Event(content="hello world", user_id="test-user", role="user")
    eid = await pg_event_store.append(event)
    assert eid == str(event.id)

    retrieved = await pg_event_store.get(eid)
    assert retrieved is not None
    assert retrieved.content == "hello world"
    assert retrieved.user_id == "test-user"


async def test_get_by_user(pg_event_store):
    from prme.models import Event

    uid = f"user-{uuid.uuid4().hex[:8]}"
    e1 = Event(content="first", user_id=uid, role="user")
    e2 = Event(content="second", user_id=uid, role="assistant")
    await pg_event_store.append(e1)
    await pg_event_store.append(e2)

    events = await pg_event_store.get_by_user(uid)
    assert len(events) >= 2
    contents = {e.content for e in events}
    assert "first" in contents
    assert "second" in contents


async def test_get_by_hash(pg_event_store):
    from prme.models import Event

    uid = f"user-{uuid.uuid4().hex[:8]}"
    event = Event(content="unique content for hash test", user_id=uid, role="user")
    await pg_event_store.append(event)

    results = await pg_event_store.get_by_hash(event.content_hash, uid)
    assert len(results) >= 1
    assert results[0].content == "unique content for hash test"


async def test_get_nonexistent(pg_event_store):
    result = await pg_event_store.get(str(uuid.uuid4()))
    assert result is None

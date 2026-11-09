"""Four engines opening one fresh PostgreSQL database at once (dwamianm/prism#411).

Each engine calls ``initialize_pg_database``. Without the schema-scoped advisory
lock, their catalog writes collide and a second engine fails on a unique index
(in practice ``pg_extension_name_index`` for the first ``CREATE EXTENSION``,
or ``pg_type_typname_nsp_index`` for a table). A barrier makes all four
initializers start together, so the test exercises the race on every run
instead of depending on scheduling.

Skipped unless ``PRME_TEST_DATABASE_URL`` names a live server whose role can
create databases, which CI provides.
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

asyncpg = pytest.importorskip("asyncpg")

from prme import MemoryEngine  # noqa: E402
from prme.config import PRMEConfig  # noqa: E402
from prme.storage.pg.vector_sql import quote_identifier  # noqa: E402
from tests.multi_model_support import small  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("PRME_TEST_DATABASE_URL"), reason="PRME_TEST_DATABASE_URL not set"
)


async def _create_fresh_database(base_url: str) -> tuple[str, str]:
    """Create an empty database beside the test one and return its name and URL."""
    name = f"prme_race_{uuid4().hex[:16]}"
    admin = await asyncpg.connect(base_url)
    try:
        await admin.execute(f"CREATE DATABASE {quote_identifier(name)}")
    finally:
        await admin.close()
    return name, urlunsplit(urlsplit(base_url)._replace(path=f"/{name}"))


async def _drop_database(base_url: str, name: str) -> None:
    admin = await asyncpg.connect(base_url)
    try:
        await admin.execute(f"DROP DATABASE IF EXISTS {quote_identifier(name)} WITH (FORCE)")
    finally:
        await admin.close()


def _config(url: str, tmp_path) -> PRMEConfig:
    return PRMEConfig(
        database_url=url,
        db_path=str(tmp_path / "memory.duckdb"),
        lexical_path=str(tmp_path / "lexical"),
        vector_path=str(tmp_path / "vectors.usearch"),
        organizer={"opportunistic_enabled": False},
        materialization_budget_ms=5000,
    )


async def test_four_engines_open_one_fresh_database(tmp_path, monkeypatch):
    import prme.storage.pg as pg

    original = pg.initialize_pg_database
    barrier = asyncio.Barrier(4)

    async def synchronized(pool, **kwargs):
        # All four engine initializers reach the schema work together.
        await asyncio.wait_for(barrier.wait(), timeout=60)
        return await original(pool, **kwargs)

    monkeypatch.setattr(pg, "initialize_pg_database", synchronized)

    base = os.environ["PRME_TEST_DATABASE_URL"]
    name, url = await _create_fresh_database(base)
    created: list[MemoryEngine] = []

    async def open_one():
        engine = await MemoryEngine.create(_config(url, tmp_path), embedding_provider=small())
        created.append(engine)
        return engine

    tasks = [asyncio.create_task(open_one()) for _ in range(4)]
    try:
        engines = await asyncio.gather(*tasks)
        # Every engine reads the same freshly created schema.
        for engine in engines:
            assert await engine.count_nodes(user_id="race-user") == 0
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            for engine in created:
                await engine.close()
        finally:
            await _drop_database(base, name)

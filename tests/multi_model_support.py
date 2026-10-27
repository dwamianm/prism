"""Fixtures for the multi-model embedding tests (dwamianm/prism#368): deterministic providers and both backends.

The providers are bag-of-words hashers, so two of them rank the same memories differently and a test can tell
which model a search used, with no model download. ``tests/test_multi_model_models.py`` runs the real registered
models.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from uuid import uuid4

import pytest

from prme.config import PRMEConfig


class BagProvider:
    """Hashes each word to a signed bucket, so memories that share words are close under every model."""

    model_version = "bag-1"

    def __init__(self, name: str, dimension: int, seed: int = 0, version: str | None = None) -> None:
        self.model_name = name
        self.dimension = dimension
        self.seed = seed
        if version is not None:
            self.model_version = version
        self.embedded: list[str] = []
        self.fail_on: set[str] = set()
        self.gate = None  # an asyncio.Event a test sets to hold embed() open
        self.on_embed = None

    def vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(f"{self.seed}:{word}".encode()).digest()
            values[int.from_bytes(digest[:4], "big") % self.dimension] += 1.0 if digest[4] % 2 else -1.0
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:
            values[0], norm = 1.0, 1.0
        return [v / norm for v in values]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.on_embed is not None:
            await self.on_embed(texts)
        if self.gate is not None:
            await self.gate.wait()
        for text in texts:
            if text in self.fail_on:
                raise RuntimeError("this text cannot be embedded")
        self.embedded.extend(texts)
        return [self.vector(text) for text in texts]


def small() -> BagProvider:
    return BagProvider("test/small", 16, seed=1)


def large() -> BagProvider:
    return BagProvider("test/large", 32, seed=2)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


TOPICS = ["jazz", "hiking", "pasta", "chess", "garden", "piano", "sailing", "coffee", "museum", "cycling"]


def memory_text(number: int) -> str:
    return f"Note {number}: enjoys {TOPICS[number % 10]} and {TOPICS[(number * 3 + 1) % 10]} on weekend {number}"


class Store:
    """One store, reopened with other providers or options; each backend keeps its own files or schema."""

    def __init__(self, backend: str, tmp_path, url: str | None) -> None:
        self.backend = backend
        self.tmp_path = tmp_path
        self.url = url

    def config(self, **overrides) -> PRMEConfig:
        (self.tmp_path / "lexical").mkdir(exist_ok=True)
        values = dict(
            db_path=str(self.tmp_path / "memory.duckdb"),
            lexical_path=str(self.tmp_path / "lexical"),
            vector_path=str(self.tmp_path / "vectors.usearch"),
            organizer={"opportunistic_enabled": False},
            materialization_budget_ms=5000,
        )
        if self.backend == "postgres":
            values["database_url"] = self.url
        values.update(overrides)
        return PRMEConfig(**values)

    def multi(self, **overrides) -> PRMEConfig:
        return self.config(enable_multi_model_embeddings=True, **overrides)


@pytest.fixture(params=["duckdb", "postgres"])
async def store(request, tmp_path):
    if request.param == "duckdb":
        yield Store("duckdb", tmp_path, None)
        return
    base = os.environ.get("PRME_TEST_DATABASE_URL")
    if not base:
        pytest.skip("PRME_TEST_DATABASE_URL not set")
    import asyncpg
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    schema = "mme_" + uuid4().hex
    admin = await asyncpg.connect(base)
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        await admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        parts = urlsplit(base)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "search_path"] + [("search_path", schema)]
        yield Store("postgres", tmp_path, urlunsplit(parts._replace(query=urlencode(query))))
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


async def rows_under(store: Store, model: str) -> int:
    """How many vectors of one model the store holds, read from storage rather than through the engine."""
    if store.backend == "postgres":
        import asyncpg

        conn = await asyncpg.connect(store.url)
        try:
            return await conn.fetchval("SELECT count(*) FROM node_embeddings WHERE model_name = $1", model)
        finally:
            await conn.close()
    import duckdb

    conn = duckdb.connect(str(store.tmp_path / "memory.duckdb"), read_only=True)
    try:
        return conn.execute("SELECT count(*) FROM vector_metadata WHERE embedding_model = ?", [model]).fetchone()[0]
    finally:
        conn.close()

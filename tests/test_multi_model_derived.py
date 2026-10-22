"""Memories that ingestion derives are written under the additional models too (dwamianm/prism#368)."""

from __future__ import annotations

import pytest

from prme import MemoryEngine
from tests.multi_model_support import large, small, store as store  # noqa: F401
from tests.test_reingest import FOUND, Scripted, install, store_all

USER = "derived-user"


def derived(store, **changes):
    return store.multi(extraction_batch_target_tokens=512, enable_reingest=True, **changes)


async def counts(engine) -> dict[str, tuple[int, bool | None]]:
    status = await engine.embedding_status()
    return {item["model"]: (item["vectors"], item["complete"]) for item in status["models"]}


async def test_ingest_writes_every_model(store):
    first, second = small(), large()
    async with MemoryEngine.open(derived(store), embedding_provider=first,
                                 additional_embedding_providers=[second]) as engine:
        install(engine, Scripted(FOUND))
        for text in FOUND:
            await engine.ingest(text, user_id=USER, session_id=None)
        await engine.process_extractions(user_id=USER)
        total = await engine.count_nodes(user_id=USER)
        assert total > len(FOUND)  # the notes, and the entities and claims extracted from them
        by_model = await counts(engine)
        assert by_model["test/small"] == (total, True)
        assert by_model["test/large"] == (total, True)


async def test_reingest_writes_every_model_and_retires_from_all(store):
    first, second = small(), large()
    async with MemoryEngine.open(derived(store), embedding_provider=first,
                                 additional_embedding_providers=[second]) as engine:
        await store_all(engine, USER)
        assert await counts(engine) == {"test/small": (4, True), "test/large": (4, True)}
        install(engine, Scripted(FOUND))
        result = await engine.reingest(USER)
        assert result.remaining == 0 and result.processed == 4
        total = await engine.count_nodes(user_id=USER)
        assert total > 4
        by_model = await counts(engine)
        assert by_model["test/small"] == (total, True)
        assert by_model["test/large"] == (total, True)


async def test_consolidation_and_profiles_write_every_model(store):
    first, second = small(), large()
    async with MemoryEngine.open(derived(store), embedding_provider=first,
                                 additional_embedding_providers=[second]) as engine:
        await store_all(engine, USER)
        install(engine, Scripted(FOUND))
        await engine.reingest(USER)
        await engine.consolidate_knowledge(user_id=USER)
        by_model = await counts(engine)
        total = await engine.count_nodes(user_id=USER)
        assert by_model["test/small"] == (total, True)
        assert by_model["test/large"] == (total, True)

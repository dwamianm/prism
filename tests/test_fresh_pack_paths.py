"""Public clients initialize a fresh configured local pack without mkdir calls."""

from pathlib import Path

import pytest

from prme import MemoryClient, MemoryEngine, PRMEConfig
from tests.test_durable_ingestion import MockEmbeddingProvider


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    monkeypatch.setattr("prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider())
    return PRMEConfig(
        database_url=None, encryption_enabled=False,
        db_path=str(tmp_path / "new" / "database" / "memory.duckdb"),
        vector_path=str(tmp_path / "new" / "search" / "vectors.usearch"),
        lexical_path=str(tmp_path / "new" / "search" / "lexical"),
        organizer={"opportunistic_enabled": False},
    )


async def test_fresh_async_pack_stores_searches_and_reopens(fresh_config):
    assert not Path(fresh_config.db_path).parent.exists()
    async with MemoryEngine.open(fresh_config) as engine:
        event_id = await engine.store("The launch code is marigold", user_id="alice")
        assert (await engine.processing_status(event_id, user_id="alice")).status == "complete"
        assert (await engine.retrieve("marigold", user_id="alice")).results
    async with MemoryEngine.open(fresh_config) as engine:
        assert (await engine.get_event(event_id, user_id="alice")).content == "The launch code is marigold"
        assert await engine._lexical_index.search("marigold", "alice")
        assert await engine._vector_index.search("marigold", "alice", k=1)


def test_sync_explicit_config_initializes_directories(fresh_config):
    with MemoryClient(config=fresh_config) as client:
        event_id = client.store("Remember the lavender launch", user_id="alice")
        assert client.retrieve("lavender", user_id="alice").results
    with MemoryClient(config=fresh_config) as client:
        assert client.get_event(event_id, user_id="alice").content == "Remember the lavender launch"


async def test_directory_collision_preserves_existing_file(fresh_config):
    path = Path(fresh_config.lexical_path)
    path.parent.mkdir(parents=True)
    path.write_text("existing unrelated file")
    with pytest.raises((FileExistsError, NotADirectoryError)):
        await MemoryEngine.create(fresh_config)
    assert path.read_text() == "existing unrelated file"

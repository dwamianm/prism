"""Explicit per-pack worker limits survive normal public workflows."""

import duckdb
import pytest
from pydantic import ValidationError

from prme import MemoryEngine, config_from_directory
from prme.config import PRMEConfig
from tests.test_custom_embedding_provider import Documents


def configuration(path, threads):
    config = config_from_directory(str(path))
    config.database_url = None
    config.duckdb_threads = threads
    config.organizer.opportunistic_enabled = False
    return config


async def test_separate_packs_keep_independent_native_limits_and_data(tmp_path):
    provider = Documents()
    a = configuration(tmp_path / "a", 1)
    b = configuration(tmp_path / "b", 2)
    async with MemoryEngine.open(a, embedding_provider=provider) as first:
        async with MemoryEngine.open(b, embedding_provider=provider) as second:
            event = await first.store("Aurora retains data for seven days", user_id="owner")
            await second.store("Aurora retains data for thirty days", user_id="owner")
            assert first._conn.execute("SELECT current_setting('threads')").fetchone()[0] == 1
            assert second._conn.execute("SELECT current_setting('threads')").fetchone()[0] == 2
            assert await second.get_event(event, user_id="owner") is None
            response = await first.retrieve("Aurora", user_id="owner")
            assert [item.node.content for item in response.results] == ["Aurora retains data for seven days"]
    async with MemoryEngine.open(a, embedding_provider=provider) as first:
        assert (await first.get_event(event, user_id="owner")).content.endswith("seven days")
        assert first._conn.execute("SELECT current_setting('threads')").fetchone()[0] == 1


@pytest.mark.parametrize("other_threads", [None, 2])
async def test_conflicting_open_cannot_reconfigure_active_pack(tmp_path, other_threads):
    config = configuration(tmp_path, 1)
    provider = Documents()
    async with MemoryEngine.open(config, embedding_provider=provider) as first:
        event = await first.store("Original source", user_id="owner")
        other = config.model_copy(update={"duckdb_threads": other_threads})
        with pytest.raises(duckdb.ConnectionException, match="different configuration"):
            await MemoryEngine.create(other, embedding_provider=provider)
        assert first._conn.execute("SELECT current_setting('threads')").fetchone()[0] == 1
        assert (await first.get_event(event, user_id="owner")).content == "Original source"
    async with MemoryEngine.open(other, embedding_provider=provider) as reopened:
        assert (await reopened.get_event(event, user_id="owner")).content == "Original source"


async def test_matching_engine_settings_can_share_an_open_pack(tmp_path):
    config = configuration(tmp_path, 1)
    provider = Documents()
    async with MemoryEngine.open(config, embedding_provider=provider) as first:
        async with MemoryEngine.open(config, embedding_provider=provider) as second:
            event = await first.store("Shared durable source", user_id="owner")
            assert (await second.get_event(event, user_id="owner")).content == "Shared durable source"


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "oops"])
def test_invalid_worker_count_is_rejected_before_open(value):
    with pytest.raises(ValidationError):
        PRMEConfig(duckdb_threads=value)


def test_environment_can_set_worker_count(monkeypatch):
    monkeypatch.setenv("PRME_DUCKDB_THREADS", "2")
    assert PRMEConfig().duckdb_threads == 2
    assert PRMEConfig(duckdb_threads=1).duckdb_threads == 1
    assert PRMEConfig(duckdb_threads=None).duckdb_threads is None

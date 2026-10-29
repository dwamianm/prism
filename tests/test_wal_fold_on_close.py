"""A store reopened after a crash folds its write-ahead log on close (#377).

DuckDB 1.4 checkpoints a database that replayed its WAL when its last
connection closes; DuckDB 2.0 leaves the replayed log beside the file. The
engine's last holder of a file checkpoints it before closing, so both versions
end with the file alone. An engine that still shares the database with another
one does not checkpoint, which would wait for the other's transaction.
"""

import os
import shutil

import pytest

from prme import MemoryEngine, PRMEConfig
from prme.storage import engine as engine_module

MEMORIES = ("Alex likes tea.", "Jordan plans a trip to Japan.", "The cutover is on Saturday.")
USER = "wal-fold-user"


def layout_config(path, layout):
    if layout == "pack":
        return PRMEConfig(pack_path=str(path / "memories.prme"))
    return PRMEConfig(db_path=str(path / "memory.duckdb"), vector_path=str(path / "vectors.usearch"),
                      lexical_path=str(path / "lexical_index"))


def database_files(path):
    return sorted(name for name in os.listdir(path) if name.startswith(("memories.prme", "memory.duckdb")))


@pytest.mark.parametrize("layout", ["pack", "directory"])
async def test_a_replayed_log_is_folded_into_the_file_on_close(tmp_path, layout):
    live, crashed = tmp_path / "live", tmp_path / "crashed"
    live.mkdir()
    crashed.mkdir()
    async with MemoryEngine.open(layout_config(live, layout)) as engine:
        for memory in MEMORIES:
            await engine.store(memory, user_id=USER)
        # Copy the files and the log as a crash would leave them.
        for name in os.listdir(live):
            source = live / name
            (shutil.copytree if source.is_dir() else shutil.copyfile)(source, crashed / name)
    assert any(name.endswith(".wal") for name in database_files(crashed))
    async with MemoryEngine.open(layout_config(crashed, layout)) as engine:
        assert engine._conn.execute("SELECT count(*) FROM nodes").fetchone() == (len(MEMORIES),)
    assert not any(name.endswith(".wal") for name in database_files(crashed))
    async with MemoryEngine.open(layout_config(crashed, layout)) as engine:
        assert engine._conn.execute("SELECT count(*) FROM nodes").fetchone() == (len(MEMORIES),)


async def test_only_the_last_holder_of_a_shared_pack_checkpoints(tmp_path, monkeypatch):
    checkpoints = []
    release = engine_module._release_database

    def counted(key, conn):
        remaining = engine_module._DATABASE_HOLDERS.get(key, 1) - 1
        checkpoints.append(remaining)
        release(key, conn)

    monkeypatch.setattr(engine_module, "_release_database", counted)
    pack = tmp_path / "memories.prme"
    async with MemoryEngine.open(PRMEConfig(pack_path=str(pack))) as first:
        async with MemoryEngine.open(PRMEConfig(pack_path=str(pack))) as second:
            await first.store(MEMORIES[0], user_id=USER)
            assert second._conn.execute("SELECT count(*) FROM nodes").fetchone() == (1,)
        assert checkpoints == [1]
    assert checkpoints == [1, 0]
    assert engine_module._DATABASE_HOLDERS.get(os.path.realpath(pack)) is None
    assert database_files(tmp_path) == ["memories.prme"]

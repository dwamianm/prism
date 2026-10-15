"""Single-file packs in the prme CLI, pack maintenance and prme-mcp (issue #247).

Every CLI command that opens a pack accepts a .prme file. prme init creates
one, prme doctor checks one, prme compact rewrites a pack into a fresh file
without its unused space, and prme migrate copies a directory pack into a new
single-file pack. prme-mcp --db-path selects a single-file pack by its suffix.
Every pack here is plain; encrypted packs need DuckDB's native encryption.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from prme import MemoryClient, MemoryEngine, PRMEConfig, __version__
from prme.cli import (
    build_parser,
    cmd_compact,
    cmd_doctor,
    cmd_export,
    cmd_info,
    cmd_init,
    cmd_migrate,
    cmd_rebuild,
    cmd_search,
    cmd_stats,
    main,
)
from prme.storage.pack_tools import (
    PackInUseError,
    compact_pack,
    migrate_directory_pack,
    open_pack_read_only,
)
from tests.test_durable_ingestion import MockEmbeddingProvider

USER = "alice"
REFERENCE_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)
MEMORIES = (
    "Alice drinks green tea every morning",
    "Alice's brother Bob drives a red pickup truck",
    "The team standup moved to 9:30 on Tuesdays",
    "Alice is allergic to peanuts and carries an epinephrine pen",
    "Carol recommended the violin teacher on Elm Street",
    "Alice prefers tea over coffee in the afternoon",
)
QUERIES = ("tea", "who drives a truck", "peanut allergy", "violin teacher")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(
        "prme.storage.engine.create_embedding_provider", lambda _: MockEmbeddingProvider(),
    )
    # Opportunistic maintenance could change scores between two reads.
    monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_ENABLED", "false")
    # The commands read storage and encryption settings from the environment.
    for name in ("PRME_DATABASE_URL", "PRME_PACK_PATH", "PRME_ENCRYPTION_ENABLED", "PRME_ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def quiet_structlog():
    """Keep library diagnostics off stdout, so JSON output parses.

    main() configures structlog for the whole process, so the configuration
    from before the test is restored afterwards.
    """
    import structlog

    saved = structlog.get_config()
    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )
    yield
    structlog.configure(**saved)


def args(**values) -> argparse.Namespace:
    return argparse.Namespace(**values)


def directory_config(directory: Path) -> PRMEConfig:
    return PRMEConfig(
        db_path=str(directory / "memory.duckdb"),
        vector_path=str(directory / "vectors.usearch"),
        lexical_path=str(directory / "lexical_index"),
    )


def pack_config(path: Path) -> PRMEConfig:
    return PRMEConfig(pack_path=str(path))


async def store_all(engine: MemoryEngine, memories=MEMORIES) -> None:
    for text in memories:
        await engine.store(text, user_id=USER)


async def read_back(engine: MemoryEngine) -> dict:
    """Results and receipts for the query suite, without per-request IDs."""
    out = {}
    for query in QUERIES:
        response = await engine.retrieve(query, user_id=USER, reference_time=REFERENCE_TIME)
        receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
        out[query] = (
            [(str(item.node.id), item.composite_score) for item in response.results],
            receipt.model_dump(mode="json", exclude={"request_id"}),
        )
    return out


async def make_pack(path: Path, memories=MEMORIES) -> dict:
    async with MemoryEngine.open(pack_config(path)) as engine:
        await store_all(engine, memories)
        return await read_back(engine)


def table_counts(path: Path) -> dict:
    with open_pack_read_only(path) as conn:
        tables = [row[0] for row in conn.execute("SHOW TABLES").fetchall()]
        return {table: conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}


def schema_objects(path: Path) -> dict:
    with open_pack_read_only(path) as conn:
        return {
            "indexes": sorted(row[0] for row in conn.execute(
                "SELECT index_name FROM duckdb_indexes() WHERE database_name = current_database()").fetchall()),
            "constraints": sorted(conn.execute(
                "SELECT table_name, constraint_type, constraint_text FROM duckdb_constraints() "
                "WHERE database_name = current_database()").fetchall()),
            "sequences": sorted(row[0] for row in conn.execute(
                "SELECT sequence_name FROM duckdb_sequences() WHERE database_name = current_database()").fetchall()),
        }


# --- Parser ---------------------------------------------------------------


def test_parser_registers_pack_commands():
    parser = build_parser()
    compact = parser.parse_args(["compact", "memories.prme", "--encryption-key", "k", "--format", "json"])
    assert (compact.func, compact.pack, compact.encryption_key, compact.format) == (
        cmd_compact, "memories.prme", "k", "json",
    )
    migrate = parser.parse_args(["migrate", "my_memories", "memories.prme"])
    assert (migrate.func, migrate.directory, migrate.pack, migrate.encryption_key, migrate.format) == (
        cmd_migrate, "my_memories", "memories.prme", None, "table",
    )
    doctor = parser.parse_args(["doctor", "memories.prme", "--encryption-key", "k"])
    assert (doctor.directory, doctor.encryption_key) == ("memories.prme", "k")


# --- init -----------------------------------------------------------------


async def test_init_creates_exactly_one_pack_file(tmp_path, capsys):
    pack = tmp_path / "new" / "memories.prme"
    await cmd_init(args(directory=str(pack)))
    out = capsys.readouterr().out
    assert f"Initialized PRME memory pack: {pack}" in out
    assert f'MemoryClient("{pack}")' in out
    assert os.listdir(pack.parent) == ["memories.prme"]
    with open_pack_read_only(pack) as conn:
        assert conn.execute("SELECT format_version FROM prme_manifest").fetchone() == (1,)
        assert conn.execute("SELECT vector_count FROM vector_snapshot").fetchone() == (0,)

    # init on an existing pack opens it as it is.
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await engine.store(MEMORIES[0], user_id=USER)
    await cmd_init(args(directory=str(pack)))
    assert table_counts(pack)["nodes"] == 1
    assert os.listdir(pack.parent) == ["memories.prme"]


async def test_init_keeps_an_existing_directory_with_the_suffix_a_directory(tmp_path, capsys):
    legacy = tmp_path / "legacy.prme"
    legacy.mkdir()
    await cmd_init(args(directory=str(legacy)))
    assert "Initialized PRME memory directory" in capsys.readouterr().out
    assert (legacy / ".env.example").exists()


async def test_init_refuses_a_file_that_is_not_a_pack(tmp_path):
    other = tmp_path / "notes.prme"
    other.write_bytes(b"not a database")
    with pytest.raises(duckdb.IOException):
        await cmd_init(args(directory=str(other)))
    assert other.read_bytes() == b"not a database"


# --- doctor ---------------------------------------------------------------


async def test_doctor_reports_a_healthy_pack_and_its_manifest(tmp_path, capsys):
    pack = tmp_path / "memories.prme"
    await make_pack(pack)
    await cmd_doctor(args(directory=str(pack), encryption_key=None))
    out = capsys.readouterr().out
    assert f"Checking memory pack: {pack}" in out
    assert "[FAIL]" not in out and " 0 failures" in out
    assert f"[OK]   Memory pack file exists ({pack.stat().st_size:,} bytes)" in out
    assert "[OK]   DuckDB database valid (" in out
    assert "[OK]   Pack manifest present" in out
    assert "Format version:   1" in out
    assert f"PRME version:     {__version__}" in out
    assert "Embedding model:  durability-test (version 1, 384 dimensions)" in out
    assert "Created:" in out and "Updated:" in out
    assert f"[OK]   Vector snapshot stored ({len(MEMORIES)} vectors," in out
    assert "Write-ahead log" not in out


async def test_doctor_warns_about_a_stale_or_missing_snapshot(tmp_path, capsys):
    pack = tmp_path / "memories.prme"
    await make_pack(pack)
    with duckdb.connect(str(pack)) as conn:
        conn.execute("UPDATE vector_snapshot SET vector_count = vector_count - 1")
    await cmd_doctor(args(directory=str(pack), encryption_key=None))
    out = capsys.readouterr().out
    assert (f"[WARN] Vector snapshot holds {len(MEMORIES) - 1} vectors but the pack has "
            f"{len(MEMORIES)} (it will be reconciled on open)") in out
    assert " 0 failures" in out

    with duckdb.connect(str(pack)) as conn:
        conn.execute("DELETE FROM vector_snapshot")
    await cmd_doctor(args(directory=str(pack), encryption_key=None))
    out = capsys.readouterr().out
    assert "[WARN] Vector snapshot missing (it will be rebuilt from vector payloads on open)" in out
    assert " 0 failures" in out

    # One open and close writes the snapshot again.
    async with MemoryEngine.open(pack_config(pack)):
        pass
    await cmd_doctor(args(directory=str(pack), encryption_key=None))
    assert f"[OK]   Vector snapshot stored ({len(MEMORIES)} vectors," in capsys.readouterr().out


async def test_doctor_fails_on_a_missing_incomplete_or_unreadable_pack(tmp_path, capsys):
    missing = tmp_path / "absent.prme"
    with pytest.raises(SystemExit):
        await cmd_doctor(args(directory=str(missing), encryption_key=None))
    out = capsys.readouterr().out
    assert f"[FAIL] Memory pack file not found: {missing}" in out
    assert f"Run 'prme init {missing}' to create it." in out
    assert not missing.exists()

    partial = tmp_path / "partial.prme"
    with duckdb.connect(str(partial)) as conn:
        conn.execute("CREATE TABLE events (id INTEGER)")
    with pytest.raises(SystemExit):
        await cmd_doctor(args(directory=str(partial), encryption_key=None))
    assert "[FAIL] DuckDB schema incomplete (missing: nodes, prme_manifest, vector_snapshot)" in (
        capsys.readouterr().out
    )

    junk = tmp_path / "junk.prme"
    junk.write_bytes(b"not a database")
    with pytest.raises(SystemExit):
        await cmd_doctor(args(directory=str(junk), encryption_key=None))
    assert "[FAIL] DuckDB cannot open:" in capsys.readouterr().out
    assert junk.read_bytes() == b"not a database"


# --- Commands that open an engine -------------------------------------------


async def test_engine_commands_open_a_pack_file(tmp_path, capsys):
    pack = tmp_path / "memories.prme"
    await make_pack(pack)

    await cmd_info(args(db_path=str(pack), format="json"))
    info = json.loads(capsys.readouterr().out)
    assert (info["db_path"], info["node_count"], info["event_count"]) == (str(pack), len(MEMORIES), len(MEMORIES))

    await cmd_stats(args(db_path=str(pack), format="json"))
    assert json.loads(capsys.readouterr().out)["totals"]["nodes"] == len(MEMORIES)

    await cmd_search(args(db_path=str(pack), format="json", query="tea", user_id=USER))
    assert json.loads(capsys.readouterr().out)["result_count"] > 0

    await cmd_export(args(db_path=str(pack)))
    exported = json.loads(capsys.readouterr().out)
    assert (exported["node_count"], exported["event_count"]) == (len(MEMORIES), len(MEMORIES))

    await cmd_rebuild(args(db_path=str(pack), format="json", batch_size=4))
    assert json.loads(capsys.readouterr().out) == {
        "nodes_indexed": len(MEMORIES), "nodes_skipped": 0, "total_active": len(MEMORIES),
    }
    # Nothing was written beside the pack.
    assert os.listdir(tmp_path) == ["memories.prme"]

    with pytest.raises(SystemExit):
        await cmd_info(args(db_path=str(tmp_path / "absent.prme"), format="json"))
    assert not (tmp_path / "absent.prme").exists()


# --- compact --------------------------------------------------------------


async def test_compact_shrinks_a_pack_and_keeps_what_it_serves(tmp_path):
    pack = tmp_path / "memories.prme"
    await make_pack(pack, MEMORIES[:3])
    # DuckDB reuses free blocks and cuts free blocks off the end of the file,
    # so only space freed before live data stays in a file for good. PRME
    # never deletes in bulk; a scratch table dropped after newer rows were
    # written behind it stands in for the history that grew a long-lived pack.
    with duckdb.connect(str(pack)) as conn:
        conn.execute("CREATE TABLE scratch AS SELECT hash(range) AS h, random() AS r FROM range(500000)")
    async with MemoryEngine.open(pack_config(pack)) as engine:
        await store_all(engine, MEMORIES[3:])
    with duckdb.connect(str(pack)) as conn:
        conn.execute("DROP TABLE scratch")
    async with MemoryEngine.open(pack_config(pack)) as engine:
        before = await read_back(engine)
    grown = pack.stat().st_size
    counts, objects = table_counts(pack), schema_objects(pack)
    assert "vector_key_seq" in objects["sequences"]

    result = compact_pack(pack)
    assert result == {
        "path": str(pack), "bytes_before": grown,
        "bytes_after": pack.stat().st_size, "encrypted": False,
    }
    # The scratch rows took about 8 MB; the copy holds only live data.
    assert result["bytes_after"] < grown - 4 * 2**20
    assert os.listdir(tmp_path) == ["memories.prme"]
    assert table_counts(pack) == counts
    # COPY FROM DATABASE carries tables with their constraints, indexes and
    # sequences; an engine open recreates any index it did not.
    assert schema_objects(pack) == objects

    async with MemoryEngine.open(pack_config(pack)) as engine:
        assert await read_back(engine) == before
        await engine.store("Alice planted basil on the balcony", user_id=USER)
    assert schema_objects(pack) == objects
    async with MemoryEngine.open(pack_config(pack)) as engine:
        # A new vector key followed the compacted sequence without a collision.
        response = await engine.retrieve("basil", user_id=USER, reference_time=REFERENCE_TIME)
        assert response.results[0].node.content == "Alice planted basil on the balcony"
        assert len(await engine._vector_index.search("basil", USER, k=50)) == len(MEMORIES) + 1


async def test_compact_keeps_a_directory_pack_working(tmp_path):
    directory = tmp_path / "pack"
    async with MemoryEngine.open(directory_config(directory)) as engine:
        await store_all(engine)
        before = await read_back(engine)
    database = directory / "memory.duckdb"
    result = compact_pack(database)
    assert result["bytes_after"] <= result["bytes_before"]
    assert {"memory.duckdb", "vectors.usearch", "lexical_index"} <= set(os.listdir(directory))
    async with MemoryEngine.open(directory_config(directory)) as engine:
        assert await read_back(engine) == before


async def test_compact_refuses_an_open_or_unclean_pack(tmp_path):
    pack = tmp_path / "memories.prme"
    await make_pack(pack)
    size = pack.stat().st_size

    with pytest.raises(FileNotFoundError):
        compact_pack(tmp_path / "absent.prme")

    # A write-ahead log means the pack is open or was not closed cleanly.
    wal = Path(str(pack) + ".wal")
    wal.write_bytes(b"")
    with pytest.raises(PackInUseError, match="write-ahead log"):
        compact_pack(pack)
    wal.unlink()
    assert os.listdir(tmp_path) == ["memories.prme"]

    # Another process holding the pack open keeps DuckDB's lock on it.
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import duckdb, sys\n"
         "conn = duckdb.connect(sys.argv[1])\n"
         "print('open', flush=True)\n"
         "sys.stdin.read()\n",
         str(pack)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "open"
        with pytest.raises(duckdb.IOException, match="lock"):
            compact_pack(pack)
    finally:
        holder.communicate(timeout=30)
    assert holder.returncode == 0
    assert os.listdir(tmp_path) == ["memories.prme"]
    assert pack.stat().st_size == size


async def test_cmd_compact_reports_sizes(tmp_path, capsys):
    pack = tmp_path / "memories.prme"
    await make_pack(pack)
    await cmd_compact(args(pack=str(pack), encryption_key=None, format="table"))
    out = capsys.readouterr().out
    assert f"Compacted memory pack: {pack}" in out
    assert f"Size after:   {pack.stat().st_size:,} bytes" in out


# --- migrate --------------------------------------------------------------


async def test_migrate_copies_a_directory_pack_into_one_file(tmp_path):
    directory = tmp_path / "pack"
    async with MemoryEngine.open(directory_config(directory)) as engine:
        await store_all(engine)
        expected = await read_back(engine)
    listing = sorted(os.listdir(directory))
    source_counts = table_counts(directory / "memory.duckdb")

    pack = tmp_path / "out" / "memories.prme"
    result = await migrate_directory_pack(directory, pack)
    assert result == {
        "source": str(directory), "destination": str(pack),
        "nodes": source_counts["nodes"], "edges": source_counts["edges"],
        "events": source_counts["events"], "vectors": len(MEMORIES),
        "bytes_before": result["bytes_before"], "bytes_after": pack.stat().st_size,
        "encrypted": False,
    }
    assert result["nodes"] == result["events"] == len(MEMORIES)
    assert result["bytes_before"] > 0
    assert os.listdir(pack.parent) == ["memories.prme"]
    assert sorted(os.listdir(directory)) == listing

    with open_pack_read_only(pack) as conn:
        assert conn.execute("SELECT ndim, vector_count FROM vector_snapshot").fetchone() == (
            MockEmbeddingProvider.dimension, len(MEMORIES),
        )
        assert conn.execute("SELECT format_version, embedding_model FROM prme_manifest").fetchall() == [
            (1, MockEmbeddingProvider.model_name),
        ]
    counts = table_counts(pack)
    assert {table: counts[table] for table in source_counts} == source_counts

    async with MemoryEngine.open(pack_config(pack)) as engine:
        assert await read_back(engine) == expected
    # The directory pack still opens and serves the same results.
    async with MemoryEngine.open(directory_config(directory)) as engine:
        assert await read_back(engine) == expected
    assert sorted(os.listdir(directory)) == listing


async def test_migrate_restores_vectors_a_legacy_pack_kept_only_in_its_snapshot_file(tmp_path):
    directory = tmp_path / "pack"
    async with MemoryEngine.open(directory_config(directory)) as engine:
        await store_all(engine)
        expected = await read_back(engine)
    # Packs written before vector payloads existed held vectors only in vectors.usearch.
    with duckdb.connect(str(directory / "memory.duckdb")) as conn:
        conn.execute("DELETE FROM vector_payloads")

    pack = tmp_path / "memories.prme"
    result = await migrate_directory_pack(directory, pack)
    assert result["vectors"] == len(MEMORIES)
    with open_pack_read_only(pack) as conn:
        assert conn.execute("SELECT count(*) FROM vector_payloads").fetchone() == (len(MEMORIES),)
    async with MemoryEngine.open(pack_config(pack)) as engine:
        assert await read_back(engine) == expected


async def test_migrate_refuses_without_changing_anything(tmp_path):
    directory = tmp_path / "pack"
    async with MemoryEngine.open(directory_config(directory)) as engine:
        await store_all(engine, MEMORIES[:2])

    taken = tmp_path / "taken.prme"
    taken.write_bytes(b"someone else's file")
    with pytest.raises(FileExistsError):
        await migrate_directory_pack(directory, taken)
    assert taken.read_bytes() == b"someone else's file"

    stale_log = tmp_path / "fresh.prme.wal"
    stale_log.write_bytes(b"")
    with pytest.raises(FileExistsError):
        await migrate_directory_pack(directory, tmp_path / "fresh.prme")
    stale_log.unlink()

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="memory.duckdb is missing"):
        await migrate_directory_pack(empty, tmp_path / "fresh.prme")
    assert os.listdir(empty) == []
    with pytest.raises(FileNotFoundError):
        await migrate_directory_pack(tmp_path / "absent", tmp_path / "fresh.prme")
    assert not (tmp_path / "absent").exists()

    encrypted = tmp_path / "encrypted"
    encrypted.mkdir()
    (encrypted / "memory.duckdb.enc").write_bytes(b"ciphertext")
    with pytest.raises(ValueError, match="encrypted; give its encryption key"):
        await migrate_directory_pack(encrypted, tmp_path / "fresh.prme")
    assert os.listdir(encrypted) == ["memory.duckdb.enc"]

    assert sorted(os.listdir(tmp_path)) == ["empty", "encrypted", "pack", "taken.prme"]


async def test_failed_migration_leaves_no_file_behind(tmp_path, monkeypatch):
    directory = tmp_path / "pack"
    async with MemoryEngine.open(directory_config(directory)) as engine:
        await store_all(engine, MEMORIES[:2])

    def refuse(*_args, **_kwargs):
        raise RuntimeError("authored publication failure")

    monkeypatch.setattr("prme.storage.pack_tools._publish", refuse)
    with pytest.raises(RuntimeError, match="authored publication failure"):
        await migrate_directory_pack(directory, tmp_path / "out" / "memories.prme")
    assert os.listdir(tmp_path / "out") == []


def test_main_migrates_and_compacts_through_the_command_line(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "pack"
    with MemoryClient(str(directory)) as client:
        for text in MEMORIES:
            client.store(text, user_id=USER)
    pack = tmp_path / "memories.prme"

    monkeypatch.setattr(sys, "argv", ["prme", "migrate", str(directory), str(pack), "--format", "json"])
    main()
    migrated = json.loads(capsys.readouterr().out)
    assert (migrated["destination"], migrated["nodes"], migrated["vectors"]) == (
        str(pack), len(MEMORIES), len(MEMORIES),
    )

    monkeypatch.setattr(sys, "argv", ["prme", "compact", str(pack), "--format", "json"])
    main()
    compacted = json.loads(capsys.readouterr().out)
    assert compacted["bytes_after"] == pack.stat().st_size

    monkeypatch.setattr(sys, "argv", ["prme", "migrate", str(directory), str(pack)])
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 1
    assert "Destination already exists" in capsys.readouterr().err

    # Only a .prme name opens as a single-file pack, so no other is created.
    other = tmp_path / "memories.duckdb"
    monkeypatch.setattr(sys, "argv", ["prme", "migrate", str(directory), str(other)])
    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 1
    assert "must end in .prme" in capsys.readouterr().err
    assert not other.exists()


async def test_cmd_migrate_reports_counts(tmp_path, capsys):
    directory = tmp_path / "pack"
    async with MemoryEngine.open(directory_config(directory)) as engine:
        await store_all(engine)
    pack = tmp_path / "memories.prme"
    await cmd_migrate(args(directory=str(directory), pack=str(pack), encryption_key=None, format="table"))
    out = capsys.readouterr().out
    assert f"Migrated memory pack: {directory}" in out
    assert f"New pack:      {pack}" in out
    assert f"Vectors:       {len(MEMORIES)}" in out
    assert "Encrypted:     no" in out


# --- prme-mcp --db-path ---------------------------------------------------

STORAGE_VARIABLES = ("PRME_PACK_PATH", "PRME_DB_PATH", "PRME_VECTOR_PATH", "PRME_LEXICAL_PATH")


def test_mcp_db_path_selects_a_single_file_pack_or_a_directory(tmp_path, monkeypatch):
    server = pytest.importorskip("prme.mcp.server")
    # No .env from the repository, and every variable restored afterwards.
    monkeypatch.chdir(tmp_path)
    for name in STORAGE_VARIABLES:
        monkeypatch.setenv(name, "unused")
        monkeypatch.delenv(name)

    pack = tmp_path / "nested" / "memories.prme"
    server._use_db_path(str(pack))
    assert os.environ["PRME_PACK_PATH"] == str(pack)
    assert not {"PRME_DB_PATH", "PRME_VECTOR_PATH", "PRME_LEXICAL_PATH"} & set(os.environ)
    assert pack.parent.is_dir() and not pack.exists()
    config = PRMEConfig()
    assert config.single_file_pack and config.pack_path == str(pack)

    # A directory keeps the directory layout, even over a configured pack path.
    directory = tmp_path / "memories"
    server._use_db_path(str(directory))
    config = PRMEConfig()
    assert not config.single_file_pack
    assert (config.db_path, config.vector_path, config.lexical_path) == (
        str(directory / "memory.duckdb"), str(directory / "vectors.usearch"),
        str(directory / "lexical_index"),
    )
    assert (directory / "lexical_index").is_dir()

    # An existing directory whose name ends in .prme stays a directory pack.
    legacy = tmp_path / "legacy.prme"
    legacy.mkdir()
    server._use_db_path(str(legacy))
    assert not PRMEConfig().single_file_pack

"""P7 bar (c) part 2: no record lost and no record changed across a reopen and a key rebuild.

    PYTHONPATH=. .venv/bin/python research/address-lookup-2026-11/probes/rebuild.py \
        --pack /tmp/p6/build/current-4050/pack \
        --out research/address-lookup-2026-11/results/rebuild.json

The bar asks two things. The prototype must not lose a record, and it must not change a record, after a pack is
reopened and the key is rebuilt.

What the probe does:

1. Copy the pack, open it, and record a digest of every node row and every event row, before anything is added.
2. Add the key columns and index, open and close through ``MemoryEngine``, and record the same digests over the same
   columns. The columns are named explicitly so the two digests cover the same fields and the two new columns cannot
   make the comparison vacuous.
3. Backfill the key, close the pack, reopen it, clear every stored key, and rebuild them from stored metadata.
   Compare the digests again.

   The keys are cleared rather than the columns dropped, because DuckDB on this build refuses to drop a column while
   any index exists on the table (``DependencyException: Cannot alter entry "nodes" because there are entries that
   depend on it``, reproduced on an untouched pack and on a three-column table with a primary key). Clearing the
   values proves the same property: a key is a pure function of stored metadata and nothing else.
4. Compare the key of every node before and after the rebuild.

The point of step 3 is that a key is a pure function of stored metadata. If that is true, dropping it and rebuilding
it must give exactly the same keys, and the lookup must find exactly the same rows. If that is false, the key is
carrying state that the store does not have, which is a defect.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(HERE))

from probes.baseline import blocked_environment  # noqa: E402

import keys  # noqa: E402

SCRATCH = os.environ.get("P7_SCRATCH", "/tmp/p7")

NODE_COLUMNS = (
    "id, node_type, user_id, session_id, scope, content, CAST(metadata AS VARCHAR), confidence, salience, "
    "lifecycle_state, valid_from, valid_to, superseded_by, CAST(evidence_refs AS VARCHAR), created_at, updated_at, "
    "epistemic_type, source_type, decay_profile, last_reinforced_at, reinforcement_boost, salience_base, "
    "confidence_base, pinned, ttl_days, event_time"
)
EVENT_COLUMNS = (
    "id, user_id, scope, CAST(content AS VARCHAR), CAST(metadata AS VARCHAR), created_at, content_hash"
)


def digest(conn, table: str, columns: str) -> dict:
    rows = conn.execute(f"SELECT {columns} FROM {table} ORDER BY id").fetchall()
    total = hashlib.sha256()
    for row in rows:
        total.update(repr(row).encode())
    return {"rows": len(rows), "sha256": total.hexdigest()}


def key_digest(conn) -> dict:
    rows = conn.execute(
        f"SELECT CAST(id AS VARCHAR), {keys.SUBJECT_COLUMN}, {keys.PREDICATE_COLUMN} FROM nodes ORDER BY id"
    ).fetchall()
    total = hashlib.sha256()
    for row in rows:
        total.update(repr(row).encode())
    return {"rows": len(rows), "sha256": total.hexdigest()}


async def open_close(pack: str) -> None:
    """Open the pack through the engine and close it, so the pack is a closed pack again.

    The engine is pointed at a directory whose ``pack`` is the pack itself, so the file that is reopened is the one
    the previous step closed and not a fresh copy.
    """
    from prme.storage.embedding import create_embedding_provider
    from prme.storage.engine import MemoryEngine
    from tests.contract.harness import product_config

    directory = os.path.dirname(pack)
    config = product_config(directory)
    provider = create_embedding_provider(config.embedding)
    engine = await MemoryEngine.create(config, embedding_provider=provider)
    await engine.close()


async def main(args) -> dict:
    blocked_environment()
    import duckdb

    work = os.path.join(SCRATCH, "rebuild")
    if os.path.exists(work):
        shutil.rmtree(work)
    os.makedirs(work)
    pack = os.path.join(work, "pack")
    shutil.copytree(args.pack, pack)
    database = os.path.join(pack, "memory.duckdb")

    report = {"pack": args.pack, "steps": {}}

    conn = duckdb.connect(database)
    report["steps"]["before"] = {
        "nodes": digest(conn, "nodes", NODE_COLUMNS),
        "events": digest(conn, "events", EVENT_COLUMNS),
    }
    conn.close()

    await open_close(pack)
    conn = duckdb.connect(database)
    keys.add_schema(conn)
    report["steps"]["after_schema"] = {
        "nodes": digest(conn, "nodes", NODE_COLUMNS),
        "events": digest(conn, "events", EVENT_COLUMNS),
    }
    report["steps"]["backfill"] = keys.backfill(conn)
    first_keys = key_digest(conn)
    conn.execute("CHECKPOINT")
    conn.close()

    await open_close(pack)
    conn = duckdb.connect(database)
    report["steps"]["after_reopen"] = {
        "nodes": digest(conn, "nodes", NODE_COLUMNS),
        "events": digest(conn, "events", EVENT_COLUMNS),
    }
    before_keys = key_digest(conn)
    conn.execute(f"UPDATE nodes SET {keys.SUBJECT_COLUMN} = NULL, {keys.PREDICATE_COLUMN} = NULL")
    keys.add_schema(conn)
    report["steps"]["backfill_again"] = keys.backfill(conn)
    after_keys = key_digest(conn)
    report["steps"]["after_rebuild"] = {
        "nodes": digest(conn, "nodes", NODE_COLUMNS),
        "events": digest(conn, "events", EVENT_COLUMNS),
    }
    conn.execute("CHECKPOINT")
    conn.close()

    await open_close(pack)
    conn = duckdb.connect(database)
    report["steps"]["after_second_reopen"] = {
        "nodes": digest(conn, "nodes", NODE_COLUMNS),
        "events": digest(conn, "events", EVENT_COLUMNS),
    }
    conn.close()

    steps = report["steps"]
    report["checks"] = {
        "no_node_lost": all(steps[name]["nodes"]["rows"] == steps["before"]["nodes"]["rows"]
                            for name in ("after_schema", "after_reopen", "after_rebuild",
                                         "after_second_reopen")),
        "no_node_changed": all(steps[name]["nodes"]["sha256"] == steps["before"]["nodes"]["sha256"]
                               for name in ("after_schema", "after_reopen", "after_rebuild",
                                            "after_second_reopen")),
        "no_event_changed": all(steps[name]["events"]["sha256"] == steps["before"]["events"]["sha256"]
                                for name in ("after_schema", "after_reopen", "after_rebuild",
                                             "after_second_reopen")),
        "key_stable_across_rebuild": first_keys["sha256"] == before_keys["sha256"],
        "key_rebuild_matches_original": first_keys["sha256"] == after_keys["sha256"],
        "key_rows": after_keys["rows"],
    }
    report["passed"] = all(value is True for name, value in report["checks"].items()
                           if name != "key_rows")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--out", default=None)
    arguments = parser.parse_args()
    result = asyncio.run(main(arguments))
    text = json.dumps(result, indent=1)
    if arguments.out:
        with open(arguments.out, "w") as handle:
            handle.write(text)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"],
                      "backfill": result["steps"]["backfill"],
                      "backfill_again": result["steps"]["backfill_again"]}, indent=1), flush=True)

"""P4: grow a copy of one LoCoMo ingest() pack k times, as if the same two people kept talking k times longer.

Scaling model (an assumption, stated in REPORT.md): every non-entity node (notes, facts, preferences,
decisions) is copied k-1 times with new ids and a new session id per copy; every edge that touches a
non-entity node is copied with its non-entity ends remapped; entities are shared, as ingest() reuses an
entity whose name and type match exactly. So the speaker hubs and topic entities gain k times the links,
and no new entity appears. Copies keep their evidence references (the same source events) and times.
Then the vector and lexical indexes are rebuilt from the graph (rebuild_indexes), so every copy is
searchable. The source pack is never opened: the script works on its own copy.

Usage: .venv/bin/python research/chat-read-cost-2026-11/scale_pack.py SOURCE_FOLDER DEST_FOLDER K
(SOURCE_FOLDER holds manifest.json and pack/, as /tmp/p4/packs/locomo/conv-42 does.)
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path


def _new_id(column: str, copy: int) -> str:
    digest = f"md5(CAST({column} AS VARCHAR) || '#{copy}')"
    return (f"CAST(substr({digest},1,8)||'-'||substr({digest},9,4)||'-'||substr({digest},13,4)||'-'||"
            f"substr({digest},17,4)||'-'||substr({digest},21,12) AS UUID)")


def grow(db: Path, k: int) -> dict:
    import duckdb

    con = duckdb.connect(str(db))
    con.execute("CREATE TEMP TABLE entity_ids AS SELECT id FROM nodes WHERE node_type = 'entity'")
    node_columns = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'nodes' ORDER BY ordinal_position"
    ).fetchall()]
    edge_columns = [r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'edges' ORDER BY ordinal_position"
    ).fetchall()]
    con.execute("CREATE TEMP TABLE base_nodes AS SELECT * FROM nodes WHERE node_type <> 'entity'")
    con.execute("""CREATE TEMP TABLE base_edges AS SELECT * FROM edges
                   WHERE source_id NOT IN (SELECT id FROM entity_ids) OR target_id NOT IN (SELECT id FROM entity_ids)""")
    for copy in range(1, k):
        mapped = {
            "id": _new_id("id", copy),
            "session_id": f"CASE WHEN session_id IS NULL THEN NULL ELSE session_id || '#{copy}' END",
            "superseded_by": (f"CASE WHEN superseded_by IS NULL OR superseded_by IN (SELECT id FROM entity_ids) "
                              f"THEN superseded_by ELSE {_new_id('superseded_by', copy)} END"),
        }
        select = ", ".join(mapped.get(c, c) for c in node_columns)
        con.execute(f"INSERT INTO nodes SELECT {select} FROM base_nodes")
        edge_mapped = {
            "id": _new_id("id", copy),
            "source_id": (f"CASE WHEN source_id IN (SELECT id FROM entity_ids) THEN source_id "
                          f"ELSE {_new_id('source_id', copy)} END"),
            "target_id": (f"CASE WHEN target_id IN (SELECT id FROM entity_ids) THEN target_id "
                          f"ELSE {_new_id('target_id', copy)} END"),
        }
        select = ", ".join(edge_mapped.get(c, c) for c in edge_columns)
        con.execute(f"INSERT INTO edges SELECT {select} FROM base_edges")
    counts = {"nodes": con.execute("SELECT count(*) FROM nodes").fetchone()[0],
              "edges": con.execute("SELECT count(*) FROM edges").fetchone()[0]}
    con.execute("CHECKPOINT")
    con.close()
    return counts


async def rebuild(pack: Path) -> dict:
    from benchmarks.diagnostics.product_packing import gate_config
    from prme import MemoryEngine

    async with MemoryEngine.open(gate_config(pack)) as engine:
        return await engine.rebuild_indexes()


def main() -> None:
    source, dest, k = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    if dest.exists():
        raise SystemExit(f"Destination exists: {dest}")
    dest.mkdir(parents=True)
    subprocess.run(["cp", "-cR", str(source / "pack"), str(dest / "pack")], check=True)
    manifest = json.loads((source / "manifest.json").read_text())
    started = time.perf_counter()
    counts = grow(dest / "pack" / "memory.duckdb", k)
    grown = time.perf_counter() - started
    summary = asyncio.run(rebuild(dest / "pack"))
    manifest.update({"scaled_from": str(source), "scale": k, "turns": manifest["turns"] * k,
                     "scale_counts": counts, "rebuild": summary,
                     "scale_seconds": {"grow": round(grown, 1), "rebuild": round(time.perf_counter() - started - grown, 1)}})
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({"dest": str(dest), "k": k, **counts, "rebuild": summary,
                      "seconds": manifest["scale_seconds"]}), flush=True)


if __name__ == "__main__":
    main()

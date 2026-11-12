"""P8 bar 4: does the cheaper read need a new index or a new column? No. Measure both ways.

    PYTHONPATH=. .venv/bin/python research/graph-read-cost-2026-11/probes/index_check.py \
        --pack /tmp/p8/m3-pack --out research/graph-read-cost-2026-11/results/index-check-m3.json

The cheap path adds one thing to the SQL: a ``ROW_NUMBER`` window in the candidate order and a ``WHERE`` that
repeats filters the stage already applied in Python. Both read columns the walk already selects. So the change
should cost nothing on the write path, and it should need no new index for the read.

This probe checks the read claim on a scratch copy of the pack, which it may write to: it times the walk alone,
the walk plus the window, and the walk plus the window with an index on the candidate order. It does not time a
write. The write claim rests on the change adding no column, no index and no write, so there is no write to time.

The index is created, timed and dropped on the copy. It is not proposed for the engine: an index on
``(user_id, lifecycle_state, confidence, created_at)`` does not make the window faster here, because DuckDB scans
the walk's rows once either way and an index on ``nodes`` has nothing to shorten.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

USER = "robot-1"
_COLUMNS = "node_id, depth, node_type, user_id, scope, confidence, created_at, valid_from, valid_to, metadata"


def timed(conn, sql: str, params: list, repeats: int) -> dict:
    values = []
    rows = 0
    for _ in range(repeats):
        started = time.perf_counter()
        rows = len(conn.execute(sql, params).fetchall())
        values.append(1000 * (time.perf_counter() - started))
    return {"rows": rows, "p50": round(statistics.median(values), 2),
            "mean": round(statistics.fmean(values), 2), "repeats": repeats}


def main(args) -> dict:
    import duckdb
    from prme.index_policy import unindexed_summary_sql
    from prme.storage.seed_walk import seed_walk_sql

    work = Path(args.work or "/tmp/p8/work/index-check")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    shutil.copy2(Path(args.pack) / "memory.duckdb", work / "memory.duckdb")

    conn = duckdb.connect(str(work / "memory.duckdb"))
    seeds = [str(row[0]) for row in conn.execute(
        "SELECT id FROM nodes WHERE node_type = 'entity' AND user_id = ? "
        "AND lower(content) LIKE '%mug%'", [USER]).fetchall()]
    base = seed_walk_sql("duckdb", with_valid_at=False)
    params = [USER, 3, seeds]
    walk_only = f"WITH b({_COLUMNS}) AS ({base}) SELECT * FROM b"
    window = (f"WITH b({_COLUMNS}) AS ({base}), "
              "ranked AS (SELECT *, ROW_NUMBER() OVER (ORDER BY confidence DESC, created_at DESC, node_id ASC) "
              f"AS rn FROM b WHERE NOT {unindexed_summary_sql('b')} AND scope IN ('personal')) "
              "SELECT node_id, depth, rn <= 150 AS kept, node_type, "
              "CASE WHEN node_type = 'entity' THEN metadata END FROM ranked")
    ordered_index = "CREATE INDEX idx_probe_order ON nodes (user_id, lifecycle_state, confidence, created_at)"

    result = {"pack": args.pack, "seeds": len(seeds), "load_before": [round(v, 2) for v in __import__("os").getloadavg()]}
    result["walk_only"] = timed(conn, walk_only, params, args.repeats)
    result["walk_and_window"] = timed(conn, window, params, args.repeats)
    conn.execute(ordered_index)
    result["walk_and_window_indexed"] = timed(conn, window, params, args.repeats)
    conn.execute("DROP INDEX IF EXISTS idx_probe_order")
    result["nodes"] = conn.execute("SELECT count(*) FROM nodes").fetchone()[0]
    result["edges"] = conn.execute("SELECT count(*) FROM edges").fetchone()[0]
    result["load_after"] = [round(v, 2) for v in __import__("os").getloadavg()]
    conn.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--work", default=None)
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    outcome = main(arguments)
    with open(arguments.out, "w") as handle:
        json.dump(outcome, handle, indent=1)
    print(json.dumps(outcome, indent=1))

"""P5 floor check: what one DuckDB statement costs on this machine, against the 5 ms a fact that 200 facts a second leaves.

    PYTHONPATH=. uv run python research/write-batch-2026-11/sql_floor.py --out research/write-batch-2026-11/results/sql-floor.json

An in-memory DuckDB table of 20,000 rows with a primary key, 2,000 statements of each kind, one at a time, as the write
path issues them. The pack's on-disk database is not faster than this, so these costs are a lower bound for the
path's statements. The batch path issues about 170 statements a fact (window results).
"""
from __future__ import annotations

import argparse
import json
import time
import uuid

import duckdb


def main() -> dict:
    conn = duckdb.connect()
    conn.execute("CREATE TABLE t(id VARCHAR PRIMARY KEY, a VARCHAR, b INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?,?,?)", [(str(uuid.uuid4()), "x" * 50, i) for i in range(20_000)])
    ids = [row[0] for row in conn.execute("SELECT id FROM t LIMIT 2000").fetchall()]
    kinds = {
        "select_constant": lambda i: conn.execute("SELECT 1").fetchall(),
        "point_lookup_by_key": lambda i: conn.execute("SELECT * FROM t WHERE id=?", [ids[i]]).fetchall(),
        "insert_one_row": lambda i: conn.execute("INSERT INTO t VALUES (?,?,?)", [str(uuid.uuid4()), "y", i]),
    }
    report = {"statements_each": 2000, "duckdb": duckdb.__version__, "ms_per_statement": {}}
    for name, run in kinds.items():
        started = time.perf_counter()
        for index in range(2000):
            run(index)
        report["ms_per_statement"][name] = round(1000 * (time.perf_counter() - started) / 2000, 3)
    budget = 5.0
    report["statements_in_5_ms"] = {name: int(budget // cost) for name, cost in report["ms_per_statement"].items()}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    arguments = parser.parse_args()
    result = main()
    with open(arguments.out, "w") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result, indent=1))

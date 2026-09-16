"""Small local query-plan/timing check for the mutable lifecycle index removal.

Synthetic SQL only; this is not end-to-end retrieval or a capacity benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import tempfile
import time

import duckdb
from prme.storage.schema import initialize_database

QUERIES = {
    'tenant_active': "SELECT id FROM nodes WHERE user_id='tenant-123' AND lifecycle_state IN ('tentative','stable') ORDER BY created_at DESC LIMIT 100",
    'global_active': "SELECT id FROM nodes WHERE lifecycle_state IN ('tentative','stable') ORDER BY created_at DESC LIMIT 100",
    'rare_contested': "SELECT id FROM nodes WHERE lifecycle_state='contested' ORDER BY created_at DESC LIMIT 100",
}


def run(rows, repetitions):
    result = {'duckdb': duckdb.__version__, 'rows': rows, 'repetitions': repetitions,
              'threads': 1, 'queries': QUERIES, 'complete': False, 'arms': {},
              'limits': ['Synthetic SQL on a shared host; not product retrieval latency.',
                         'Fixed indexed-then-unindexed order can have cache effects.',
                         'No inference about large-pack capacity or universal query performance.']}
    with tempfile.TemporaryDirectory(prefix='prme-lifecycle-index-') as directory:
        with duckdb.connect(str(Path(directory) / 'memory.duckdb'), config={'threads': 1}) as conn:
            initialize_database(conn)
            conn.execute("INSERT INTO nodes (id,node_type,user_id,content,lifecycle_state,created_at) "
                         "SELECT md5(i::VARCHAR)::UUID,'fact','tenant-' || (i%1000)::VARCHAR,'Authored source ' || i::VARCHAR, "
                         "CASE WHEN i%10000=0 THEN 'contested' WHEN (i//1000)%10=0 THEN 'archived' ELSE 'stable' END, "
                         "TIMESTAMPTZ '2024-01-01' + i * INTERVAL '1 second' FROM range(?) t(i)", [rows])
            conn.execute('ANALYZE nodes')
            for arm in ('with_lifecycle_index', 'without_lifecycle_index'):
                if arm == 'with_lifecycle_index':
                    conn.execute('CREATE INDEX idx_nodes_lifecycle ON nodes(lifecycle_state)')
                else:
                    conn.execute('DROP INDEX idx_nodes_lifecycle')
                measurements = {}
                for name, sql in QUERIES.items():
                    plan = conn.execute('EXPLAIN ' + sql).fetchall()
                    for _ in range(5):
                        found = conn.execute(sql).fetchall()
                    expected = hashlib.sha256(json.dumps(found, default=str).encode()).hexdigest()
                    elapsed = []
                    for _ in range(repetitions):
                        start = time.perf_counter()
                        actual = conn.execute(sql).fetchall()
                        elapsed.append((time.perf_counter() - start) * 1000)
                        assert hashlib.sha256(json.dumps(actual, default=str).encode()).hexdigest() == expected
                    measurements[name] = {'rows_returned': len(found), 'result_sha256': expected,
                                          'median_ms': statistics.median(elapsed),
                                          'p95_ms': sorted(elapsed)[int(.95 * (repetitions - 1))],
                                          'explain': plan}
                result['arms'][arm] = measurements
            assert all(result['arms']['with_lifecycle_index'][q]['result_sha256'] ==
                       result['arms']['without_lifecycle_index'][q]['result_sha256'] for q in QUERIES)
    result['complete'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rows', type=int, default=100000)
    parser.add_argument('--repetitions', type=int, default=50)
    args = parser.parse_args()
    if args.output.exists() or args.rows < 10000 or args.repetitions < 10:
        parser.error('Fresh output, at least 10000 rows and 10 repetitions required')
    report = run(args.rows, args.repetitions)
    report['module_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with args.output.open('x') as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write('\n')


if __name__ == '__main__':
    main()

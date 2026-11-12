"""Probe: durable append cost for three shapes (single as now, single with block ordinals, 10 per transaction)."""
import asyncio, copy, json, os, shutil, sys, time
sys.path.insert(0, os.path.expanduser("~/Sites/prism-write-batch/research/write-rate-2026-11"))
import common

def q(v):
    v = sorted(v); n = len(v)
    return {"n": n, "p50": round(v[n // 2], 3), "p95": round(v[int(n * .95)], 3), "max": round(v[-1], 3)}

async def main(pack, n):
    common.blocked_environment()
    provider = await common.embedding_provider()
    d = "/tmp/p6/probe/shapes"
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(pack, d + "/pack")
    engine, guard = await common.new_engine(d, provider)
    from prme.models import Event
    from prme.storage.extraction_work import insert_duck_work
    es = engine._event_store
    cur = es._conn.cursor()
    msgs = common.observations(3 * n + 10 * n)
    ev = lambda m: Event(content=m["content"], user_id=common.USER, role="system", metadata={}, event_time=m["event_time"])
    out = {}
    # (a) as EventStore._append_with_work_sync does it
    t_a = []
    for m in msgs[:n]:
        e = ev(m); t = time.perf_counter()
        cur.execute("BEGIN TRANSACTION")
        cur.execute(es.insert_sql, es._insert_values(e))
        cur.execute("INSERT INTO event_materializations (event_id) VALUES (?)", [str(e.id)])
        insert_duck_work(cur, str(e.id))
        cur.execute("COMMIT")
        t_a.append((time.perf_counter() - t) * 1000)
    out["a_single_as_now"] = q(t_a)
    # (b) block of ordinals reserved once
    start = cur.execute(f"UPDATE extraction_work_sequence SET next_ordinal = next_ordinal + {n} RETURNING next_ordinal - {n}").fetchone()[0]
    t_b = []
    for i, m in enumerate(msgs[n:2 * n]):
        e = ev(m); t = time.perf_counter()
        cur.execute("BEGIN TRANSACTION")
        cur.execute(es.insert_sql, es._insert_values(e))
        cur.execute("INSERT INTO event_materializations (event_id) VALUES (?)", [str(e.id)])
        cur.execute("INSERT INTO event_extractions (event_id, work_order) VALUES (?, ?)", [str(e.id), start + i])
        cur.execute("COMMIT")
        t_b.append((time.perf_counter() - t) * 1000)
    out["b_single_block_ordinals"] = q(t_b)
    # (c) 10 per transaction
    t_c = []
    rest = msgs[2 * n:2 * n + 10 * (n // 10)]
    for k in range(0, len(rest), 10):
        es_ = [ev(m) for m in rest[k:k + 10]]
        t = time.perf_counter()
        cur.execute("BEGIN TRANSACTION")
        cur.executemany(es.insert_sql, [es._insert_values(e) for e in es_])
        cur.executemany("INSERT INTO event_materializations (event_id) VALUES (?)", [(str(e.id),) for e in es_])
        first = cur.execute(f"UPDATE extraction_work_sequence SET next_ordinal = next_ordinal + {len(es_)} RETURNING next_ordinal - {len(es_)}").fetchone()[0]
        cur.executemany("INSERT INTO event_extractions (event_id, work_order) VALUES (?, ?)", [(str(e.id), first + j) for j, e in enumerate(es_)])
        cur.execute("COMMIT")
        t_c.append((time.perf_counter() - t) * 1000)
    out["c_ten_per_transaction_per_call"] = q(t_c)
    out["c_per_fact"] = q([x / 10 for x in t_c])
    t_e = []
    for _ in range(n):
        t = time.perf_counter(); cur.execute("BEGIN TRANSACTION"); cur.execute("COMMIT"); t_e.append((time.perf_counter() - t) * 1000)
    out["empty_transaction"] = q(t_e)
    print(json.dumps(out), flush=True)
    os._exit(0)

asyncio.run(main(sys.argv[1], int(sys.argv[2])))

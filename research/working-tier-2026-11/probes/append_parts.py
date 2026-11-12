"""Probe: cost of each part of one durable event append, and of the events row alone in autocommit."""
import asyncio, json, os, shutil, sys, time
sys.path.insert(0, os.path.expanduser("~/Sites/prism-write-batch/research/write-rate-2026-11"))
import common

def q(v):
    v = sorted(v); n = len(v)
    return {"p50": round(v[n // 2], 3), "p95": round(v[int(n * .95)], 3), "max": round(v[-1], 3)}

async def main(pack, n):
    common.blocked_environment()
    provider = await common.embedding_provider()
    d = "/tmp/p6/probe/parts"
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(pack, d + "/pack")
    engine, guard = await common.new_engine(d, provider)
    from prme.models import Event
    es = engine._event_store
    cur = es._conn.cursor()
    msgs = common.observations(4 * n)
    ev = lambda m: Event(content=m["content"], user_id=common.USER, role="system", metadata={"assertions": m["assertions"]}, event_time=m["event_time"])
    parts = {k: [] for k in ("begin", "event_row", "materialization_row", "ordinal", "work_row", "commit")}
    for m in msgs[:n]:
        e = ev(m)
        for name, call in (("begin", lambda: cur.execute("BEGIN TRANSACTION")),
                           ("event_row", lambda: cur.execute(es.insert_sql, es._insert_values(e))),
                           ("materialization_row", lambda: cur.execute("INSERT INTO event_materializations (event_id) VALUES (?)", [str(e.id)])),
                           ("ordinal", lambda: cur.execute("UPDATE extraction_work_sequence SET next_ordinal = next_ordinal + 1 RETURNING next_ordinal - 1").fetchone()),
                           ("work_row", lambda: cur.execute("INSERT INTO event_extractions (event_id, work_order) VALUES (?, ?)", [str(e.id), 10**9 + len(parts['work_row'])])),
                           ("commit", lambda: cur.execute("COMMIT"))):
            t = time.perf_counter(); call(); parts[name].append((time.perf_counter() - t) * 1000)
    out = {k: q(v) for k, v in parts.items()}
    t_auto = []
    for m in msgs[n:2 * n]:
        e = ev(m); t = time.perf_counter()
        cur.execute(es.insert_sql, es._insert_values(e))
        t_auto.append((time.perf_counter() - t) * 1000)
    out["event_row_autocommit"] = q(t_auto)
    t_build = []
    for m in msgs[2 * n:3 * n]:
        t = time.perf_counter(); e = ev(m); es._insert_values(e); t_build.append((time.perf_counter() - t) * 1000)
    out["event_object_and_values_python"] = q(t_build)
    # the same row through a prepared statement held open
    print(json.dumps(out), flush=True)
    os._exit(0)

asyncio.run(main(sys.argv[1], int(sys.argv[2])))

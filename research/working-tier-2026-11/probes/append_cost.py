"""Probe: cost of one durable event append (event row, materialization row, extraction work row, commit)."""
import asyncio, copy, os, shutil, statistics, sys, time
sys.path.insert(0, os.path.expanduser("~/Sites/prism-write-batch/research/write-rate-2026-11"))
import common

async def main(pack, n):
    common.blocked_environment()
    provider = await common.embedding_provider()
    d = "/tmp/p6/probe/run"
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(pack, d + "/pack")
    engine, guard = await common.new_engine(d, provider)
    from prme.models import Event
    msgs = common.observations(n)
    store = copy.copy(engine._event_store)
    store._conn = engine._event_store._conn.cursor()
    times = []
    for m in msgs:
        e = Event(content=m["content"], user_id=common.USER, role="system", metadata={}, event_time=m["event_time"])
        t = time.perf_counter()
        store._append_with_work_sync(e, True, True)
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    print({"n": n, "p50": round(times[n // 2], 3), "p95": round(times[int(n * .95)], 3), "max": round(times[-1], 3)}, flush=True)
    os._exit(0)

asyncio.run(main(sys.argv[1], int(sys.argv[2])))

"""P3: time each graph-walk query as written (n.* rows) against the same query returning only id and depth."""
import asyncio, os, shutil, sys, time
sys.path.insert(0, os.path.abspath("research/write-rate-2026-11"))
sys.path.insert(0, os.path.abspath("."))
import common, m4_compare  # noqa: E402

PACKS = {4000: "data/write-rate-2026-11/m4/current-a/4000/pack", 20500: "data/write-rate-2026-11/m3-pack/pack"}


class Proxy:
    def __init__(self, real, log):
        self._real, self._log = real, log

    def execute(self, query, params=None):
        if "WITH RECURSIVE neighborhood" not in query:
            return self._real.execute(query, params) if params is not None else self._real.execute(query)
        t = time.perf_counter(); rows = self._real.execute(query, params).fetchall(); full = time.perf_counter() - t
        narrow_query = query.replace("SELECT n.*, nb.min_depth", "SELECT n.id, nb.min_depth")
        assert narrow_query != query
        t = time.perf_counter(); ids = self._real.execute(narrow_query, params).fetchall(); narrow = time.perf_counter() - t
        assert len(ids) == len(rows)
        depths = {}
        for _id, d in ids:
            depths[d] = depths.get(d, 0) + 1
        self._log.append((full, narrow, len(rows), depths))
        return _Rows(rows)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


async def main(stored):
    common.blocked_environment()
    provider = await common.embedding_provider()
    directory = f"/tmp/p3/scratch/narrow-{stored}"
    if os.path.exists(directory):
        shutil.rmtree(directory)
    shutil.copytree(PACKS[stored], os.path.join(directory, "pack"))
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
    store = engine._graph_store
    real_sync = store._get_neighborhood_with_depth_sync
    log = []

    def sync(*a, **k):
        real = store._conn
        store._conn = Proxy(real, log)
        try:
            return real_sync(*a, **k)
        finally:
            store._conn = real

    store._get_neighborhood_with_depth_sync = sync
    asked = [q for q, e in m4_compare.questions(min(stored, 20000)) if e][:2]
    await engine.retrieve(asked[0], user_id=common.USER)
    log.clear()
    await engine.retrieve(asked[1], user_id=common.USER)
    await engine.close()
    depth_total = {}
    for *_x, depths in log:
        for d, c in depths.items():
            depth_total[d] = depth_total.get(d, 0) + c
    print(stored, asked[1], f"walks {len(log)}; n.* query+fetch {1000 * sum(f for f, *_ in log):.0f} ms;",
          f"id+depth query+fetch {1000 * sum(n for _, n, *_ in log):.0f} ms; rows {sum(r for *_x, r, _d in log)};",
          f"rows by depth {dict(sorted(depth_total.items()))}")


asyncio.run(main(int(sys.argv[1])))

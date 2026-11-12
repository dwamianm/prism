"""P3: split graph-walk time at one size into SQL, row decoding and the owner view (one retrieve())."""
import asyncio, collections, os, shutil, sys, time
sys.path.insert(0, os.path.abspath("research/write-rate-2026-11"))
sys.path.insert(0, os.path.abspath("."))
import common, m4_compare  # noqa: E402

PACKS = {4000: "data/write-rate-2026-11/m4/current-a/4000/pack", 20500: "data/write-rate-2026-11/m3-pack/pack"}


async def main(stored):
    common.blocked_environment()
    provider = await common.embedding_provider()
    directory = f"/tmp/p3/scratch/split-{stored}"
    if os.path.exists(directory):
        shutil.rmtree(directory)
    shutil.copytree(PACKS[stored], os.path.join(directory, "pack"))
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
    store = engine._graph_store
    seconds = collections.defaultdict(float)
    counts = collections.Counter()
    real_row, real_view, real_sync = store._row_to_node, store._owned_view_sync, store._get_neighborhood_with_depth_sync
    inside = {"on": False}

    def row(r):
        t = time.perf_counter(); out = real_row(r)
        if inside["on"]:
            seconds["row_to_node"] += time.perf_counter() - t; counts["rows"] += 1
        return out

    def view(nodes, user_id):
        t = time.perf_counter(); out = real_view(nodes, user_id)
        if inside["on"]:
            seconds["owned_view"] += time.perf_counter() - t
        return out

    def sync(*a, **k):
        inside["on"] = True
        t = time.perf_counter()
        try:
            return real_sync(*a, **k)
        finally:
            seconds["walk_total"] += time.perf_counter() - t; counts["walks"] += 1
            inside["on"] = False

    store._row_to_node, store._owned_view_sync, store._get_neighborhood_with_depth_sync = row, view, sync
    asked = [q for q, e in m4_compare.questions(min(stored, 20000)) if e][:3]
    await engine.retrieve(asked[0], user_id=common.USER)
    seconds.clear(); counts.clear()
    for q in asked[1:]:
        await engine.retrieve(q, user_id=common.USER)
    # one walk's SQL alone, from the busiest seed of the last question
    con = store._conn
    hub = con.execute("""select n.id, n.content, count(*) c from nodes n join edges e on e.source_id = n.id or e.target_id = n.id
                         where n.node_type = 'entity' group by 1, 2 order by c desc limit 1""").fetchone()
    obj = con.execute("""select n.id, n.content, count(*) c from nodes n join edges e on e.source_id = n.id or e.target_id = n.id
                         where n.node_type = 'entity' and n.content like '%bowl%' group by 1, 2 order by c desc limit 1""").fetchone()
    await engine.close()
    walks = counts["walks"] / 2
    print(stored, {k: round(1000 * v / 2, 1) for k, v in seconds.items()}, "ms per read;",
          f"walks per read {walks:.0f}, rows per read {counts['rows'] / 2:.0f}")
    sql_ms = (seconds["walk_total"] - seconds["row_to_node"] - seconds["owned_view"]) * 1000 / 2
    print(stored, f"SQL and fetch (rest) {sql_ms:.1f} ms per read; busiest entity {hub[1]!r} degree {hub[2]};",
          f"bowl entity {obj[1]!r} degree {obj[2]}")


asyncio.run(main(int(sys.argv[1])))

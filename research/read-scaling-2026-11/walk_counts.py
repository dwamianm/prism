"""P3: count seeds, rows and time of each graph-walk query inside one retrieve() (organizer off)."""
import argparse, asyncio, collections, json, os, shutil, sys, time
sys.path.insert(0, os.path.abspath("research/write-rate-2026-11"))
sys.path.insert(0, os.path.abspath("."))
import common, m4_compare  # noqa: E402

PACKS = {400: "data/write-rate-2026-11/m4/current-a/400/pack",
         4000: "data/write-rate-2026-11/m4/current-a/4000/pack",
         20500: "data/write-rate-2026-11/m3-pack/pack"}


async def main(args):
    common.blocked_environment()
    provider = await common.embedding_provider()
    import prme.retrieval.candidates as candidates
    report = {}
    for stored in args.sizes:
        directory = os.path.join(args.scratch, str(stored))
        if os.path.exists(directory):
            shutil.rmtree(directory)
        shutil.copytree(PACKS[stored], os.path.join(directory, "pack"))
        engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
        store = engine._graph_store
        log = collections.defaultdict(list)
        real_hood, real_edges, real_seeds = (store.get_neighborhood_with_depth, store.get_edges,
                                              candidates._find_seed_entities)

        async def hood(node_id, **kwargs):
            started = time.perf_counter()
            rows = await real_hood(node_id, **kwargs)
            log["hood"].append((time.perf_counter() - started, len(rows)))
            return rows

        async def edges(*a, **kwargs):
            started = time.perf_counter()
            rows = await real_edges(*a, **kwargs)
            log["edges"].append((time.perf_counter() - started, len(rows), len(kwargs.get("node_ids") or [])))
            return rows

        async def seeds(*a, **kwargs):
            started = time.perf_counter()
            found = await real_seeds(*a, **kwargs)
            log["seeds"].append((time.perf_counter() - started, [n.content for n in found]))
            return found

        store.get_neighborhood_with_depth, store.get_edges = hood, edges
        candidates._find_seed_entities = seeds
        asked = [q for q, expected in m4_compare.questions(min(stored, 20000)) if expected][:6]
        per = []
        try:
            await engine.retrieve(asked[0], user_id=common.USER)
            for question in asked[1:]:
                log.clear()
                started = time.perf_counter()
                await engine.retrieve(question, user_id=common.USER)
                total = time.perf_counter() - started
                hoods = log["hood"]
                per.append({
                    "question": question, "retrieve_s": round(total, 3),
                    "seed_lookup_ms": round(1000 * sum(s for s, _ in log["seeds"]), 1),
                    "seeds": len(log["seeds"][0][1]) if log["seeds"] else 0,
                    "seed_names": log["seeds"][0][1][:60] if log["seeds"] else [],
                    "walk_queries": len(hoods),
                    "walk_s": round(sum(s for s, _ in hoods), 3),
                    "walk_rows_total": sum(n for _, n in hoods),
                    "walk_rows_max": max((n for _, n in hoods), default=0),
                    "walk_ms_max": round(1000 * max((s for s, _ in hoods), default=0), 1),
                    "edge_reads": [(round(1000 * s, 1), n, k) for s, n, k in log["edges"]],
                })
            assert not guard.misses
        finally:
            store.get_neighborhood_with_depth, store.get_edges = real_hood, real_edges
            candidates._find_seed_entities = real_seeds
            con = store._conn
            types = con.execute("select node_type, count(*) from nodes group by 1 order by 2 desc").fetchall()
            edge_types = con.execute("select edge_type, count(*) from edges group by 1 order by 2 desc").fetchall()
            degree = con.execute("""
                with d as (select id, count(*) c from (select source_id id from edges union all select target_id from edges) group by id)
                select n.node_type, count(*), round(avg(c),1), max(c) from d join nodes n using (id) group by 1 order by 2 desc""").fetchall()
            await engine.close()
        report[str(stored)] = {"node_types": types, "edge_types": edge_types,
                               "degree_by_type": [list(map(str, r)) for r in degree], "questions": per}
        print(stored, json.dumps({k: v for k, v in report[str(stored)].items() if k != "questions"}), flush=True)
        for row in per:
            print("  ", json.dumps({k: v for k, v in row.items() if k not in ("seed_names", "edge_reads")}),
                  "edges:", row["edge_reads"], flush=True)
        print("   seeds of first question:", per[0]["seed_names"], flush=True)
    with open(os.path.join(args.out, f"walk-{args.label}.json"), "w") as handle:
        json.dump(report, handle, indent=1, default=str)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[400, 4000, 20500])
    parser.add_argument("--label", default="p2code")
    parser.add_argument("--scratch", default="/tmp/p3/scratch")
    parser.add_argument("--out", default="/tmp/p3/out")
    a = parser.parse_args()
    os.makedirs(a.out, exist_ok=True)
    asyncio.run(main(a))

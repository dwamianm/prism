"""Task 21: where the seed reach edge read spends its time at 20,500 stated facts (scratch copy, providers blocked)."""
import asyncio, os, shutil, statistics, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/Sites/prism-write-rate/research/write-rate-2026-11"))
os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
PACK = os.path.expanduser("~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack")
captured = []


async def main():
    import common, m4_compare
    from prme.storage.duckpgq_graph import _uuid_literals
    provider = await common.embedding_provider()
    directory = "/tmp/task21/edges-scratch"
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory)
    os.system(f"cp -cR '{PACK}' '{directory}/pack'")
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
    store = engine._graph_store
    real = store.get_edges

    async def spy(**kw):
        captured.append(list(kw.get("node_ids") or []))
        return await real(**kw)
    store.get_edges = spy
    asked = [q for q, expected in m4_compare.questions(20000) if expected][:6]
    try:
        for q in asked:
            await engine.retrieve(q, user_id=common.USER)
        conn = store._conn
        for ids in captured[-5:]:
            lit = _uuid_literals(ids)
            t = time.perf_counter()
            rows = conn.execute(f"SELECT * FROM edges WHERE (source_id IN ({lit}) OR target_id IN ({lit})) ORDER BY created_at DESC").fetchall()
            sql = time.perf_counter() - t
            t = time.perf_counter()
            edges = [store._row_to_edge(r) for r in rows]
            dec = time.perf_counter() - t
            t = time.perf_counter()
            slim = conn.execute(f"SELECT CAST(source_id AS VARCHAR), CAST(target_id AS VARCHAR), edge_type, metadata FROM edges WHERE (source_id IN ({lit}) OR target_id IN ({lit}))").fetchall()
            slim_t = time.perf_counter() - t
            t = time.perf_counter()
            both = conn.execute(f"SELECT CAST(source_id AS VARCHAR), CAST(target_id AS VARCHAR), edge_type, metadata FROM edges WHERE source_id IN ({lit}) AND target_id IN ({lit})").fetchall()
            both_t = time.perf_counter() - t
            print(f"ids {len(ids):5d} rows {len(rows):6d} sql {1000*sql:6.1f} decode {1000*dec:6.1f} slim {1000*slim_t:6.1f} both-ends rows {len(both):6d} {1000*both_t:6.1f} ms")
    finally:
        await engine.close()
    print("load", os.getloadavg())

asyncio.run(main())

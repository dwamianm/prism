"""Task 21 cycle 2 prototype: get_nodes on the 150 kept ids, bound parameters against literals (scratch copy, providers blocked)."""
import asyncio, os, shutil, sys, time
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
    directory = "/tmp/task21/nodes-scratch"
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory)
    os.system(f"cp -cR '{PACK}' '{directory}/pack'")
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
    store = engine._graph_store
    real = store.get_nodes

    async def spy(ids, **kw):
        captured.append((list(ids), kw))
        return await real(ids, **kw)
    store.get_nodes = spy
    asked = [q for q, expected in m4_compare.questions(20000) if expected][:4]
    try:
        for q in asked:
            await engine.retrieve(q, user_id=common.USER)
        conn = store._conn
        for ids, kw in captured[-12:]:
            t = time.perf_counter(); full = await real(ids, **kw); whole = time.perf_counter() - t
            ph = ", ".join("?" for _ in ids)
            sql = f"SELECT * FROM nodes WHERE id IN ({{}}) AND lifecycle_state IN ('tentative', 'stable', 'contested')"
            t = time.perf_counter(); rows = conn.execute(sql.format(ph), ids).fetchall(); bound = time.perf_counter() - t
            lit = _uuid_literals(ids)
            t = time.perf_counter(); rows2 = conn.execute(sql.format(lit), []).fetchall() if lit else []; literal = time.perf_counter() - t
            t = time.perf_counter(); decoded = [store._row_to_node(r) for r in rows]; dec = time.perf_counter() - t
            print(f"ids {len(ids):5d} kw {kw} get_nodes {1000*whole:6.1f} bound sql {1000*bound:6.1f} literal sql {1000*literal:6.1f} same {sorted(map(str, rows)) == sorted(map(str, rows2))} decode {1000*dec:6.1f} ms")
    finally:
        await engine.close()
    print("load", os.getloadavg())

asyncio.run(main())

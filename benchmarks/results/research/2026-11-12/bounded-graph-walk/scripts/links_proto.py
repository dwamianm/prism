"""Task 21 cycle 2 prototype: the seed reach links read, three SQL shapes, same rows as get_edges then the Python filter."""
import asyncio, os, shutil, statistics, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/Sites/prism-write-rate/research/write-rate-2026-11"))
os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
PACK = os.path.expanduser("~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack")
captured = []
ALLOWED = ("NOT (e.edge_type = 'relates_to' AND COALESCE(json_extract_string(e.metadata, '$.relation'), '') = 'alias' "
           "AND COALESCE(json_extract_string(e.metadata, '$.identity_verified'), '') = 'false')")


async def main():
    import common, m4_compare
    import prme.retrieval.candidates as candidates
    from prme.storage.duckpgq_graph import _uuid_literals
    provider = await common.embedding_provider()
    directory = "/tmp/task21/links-scratch"
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory)
    os.system(f"cp -cR '{PACK}' '{directory}/pack'")
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
    store = engine._graph_store
    real = candidates._seed_reach

    async def spy(graph_store, seeds, nodes, max_hops, depths=None):
        known = {**nodes, **{str(s.id): s for s in seeds}}
        from prme.ingestion.owner_name import OWNER_NAME_KEY
        owners = {i for i, n in known.items() if candidates._is_owner_entity(n, OWNER_NAME_KEY)}
        sids = {str(s.id) for s in seeds}
        exp = (set(known) if depths is None else sids | {i for i, d in depths.items() if d < max_hops}) - (owners - sids)
        captured.append((sorted(exp), sorted(known)))
        return await real(graph_store, seeds, nodes, max_hops, depths)
    candidates._seed_reach = spy
    asked = [q for q, expected in m4_compare.questions(20000) if expected][:6]
    try:
        for q in asked:
            await engine.retrieve(q, user_id=common.USER)
        conn = store._conn
        for exp, known in captured[-5:]:
            want = set()
            for edge in await store.get_edges(node_ids=exp):
                s, t = str(edge.source_id), str(edge.target_id)
                if s != t and s in set(known) and t in set(known) and not candidates._unverified_alias(edge):
                    want.add((s, t))
            le, lk = _uuid_literals(exp), _uuid_literals(known)
            shapes = {
                "literals": (f"SELECT CAST(source_id AS VARCHAR), CAST(target_id AS VARCHAR) FROM edges e "
                             f"WHERE source_id <> target_id AND source_id IN ({lk}) AND target_id IN ({lk}) "
                             f"AND (source_id IN ({le}) OR target_id IN ({le})) AND {ALLOWED}", []),
                "unnest": ("WITH k AS MATERIALIZED (SELECT DISTINCT unnest($1::uuid[]) AS id), "
                           "x AS MATERIALIZED (SELECT DISTINCT unnest($2::uuid[]) AS id) "
                           "SELECT CAST(e.source_id AS VARCHAR), CAST(e.target_id AS VARCHAR) FROM edges e "
                           "WHERE e.source_id <> e.target_id AND e.source_id IN (SELECT id FROM k) "
                           "AND e.target_id IN (SELECT id FROM k) "
                           f"AND (e.source_id IN (SELECT id FROM x) OR e.target_id IN (SELECT id FROM x)) AND {ALLOWED}",
                           [known, exp]),
                "join": ("WITH k AS MATERIALIZED (SELECT DISTINCT unnest($1::uuid[]) AS id), "
                         "x AS MATERIALIZED (SELECT DISTINCT unnest($2::uuid[]) AS id) "
                         "SELECT CAST(e.source_id AS VARCHAR), CAST(e.target_id AS VARCHAR) FROM edges e "
                         "JOIN k a ON a.id = e.source_id JOIN k b ON b.id = e.target_id "
                         "WHERE e.source_id <> e.target_id "
                         f"AND (e.source_id IN (SELECT id FROM x) OR e.target_id IN (SELECT id FROM x)) AND {ALLOWED}",
                         [known, exp]),
            }
            line = [f"exp {len(exp)} known {len(known)} want {len(want)}"]
            for name, (sql, params) in shapes.items():
                times = []
                for _ in range(3):
                    t = time.perf_counter()
                    rows = conn.execute(sql, params).fetchall()
                    times.append(time.perf_counter() - t)
                got = {(s, t) for s, t in rows}
                line.append(f"{name} {1000*min(times):6.1f} ms rows {len(rows)} same {got == want} dup {len(rows) - len(got)}")
            print(" | ".join(line))
    finally:
        await engine.close()
    print("load", os.getloadavg())

asyncio.run(main())

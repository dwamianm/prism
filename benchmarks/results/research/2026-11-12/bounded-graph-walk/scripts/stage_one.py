"""Task 21: time the store calls of 20,500 stated-fact reads on the current worktree (scratch copy, providers blocked)."""
import asyncio, collections, functools, os, shutil, statistics, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/Sites/prism-write-rate/research/write-rate-2026-11"))
os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
PACK = os.path.expanduser("~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack")
seconds = collections.defaultdict(list)
current = collections.defaultdict(float)


def wrap(owner, name, label):
    import inspect
    real = getattr(owner, name)
    if inspect.iscoroutinefunction(real):
        @functools.wraps(real)
        async def timed(*a, **k):
            t = time.perf_counter()
            try:
                return await real(*a, **k)
            finally:
                current[label] += time.perf_counter() - t
    else:
        @functools.wraps(real)
        def timed(*a, **k):
            t = time.perf_counter()
            try:
                return real(*a, **k)
            finally:
                current[label] += time.perf_counter() - t
    setattr(owner, name, timed)


async def main():
    import common, m4_compare
    import prme.retrieval.candidates as candidates
    import prme.retrieval.pipeline as pipeline
    provider = await common.embedding_provider()
    directory = "/tmp/task21/profile-scratch"
    shutil.rmtree(directory, ignore_errors=True)
    os.makedirs(directory)
    os.system(f"cp -cR '{PACK}' '{directory}/pack'")
    engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
    store = engine._graph_store
    for name in ("walk_from_seeds", "get_edges", "get_nodes", "query_nodes", "get_neighborhood_with_depth"):
        if hasattr(store, name):
            wrap(store, name, "store." + name)
    for name in ("_generate_graph_candidates", "_generate_vector_candidates", "_generate_lexical_candidates",
                 "_generate_pinned_candidates", "_find_seed_entities", "_seed_reach"):
        wrap(candidates, name, name)
    for name in ("generate_candidates", "score_and_rank", "pack_context", "expand_session_context", "fold_claim_statements", "generate_summary_candidates"):
        if hasattr(pipeline, name):
            wrap(pipeline, name, name)
    asked = [q for q, expected in m4_compare.questions(20000) if expected][:6]
    try:
        await engine.retrieve(asked[0], user_id=common.USER)
        for _ in range(2):
            for q in asked[1:]:
                current.clear()
                t = time.perf_counter()
                await engine.retrieve(q, user_id=common.USER)
                current["retrieve"] = time.perf_counter() - t
                for k, v in current.items():
                    seconds[k].append(v)
    finally:
        await engine.close()
    for k, v in sorted(seconds.items(), key=lambda kv: -statistics.median(kv[1])):
        print(f"{k:40s} median {1000 * statistics.median(v):8.1f} ms  n={len(v)}")
    print("load", os.getloadavg())

asyncio.run(main())

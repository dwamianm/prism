"""Stage timing: where retrieve() spends its time at several store sizes. No model call.

    PYTHONPATH=. uv run python memory_bank/thesis-2026-11-10/stage_timing.py 300 3000 20000
"""
import asyncio, tempfile, os, sys, random, time, inspect, functools, collections
from datetime import timedelta
sys.path.insert(0, "memory_bank/thesis-2026-11-10")
import embodied_probe as p
from prme.client import config_from_directory
from prme.storage.engine import MemoryEngine
import prme.retrieval.pipeline as pl
import prme.models.relevance as rel
T = collections.defaultdict(float)
def wrap(mod, name):
    fn = getattr(mod, name)
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def w(*a, **k):
            t = time.perf_counter()
            try: return await fn(*a, **k)
            finally: T[name] += time.perf_counter() - t
    else:
        @functools.wraps(fn)
        def w(*a, **k):
            t = time.perf_counter()
            try: return fn(*a, **k)
            finally: T[name] += time.perf_counter() - t
    setattr(mod, name, w)
for n in ["analyze_query", "generate_candidates", "score_and_rank", "pack_context", "expand_session_context", "fold_claim_statements", "generate_summary_candidates"]:
    if hasattr(pl, n): wrap(pl, n)
import prme.retrieval.candidates as cd
for n in ["_generate_vector_candidates", "_generate_lexical_candidates", "_generate_graph_candidates", "merge_candidates"]:
    wrap(cd, n)
async def main():
    for N in [int(x) for x in sys.argv[1:]]:
        d = tempfile.mkdtemp()
        eng = await MemoryEngine.create(config_from_directory(os.path.join(d, "pack")))
        rng = random.Random(1)
        items = [{"content": p.routine(rng, i), "event_time": p.BASE + timedelta(seconds=20*i), "role": "system"} for i in range(N)]
        for o in range(0, N, 500):
            await eng.ingest_fast_many(items[o:o+500], user_id="u")
        t = time.perf_counter()
        while (await eng.process_pending(user_id="u", budget_ms=5000)).pending: pass
        idx = time.perf_counter() - t
        ref = p.BASE + timedelta(days=30)
        qs = ["Is there a leak anywhere?", "Where is the red mug now?", "What is the temperature in the office?", "Did anything move in the garage?", "Was the front door left open?"]
        await eng.retrieve(qs[0], user_id="u", reference_time=ref)
        T.clear(); t = time.perf_counter()
        for q in qs * 2:
            r = await eng.retrieve(q, user_id="u", reference_time=ref)
        wall = (time.perf_counter() - t) * 100
        size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(d) for f in fs)
        print(f"N={N} index {N/idx:.0f}/s  retrieve {wall:.0f} ms  pack {size/1e6:.1f} MB ({size/N:.0f} B/memory)  candidates {r.metadata.candidates_included} packed {r.bundle.included_count}")
        print("   " + "  ".join(f"{k}={v*100:.0f}ms" for k, v in sorted(T.items(), key=lambda kv: -kv[1])) + f"  other={wall - 100*sum(T[k] for k in ('analyze_query','generate_candidates','score_and_rank','pack_context','expand_session_context','generate_summary_candidates')):.0f}ms")
        await eng.close()
asyncio.run(main())

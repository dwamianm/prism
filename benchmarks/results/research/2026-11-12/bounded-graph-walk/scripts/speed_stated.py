"""Task 21 bar (b): retrieve() time at 400, 4,000 and 20,500 stated facts, P3's method.

P3 (research/read-scaling-2026-11/profile_read.py): scratch copy, organizer off, the first of six questions with an
expected answer warms up, the next five are timed. Here the five are timed three times over (15 reads) so the
median is less sensitive to load. Run from a worktree root: PYTHONPATH=. uv run python <this> --label <side>
"""
import argparse, asyncio, json, os, shutil, statistics, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/Sites/prism-write-rate/research/write-rate-2026-11"))
os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
P2 = os.path.expanduser("~/Sites/prism-write-rate/data/write-rate-2026-11")
PACKS = {400: f"{P2}/m4/current-a/400/pack", 4000: f"{P2}/m4/current-a/4000/pack", 20500: f"{P2}/m3-pack/pack"}


async def main(args):
    import common, m4_compare
    import prme.retrieval.candidates as candidates
    provider = await common.embedding_provider()
    graph_seconds = []
    real = candidates._generate_graph_candidates

    async def timed_graph(*a, **k):
        started = time.perf_counter()
        try:
            return await real(*a, **k)
        finally:
            graph_seconds.append(time.perf_counter() - started)
    candidates._generate_graph_candidates = timed_graph
    report = {"label": args.label, "load_1m_start": os.getloadavg()[0], "sizes": {}}
    for stored in args.sizes:
        directory = os.path.join(args.scratch, args.label, str(stored))
        shutil.rmtree(directory, ignore_errors=True)
        os.makedirs(directory)
        os.system(f"cp -cR '{PACKS[stored]}' '{directory}/pack'")
        engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
        asked = [q for q, expected in m4_compare.questions(min(stored, 20000)) if expected][:6]
        seconds, graph = [], []
        try:
            await engine.retrieve(asked[0], user_id=common.USER)
            for _ in range(args.rounds):
                for question in asked[1:]:
                    graph_seconds.clear()
                    started = time.perf_counter()
                    await engine.retrieve(question, user_id=common.USER)
                    seconds.append(time.perf_counter() - started)
                    graph.append(sum(graph_seconds))
            assert not guard.misses
        finally:
            await engine.close()
        ordered = sorted(seconds)
        report["sizes"][str(stored)] = {
            "reads": len(seconds), "median_ms": round(1000 * statistics.median(seconds), 1),
            "p95_ms": round(1000 * ordered[-(-len(ordered) * 95 // 100) - 1], 1),
            "graph_median_ms": round(1000 * statistics.median(graph), 1),
            "seconds": [round(s, 4) for s in seconds]}
        print(stored, json.dumps({k: v for k, v in report["sizes"][str(stored)].items() if k != "seconds"}), flush=True)
    report["load_1m_end"] = os.getloadavg()[0]
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"speed-{args.label}.json"), "w") as handle:
        json.dump(report, handle, indent=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=[400, 4000, 20500])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--scratch", default="/tmp/task21/speed-scratch")
    parser.add_argument("--out", default="/tmp/task21/speed")
    asyncio.run(main(parser.parse_args()))

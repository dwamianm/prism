"""P3: per-stage time and cProfile of retrieve() on scratch copies of the P2 packs (organizer off)."""
import argparse, asyncio, cProfile, io, json, os, pstats, shutil, statistics, sys, time
sys.path.insert(0, os.path.abspath("research/write-rate-2026-11"))
sys.path.insert(0, os.path.abspath("."))
import common, m4_compare  # noqa: E402

PACKS = {400: "data/write-rate-2026-11/m4/current-a/400/pack",
         4000: "data/write-rate-2026-11/m4/current-a/4000/pack",
         20500: "data/write-rate-2026-11/m3-pack/pack"}


import collections, functools, inspect

STAGES = ("analyze_query", "generate_candidates", "score_and_rank", "pack_context", "expand_session_context",
          "fold_claim_statements", "generate_summary_candidates")
CANDIDATE_STAGES = ("_generate_vector_candidates", "_generate_lexical_candidates", "_generate_graph_candidates",
                    "_generate_pinned_candidates", "merge_candidates")


class StageTimer:
    """Same as read_cost.StageTimer at 455b6b7a (read-path-cost-2026-11), copied so the P2 tree runs it."""

    def __init__(self):
        self.seconds = collections.defaultdict(float)
        self.active = False

    def wrap(self, module, name):
        function = getattr(module, name, None)
        if function is None:
            return
        timer = self
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def wrapped(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return await function(*args, **kwargs)
                finally:
                    if timer.active:
                        timer.seconds[name] += time.perf_counter() - started
        else:
            @functools.wraps(function)
            def wrapped(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    if timer.active:
                        timer.seconds[name] += time.perf_counter() - started
        setattr(module, name, wrapped)


def install_stage_timer():
    import prme.retrieval.candidates as candidates
    import prme.retrieval.pipeline as pipeline
    timer = StageTimer()
    for name in STAGES:
        timer.wrap(pipeline, name)
    for name in CANDIDATE_STAGES:
        timer.wrap(candidates, name)
    return timer


async def main(args):
    common.blocked_environment()
    provider = await common.embedding_provider()
    timer = install_stage_timer()
    report = {}
    for stored in args.sizes:
        directory = os.path.join(args.scratch, str(stored))
        if os.path.exists(directory):
            shutil.rmtree(directory)
        shutil.copytree(PACKS[stored], os.path.join(directory, "pack"))
        engine, guard = await common.new_engine(directory, provider, organizer={"opportunistic_enabled": False})
        asked = [q for q, expected in m4_compare.questions(min(stored, 20000)) if expected][:6]
        seconds, results, stages_per = [], [], []
        try:
            await engine.retrieve(asked[0], user_id=common.USER)
            for question in asked[1:]:
                if timer:
                    timer.seconds.clear(); timer.active = True
                started = time.perf_counter()
                response = await engine.retrieve(question, user_id=common.USER)
                seconds.append(time.perf_counter() - started)
                if timer:
                    timer.active = False
                    stages_per.append(dict(timer.seconds))
                results.append(len(response.results))
            profile = cProfile.Profile()
            profile.enable()
            await engine.retrieve(asked[1], user_id=common.USER)
            profile.disable()
            assert not guard.misses
        finally:
            await engine.close()
        stream = io.StringIO()
        stats = pstats.Stats(profile, stream=stream)
        stats.sort_stats("cumulative").print_stats(80)
        stats.sort_stats("tottime").print_stats(30)
        with open(os.path.join(args.out, f"profile-{stored}.txt"), "w") as handle:
            handle.write(stream.getvalue())
        stats.dump_stats(os.path.join(args.out, f"profile-{stored}.pstats"))
        stage_ms = {}
        for name in (stages_per[0] if stages_per else {}):
            stage_ms[name] = round(1000 * statistics.median(s.get(name, 0.0) for s in stages_per), 1)
        report[str(stored)] = {"seconds": [round(s, 3) for s in seconds], "median_s": round(statistics.median(seconds), 3),
                               "results": results, "stage_ms_median": dict(sorted(stage_ms.items(), key=lambda kv: -kv[1]))}
        print(stored, json.dumps(report[str(stored)]), flush=True)
    with open(os.path.join(args.out, f"stages-{args.label}.json"), "w") as handle:
        json.dump(report, handle, indent=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[400, 4000, 20500])
    parser.add_argument("--label", default="p2code")
    parser.add_argument("--scratch", default="/tmp/p3/scratch")
    parser.add_argument("--out", default="/tmp/p3/out")
    a = parser.parse_args()
    os.makedirs(a.out, exist_ok=True)
    asyncio.run(main(a))

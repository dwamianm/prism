"""Task 22: time, profile and fingerprint retrieve() on two 20,000-fact stores.

Packs (each read from a fresh copy, so stored receipts never grow the saved pack):
- stated: 20,500 stated facts, ~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack (task P3/21).
  Questions: m4_compare.questions(20000), 20 "where is X now" and 5 "what is on P".
- bench: 20,000 benchmark-shaped memories, data/read-cost-v1/packs/n20000-s1/pack (read_cost.py, task 18/19).
  Questions: read_cost.QUESTIONS (60).
Config: gate_config (current defaults, env and .env ignored, organizer off) plus --set overrides.
Run from a worktree root: PYTHONPATH=. uv run python /tmp/task22/robot_read.py --pack stated --label x
"""
import argparse, asyncio, cProfile, json, os, shutil, statistics, sys, time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/Sites/prism-write-rate/research/write-rate-2026-11"))
os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                   "TOKENIZERS_PARALLELISM": "false"})
PACKS = {"stated": Path("~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack").expanduser(),
         "bench": Path("~/Sites/prism/data/read-cost-v1/packs/n20000-s1/pack").expanduser()}


def setup(pack: str):
    if pack == "stated":
        import common, m4_compare
        questions = [q for q, _ in m4_compare.questions(20000)]
        when = common.BASE + common.STEP * 20500 + timedelta(minutes=1)
        return questions, when, common.USER
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "read_cost", os.path.expanduser("~/Sites/prism-read-cost/benchmarks/diagnostics/read_cost.py"))
    read_cost = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(read_cost)
    return list(read_cost.QUESTIONS), read_cost.reference_time(20000), read_cost.USER


def parse_sets(items):
    out = {}
    for item in items or []:
        key, value = item.split("=", 1)
        value = json.loads(value) if value[:1] in "[{0123456789-tfn\"" else value
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


async def main(args):
    from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "read_cost", os.path.expanduser("~/Sites/prism-read-cost/benchmarks/diagnostics/read_cost.py"))
    read_cost = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(read_cost)
    fingerprint, install_stage_timer = read_cost.fingerprint, read_cost.install_stage_timer
    percentile, profile_text = read_cost.percentile, read_cost.profile_text
    from prme import MemoryEngine
    _quiet_offline_cli()
    questions, when, user = setup(args.pack)
    timer = install_stage_timer()
    work = Path(args.scratch) / args.label / args.pack
    shutil.rmtree(work, ignore_errors=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    os.system(f"cp -cR '{PACKS[args.pack]}' '{work}'")
    config = gate_config(work, parse_sets(args.set))
    seconds, fingerprints, receipt_bytes, row_bytes, replay_ok, n_results = [], {}, [], [], 0, []
    try:
        async with MemoryEngine.open(config) as engine:
            await engine.retrieve("warm-up question about the house", user_id=user, reference_time=when)
            timer.seconds.clear()
            timer.active = True
            for round_index in range(args.rounds):
                for question in questions:
                    started = time.perf_counter()
                    response = await engine.retrieve(question, user_id=user, reference_time=when, limit=args.limit)
                    seconds.append(time.perf_counter() - started)
                    if round_index == 0:
                        timer.active = False
                        rid = str(response.metadata.request_id)
                        receipt = await engine.get_retrieval_receipt(rid, user_id=user)
                        fingerprints[question] = fingerprint(response, receipt)
                        receipt_bytes.append(len(receipt.model_dump_json()))
                        row, = engine._conn.execute(
                            "SELECT strlen(payload::VARCHAR) FROM operations WHERE target_id = ?",
                            [rid]).fetchone()
                        row_bytes.append(row)
                        ranked = tuple(c.node.id for c in response.results)
                        n_results.append(len(ranked))
                        replay_ok += receipt.replay_ranking() == ranked
                        timer.active = True
            timer.active = False
            stages = {name: round(1000 * value / len(seconds), 2)
                      for name, value in sorted(timer.seconds.items(), key=lambda item: -item[1])}
            if args.profile:
                profile = cProfile.Profile()
                profile.enable()
                for question in questions:
                    await engine.retrieve(question, user_id=user, reference_time=when, limit=args.limit)
                profile.disable()
                out = Path(args.out) / f"profile-{args.label}-{args.pack}.txt"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(profile_text(profile, "tottime", 45) + "\n" + profile_text(profile, "cumulative", 80))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    ms = [1000 * s for s in seconds]
    report = {"label": args.label, "pack": args.pack, "sets": args.set, "limit": args.limit, "reads": len(ms), "rounds": args.rounds,
              "questions": len(questions), "load_1m": os.getloadavg()[0],
              "p50_ms": round(statistics.median(ms), 1), "p95_ms": round(percentile(ms, 0.95), 1),
              "mean_ms": round(statistics.fmean(ms), 1), "max_ms": round(max(ms), 1),
              "receipt_bytes_median": int(statistics.median(receipt_bytes)), "receipt_bytes_max": max(receipt_bytes),
              "stored_row_bytes_median": int(statistics.median(row_bytes)), "stored_row_bytes_max": max(row_bytes),
              "replay_ranking_ok": f"{replay_ok}/{len(questions)}", "results_median": int(statistics.median(n_results)),
              "stage_ms_per_read": stages, "fingerprints": fingerprints, "ms": [round(v, 2) for v in ms]}
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / f"{args.label}-{args.pack}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k not in ("fingerprints", "ms")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", choices=sorted(PACKS), required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--set", action="append")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--scratch", default="/tmp/task22/scratch")
    parser.add_argument("--out", default="/tmp/task22/reports")
    asyncio.run(main(parser.parse_args()))

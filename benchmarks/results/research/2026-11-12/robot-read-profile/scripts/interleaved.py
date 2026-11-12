"""Task 22 step 2, bar 1 and 3: off and on reads interleaved in pairs, on one machine at one time.

Two engines open side by side, each on its own fresh copy of one pack:
- off: gate_config (current defaults) plus --mode
- on:  the same plus read_profile="robot"
Each question is read on both, in pairs; the order flips each pair (off, on), (on, off), so load drift hits both.
The 1-minute load average is recorded before every pair.
Round 0 also records per side: fingerprint, receipt bytes, stored receipt row bytes, replay check, result count and
packed record count.

Run from the tree under test:
  cd /tmp/task22/robot && PYTHONPATH=src:. /tmp/task22/stack/.venv/bin/python /tmp/task22/interleaved.py \
      --pack bench --mode classic --label x
"""
import argparse, asyncio, contextlib, importlib.util, json, os, shutil, statistics, subprocess, sys, time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/Sites/prism-write-rate/research/write-rate-2026-11"))
os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked", "PRME_EXTRACTION_API_KEY": "",
                   "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
                   "TOKENIZERS_PARALLELISM": "false"})
PACKS = {"stated": Path("~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack").expanduser(),
         "bench": Path("~/Sites/prism/data/read-cost-v1/packs/n20000-s1/pack").expanduser()}
# read_cost.py is on read-path-cost-2026-11 only; load its helpers (questions, fingerprint) from that file.
_spec = importlib.util.spec_from_file_location(
    "read_cost", os.path.expanduser("~/Sites/prism-read-cost/benchmarks/diagnostics/read_cost.py"))
read_cost = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(read_cost)


def setup(pack: str):
    if pack == "stated":
        import common, m4_compare
        questions = [q for q, _ in m4_compare.questions(20000)]
        return questions, common.BASE + common.STEP * 20500 + timedelta(minutes=1), common.USER
    return list(read_cost.QUESTIONS), read_cost.reference_time(20000), read_cost.USER


def summary(ms):
    return {"p50_ms": round(statistics.median(ms), 1), "p95_ms": round(read_cost.percentile(ms, 0.95), 1),
            "mean_ms": round(statistics.fmean(ms), 1), "max_ms": round(max(ms), 1)}


async def main(args):
    from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config
    from prme import MemoryEngine
    _quiet_offline_cli()
    questions, when, user = setup(args.pack)
    base = Path(args.scratch) / args.label / args.pack
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    sides = {"off": {"memory_mode": args.mode}, "on": {"memory_mode": args.mode, "read_profile": "robot"}}
    engines, data = {}, {}
    stack = contextlib.AsyncExitStack()
    try:
        for side, overrides in sides.items():
            subprocess.run(["cp", "-cR", str(PACKS[args.pack]), str(base / side)], check=True)
            engines[side] = await stack.enter_async_context(MemoryEngine.open(gate_config(base / side, overrides)))
            await engines[side].retrieve("warm-up question about the house", user_id=user, reference_time=when)
            data[side] = {"ms": [], "fingerprints": {}, "receipt_bytes": [], "row_bytes": [], "replay_ok": 0,
                          "results": [], "packed": []}
        loads, pair = [], 0
        for round_index in range(args.rounds):
            for question in questions:
                loads.append(os.getloadavg()[0])
                for side in (("off", "on") if pair % 2 == 0 else ("on", "off")):
                    engine, record = engines[side], data[side]
                    started = time.perf_counter()
                    response = await engine.retrieve(question, user_id=user, reference_time=when)
                    record["ms"].append(1000 * (time.perf_counter() - started))
                    if round_index == 0:
                        rid = str(response.metadata.request_id)
                        receipt = await engine.get_retrieval_receipt(rid, user_id=user)
                        record["fingerprints"][question] = read_cost.fingerprint(response, receipt)
                        record["receipt_bytes"].append(len(receipt.model_dump_json()))
                        row, = engine._conn.execute(
                            "SELECT strlen(payload::VARCHAR) FROM operations WHERE target_id = ?", [rid]).fetchone()
                        record["row_bytes"].append(row)
                        ranked = tuple(c.node.id for c in response.results)
                        record["results"].append(len(ranked))
                        record["packed"].append(sum(len(g) for g in response.bundle.sections.values()))
                        record["replay_ok"] += receipt.replay_ranking() == ranked
                pair += 1
    finally:
        await stack.aclose()
        shutil.rmtree(base, ignore_errors=True)
    report = {"label": args.label, "pack": args.pack, "mode": args.mode, "rounds": args.rounds,
              "questions": len(questions), "pairs": pair,
              "load_1m": {"min": round(min(loads), 2), "median": round(statistics.median(loads), 2),
                          "max": round(max(loads), 2)}}
    for side, record in data.items():
        report[side] = {**summary(record["ms"]), "reads": len(record["ms"]),
                        "receipt_bytes_median": int(statistics.median(record["receipt_bytes"])),
                        "receipt_bytes_max": max(record["receipt_bytes"]),
                        "stored_row_bytes_median": int(statistics.median(record["row_bytes"])),
                        "stored_row_bytes_max": max(record["row_bytes"]),
                        "replay_ranking_ok": f"{record['replay_ok']}/{len(questions)}",
                        "results_median": statistics.median(record["results"]), "results_max": max(record["results"]),
                        "packed_median": statistics.median(record["packed"]), "packed_max": max(record["packed"])}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    full = {**report, "ms": {s: [round(v, 2) for v in d["ms"]] for s, d in data.items()},
            "fingerprints": {s: d["fingerprints"] for s, d in data.items()}}
    (out / f"{args.label}-{args.pack}-{args.mode}.json").write_text(json.dumps(full, indent=1))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", choices=sorted(PACKS), required=True)
    parser.add_argument("--mode", choices=["classic", "address_first"], required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--scratch", default="/tmp/task22/scratch")
    parser.add_argument("--out", default="/tmp/task22/reports-step2")
    asyncio.run(main(parser.parse_args()))

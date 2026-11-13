"""Task 24 bars 1 and 2: main and the change read the same questions in turns, one reader process each.

    ~/Sites/prism-graph-candidates/.venv/bin/python pairs.py --pack m3 --set '{}' --rounds 3 --label speed-m3 \
        --main /tmp/task24/main --change ~/Sites/prism-graph-candidates --out /tmp/task24/reports

Both readers start on their own fresh copy of the pack. Each question is read once by each side; the order flips
every pair, (main, change) then (change, main), so drift in load hits both. The 1-minute load average is recorded
before every pair. Round 0 also records each side's fingerprint (``reader.py``) for the same-output bar. The
questions are P8's 20 (research/graph-read-cost-2026-11/probes/stages.py), at reference time 2026-11-05 UTC.
"""
import argparse, json, os, statistics, subprocess, sys
from pathlib import Path

PACKS = {"m3": os.path.expanduser("~/Sites/prism-write-rate/data/write-rate-2026-11/m3-pack/pack"),
         "flat": os.path.expanduser("~/Sites/prism/data/read-cost-v1/packs/n20000-s1/pack")}
QUESTIONS = [
    "Where is the red mug now?", "Where are the house keys?", "Where did I leave the blue notebook?",
    "Where is the phone charger?", "Where are the reading glasses?", "Where is the tv remote?",
    "Where is the black umbrella?", "Where is the dog leash?", "Where is the screwdriver set?",
    "Where is the first aid kit?",
    "What is on the kitchen counter?", "What is on the office desk?", "What is in the garage workbench area?",
    "Is the front porch clear?", "What is in the laundry room?", "What changed since the last pass?",
    "Summarize the last hour.", "What happened in the hallway this morning?", "Did a delivery arrive?",
    "Is there a leak anywhere?",
]
HERE = Path(__file__).resolve().parent


def percentile(values, fraction):
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def start(tree, args, side):
    env = {**os.environ, "PYTHONPATH": f"{tree}/src:{tree}"}
    process = subprocess.Popen(
        [sys.executable, str(HERE / "reader.py"), "--tree", tree, "--pack", PACKS[args.pack],
         "--copy", f"{args.scratch}/{args.label}/{side}", "--set", args.set],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env, cwd=tree)
    ready = json.loads(process.stdout.readline())
    return process, ready


def ask(process, question, fingerprint):
    process.stdin.write(json.dumps({"question": question, "fingerprint": fingerprint}) + "\n")
    process.stdin.flush()
    return json.loads(process.stdout.readline())


def main(args):
    trees = {"main": os.path.expanduser(args.main), "change": os.path.expanduser(args.change)}
    readers = {}
    for side, tree in trees.items():
        readers[side] = start(tree, args, side)
    data = {side: {"ms": [], "fingerprints": {}} for side in trees}
    loads, saving, pair = [], [], 0
    try:
        for round_index in range(args.rounds):
            for question in QUESTIONS:
                loads.append(os.getloadavg()[0])
                order = ("main", "change") if pair % 2 == 0 else ("change", "main")
                times = {}
                for side in order:
                    answer = ask(readers[side][0], question, round_index == 0)
                    times[side] = answer["ms"]
                    data[side]["ms"].append(answer["ms"])
                    if round_index == 0:
                        data[side]["fingerprints"][question] = {k: v for k, v in answer.items() if k != "ms"}
                saving.append(times["main"] - times["change"])
                pair += 1
    finally:
        for process, _ready in readers.values():
            process.stdin.close()
            process.wait()
    same = {}
    for field in ("ranked", "packed", "context_sha256", "receipt_sha256", "tokens_used", "candidates_generated"):
        same[field] = sum(data["main"]["fingerprints"][q]["fingerprint"][field]
                          == data["change"]["fingerprints"][q]["fingerprint"][field] for q in QUESTIONS)
    for field in ("stored_receipt_sha256", "stored_receipt_masked_sha256", "stored_log_fields_sha256", "replay_ok"):
        same[field] = sum(data["main"]["fingerprints"][q][field] == data["change"]["fingerprints"][q][field]
                          for q in QUESTIONS)
    replay = {side: sum(bool(d["fingerprints"][q]["replay_ok"]) for q in QUESTIONS) for side, d in data.items()}
    sources = {side: d["fingerprints"][QUESTIONS[0]]["source_files_sha256"] for side, d in data.items()}
    changed_sources = sorted(k for k in (sources["main"] or {}) if sources["main"][k] != (sources["change"] or {}).get(k))
    report = {
        "label": args.label, "pack": args.pack, "set": json.loads(args.set), "rounds": args.rounds,
        "questions": len(QUESTIONS), "pairs": pair, "prme": {s: r[1]["prme"] for s, r in readers.items()},
        "load_1m": {"min": round(min(loads), 2), "median": round(statistics.median(loads), 2),
                    "max": round(max(loads), 2)},
        "same_of_20": same, "replay_ok": replay, "source_files_changed": changed_sources,
        "median_paired_saving_ms": round(statistics.median(saving), 1),
        "candidates_generated_main": [data["main"]["fingerprints"][q]["fingerprint"]["candidates_generated"]
                                      for q in QUESTIONS],
    }
    for side, record in data.items():
        report[side] = {"p50_ms": round(statistics.median(record["ms"]), 1),
                        "p95_ms": round(percentile(record["ms"], 0.95), 1),
                        "mean_ms": round(statistics.fmean(record["ms"]), 1), "reads": len(record["ms"])}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    full = {**report, "loads": [round(v, 2) for v in loads], "paired_saving_ms": [round(v, 2) for v in saving],
            "ms": {s: [round(v, 2) for v in d["ms"]] for s, d in data.items()},
            "fingerprints": {s: d["fingerprints"] for s, d in data.items()}}
    (out / f"{args.label}.json").write_text(json.dumps(full, indent=1))
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", choices=sorted(PACKS), required=True)
    parser.add_argument("--set", default="{}")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--label", required=True)
    parser.add_argument("--main", required=True)
    parser.add_argument("--change", required=True)
    parser.add_argument("--scratch", default="/tmp/task24/scratch")
    parser.add_argument("--out", default="/tmp/task24/reports")
    main(parser.parse_args())

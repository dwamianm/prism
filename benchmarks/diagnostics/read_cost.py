"""Measure the cost of one ``retrieve()`` at several store sizes, and fingerprint its output.

Robots need a read under 50 ms at the 95th percentile. This tool builds a synthetic home-robot store of a
given size, the same routine patrol observations the address-first thesis timed
(``memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md``, finding F), with a few rare events among them, and
times ``retrieve()`` with the current defaults. No model is called: memories are admitted with
``ingest_fast_many()`` and indexed with ``process_pending()``, which embed locally and never extract.

Choices, and why:

- Fixed seed and a fixed clock. Each memory's text, event time and order come from ``random.Random(seed)``,
  so two builds of one size store the same text. The build is kept under ``--root`` and reused, so a code
  change is timed and compared on the same pack files.
- The configuration is the evidence gate's (``gate_config``): current defaults, environment variables and
  ``.env`` ignored, the opportunistic organizer off. The organizer's background pass would otherwise rewrite
  the store between reads, and two runs would not read the same store.
- Warm reads. Each run opens the pack, answers one warm-up question, then answers every question
  ``--rounds`` times. The questions repeat, so the reported percentiles come from 60 questions times the
  rounds. Each read is timed around ``retrieve()`` alone.
- Each read is fingerprinted on its first round: the ranked node ids and scores, the packed node ids, the
  rendered context and the stored receipt. The receipt's request id is the one field that differs between two
  identical reads, so the fingerprint replaces it with a placeholder; nothing else is dropped. ``compare``
  checks two runs question by question and fails on any difference.
- ``--profile`` runs one more round under ``cProfile`` and writes the top functions by own and cumulative time,
  so the costs have names. Stage times come from wrapping the pipeline's stage functions, as the thesis's
  ``stage_timing.py`` did.

    uv run python -m benchmarks.diagnostics.read_cost run --label main --sizes 3000 20000 --profile
    uv run python -m benchmarks.diagnostics.read_cost compare main changed

Packs and reports live under the ignored ``data/read-cost-v1/``.
"""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import collections
from datetime import datetime, timedelta, timezone
import functools
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import pstats
import random
import statistics
import subprocess
import time

from benchmarks.diagnostics.product_packing import _quiet_offline_cli, gate_config
from prme import MemoryEngine

KIND = "prme-read-cost"
SCHEMA_VERSION = 1
USER = "robot-1"
BASE = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)
SPACING = timedelta(seconds=20)
ROOMS = ["hallway", "kitchen", "garage", "living room", "office", "bedroom"]
OBJECTS = ["red mug", "house keys", "blue notebook", "phone charger", "reading glasses", "tv remote",
           "black umbrella", "dog leash", "screwdriver set", "first aid kit"]
PLACES = ["kitchen counter", "dishwasher", "living room table", "office desk", "hallway shelf",
          "bedroom nightstand", "garage workbench", "bathroom cabinet", "front porch", "laundry room"]
PEOPLE = ["Dana", "Ravi", "Mei", "Tomas", "Aisha"]
RARE = [
    "Water is pooling under the kitchen sink.",
    "The front door was left open for eleven minutes.",
    "Smoke detector in the garage chirped: low battery.",
    "Grandma took her blood pressure pill at the dining table.",
    "A package was dropped on the front porch by a courier.",
    "The stove burner was on with no pot on it.",
    "The cat knocked a vase off the bookshelf and it broke.",
    "An unknown person stood at the back gate for two minutes.",
]
QUESTIONS = [
    "Is there a leak anywhere?", "Was any entrance left unlocked or open?", "Does any alarm need maintenance?",
    "Did she take her medication?", "Did a delivery arrive?", "Was any appliance left running unsafely?",
    "Did anything get broken?", "Was there a stranger near the house?",
    "Where is the red mug now?", "Where are the house keys?", "Where did I leave the blue notebook?",
    "Where is the phone charger?", "Where are the reading glasses?", "Where is the tv remote?",
    "Where is the black umbrella?", "Where is the dog leash?", "Where is the screwdriver set?",
    "Where is the first aid kit?",
    "What is the temperature in the office?", "What is the temperature in the kitchen?",
    "How bright is the garage?", "Is the hallway clear?", "Did anything move in the garage?",
    "Was the front door left open?", "Is anyone in the living room?", "What did the bedroom camera see?",
    "When was the last patrol of the kitchen?", "Has anything changed in the office today?",
    "What happened in the hallway this morning?", "Were there obstacles in the living room?",
    "What did Dana ask for?", "Who visited today?", "What did Ravi say about the garage?",
    "Did Mei come by?", "What did Tomas move?", "Where did Aisha put her bag?",
    "What was the warmest room?", "Which room was darkest?", "Were any doors different from before?",
    "Did the robot see any people?", "What is on the kitchen counter?", "What is on the office desk?",
    "What is in the garage workbench area?", "Is the front porch clear?", "What is in the laundry room?",
    "What changed since the last pass?", "Summarize the last hour.", "What happened yesterday?",
    "Anything unusual this week?", "Was there any noise at night?",
    "Did the dog go outside?", "When did the courier come?", "Is the stove off now?",
    "Is the vase still on the bookshelf?", "Was the back gate visited?", "Did the smoke detector beep?",
    "Is the sink dry now?", "Where is everything in the hallway?", "What is the light level in the bedroom?",
    "Tell me about the office.",
]
STAGES = ("analyze_query", "generate_candidates", "score_and_rank", "pack_context", "expand_session_context",
          "fold_claim_statements", "generate_summary_candidates")
CANDIDATE_STAGES = ("_generate_vector_candidates", "_generate_lexical_candidates", "_generate_graph_candidates",
                    "merge_candidates")
REQUEST_ID = "00000000-0000-0000-0000-000000000000"


def default_root() -> Path:
    from benchmarks import checkout

    return checkout.main_checkout() / "data" / "read-cost-v1"


def memory_text(rng: random.Random, index: int) -> str:
    """One observation: mostly routine patrol notes, with object moves, visitors and a few rare events."""
    room = ROOMS[index % len(ROOMS)]
    roll = rng.random()
    if roll < 0.002:
        return rng.choice(RARE)
    if roll < 0.08:
        return f"The {rng.choice(OBJECTS)} is on the {rng.choice(PLACES)}."
    if roll < 0.10:
        person = rng.choice(PEOPLE)
        return f"{person} came into the {room} and asked for the {rng.choice(OBJECTS)}."
    return rng.choice([
        f"{room.capitalize()} camera: no people, no obstacles, light level {rng.randint(1, 9)}.",
        f"Patrol pass through the {room}: floor clear, doors as before, temperature {rng.randint(18, 24)} C.",
        f"{room.capitalize()} check: nothing moved since the last pass.",
    ])


def items(size: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    return [{"content": memory_text(rng, index), "event_time": BASE + SPACING * index, "role": "system"}
            for index in range(size)]


def reference_time(size: int) -> datetime:
    return BASE + SPACING * size + timedelta(minutes=5)


async def build(pack: Path, size: int, seed: int) -> dict:
    pack.mkdir(parents=True)
    (pack / "lexical_index").mkdir()
    started = time.perf_counter()
    async with MemoryEngine.open(gate_config(pack)) as engine:
        batch = items(size, seed)
        for offset in range(0, size, 500):
            await engine.ingest_fast_many(batch[offset:offset + 500], user_id=USER)
        while (await engine.process_pending(user_id=USER, budget_ms=5000)).pending:
            pass
    record = {"size": size, "seed": seed, "seconds": round(time.perf_counter() - started, 1),
              "text_sha256": hashlib.sha256(json.dumps([item["content"] for item in batch]).encode()).hexdigest()}
    (pack.parent / "build.json").write_text(json.dumps(record, indent=1) + "\n")
    return record


class StageTimer:
    """Adds each wrapped stage function's wall time, as the thesis's stage timing did."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = collections.defaultdict(float)
        self.active = False

    def wrap(self, module, name: str) -> None:
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


def install_stage_timer() -> StageTimer:
    import prme.retrieval.candidates as candidates
    import prme.retrieval.pipeline as pipeline

    timer = StageTimer()
    for name in STAGES:
        timer.wrap(pipeline, name)
    for name in CANDIDATE_STAGES:
        timer.wrap(candidates, name)
    return timer


def fingerprint(response, receipt) -> dict:
    """Everything a caller reads from one retrieval, with the request id the only field replaced."""
    request_id = str(response.metadata.request_id)
    receipt_json = receipt.model_dump_json().replace(request_id, REQUEST_ID)
    context = response.bundle.render()
    return {
        "ranked": [[str(candidate.node.id), candidate.composite_score] for candidate in response.results],
        "packed": [str(candidate.node.id) for group in response.bundle.sections.values() for candidate in group],
        "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "receipt_sha256": hashlib.sha256(receipt_json.encode()).hexdigest(),
        "tokens_used": response.bundle.tokens_used,
        "candidates_generated": response.metadata.candidates_generated,
    }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def profile_text(profile: cProfile.Profile, sort: str, lines: int) -> str:
    stream = io.StringIO()
    stats = pstats.Stats(profile, stream=stream)
    stats.strip_dirs().sort_stats(sort).print_stats(lines)
    return stream.getvalue()


async def measure(pack: Path, size: int, rounds: int, timer: StageTimer, profile_path: Path | None) -> dict:
    when = reference_time(size)
    fingerprints: dict[str, dict] = {}
    seconds: list[float] = []
    async with MemoryEngine.open(gate_config(pack)) as engine:
        await engine.retrieve("warm-up question about the house", user_id=USER, reference_time=when)
        timer.seconds.clear()
        timer.active = True
        for round_index in range(rounds):
            for question in QUESTIONS:
                started = time.perf_counter()
                response = await engine.retrieve(question, user_id=USER, reference_time=when)
                seconds.append(time.perf_counter() - started)
                if round_index == 0:
                    timer.active = False
                    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=USER)
                    fingerprints[question] = fingerprint(response, receipt)
                    timer.active = True
        timer.active = False
        stages = {name: round(1000 * value / len(seconds), 2)
                  for name, value in sorted(timer.seconds.items(), key=lambda item: -item[1])}
        if profile_path is not None:
            profile = cProfile.Profile()
            profile.enable()
            for question in QUESTIONS:
                await engine.retrieve(question, user_id=USER, reference_time=when)
            profile.disable()
            profile_path.write_text(
                f"cProfile over one round of {len(QUESTIONS)} questions at {size} memories\n\n"
                + profile_text(profile, "tottime", 40) + "\n" + profile_text(profile, "cumulative", 60))
    milliseconds = [1000 * value for value in seconds]
    return {
        "size": size, "reads": len(milliseconds), "questions": len(QUESTIONS), "rounds": rounds,
        "p50_ms": round(statistics.median(milliseconds), 1), "p95_ms": round(percentile(milliseconds, 0.95), 1),
        "mean_ms": round(statistics.fmean(milliseconds), 1), "max_ms": round(max(milliseconds), 1),
        "stage_ms_per_read": stages, "fingerprints": fingerprints,
    }


def git_identity() -> dict:
    def run(*args: str) -> str:
        return subprocess.check_output(["git", *args], text=True).strip()

    return {"commit": run("rev-parse", "HEAD"), "src_tree": run("rev-parse", "HEAD:src"),
            "src_dirty": bool(run("status", "--porcelain", "--", "src"))}


async def run(label: str, sizes: list[int], seed: int, rounds: int, root: Path, profile: bool) -> dict:
    timer = install_stage_timer()
    out = root / "reports" / label
    out.mkdir(parents=True, exist_ok=True)
    report = {"kind": KIND, "schema_version": SCHEMA_VERSION, "label": label, "seed": seed, **git_identity(),
              "sizes": {}}
    for size in sizes:
        pack = root / "packs" / f"n{size}-s{seed}" / "pack"
        if not (pack / "memory.duckdb").exists():
            print(f"building {size} memories ...", flush=True)
            print(json.dumps(await build(pack, size, seed)), flush=True)
        result = await measure(pack, size, rounds, timer, out / f"profile-n{size}.txt" if profile else None)
        report["sizes"][str(size)] = result
        print(f"N={size} reads={result['reads']} p50={result['p50_ms']} ms p95={result['p95_ms']} ms "
              f"mean={result['mean_ms']} ms max={result['max_ms']} ms", flush=True)
        print("  " + "  ".join(f"{name}={value}" for name, value in result["stage_ms_per_read"].items()), flush=True)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def compare(root: Path, before: str, after: str) -> int:
    load = lambda label: json.loads((root / "reports" / label / "report.json").read_text())  # noqa: E731
    first, second = load(before), load(after)
    differences = 0
    for size, result in first["sizes"].items():
        other = second["sizes"].get(size)
        if other is None:
            print(f"N={size}: missing from {after}")
            differences += 1
            continue
        changed = [question for question in result["fingerprints"]
                   if result["fingerprints"][question] != other["fingerprints"].get(question)]
        differences += len(changed)
        print(f"N={size}: {len(result['fingerprints']) - len(changed)}/{len(result['fingerprints'])} identical; "
              f"p50 {result['p50_ms']} -> {other['p50_ms']} ms, p95 {result['p95_ms']} -> {other['p95_ms']} ms "
              f"({other['p95_ms'] / result['p95_ms']:.2f}x)")
        for question in changed[:10]:
            print(f"  differs: {question!r}")
    return 1 if differences else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=None, help="Where packs and reports live")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("run", help="Build missing packs, then time and fingerprint retrieve()")
    command.add_argument("--label", required=True)
    command.add_argument("--sizes", type=int, nargs="+", default=[3000, 20000])
    command.add_argument("--seed", type=int, default=1)
    command.add_argument("--rounds", type=int, default=3)
    command.add_argument("--profile", action="store_true", help="Write a cProfile of one more round per size")
    command = commands.add_parser("compare", help="Check two runs read identically and compare their times")
    command.add_argument("before")
    command.add_argument("after")
    args = parser.parse_args(argv)
    _quiet_offline_cli()
    root = default_root() if args.root is None else args.root
    if args.command == "run":
        asyncio.run(run(args.label, args.sizes, args.seed, args.rounds, root, args.profile))
    else:
        raise SystemExit(compare(root, args.before, args.after))


if __name__ == "__main__":
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    main()

"""M1 (PROTOCOL.md): the current path's sustained rate and stage profile at 400, 4,000 and 20,000 stored.

Run from the worktree root:
    PYTHONPATH=. uv run python research/write-rate-2026-11/m1_current.py --out data/write-rate-2026-11/m1.json

``--total`` and ``--windows`` shrink the run for a smoke test. The pack is kept in ``--pack`` (default a new temporary
directory) so M4 can read it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


async def main(args) -> dict:
    common.blocked_environment()
    common.instrument()
    if args.batch_lexical:
        common.batch_lexical_commit()
    provider = await common.embedding_provider()
    directory = args.pack or tempfile.mkdtemp(prefix="prme-write-rate-m1-")
    os.makedirs(directory, exist_ok=True)
    # M3 runs the same measurement with the one prototype change on (PROTOCOL.md, M3).
    engine, guard = await common.new_engine(directory, provider, **(common.PROTOTYPE if args.prototype else {}))
    messages = common.observations(args.total)
    windows = sorted((start, start + size) for start, size in args.windows)
    report = {"pack": directory, "total": args.total, "path": "prototype" if args.prototype else "current",
              "settings": common.PROTOTYPE if args.prototype else {}, "batch_lexical_commit": args.batch_lexical,
              "windows": [], "progress": []}
    written = 0
    started_all = time.perf_counter()
    try:
        for start, end in windows:
            while written < start:
                # Progress every 1,000 stored, so the rate is seen as the store grows.
                step_end = min(start, (written // 1000 + 1) * 1000)
                step_started = time.perf_counter()
                await common.write_current(engine, messages[written:step_end])
                step_seconds = time.perf_counter() - step_started
                report["progress"].append({"from": written, "to": step_end, "seconds": round(step_seconds, 1),
                                           "facts_per_second": round((step_end - written) / step_seconds, 2),
                                           "elapsed": round(time.perf_counter() - started_all, 1)})
                written = step_end
                print(f"written {written} at {time.perf_counter() - started_all:.0f} s, "
                      f"{report['progress'][-1]['facts_per_second']} facts/s", flush=True)
                with open(args.out + ".partial", "w") as handle:
                    json.dump(report, handle, indent=2)
            before = common.snapshot()
            started = time.perf_counter()
            await common.write_current(engine, messages[start:end])
            seconds = time.perf_counter() - started
            after = common.snapshot()
            written = end
            facts = end - start
            change = common.delta(before, after)
            window = {
                "stored_at_start": start, "facts": facts, "seconds": round(seconds, 3),
                "facts_per_second": round(facts / seconds, 2), "ms_per_fact": round(1000 * seconds / facts, 2),
                "stage_ms_per_fact": {key: round(1000 * value / facts, 3)
                                      for key, value in sorted(change["time"].items(), key=lambda kv: -kv[1])},
                "stage_calls_per_fact": {key: round(value / facts, 3) for key, value in sorted(change["calls"].items())},
                "sql_per_fact": {key: round(value / facts, 3)
                                 for key, value in sorted(change["sql"].items(), key=lambda kv: -kv[1])},
                "index_work_per_fact": {key: round(value / facts, 3) for key, value in sorted(change["counts"].items())},
            }
            report["windows"].append(window)
            with open(args.out + ".partial", "w") as handle:
                json.dump(report, handle, indent=2)
            print(f"window {start}-{end}: {window['facts_per_second']} facts/s, {window['ms_per_fact']} ms/fact, "
                  f"{window['sql_per_fact'].get('(total)')} SQL/fact", flush=True)
        status = await engine.process_pending(user_id=common.USER, budget_ms=0)
        report["pending_after"] = status.pending
        report["failed_after"] = status.failed
        report["model_calls_missed"] = len(guard.misses)
        assert not guard.misses, f"a model was called: {guard.misses[:3]}"
    finally:
        await engine.close()
    report["seconds_total"] = round(time.perf_counter() - started_all, 1)
    return report


def window_arg(text: str) -> tuple[int, int]:
    start, size = text.split(":")
    return int(start), int(size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=20_500)
    parser.add_argument("--windows", type=window_arg, nargs="+", default=[(400, 200), (4000, 200), (20000, 500)],
                        help="start:size pairs; the store holds 'start' observations when each window begins")
    parser.add_argument("--pack", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--prototype", action="store_true", help="M3: the prototype change on (common.PROTOTYPE)")
    parser.add_argument("--batch-lexical", action="store_true",
                        help="measurement only: one lexical commit per batch instead of per fact (upper bound)")
    args = parser.parse_args()
    result = asyncio.run(main(args))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({key: value for key, value in result.items() if key != "windows"}, indent=2))

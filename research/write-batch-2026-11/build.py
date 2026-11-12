"""P5 bar 3 inputs: build a pack through one write path and keep closed copies at given store sizes.

    PYTHONPATH=. uv run python research/write-batch-2026-11/build.py --path batch --to 20500 \
        --snapshots 400 4000 20500 --dir /tmp/p5/build/batch

P2's ``m4_build.py`` with the batch path as a choice. No timing is taken; the copies are what ``compare.py`` reads.
The writes are P2's ``write_current`` (100 caller-stated facts a call, waiting, then ``process_pending``).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

import batch as harness

common = harness.common


async def main(args) -> None:
    common.blocked_environment()
    provider = await common.embedding_provider()
    work = os.path.join(args.dir, "work")
    os.makedirs(work, exist_ok=True)
    started = time.perf_counter()
    engine, guard, written = await common.write_steps(
        work, provider, common.observations(args.to), sorted(set(args.snapshots) | {args.to}), args.dir,
        settings=harness.PATHS[args.path])
    try:
        status = await engine.process_pending(user_id=common.USER, budget_ms=0)
        assert status.pending == 0 and status.failed == 0, status
        assert engine._lexical_index._uncommitted == 0
    finally:
        await engine.close()
    print(f"built {written} through the {args.path} path in {time.perf_counter() - started:.0f} s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", choices=tuple(harness.PATHS), required=True)
    parser.add_argument("--to", type=int, required=True)
    parser.add_argument("--snapshots", type=int, nargs="*", default=[])
    parser.add_argument("--dir", required=True)
    asyncio.run(main(parser.parse_args()))

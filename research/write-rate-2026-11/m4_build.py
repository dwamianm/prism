"""M4 inputs (PROTOCOL.md): build a pack through one path and keep closed copies at given store sizes.

    PYTHONPATH=. uv run python research/write-rate-2026-11/m4_build.py --path current --to 4200 \
        --snapshots 400 600 4200 --dir data/write-rate-2026-11/current-a

No timing is taken here; the copies are what M2 and M4 read.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


async def main(args) -> None:
    common.blocked_environment()
    provider = await common.embedding_provider()
    settings = common.PROTOTYPE if args.path == "prototype" else {}
    work = os.path.join(args.dir, "work")
    os.makedirs(work, exist_ok=True)
    started = time.perf_counter()
    engine, guard, written = await common.write_steps(
        work, provider, common.observations(args.to), sorted(set(args.snapshots) | {args.to}), args.dir,
        settings=settings)
    try:
        status = await engine.process_pending(user_id=common.USER, budget_ms=0)
        assert status.pending == 0 and status.failed == 0, status
    finally:
        await engine.close()
    print(f"built {written} through the {args.path} path in {time.perf_counter() - started:.0f} s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", choices=("current", "prototype"), required=True)
    parser.add_argument("--to", type=int, required=True)
    parser.add_argument("--snapshots", type=int, nargs="*", default=[])
    parser.add_argument("--dir", required=True)
    asyncio.run(main(parser.parse_args()))

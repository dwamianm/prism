"""Time every organizer job on PostgreSQL at a fixed corpus size (issue #518).

The hosted service schedules one ``organize()`` pass per scope. This tool
measures what each registered job costs when it runs over one tenant's store
at 1,000 and 10,000 nodes, first over the seeded corpus (full) and then over a
ten-record increment. It never starts a server: point it at a temporary
PostgreSQL with ``--database-url`` or ``PRME_TEST_DATABASE_URL``, and it
creates no roles or databases.

This tool times each default job on its own with the full budget, then again
after a ten-record increment. A real ``organize()`` pass shares one budget
across the registry; the page reports the per-job sum so a scheduled budget
can be sized from it. Every job except ``index_compaction`` reads at most 500
nodes per pass (100 for deduplicate and alias_resolve), and
``index_compaction`` bounds its stale query to 500 rows. The report shows each
job's ``processed``. A pass is bounded maintenance, not a corpus scan.

Usage:

    uv run python -m benchmarks.diagnostics.organizer_timing \
        --database-url postgresql://user@127.0.0.1:55432/prme_test \
        --sizes 1000,10000 --output benchmarks/results/research/2026-11-09/ORGANIZER-JOBS-518.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from prme.config import OrganizerConfig, PRMEConfig
from prme.models.nodes import MemoryNode
from prme.organizer.jobs import DEFAULT_JOBS
from prme.storage.engine import MemoryEngine
from prme.types import NodeType


async def _store(engine: MemoryEngine, owner: str, content: str, **kwargs) -> None:
    await engine.store(content, user_id=owner, **kwargs)


async def seed(engine: MemoryEngine, owner: str, count: int) -> dict:
    """Seed one owner with a mix every job can examine.

    The fixed extras are 40 duplicate nodes, 40 alias entities, 5 consolidation
    candidates and 100 aged tentative facts. The rest are notes and facts, so
    the store holds exactly ``count`` graph nodes for the owner.
    """
    fixed_extras = 40 + 40 + 5 + 100
    notes = max(0, count - fixed_extras - 2 * (count // 20))
    for index in range(notes):
        kwargs = {"ttl_days": 0} if index % 50 == 0 else {}
        await _store(
            engine, owner, f"memory {index} about telescopes, cities and travel",
            node_type=NodeType.NOTE, **kwargs,
        )
    for index in range(count // 20):
        await _store(
            engine, owner, f"entity {index}", node_type=NodeType.ENTITY,
        )
    for index in range(count // 20):
        await _store(
            engine, owner, f"fact {index} about the project", node_type=NodeType.FACT,
        )

    stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    provenance = uuid4()
    for index in range(20):
        for _ in range(2):
            await engine._graph_store.create_node(MemoryNode(
                content=f"duplicate fact {index}", user_id=owner,
                node_type=NodeType.FACT, valid_from=stamp,
                evidence_refs=[provenance],
            ))
    for index in range(20):
        await _store(engine, owner, "PostgreSQL", node_type=NodeType.ENTITY)
        await _store(engine, owner, "postgres", node_type=NodeType.ENTITY)
    for _ in range(5):
        await _store(engine, owner, "the telescope is blue", node_type=NodeType.NOTE)

    old = datetime.now(timezone.utc) - timedelta(days=400)
    for index in range(100):
        await engine._graph_store.create_node(MemoryNode(
            content=f"aged tentative fact {index}", user_id=owner,
            node_type=NodeType.FACT, created_at=old, last_reinforced_at=old,
            evidence_refs=[provenance],
        ))
    return {"seeded": count, "owner": owner}


async def _pass(engine: MemoryEngine, owner: str, budget_ms: int) -> dict:
    """Time each default job on its own with its own budget.

    One shared ``organize()`` pass stops later jobs when the budget runs out,
    which is right for a scheduled run and wrong for a per-job table. Giving
    every job the full budget measures the job, not its position in the order.
    """
    started = datetime.now(timezone.utc)
    per_job = {}
    skipped = []
    for job_name in DEFAULT_JOBS:
        result = await engine.organize(
            user_id=owner, jobs=[job_name], budget_ms=budget_ms,
        )
        job = result.per_job.get(job_name)
        if job is None:
            skipped.append(job_name)
            continue
        per_job[job_name] = {
            "duration_ms": job.duration_ms,
            "processed": job.nodes_processed,
            "modified": job.nodes_modified,
            "errors": job.errors,
        }
    return {
        "wall_ms": (datetime.now(timezone.utc) - started).total_seconds() * 1000,
        "jobs_run": list(per_job),
        "jobs_skipped": skipped,
        "per_job": per_job,
    }


async def measure(database_url: str, sizes: list[int], budget_ms: int) -> dict:
    config = PRMEConfig(
        database_url=database_url,
        organizer=OrganizerConfig(opportunistic_enabled=False),
        materialization_budget_ms=5000,
    )
    engine = await MemoryEngine.create(config)
    try:
        async with engine._graph_store._pool.acquire() as conn:
            server = await conn.fetchval("SELECT version()")
        runs = {}
        for size in sizes:
            owner = f"organizer-timing-{size}-{uuid4().hex[:8]}"
            await seed(engine, owner, size)
            full = await _pass(engine, owner, budget_ms)
            for index in range(10):
                await _store(
                    engine, owner, f"incremental memory {index} about travel",
                    node_type=NodeType.NOTE,
                )
            incremental = await _pass(engine, owner, budget_ms)
            runs[str(size)] = {"full": full, "incremental": incremental}
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "machine": {
                "platform": platform.platform(),
                "processor": platform.processor(),
                "python": sys.version.split()[0],
                "postgres": server,
            },
            "budget_ms": budget_ms,
            "runs": runs,
        }
    finally:
        await engine.close()


def markdown(report: dict) -> str:
    lines = [
        "| Nodes | Job | Full ms | Full processed | Incremental ms | Incremental processed |",
        "|---|---|---|---|---|---|",
    ]
    for size, run in report["runs"].items():
        for job in DEFAULT_JOBS:
            full = run["full"]["per_job"].get(job)
            incremental = run["incremental"]["per_job"].get(job)
            def cell(value, key):
                if value is None:
                    return "skipped"
                return str(value[key])
            lines.append(
                f"| {size} | {job} | {cell(full, 'duration_ms')} | "
                f"{cell(full, 'processed')} | {cell(incremental, 'duration_ms')} | "
                f"{cell(incremental, 'processed')} |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("PRME_TEST_DATABASE_URL"),
        help="Temporary PostgreSQL URL. Defaults to PRME_TEST_DATABASE_URL.",
    )
    parser.add_argument("--sizes", default="1000,10000")
    parser.add_argument("--budget-ms", type=int, default=120000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not args.database_url:
        parser.error("pass --database-url or set PRME_TEST_DATABASE_URL")
    sizes = [int(value) for value in args.sizes.split(",") if value.strip()]
    report = asyncio.run(measure(args.database_url, sizes, args.budget_ms))
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    print()
    print(markdown(report))


if __name__ == "__main__":
    main()

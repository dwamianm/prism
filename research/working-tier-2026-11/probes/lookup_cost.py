"""What an address lookup costs today, at a given store size (get_assertion_state)."""
import asyncio, os, statistics, sys, time


async def main(pack, subject, predicate, samples):
    sys.path.insert(0, os.path.expanduser("~/Sites/prism-working-tier"))
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
    from datetime import datetime, timezone
    from prme.models.temporal import AssertionStateQuery
    from prme.storage.embedding import create_embedding_provider
    from prme.storage.engine import MemoryEngine
    from prme.types import Scope
    from tests.contract.harness import product_config

    directory = os.path.dirname(pack.rstrip("/"))
    config = product_config(directory)
    provider = create_embedding_provider(config.embedding)
    engine = await MemoryEngine.create(config, embedding_provider=provider)
    query = AssertionStateQuery(subject=subject, predicate=predicate, scope=Scope.PERSONAL,
                               valid_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    times = []
    for _ in range(samples):
        t = time.perf_counter()
        state = await engine.get_assertion_state(query, user_id="robot-1")
        times.append(1000 * (time.perf_counter() - t))
    ordered = sorted(times)
    print({"count": len(ordered), "p50": round(ordered[len(ordered)//2], 1),
           "p95": round(ordered[int(0.95*len(ordered))], 1), "max": round(ordered[-1], 1),
           "scanned_nodes": state.scanned_nodes, "matched": state.matched_records,
           "status": state.status}, flush=True)
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])))

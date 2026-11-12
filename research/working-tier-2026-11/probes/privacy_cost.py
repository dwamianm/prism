"""Cost of the privacy range check the read path runs on every retrieve()."""
import asyncio, os, sys, time


async def main(pack, samples):
    sys.path.insert(0, os.path.expanduser("~/Sites/prism-working-tier"))
    os.environ.update({"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "sk-ant-blocked",
                       "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"})
    from prme.storage.embedding import create_embedding_provider
    from prme.storage.engine import MemoryEngine
    from tests.contract.harness import product_config

    directory = os.path.dirname(pack.rstrip("/"))
    config = product_config(directory)
    provider = create_embedding_provider(config.embedding)
    engine = await MemoryEngine.create(config, embedding_provider=provider)
    times = []
    for _ in range(samples):
        t = time.perf_counter()
        await engine._privacy.repository.require_legacy_range("robot-1", "personal")
        times.append(1000 * (time.perf_counter() - t))
    ordered = sorted(times)
    print({"count": len(ordered), "p50": round(ordered[len(ordered)//2], 2),
           "p95": round(ordered[int(0.95*len(ordered))], 2), "max": round(ordered[-1], 2)}, flush=True)
    await engine.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))

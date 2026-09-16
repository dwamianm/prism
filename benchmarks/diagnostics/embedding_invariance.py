"""Measure real local embedding cache invariance and batch throughput."""

import argparse
import asyncio
import hashlib
from importlib.metadata import version
import inspect
import json
from pathlib import Path
import statistics
import sys
import time

from benchmarks.diagnostics._process import checked_report


async def run():
    import numpy as np
    from prme.storage.embedding import CachedEmbeddingProvider, FastEmbedProvider

    texts = [
        "Aurora requires deployment approval.",
        "Aurora keeps nightly backups for 30 days.",
        "First line.\nSecond line.",
        "Café notes: naïve examples.",
    ]
    provider = FastEmbedProvider()
    cold, warm = CachedEmbeddingProvider(provider), CachedEmbeddingProvider(provider)
    first = np.asarray(await cold.embed(texts))
    await warm.embed(texts[1:])
    second = np.asarray(await warm.embed(texts))
    single = np.asarray([(await provider.embed([text]))[0] for text in texts])
    reversed_batch = np.asarray(await provider.embed(list(reversed(texts))))[::-1]
    differences = {
        "cold_vs_partial_cache": float(np.max(np.abs(first - second))),
        "batch_vs_individual": float(np.max(np.abs(first - single))),
        "input_order": float(np.max(np.abs(first - reversed_batch))),
    }
    timings = {}
    for label, corpus in {
        "short": texts * 16,
        "mixed_length": [texts[i % 4] * (1 + i % 20) for i in range(64)],
    }.items():
        samples = {1: [], 256: []}
        for repetition in range(4):
            # Alternate order; exclude the first pair as warmup.
            for batch_size in [1, 256] if repetition % 2 else [256, 1]:
                start = time.perf_counter()
                vectors = list(provider._model.embed(corpus, batch_size=batch_size))
                elapsed = time.perf_counter() - start
                assert len(vectors) == len(corpus)
                if repetition:
                    samples[batch_size].append(elapsed)
        timings[label] = {
            "texts": len(corpus),
            "corpus_sha256": hashlib.sha256(json.dumps(corpus).encode()).hexdigest(),
            "seconds": samples,
            "median_seconds": {k: statistics.median(v) for k, v in samples.items()},
        }
    model_dir = Path(provider._model.model._model_dir)
    return {
        "passed": all(value == 0 for value in differences.values()),
        "maximum_absolute_differences": differences,
        "package_path": inspect.getfile(FastEmbedProvider),
        "provider_source_sha256": hashlib.sha256(
            Path(inspect.getfile(FastEmbedProvider)).read_bytes()
        ).hexdigest(),
        "versions": {
            name: version(name) for name in ["fastembed", "onnxruntime", "numpy"]
        },
        "model": provider.model_name,
        "model_version": provider.model_version,
        "model_assets_sha256": {
            str(path.relative_to(model_dir)): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(model_dir.rglob("*"))
            if path.is_file()
        },
        "timings": timings,
        "limits": "Four authored invariance inputs and two small timing corpora on one host. "
        "No cross-hardware bitwise guarantee, full ingestion throughput or answer-quality claim.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = (
            asyncio.run(run())
            if args.worker
            else checked_report(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.diagnostics.embedding_invariance",
                    "--worker",
                ],
                timeout=240,
            )
        )
    except Exception as exc:
        result = {"passed": False, "error_type": type(exc).__name__}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

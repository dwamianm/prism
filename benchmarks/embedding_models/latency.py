"""Embedding time per text on one ONNX thread, the model's size and the memory it holds (``GATE.md``, measure 3).

Each model runs in its own process so its memory is its own: the provider the engine builds (``FastEmbedProvider`` with
``threads=1``, one text per ONNX call), a warm-up, then every text of a fixed sample embedded one at a time.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

from benchmarks.embedding_models.models import MODELS

SAMPLE = 400  # short memory sentences: the 189 core memories, then look-alikes
TURNS = 100  # ordinary conversation turns, for the longer-text figure


def sample_texts() -> tuple[list[str], list[str]]:
    from benchmarks.graph_oracle.scale import conversation_pad, load_core, make_clones

    _, core = load_core()
    clones, _, _ = make_clones(core, SAMPLE - len(core.memories))
    sentences = [memory.text for memory in core.memories] + [memory.text for memory, _ in clones]
    return sentences, conversation_pad(TURNS)


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * share
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def current_rss_mb() -> float:
    return int(subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout.strip()) / 1024


def model_dir(name: str) -> Path | None:
    """The folder FastEmbed keeps a registered model's files in."""
    from fastembed import TextEmbedding

    for description in TextEmbedding.list_supported_models():
        if description["model"] == name:
            import tempfile

            root = Path(os.environ.get("FASTEMBED_CACHE_PATH") or Path(tempfile.gettempdir()) / "fastembed_cache")
            wanted = "models--" + description["sources"]["hf"].replace("/", "--")
            return next((path for path in root.iterdir() if path.name.lower() == wanted.lower()), None)
    return None


def directory_mb(path: Path | None) -> float | None:
    if path is None:
        return None
    # The files are links into a shared blob folder; count each real file once.
    files = {item.resolve() for item in path.rglob("*") if item.resolve().is_file()}
    return round(sum(file.stat().st_size for file in files) / 1e6, 1)


def measure(key: str) -> dict:
    """One model's figures. Run in a fresh process."""
    from prme.storage.embedding import FastEmbedProvider

    model = MODELS[key]
    sentences, turns = sample_texts()
    before = current_rss_mb()
    provider = FastEmbedProvider(model.name, dimension=model.dimension, threads=1)
    started = time.perf_counter()
    provider._embed_sync(["warm up the model"])  # downloads on first use, then loads
    loaded = time.perf_counter() - started
    for text in sentences[:10]:
        provider._embed_sync([text])
    after_load = current_rss_mb()
    seconds = []
    for text in sentences:
        began = time.perf_counter()
        provider._embed_sync([text])
        seconds.append(time.perf_counter() - began)
    turn_seconds = []
    for text in turns:
        began = time.perf_counter()
        provider._embed_sync([text])
        turn_seconds.append(time.perf_counter() - began)
    ms = [value * 1000 for value in seconds]
    turn_ms = [value * 1000 for value in turn_seconds]
    return {
        "model": key, "name": model.name, "dimension": model.dimension, "texts": len(sentences),
        "ms": {"p50": round(statistics.median(ms), 2), "p95": round(percentile(ms, 0.95), 2), "mean": round(statistics.fmean(ms), 2),
               "max": round(max(ms), 1)},
        "turns": {"texts": len(turn_ms), "p50": round(statistics.median(turn_ms), 2), "p95": round(percentile(turn_ms, 0.95), 2),
                  "mean_chars": round(statistics.fmean(len(text) for text in turns))},
        "mean_chars_sentences": round(statistics.fmean(len(text) for text in sentences)),
        "load_seconds": round(loaded, 1), "rss_mb_before": round(before), "rss_mb_loaded": round(after_load),
        "rss_mb_added": round(after_load - before), "peak_rss_mb": round(current_rss_mb()),
        "download_mb": directory_mb(model_dir(model.name)), "load_average": round(os.getloadavg()[0], 1),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.embedding_models latency")
    parser.add_argument("--models", nargs="+", choices=list(MODELS), default=list(MODELS))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--one", action="store_true", help="Internal: measure one model in this process and print JSON")
    args = parser.parse_args(argv)
    if args.one:
        print(json.dumps(measure(args.models[0])))
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    results = {}
    for key in args.models:
        run = subprocess.run([sys.executable, "-m", "benchmarks.embedding_models", "latency", "--one", "--models", key, "--out", str(args.out)],
                             capture_output=True, text=True, check=False)
        if run.returncode:
            raise SystemExit(run.stderr[-2000:])
        results[key] = json.loads(run.stdout.strip().splitlines()[-1])
        print(key, results[key]["ms"], f"{results[key]['rss_mb_added']} MB added", file=sys.stderr)
        args.out.write_text(json.dumps(results, indent=1) + "\n")

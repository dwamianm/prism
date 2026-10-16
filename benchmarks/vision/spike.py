"""Visual memory harness (spike 2026-10-15; RFC-0018 phase 1 harness since).

Pipeline: video and photo sources -> ``MemoryEngine.observe()`` (keyframes,
local vision model descriptions, repeat filter, admission through
``ingest()`` with extraction or ``store()``) -> questions answered by a local
reader over ``retrieve()`` context -> local judge. Every model call goes to
the local Ollama server; nothing is billed. Raw outputs live under
``benchmarks/vision/runs/`` (ignored by git).

The spike's own pipeline code moved into ``src/prme/vision/`` with RFC-0018
phase 1; this harness drives that code through the engine, so a run here
measures the product path. Two things stay here because they are
measurement tools, not product behavior: the describe cache (keyed by the
frame's SHA-256, the vision model and the prompt version, so a second run
with another ``--mode`` or extraction model pays nothing for descriptions)
and the question set with its reader and judge.

Usage::

    uv run python -m benchmarks.vision.spike corpus --run spike-1
    uv run python -m benchmarks.vision.spike keyframes --run spike-1 --sources meeting,tahoe
    uv run python -m benchmarks.vision.spike run --run spike-1 --sources meeting,photos,tahoe \\
        --mode ingest --vision-model qwen3.5:9b --extract-model prme-qwen3.5:35b-a3b-8k
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import statistics
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import duckdb
import httpx
import ollama

from benchmarks.vision import corpus as corpus_module
from prme.config import VisionConfig
from prme.vision.describe import DESCRIBE_PROMPT_VERSION
from prme.vision.frames import decode_video_frames
from prme.vision.keyframes import select_keyframes
from prme.vision.observe import ObservationReport, observe_source
from prme.vision.provider import OllamaVisionProvider

RUNS = Path(__file__).resolve().parent / "runs"
CACHE = RUNS / "_describe_cache"
QUESTIONS = Path(__file__).resolve().parent / "questions.json"
OLLAMA_HOST = "http://127.0.0.1:11434"
USER_ID = "viewer"

READER_SYSTEM = ("You answer questions from a personal memory. Use only the memory context. "
                 "If the context does not contain the answer, reply exactly: I don't know. "
                 "Answer in one short sentence.")


class CachingVisionProvider:
    """The Ollama vision provider with a description cache on disk.

    Cache entries are keyed by the frame's SHA-256, the model and the prompt
    version, as the spike's were, so the ``v2`` entries written by the spike
    runs are reused as long as the prompt text is unchanged.
    """

    def __init__(self, model: str, *, host: str) -> None:
        self._inner = OllamaVisionProvider(model, base_url=host)
        self._folder = CACHE / model.replace(":", "_") / f"v{DESCRIBE_PROMPT_VERSION}"
        self._folder.mkdir(parents=True, exist_ok=True)
        self.records: list[dict] = []

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def describe(self, image: bytes, *, prompt: str) -> str:
        sha = hashlib.sha256(image).hexdigest()
        cached = self._folder / f"{sha}.json"
        if cached.exists():
            record = json.loads(cached.read_text())
            self.records.append({**record, "cached": True})
            return record["text"]
        started = time.perf_counter()
        text = await self._inner.describe(image, prompt=prompt)
        record = {"text": text, "seconds": round(time.perf_counter() - started, 2), "model": self.model_name,
                  "prompt_version": DESCRIBE_PROMPT_VERSION}
        cached.write_text(json.dumps(record, indent=2) + "\n")
        self.records.append({**record, "cached": False})
        return text


def vision_config(args: argparse.Namespace) -> VisionConfig:
    return VisionConfig(enabled=True, model=args.vision_model, base_url=OLLAMA_HOST,
                        extract=args.mode == "ingest", fps=args.fps, change_threshold=args.change_threshold,
                        max_gap_seconds=args.max_gap, repeat_similarity=args.repeat_similarity)


def engine_config(pack: Path, extract_model: str, vision: VisionConfig):
    """Production retrieval defaults, isolated from .env, extracting through the local Ollama server."""
    from benchmarks.diagnostics.extracted_packs import build_config

    return build_config(pack, model=extract_model).model_copy(update={"vision": vision})


def sources_of(manifest: dict, sources: list[str]) -> list[tuple[str, Path, datetime]]:
    """(source name, path, capture clock) for every file the run observes, in order."""
    entries: list[tuple[str, Path, datetime]] = []
    for name in ("meeting", "tahoe"):
        if name in sources:
            info = manifest[name]
            entries.append((name, Path(info["path"]), datetime.fromisoformat(info["start"])))
    if "photos" in sources:
        for photo in manifest["photos"]:
            entries.append(("photos", Path(photo["path"]), datetime.fromisoformat(photo["taken"])))
    return entries


def keyframes_only(run: Path, entries: list[tuple[str, Path, datetime]], args: argparse.Namespace) -> dict:
    """Decode and select keyframes without a model call, and write the decision log per video."""
    stats: dict[str, dict] = {}
    for name, path, _ in entries:
        if name == "photos":
            stats.setdefault("photos", {"photos": 0})["photos"] += 1
            continue
        with tempfile.TemporaryDirectory(prefix="prme-keyframes-") as tmp:
            frames = decode_video_frames(path, Path(tmp), fps=args.fps, width=1280)
            decisions = select_keyframes(frames, fps=args.fps, threshold=args.change_threshold, max_gap=args.max_gap)
        (run / f"keyframes_{name}.json").write_text(json.dumps([d.to_dict() for d in decisions], indent=1) + "\n")
        changes = [d.change for d in decisions if d.change is not None]
        stats[name] = {"frames": len(decisions), "keyframes": sum(1 for d in decisions if d.kept),
                       "keyframe_times_s": [d.time_s for d in decisions if d.kept],
                       "change_median": round(statistics.median(changes), 3) if changes else None,
                       "change_max": max(changes) if changes else None}
    return stats


async def build_pack(run: Path, entries: list[tuple[str, Path, datetime]], args: argparse.Namespace,
                     ) -> tuple[list[ObservationReport], list[dict]]:
    from prme.storage.engine import MemoryEngine

    pack = run / "pack"
    if pack.exists():
        shutil.rmtree(pack)
    (pack / "lexical_index").mkdir(parents=True)
    provider = CachingVisionProvider(args.vision_model, host=OLLAMA_HOST)
    reports: list[ObservationReport] = []
    async with MemoryEngine.open(engine_config(pack, args.extract_model, vision_config(args))) as engine:
        # The harness owns the provider so descriptions are cached across runs.
        engine._vision_provider_instance = provider
        for name, path, start in entries:
            report = await observe_source(engine, path, user_id=USER_ID, captured_at=start)
            reports.append(report)
            (run / f"keyframes_{name}.json").write_text(
                json.dumps([d.to_dict() for d in report.keyframe_log], indent=1) + "\n")
            for record in report.observations:
                print(f"  {args.mode} {name} {report.source} t={record.time_s} {record.status}"
                      f" describe={record.describe_seconds}s admit={record.admit_seconds}s"
                      f" similarity={record.similarity}"
                      f"{' ERROR ' + record.error if record.error else ''}"
                      f" {(record.description or '')[:80]!r}", flush=True)
    with (run / "observations.jsonl").open("w") as handle:
        for report in reports:
            for record in report.observations:
                handle.write(json.dumps({"source": report.source, "kind": report.kind, **record.to_dict()}) + "\n")
    return reports, provider.records


def pack_stats(pack: Path) -> dict:
    conn = duckdb.connect(str(pack / "memory.duckdb"), read_only=True)
    try:
        nodes = conn.execute("SELECT node_type, count(*) FROM nodes GROUP BY 1 ORDER BY 1").fetchall()
        edges = conn.execute("SELECT edge_type, count(*) FROM edges GROUP BY 1 ORDER BY 1").fetchall()
        events = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        sample = conn.execute("SELECT node_type, content FROM nodes ORDER BY node_type, content LIMIT 120").fetchall()
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        thumbs = conn.execute("SELECT count(*), coalesce(sum(size), 0) FROM blobs").fetchone() if "blobs" in tables else (0, 0)
    finally:
        conn.close()
    files = [path for path in pack.rglob("*") if path.is_file()]
    return {"events": events, "nodes": dict(nodes), "edges": dict(edges), "pack_bytes": sum(p.stat().st_size for p in files),
            "thumbnails": thumbs[0], "thumbnail_bytes": int(thumbs[1]),
            "node_sample": [{"type": kind, "content": content} for kind, content in sample]}


def chat(client: ollama.Client, model: str, messages: list[dict], num_ctx: int | None = None) -> str:
    options = {"temperature": 0, "seed": 0}
    if num_ctx:
        options["num_ctx"] = num_ctx
    return client.chat(model=model, messages=messages, think=False, options=options)["message"]["content"].strip()


def read_answer(client: ollama.Client, model: str, context: str, question: str) -> str:
    return chat(client, model, [{"role": "system", "content": READER_SYSTEM},
                                {"role": "user", "content": f"Memory context:\n{context}\n\nQuestion: {question}"}],
                num_ctx=8192)


def judge_answer(client: ollama.Client, model: str, question: dict, answer: str) -> tuple[bool | None, str]:
    prompt = (f"Question: {question['question']}\nGold answer: {question['gold']}\nCandidate answer: {answer}\n\n"
              "Does the candidate answer give the same fact as the gold answer? Different wording, a different date "
              "format or extra correct detail is fine. A missing or wrong fact, or 'I don't know', is not. "
              "Reply with one word: yes or no.")
    verdict = chat(client, model, [{"role": "user", "content": prompt}]).lower()
    if verdict.startswith("yes"):
        return True, verdict
    if verdict.startswith("no"):
        return False, verdict
    return None, verdict


async def ask(run: Path, questions: list[dict], args: argparse.Namespace) -> list[dict]:
    from prme.storage.engine import MemoryEngine

    client = ollama.Client(host=OLLAMA_HOST)
    results = []
    async with MemoryEngine.open(engine_config(run / "pack", args.extract_model, vision_config(args))) as engine:
        for question in questions:
            started = time.perf_counter()
            response = await engine.retrieve(question["question"], user_id=USER_ID, token_budget=args.token_budget)
            context = response.bundle.render()
            retrieval_seconds = round(time.perf_counter() - started, 3)
            lowered = context.lower()
            hit = any(needle in lowered for needle in question["needles"])
            answer = read_answer(client, args.reader_model, context, question["question"])
            correct, verdict = judge_answer(client, args.judge_model, question, answer)
            results.append({**question, "retrieval_hit": hit, "answer": answer, "correct": correct, "verdict": verdict,
                            "context_tokens": response.bundle.tokens_used, "retrieval_seconds": retrieval_seconds,
                            "context": context})
            mark = "ok " if correct else ("?? " if correct is None else "XX ")
            print(f"  {mark}{question['id']} hit={hit} {answer[:90]}", flush=True)
    with (run / "answers.jsonl").open("w") as handle:
        for result in results:
            handle.write(json.dumps(result) + "\n")
    return results


def summarize(values: list[float]) -> dict | None:
    values = [value for value in values if value is not None]
    if not values:
        return None
    return {"n": len(values), "mean": round(statistics.mean(values), 2), "median": round(statistics.median(values), 2),
            "min": round(min(values), 2), "max": round(max(values), 2)}


def ollama_version() -> str | None:
    try:
        return httpx.get(OLLAMA_HOST + "/api/version", timeout=5).json().get("version")
    except Exception:  # noqa: BLE001
        return None


def frame_stats_of(reports: list[ObservationReport]) -> dict:
    stats: dict[str, dict] = {}
    for report in reports:
        if report.kind == "photo":
            bucket = stats.setdefault("photos", {"photos": 0, "admitted": 0})
            bucket["photos"] += 1
            bucket["admitted"] += report.admitted
            continue
        changes = [d.change for d in report.keyframe_log if d.change is not None]
        stats[report.source] = {"frames": report.frames, "keyframes": report.keyframes, "described": report.described,
                                "repeats_dropped": report.repeats_dropped, "admitted": report.admitted,
                                "keyframe_times_s": [d.time_s for d in report.keyframe_log if d.kept],
                                "change_median": round(statistics.median(changes), 3) if changes else None,
                                "change_max": max(changes) if changes else None}
    stats["described"] = sum(report.described for report in reports)
    stats["unique_after_description"] = sum(report.admitted for report in reports)
    return stats


def write_report(run: Path, args: argparse.Namespace, reports: list[ObservationReport], descriptions: list[dict],
                 stats: dict, results: list[dict]) -> dict:
    by_source: dict[str, dict] = {}
    for result in results:
        bucket = by_source.setdefault(result["source"], {"questions": 0, "retrieval_hits": 0, "correct": 0})
        bucket["questions"] += 1
        bucket["retrieval_hits"] += int(result["retrieval_hit"])
        bucket["correct"] += int(bool(result["correct"]))
    by_kind: dict[str, dict] = {}
    for result in results:
        bucket = by_kind.setdefault(result["kind"], {"questions": 0, "correct": 0})
        bucket["questions"] += 1
        bucket["correct"] += int(bool(result["correct"]))
    fresh = [d for d in descriptions if not d.get("cached")]
    records = [record for report in reports for record in report.observations]
    frame_stats = frame_stats_of(reports)
    report = {
        "run": run.name, "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "ollama_version": ollama_version(),
        "settings": {key: value for key, value in vars(args).items() if key != "command"},
        "vision_config": vision_config(args).model_dump(),
        "frames": frame_stats,
        "describe": {"calls": len(descriptions), "fresh": len(fresh),
                     "seconds": summarize([d["seconds"] for d in fresh])},
        "build": {"mode": args.mode, "observations": sum(r.admitted for r in reports),
                  "errors": sum(1 for r in records if r.error),
                  "seconds": summarize([r.admit_seconds for r in records if r.status == "admitted"])},
        "pack": {key: value for key, value in stats.items() if key != "node_sample"},
        "questions": {"total": len(results), "retrieval_hits": sum(int(r["retrieval_hit"]) for r in results),
                      "correct": sum(int(bool(r["correct"])) for r in results),
                      "unresolved": sum(1 for r in results if r["correct"] is None),
                      "context_tokens": summarize([r["context_tokens"] for r in results]),
                      "retrieval_seconds": summarize([r["retrieval_seconds"] for r in results]),
                      "by_source": by_source, "by_kind": by_kind},
    }
    (run / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [f"# Visual memory run: {run.name}", "",
             f"Commit `{report['commit']}`, Ollama {report['ollama_version']}, finished {report['finished_at']}.", "",
             "## Settings", "", "```json", json.dumps(report["settings"], indent=2), "```", "",
             "## Frames and keyframes", "",
             "| Source | Frames | Keyframes | Described | Repeats dropped | Admitted | Median change | Max change |",
             "|---|---|---|---|---|---|---|---|"]
    for name, info in frame_stats.items():
        if isinstance(info, dict) and "frames" in info:
            lines.append(f"| {name} | {info['frames']} | {info['keyframes']} | {info['described']} | "
                         f"{info['repeats_dropped']} | {info['admitted']} | {info['change_median']} | {info['change_max']} |")
        elif isinstance(info, dict):
            lines.append(f"| {name} | {info['photos']} photos | {info['photos']} | {info['photos']} | 0 | {info['admitted']} | | |")
    lines.append(f"\nDescribed {frame_stats.get('described')} images; {frame_stats.get('unique_after_description')} "
                 "admitted after dropping repeated descriptions.")
    lines += ["", "## Describe calls (local vision model)", "", "```json",
              json.dumps(report["describe"], indent=2), "```", "",
              f"## Pack build ({args.mode})", "", "```json", json.dumps({**report["build"], **report["pack"]}, indent=2),
              "```", "", "## Answers", "", "```json", json.dumps(report["questions"], indent=2), "```", "",
              "| Id | Kind | Hit | Correct | Answer |", "|---|---|---|---|---|"]
    for result in results:
        correct = {True: "yes", False: "no", None: "?"}[result["correct"]]
        answer = result["answer"].replace("|", "/").replace("\n", " ")[:140]
        lines.append(f"| {result['id']} | {result['kind']} | {'yes' if result['retrieval_hit'] else 'no'} | {correct} | {answer} |")
    lines += ["", "## Extracted node sample", ""]
    for entry in stats.get("node_sample", [])[:120]:
        lines.append(f"- `{entry['type']}`: {entry['content'][:200]}")
    (run / "REPORT.md").write_text("\n".join(lines) + "\n")
    return report


def load_manifest(run: Path) -> dict:
    manifest = run / "corpus" / "manifest.json"
    if not manifest.exists():
        print("building corpus", flush=True)
        corpus_module.build(run / "corpus")
    return json.loads(manifest.read_text())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    defaults = VisionConfig()
    for name in ("corpus", "keyframes", "run"):
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        command.add_argument("--sources", default="meeting,photos,tahoe")
        command.add_argument("--fps", type=float, default=defaults.fps)
        command.add_argument("--change-threshold", type=float, default=defaults.change_threshold,
                             help="frame_change() at or above this starts a new keyframe")
        command.add_argument("--max-gap", type=float, default=defaults.max_gap_seconds,
                             help="heartbeat keyframe every this many seconds")
        command.add_argument("--repeat-similarity", type=float, default=defaults.repeat_similarity,
                             help="description cosine similarity at or above this, with the same quoted text, "
                                  "drops a frame as a repeat of the previous kept frame")
        command.add_argument("--mode", choices=("ingest", "store"), default="ingest")
        command.add_argument("--vision-model", default=defaults.model)
        command.add_argument("--extract-model", default="prme-qwen3.5:35b-a3b-8k")
        command.add_argument("--reader-model", default="qwen3.5:9b")
        command.add_argument("--judge-model", default="qwen3.5:9b")
        command.add_argument("--token-budget", type=int, default=4000)
    args = parser.parse_args(argv)
    run = RUNS / args.run
    run.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(run)
    if args.command == "corpus":
        print(json.dumps(manifest, indent=2))
        return
    entries = sources_of(manifest, args.sources.split(","))
    if args.command == "keyframes":
        print(json.dumps(keyframes_only(run, entries, args), indent=2))
        return
    reports, descriptions = asyncio.run(build_pack(run, entries, args))
    print(json.dumps(frame_stats_of(reports), indent=2))
    stats = pack_stats(run / "pack")
    sources = args.sources.split(",")
    questions = [q for q in json.loads(QUESTIONS.read_text()) if q["source"] in sources]
    results = asyncio.run(ask(run, questions, args))
    report = write_report(run, args, reports, descriptions, stats, results)
    print(json.dumps({"describe": report["describe"], "build": report["build"], "questions": report["questions"]}, indent=2))
    print(f"report: {run / 'REPORT.md'}")


if __name__ == "__main__":
    main()

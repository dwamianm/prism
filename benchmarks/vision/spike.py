"""Visual memory spike (exploration, 2026-10-15).

Pipeline: video frames -> keyframes (perceptual hash) -> local vision model
descriptions -> PRME events (``ingest()`` with extraction, or ``store()``) ->
questions answered by a local reader over ``retrieve()`` context -> local
judge. Every model call goes to the local Ollama server; nothing is billed.
Raw outputs live under ``benchmarks/vision/runs/`` (ignored by git).

Usage::

    uv run python -m benchmarks.vision.spike corpus --run spike-1
    uv run python -m benchmarks.vision.spike keyframes --run spike-1 --sources meeting,tahoe
    uv run python -m benchmarks.vision.spike run --run spike-1 --sources meeting,photos,tahoe \\
        --mode ingest --vision-model qwen3.5:9b --extract-model prme-qwen3.5:35b-a3b-8k

The describe cache under ``runs/_describe_cache`` is keyed by the frame's
SHA-256, the vision model and the prompt version, so a second run with another
``--mode`` or extraction model pays nothing for descriptions.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import shutil
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import httpx
import numpy as np
import ollama
from PIL import Image, ImageFilter

from benchmarks.vision import corpus as corpus_module

RUNS = Path(__file__).resolve().parent / "runs"
CACHE = RUNS / "_describe_cache"
QUESTIONS = Path(__file__).resolve().parent / "questions.json"
OLLAMA_HOST = "http://127.0.0.1:11434"
USER_ID = "viewer"

# Version 1 asked for a narrated description ("the slide says ..."). Extraction then recorded the
# narration as the claim and produced no fact about what the slide said. Version 2 asks for the
# facts the image states as standalone sentences with their subject named, after a short scene line.
DESCRIBE_PROMPT_VERSION = 2
DESCRIBE_PROMPT = """You are the eyes of a personal memory system. {kind_hint}
Write two parts in plain English with no headings, no bullet points and no commentary.
Part 1, one or two sentences: what kind of image this is and what it shows: the place, any people and what they are doing, the objects and the colors.
Part 2, the facts the image states, one sentence per fact. Each sentence must make sense on its own, so name its subject (for example: The store's total is "$8.40".). Quote visible text exactly as written, including numbers, dates, names and prices, inside double quotes. If a value is crossed out and replaced, say which value is crossed out and which is current. Read every value off any chart or table as its own sentence. If the image has no text, state three to five facts about what is visible. Do not guess at anything you cannot see."""
KIND_HINTS = {"video_frame": "The image is one frame of a video recording; say that it is a video frame.",
              "photo": "The image is a photo from the person's camera roll; say that it is a photo."}
RETRY_DELAYS = (5.0, 15.0, 60.0)

READER_SYSTEM = ("You answer questions from a personal memory. Use only the memory context. "
                 "If the context does not contain the answer, reply exactly: I don't know. "
                 "Answer in one short sentence.")


@dataclass
class Observation:
    source: str
    label: str
    kind: str  # video_frame | photo
    path: str
    time_s: float | None
    event_time: str
    sha256: str


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_frames(video: Path, out: Path, fps: float, width: int = 1280) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    if not any(out.glob("*.jpg")):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-hwaccel", "videotoolbox", "-i", str(video),
                        "-vf", f"fps={fps},scale={width}:-2", "-q:v", "3", str(out / "%05d.jpg")], check=True)
    return sorted(out.glob("*.jpg"))


SIGNATURE_SIZE = (320, 180)
SIGNATURE_MARGIN = 12


def frame_signature(path: Path) -> np.ndarray:
    """A small grayscale copy, blurred a little so sub-pixel camera shake does not read as change."""
    image = Image.open(path).convert("L").resize(SIGNATURE_SIZE, Image.Resampling.BOX)
    return np.asarray(image.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)


def _aligned(reference: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    """Shift ``candidate`` by the whole-pixel translation that best matches ``reference`` (phase correlation)."""
    cross = np.fft.fft2(reference - reference.mean()) * np.conj(np.fft.fft2(candidate - candidate.mean()))
    cross /= np.abs(cross) + 1e-6
    peak = np.fft.ifft2(cross).real
    dy, dx = (int(value) for value in np.unravel_index(np.argmax(peak), peak.shape))
    if dy > reference.shape[0] // 2:
        dy -= reference.shape[0]
    if dx > reference.shape[1] // 2:
        dx -= reference.shape[1]
    return np.roll(candidate, (dy, dx), axis=(0, 1))


def frame_change(reference: np.ndarray, candidate: np.ndarray) -> float:
    """How much the candidate differs from the reference after alignment and contrast normalization.

    The value is the largest block-mean absolute difference between the two z-scored images over a
    13x37 grid, so a change confined to one line of text still registers while exposure drift and a
    few pixels of shake do not. Measured on the spike corpus: same-layout slides whose text changed
    scored about 0.7, any other scene change above 2.3, shake and exposure noise at most 0.37, and
    consecutive seconds of a slow aerial pan about 0.5.
    """
    margin = SIGNATURE_MARGIN
    a = reference[margin:-margin, margin:-margin]
    b = _aligned(reference, candidate)[margin:-margin, margin:-margin]
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    difference = np.abs(a - b)
    rows, cols = 13, 37
    block_h, block_w = difference.shape[0] // rows, difference.shape[1] // cols
    blocks = difference[: block_h * rows, : block_w * cols].reshape(rows, block_h, cols, block_w).mean(axis=(1, 3))
    return float(blocks.max())


def select_keyframes(frames: list[Path], *, fps: float, threshold: float, max_gap: float,
                     cap: int) -> tuple[list[int], list[dict]]:
    """Keep a frame when it changed from the last kept frame by ``threshold`` or more, or when
    ``max_gap`` seconds passed since the last kept frame (a heartbeat; the description step drops
    frames whose description repeats the previous kept one). ``cap`` subsamples evenly."""
    kept, log = [], []
    last_signature, last_time = None, None
    for index, frame in enumerate(frames):
        time_s = index / fps
        signature = frame_signature(frame)
        change = None if last_signature is None else round(frame_change(last_signature, signature), 3)
        keep = last_signature is None or change >= threshold or (time_s - last_time) >= max_gap
        log.append({"index": index, "time_s": time_s, "change": change, "kept": keep})
        if keep:
            kept.append(index)
            last_signature, last_time = signature, time_s
    if len(kept) > cap:
        step = len(kept) / cap
        kept = [kept[int(position * step)] for position in range(cap)]
        for entry in log:
            entry["kept"] = entry["index"] in kept
    return kept, log


def thumbnail(path: Path, target: Path, width: int = 320) -> Path:
    image = Image.open(path).convert("RGB")
    image.thumbnail((width, width))
    image.save(target, quality=80)
    return target


def describe(client: ollama.Client, model: str, path: Path, sha: str, kind: str) -> dict:
    folder = CACHE / model.replace(":", "_") / f"v{DESCRIBE_PROMPT_VERSION}"
    folder.mkdir(parents=True, exist_ok=True)
    cached = folder / f"{sha}.json"
    if cached.exists():
        return {**json.loads(cached.read_text()), "cached": True}
    started = time.perf_counter()
    prompt = DESCRIBE_PROMPT.format(kind_hint=KIND_HINTS[kind])
    response = client.chat(model=model, messages=[{"role": "user", "content": prompt, "images": [str(path)]}],
                           think=False, options={"temperature": 0, "seed": 0})
    record = {"text": response["message"]["content"].strip(), "seconds": round(time.perf_counter() - started, 2),
              "prompt_tokens": response.get("prompt_eval_count"), "eval_tokens": response.get("eval_count"),
              "model": model, "prompt_version": DESCRIBE_PROMPT_VERSION}
    cached.write_text(json.dumps(record, indent=2) + "\n")
    return {**record, "cached": False}


def gather_observations(run: Path, manifest: dict, sources: list[str], *, fps: float, threshold: int,
                        max_gap: float, cap: int) -> tuple[list[Observation], dict]:
    observations, stats = [], {}
    for name in ("meeting", "tahoe"):
        if name not in sources:
            continue
        info = manifest[name]
        video = Path(info["path"])
        frames = extract_frames(video, run / "frames" / name, fps)
        kept, log = select_keyframes(frames, fps=fps, threshold=threshold, max_gap=max_gap, cap=cap)
        (run / f"keyframes_{name}.json").write_text(json.dumps(log, indent=1) + "\n")
        start = datetime.fromisoformat(info["start"])
        for index in kept:
            time_s = index / fps
            observations.append(Observation(source=name, label=video.name, kind="video_frame", path=str(frames[index]),
                                            time_s=time_s, event_time=(start + timedelta(seconds=time_s)).isoformat(),
                                            sha256=sha256_of(frames[index])))
        changes = [entry["change"] for entry in log if entry["change"] is not None]
        stats[name] = {"frames": len(frames), "keyframes": len(kept), "keyframe_times_s": [index / fps for index in kept],
                       "change_median": round(statistics.median(changes), 3) if changes else None,
                       "change_max": max(changes) if changes else None}
    if "photos" in sources:
        for photo in manifest["photos"]:
            path = Path(photo["path"])
            observations.append(Observation(source="photos", label=path.name, kind="photo", path=str(path), time_s=None,
                                            event_time=photo["taken"], sha256=sha256_of(path)))
        stats["photos"] = {"photos": len(manifest["photos"])}
    return observations, stats


QUOTE_RE = re.compile(r'"([^"]{1,200})"')
REPEAT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"  # the pack's own default text embedding
_embedder = None


def description_vector(text: str) -> np.ndarray:
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(REPEAT_EMBEDDING_MODEL)
    vector = np.asarray(next(iter(_embedder.embed([text]))), dtype=np.float32)
    return vector / (np.linalg.norm(vector) + 1e-9)


def repeat_similarity(previous: tuple[str, np.ndarray] | None, current: tuple[str, np.ndarray]) -> float | None:
    """Cosine similarity of the two descriptions when their quoted text is the same set, else None.

    Any difference in quoted text keeps the frame, so a whiteboard with one changed value is never
    dropped. Measured on the spike corpus with the 9B model: the same whiteboard described twice
    scored 0.985, the same lake from the air described in new words 0.94 to 0.975, and two slides
    with different text 0.87 to 0.96 (those differ in quoted text, so the gate alone rejects them).
    Word overlap was tried first and failed: the same whiteboard overlapped only 0.61.
    """
    if previous is None:
        return None
    if set(QUOTE_RE.findall(previous[0])) != set(QUOTE_RE.findall(current[0])):
        return None
    return float(np.dot(previous[1], current[1]))


def content_for(observation: Observation, description: str) -> str:
    """The event text is the description alone. Version 1 prefixed the source and frame time, and
    extraction turned that prefix into facts such as "Video frame at 00:03 of Tahoe Day.mov."; the
    source and time live in the event's metadata and event_time instead."""
    return description


def engine_config(pack: Path, extract_model: str):
    """Production retrieval defaults, isolated from .env, extracting through the local Ollama server."""
    from benchmarks.diagnostics.extracted_packs import build_config

    return build_config(pack, model=extract_model)


async def retry_extraction(engine, event_id: str) -> str:
    """Retry one event's extraction after each delay in RETRY_DELAYS; returns its final status."""
    status = await engine.extraction_status(event_id, user_id=USER_ID)
    for delay in RETRY_DELAYS:
        if status is not None and status.status == "complete":
            break
        await asyncio.sleep(delay)
        await engine.retry_extraction(event_id, user_id=USER_ID)
        await engine.process_extractions(user_id=USER_ID, limit=1, budget_ms=10 ** 9)
        status = await engine.extraction_status(event_id, user_id=USER_ID)
    return "missing" if status is None else status.status


async def build_pack(run: Path, observations: list[Observation], descriptions: list[dict], *, mode: str,
                     extract_model: str) -> list[dict]:
    from prme.ingestion.pipeline import ExtractionError
    from prme.storage.engine import MemoryEngine

    pack = run / "pack"
    if pack.exists():
        shutil.rmtree(pack)
    (pack / "lexical_index").mkdir(parents=True)
    (pack / "frames").mkdir()
    records = []
    async with MemoryEngine.open(engine_config(pack, extract_model)) as engine:
        for observation, description in zip(observations, descriptions, strict=True):
            thumbnail(Path(observation.path), pack / "frames" / f"{observation.sha256}.jpg")
            metadata = {"modality": observation.kind, "source_file": observation.label,
                        "frame_time_s": observation.time_s, "frame_sha256": observation.sha256,
                        "thumbnail": f"frames/{observation.sha256}.jpg", "vision_model": description["model"],
                        "describe_prompt_version": description["prompt_version"]}
            content = content_for(observation, description["text"])
            event_time = datetime.fromisoformat(observation.event_time)
            session = (f"video:{observation.label}" if observation.kind == "video_frame"
                       else f"photo:{observation.label}")
            started, error, event_id, status = time.perf_counter(), None, None, "complete"
            try:
                if mode == "ingest":
                    event_id = await engine.ingest(content, user_id=USER_ID, role="tool", session_id=session,
                                                   metadata=metadata, event_time=event_time, wait_for_extraction=True)
                else:
                    event_id = await engine.store(content, user_id=USER_ID, role="tool", session_id=session,
                                                  metadata=metadata, event_time=event_time)
            except ExtractionError as exc:
                # The event is saved before extraction runs. Retry like the extracted-pack builder
                # does, because one failed extraction leaves every later one pending behind it.
                error, event_id = f"{type(exc).__name__}: {exc}", exc.event_id
                status = await retry_extraction(engine, str(event_id)) if event_id else "missing"
            except Exception as exc:  # noqa: BLE001 - the spike records every failure and goes on
                error, status = f"{type(exc).__name__}: {exc}", "failed"
            records.append({**asdict(observation), "event_id": str(event_id) if event_id else None,
                            "seconds": round(time.perf_counter() - started, 2), "error": error, "status": status,
                            "content": content})
            print(f"  {mode} {observation.source} {observation.label} t={observation.time_s} "
                  f"{records[-1]['seconds']}s{' ERROR ' + error + ' -> ' + status if error else ''}", flush=True)
    with (run / "observations.jsonl").open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return records


def pack_stats(pack: Path) -> dict:
    conn = duckdb.connect(str(pack / "memory.duckdb"), read_only=True)
    try:
        nodes = conn.execute("SELECT node_type, count(*) FROM nodes GROUP BY 1 ORDER BY 1").fetchall()
        edges = conn.execute("SELECT edge_type, count(*) FROM edges GROUP BY 1 ORDER BY 1").fetchall()
        events = conn.execute("SELECT count(*) FROM events").fetchone()[0]
        sample = conn.execute("SELECT node_type, content FROM nodes ORDER BY node_type, content LIMIT 120").fetchall()
    finally:
        conn.close()
    files = [path for path in pack.rglob("*") if path.is_file()]
    thumbs = sum(path.stat().st_size for path in (pack / "frames").glob("*.jpg"))
    return {"events": events, "nodes": dict(nodes), "edges": dict(edges), "pack_bytes": sum(p.stat().st_size for p in files),
            "thumbnail_bytes": thumbs, "node_sample": [{"type": kind, "content": content} for kind, content in sample]}


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


async def ask(run: Path, questions: list[dict], *, extract_model: str, reader_model: str, judge_model: str,
              token_budget: int) -> list[dict]:
    from prme.storage.engine import MemoryEngine

    client = ollama.Client(host=OLLAMA_HOST)
    results = []
    async with MemoryEngine.open(engine_config(run / "pack", extract_model)) as engine:
        for question in questions:
            started = time.perf_counter()
            response = await engine.retrieve(question["question"], user_id=USER_ID, token_budget=token_budget)
            context = response.bundle.render()
            retrieval_seconds = round(time.perf_counter() - started, 3)
            lowered = context.lower()
            hit = any(needle in lowered for needle in question["needles"])
            answer = read_answer(client, reader_model, context, question["question"])
            correct, verdict = judge_answer(client, judge_model, question, answer)
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
    if not values:
        return None
    return {"n": len(values), "mean": round(statistics.mean(values), 2), "median": round(statistics.median(values), 2),
            "min": round(min(values), 2), "max": round(max(values), 2)}


def ollama_version() -> str | None:
    try:
        return httpx.get(OLLAMA_HOST + "/api/version", timeout=5).json().get("version")
    except Exception:  # noqa: BLE001
        return None


def write_report(run: Path, args: argparse.Namespace, frame_stats: dict, descriptions: list[dict], records: list[dict],
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
    report = {
        "run": run.name, "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "ollama_version": ollama_version(),
        "settings": {key: value for key, value in vars(args).items() if key != "command"},
        "frames": frame_stats,
        "describe": {"calls": len(descriptions), "fresh": len(fresh),
                     "seconds": summarize([d["seconds"] for d in fresh]),
                     "prompt_tokens": summarize([d["prompt_tokens"] for d in descriptions if d.get("prompt_tokens")]),
                     "eval_tokens": summarize([d["eval_tokens"] for d in descriptions if d.get("eval_tokens")])},
        "build": {"mode": args.mode, "observations": len(records), "errors": sum(1 for r in records if r["error"]),
                  "seconds": summarize([r["seconds"] for r in records if not r["error"]])},
        "pack": {key: value for key, value in stats.items() if key != "node_sample"},
        "questions": {"total": len(results), "retrieval_hits": sum(int(r["retrieval_hit"]) for r in results),
                      "correct": sum(int(bool(r["correct"])) for r in results),
                      "unresolved": sum(1 for r in results if r["correct"] is None),
                      "context_tokens": summarize([r["context_tokens"] for r in results]),
                      "retrieval_seconds": summarize([r["retrieval_seconds"] for r in results]),
                      "by_source": by_source, "by_kind": by_kind},
    }
    (run / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [f"# Visual memory spike: {run.name}", "",
             f"Commit `{report['commit']}`, Ollama {report['ollama_version']}, finished {report['finished_at']}.", "",
             "## Settings", "", "```json", json.dumps(report["settings"], indent=2), "```", "",
             "## Frames and keyframes", "", "| Source | Frames | Keyframes | Median change | Max change |", "|---|---|---|---|---|"]
    for name, info in frame_stats.items():
        if isinstance(info, dict) and "frames" in info:
            lines.append(f"| {name} | {info['frames']} | {info['keyframes']} | {info['change_median']} | {info['change_max']} |")
        elif isinstance(info, dict):
            lines.append(f"| {name} | {info['photos']} photos | {info['photos']} | | |")
    lines.append(f"\nDescribed {frame_stats.get('described')} images; {frame_stats.get('unique_after_description')} "
                 "left after dropping repeated descriptions.")
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
    for name in ("corpus", "keyframes", "run"):
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        command.add_argument("--sources", default="meeting,photos,tahoe")
        command.add_argument("--fps", type=float, default=1.0)
        command.add_argument("--change-threshold", type=float, default=0.55,
                             help="frame_change() at or above this starts a new keyframe")
        command.add_argument("--max-gap", type=float, default=30.0, help="heartbeat keyframe every this many seconds")
        command.add_argument("--repeat-similarity", type=float, default=0.93,
                             help="description cosine similarity at or above this, with the same quoted text, "
                                  "drops a frame as a repeat of the previous kept frame")
        command.add_argument("--cap", type=int, default=40, help="most keyframes kept per video")
        command.add_argument("--mode", choices=("ingest", "store"), default="ingest")
        command.add_argument("--vision-model", default="qwen3.5:9b")
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
    sources = args.sources.split(",")
    observations, frame_stats = gather_observations(run, manifest, sources, fps=args.fps, threshold=args.change_threshold,
                                                    max_gap=args.max_gap, cap=args.cap)
    print(json.dumps(frame_stats, indent=2))
    if args.command == "keyframes":
        return
    client = ollama.Client(host=OLLAMA_HOST)
    descriptions, unique, dropped = [], [], []
    previous: dict[str, tuple[str, np.ndarray] | None] = {}
    for observation in observations:
        description = describe(client, args.vision_model, Path(observation.path), observation.sha256, observation.kind)
        descriptions.append(description)
        current = (description["text"], description_vector(description["text"]))
        similarity = repeat_similarity(previous.get(observation.source), current)
        duplicate = similarity is not None and similarity >= args.repeat_similarity
        print(f"  describe {observation.source} t={observation.time_s} {observation.label}: "
              f"{description['seconds']}s{' (cached)' if description['cached'] else ''}"
              f" similarity={'none' if similarity is None else round(similarity, 3)}"
              f"{' DUPLICATE' if duplicate else ''} {description['text'][:80]!r}", flush=True)
        if duplicate:
            dropped.append({**asdict(observation), "similarity": round(similarity, 4)})
            continue
        previous[observation.source] = current
        unique.append((observation, description))
    frame_stats["described"] = len(descriptions)
    frame_stats["unique_after_description"] = len(unique)
    (run / "dropped_duplicates.json").write_text(json.dumps(dropped, indent=1) + "\n")
    records = asyncio.run(build_pack(run, [o for o, _ in unique], [d for _, d in unique], mode=args.mode,
                                     extract_model=args.extract_model))
    stats = pack_stats(run / "pack")
    questions = [q for q in json.loads(QUESTIONS.read_text()) if q["source"] in sources]
    results = asyncio.run(ask(run, questions, extract_model=args.extract_model, reader_model=args.reader_model,
                              judge_model=args.judge_model, token_budget=args.token_budget))
    report = write_report(run, args, frame_stats, descriptions, records, stats, results)
    print(json.dumps({"describe": report["describe"], "build": report["build"], "questions": report["questions"]}, indent=2))
    print(f"report: {run / 'REPORT.md'}")


if __name__ == "__main__":
    main()

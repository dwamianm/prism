"""Count evaluation for the description prompt (dwamianm/prism#388).

Describes a small labeled set of frames with each local vision model under
each description prompt version and reports, per model and version, how
often the description states each labeled count correctly as a number, and
the seconds per frame. The same run describes three probe frames of the
spike corpus (``corpus.py``), the two whiteboards and the receipt, and
reports how many of their values each description still quotes, so a new
prompt version can be checked for a loss on text.

The committed set is synthetic: Pillow draws N simple objects per frame
(``count_frames``). Real frames, such as camera frames of raised fingers,
can be added from a labels file outside the repository (``--extra-labels``),
so personal images are never committed::

    [{"id": "two-hands", "file": "/abs/path/frame.jpg",
      "targets": [{"what": "raised fingers", "noun": "fingers?", "count": 10, "per_hand": 5},
                  {"what": "raised hands", "noun": "hands?", "count": 2}]}]

Every call goes to the local Ollama server with the request
``OllamaVisionProvider`` sends (thinking off, temperature 0, seed 0), so the
descriptions are what ``observe()`` would store. Models run one at a time,
each unloaded after its frames; within a model, each version describes all
frames in turn, so no call reuses the previous call's image from the
server's prompt cache. Seconds per frame are the server's total time minus
its model load time.

A count is read from the description, not judged by a model: a number (a
numeral, a number word, "single", "both" or "pair") followed within four
words by the counted noun, in the same clause. A frame scores correct when
every target count it states is right and none is missing; a target is
"missing" when no number precedes its noun, which is how version 2 states
"all fingers spread". ``per_hand`` accepts a per-hand count in a sentence
that says each or both hands. Raw descriptions go to
``benchmarks/vision/runs/<run>/`` (ignored by git).

Usage::

    uv run python -m benchmarks.vision.counts --run counts-1 \\
        --models qwen3.5:9b,gemma4:26b,qwen3.5:35b-a3b --versions 2,3 --extra-labels /tmp/gestures.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import time
from datetime import datetime
from pathlib import Path

import httpx
import ollama
from PIL import Image, ImageDraw

from benchmarks.vision import corpus as corpus_module
from prme.vision.describe import DESCRIBE_PROMPTS, describe_prompt

RUNS = Path(__file__).resolve().parent / "runs"
OLLAMA_HOST = "http://127.0.0.1:11434"
SIZE = (1280, 720)

# --- the synthetic count set ---------------------------------------------------

BALLS = "balls?|circles?|spheres?|dots?|discs?|disks?|orbs?"
SQUARES = "blocks?|squares?|cubes?|boxes?|tiles?|rectangles?"
PEOPLE = "people|persons?|figures?|individuals|men|humans?|characters?|stick figures?"


def _place(rng: random.Random, count: int, radius: int, area: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """*count* centers inside *area* whose circles of *radius* keep a clear gap between them."""
    x0, y0, x1, y1 = area
    centers: list[tuple[int, int]] = []
    while len(centers) < count:
        x, y = rng.randint(x0 + radius, x1 - radius), rng.randint(y0 + radius, y1 - radius)
        if all((x - cx) ** 2 + (y - cy) ** 2 > (2.6 * radius) ** 2 for cx, cy in centers):
            centers.append((x, y))
    return centers


def _table(top: tuple[int, int, int] = (236, 232, 222)) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", SIZE, top)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 470, SIZE[0], SIZE[1]), fill=(150, 108, 70))  # a wooden table edge
    return image, draw


def _ball(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, color: tuple[int, int, int]) -> None:
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline=(40, 40, 40), width=3)
    shine = radius // 3
    draw.ellipse((x - radius // 2, y - radius // 2, x - radius // 2 + shine, y - radius // 2 + shine),
                 fill=(255, 255, 255))


def _star(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int, color: tuple[int, int, int]) -> None:
    import math

    x, y = center
    points = [(x + (radius if i % 2 == 0 else radius * 0.45) * math.sin(i * math.pi / 5),
               y - (radius if i % 2 == 0 else radius * 0.45) * math.cos(i * math.pi / 5)) for i in range(10)]
    draw.polygon(points, fill=color, outline=(60, 50, 0))


def _person(draw: ImageDraw.ImageDraw, x: int, color: tuple[int, int, int]) -> None:
    """A simple standing figure: head, body, arms and legs."""
    draw.ellipse((x - 40, 150, x + 40, 230), fill=(224, 182, 140), outline=(40, 40, 40), width=3)
    draw.rounded_rectangle((x - 55, 240, x + 55, 420), radius=24, fill=color, outline=(40, 40, 40), width=3)
    draw.line((x - 55, 260, x - 95, 400), fill=color, width=22)
    draw.line((x + 55, 260, x + 95, 400), fill=color, width=22)
    draw.line((x - 25, 420, x - 35, 600), fill=(40, 40, 70), width=26)
    draw.line((x + 25, 420, x + 35, 600), fill=(40, 40, 70), width=26)


def count_frames() -> list[tuple[dict, Image.Image]]:
    """The synthetic frames and their labels, drawn the same way on every run."""
    frames: list[tuple[dict, Image.Image]] = []
    rng = random.Random(388)

    image, draw = _table()
    for center in _place(rng, 3, 60, (100, 330, 1180, 600)):
        _ball(draw, center, 60, (205, 35, 35))
    frames.append(({"id": "balls-3", "targets": [{"what": "red balls", "noun": BALLS, "count": 3}]}, image))

    image, draw = _table()
    for x, y in _place(rng, 5, 55, (80, 300, 1200, 620)):
        draw.rectangle((x - 55, y - 55, x + 55, y + 55), fill=(40, 80, 200), outline=(20, 30, 80), width=4)
    frames.append(({"id": "blocks-5", "targets": [{"what": "blue blocks", "noun": SQUARES, "count": 5}]}, image))

    image = Image.new("RGB", SIZE, (18, 30, 80))
    draw = ImageDraw.Draw(image)
    for center in _place(rng, 7, 62, (40, 40, 1240, 680)):
        _star(draw, center, 62, (250, 210, 40))
    frames.append(({"id": "stars-7", "targets": [{"what": "yellow stars", "noun": "stars?", "count": 7}]}, image))

    image, draw = _table()
    centers = _place(rng, 6, 55, (80, 300, 1200, 620))
    for x, y in centers[:4]:
        draw.polygon([(x, y - 60), (x - 58, y + 45), (x + 58, y + 45)], fill=(40, 150, 70), outline=(10, 60, 20))
    for center in centers[4:]:
        _ball(draw, center, 50, (240, 140, 30))
    frames.append(({"id": "triangles-4-circles-2", "targets": [
        {"what": "green triangles", "noun": "triangles?|pyramids?|cones?", "count": 4},
        {"what": "orange circles", "noun": BALLS + "|oranges?", "count": 2}]}, image))

    image = Image.new("RGB", SIZE, (60, 66, 74))
    draw = ImageDraw.Draw(image)
    for x, y in _place(rng, 9, 50, (60, 60, 1220, 660)):
        draw.ellipse((x - 50, y - 50, x + 50, y + 50), fill=(212, 175, 55), outline=(120, 90, 20), width=5)
        draw.ellipse((x - 34, y - 34, x + 34, y + 34), outline=(150, 115, 30), width=3)
    frames.append(({"id": "coins-9", "targets": [{"what": "gold coins", "noun": "coins?|" + BALLS + "|tokens?",
                                                  "count": 9}]}, image))

    image = Image.new("RGB", SIZE, (225, 235, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 600, SIZE[0], SIZE[1]), fill=(120, 170, 90))
    for x, color in zip((300, 640, 980), ((200, 60, 60), (60, 110, 200), (230, 170, 40)), strict=True):
        _person(draw, x, color)
    frames.append(({"id": "people-3", "targets": [{"what": "people", "noun": PEOPLE, "count": 3}]}, image))
    return frames


def probe_frames() -> list[tuple[dict, Image.Image]]:
    """The spike corpus's two whiteboards and its receipt, with the values a description must keep.

    Each needle is a list of spellings, compared after casefolding and turning
    dashes into spaces; a probe scores the needles its description contains.
    """
    green, blue, red = corpus_module.GREEN, corpus_module.BLUE, corpus_module.RED
    before = corpus_module.whiteboard([("Falcon launch", blue), ("Launch date: 3 Nov", blue),
                                       ("Budget: $12,500", blue), ("Vendor: Northwind Sensors", green),
                                       ("Owner: Priya", green)])
    after = corpus_module.whiteboard([("Falcon launch", blue), ("Launch date: 3 Nov  10 Nov", red),
                                      ("Budget: $14,000", red), ("Vendor: Northwind Sensors", green),
                                      ("Owner: Priya", green)], struck="3 Nov")
    three, ten = ["3 nov", "nov 3", "november 3", "3 november"], ["10 nov", "nov 10", "november 10", "10 november"]
    return [
        ({"id": "whiteboard-before", "needles": [["falcon"], three, ["12,500", "12500"], ["northwind"], ["priya"]]},
         before),
        ({"id": "whiteboard-after", "needles": [["falcon"], ten, three, ["crossed", "struck", "strike"],
                                                ["14,000", "14000"], ["northwind"], ["priya"]]}, after),
        ({"id": "receipt", "needles": [["blue bottle"], ["oakland"],
                                       ["12 oct 2026", "oct 12, 2026", "october 12, 2026", "12 october 2026"],
                                       ["08:41", "8:41"], ["oat latte"], ["11.50"], ["4421"]]},
         corpus_module.receipt()),
    ]


# --- reading a count -------------------------------------------------------------

NUMBERS = {word: value for value, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
NUMBERS.update({"single": 1, "both": 2, "pair": 2})
_TOKENS = re.compile(r"\d+|[a-z]+(?:['-][a-z]+)*|[^\sa-z\d]")
_STOP = {"and", "or", "with", "on", "in", "at", "while", "but", "from", "than", "to", "by", "is", "are"}
_COUNTED = re.compile(r"(?:hands?|fingers?|thumbs?|" + PEOPLE + r"|arms?|objects?|items?|shapes?)$")
_PER_HAND = re.compile(r"\b(?:each|per|both|either|every)\b")
UNCERTAIN = re.compile(r"\buncertain")


def stated_counts(description: str, noun: str) -> list[tuple[int, str]]:
    """Every (number, sentence) where a number is followed by *noun* within four words of its clause."""
    target = re.compile(f"(?:{noun})$")
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", description.casefold()):
        tokens = _TOKENS.findall(sentence)
        for index, token in enumerate(tokens):
            value = int(token) if token.isdigit() else NUMBERS.get(token)
            following_word = tokens[index + 1] if index + 1 < len(tokens) else ""
            if value is None or (following_word == "of" and token != "pair"):  # "one of the people" is no count
                continue
            for following in tokens[index + 1:index + 6]:
                if target.match(following):
                    found.append((value, sentence))
                    break
                if not following[0].isalpha() or following in _STOP or _COUNTED.match(following):
                    break
    return found


def score_target(description: str, target: dict) -> dict:
    """Whether *description* states *target*'s count: correct, wrong, mixed or missing."""
    stated = stated_counts(description, target["noun"])
    right = [value for value, sentence in stated
             if value == target["count"]
             or (target.get("per_hand") is not None and value == target["per_hand"] and _PER_HAND.search(sentence))]
    wrong = [value for value, _ in stated if value not in right]
    status = "missing" if not stated else "mixed" if right and wrong else "correct" if right else "wrong"
    return {"what": target["what"], "count": target["count"], "stated": [value for value, _ in stated],
            "status": status}


def score_probe(description: str, needles: list[list[str]]) -> dict:
    text = " ".join(re.sub(r"[-\u2010-\u2015]+", " ", description.casefold()).split())
    found = [any(spelling in text for spelling in spellings) for spellings in needles]
    return {"found": sum(found), "needles": len(needles),
            "missing": [spellings[0] for spellings, hit in zip(needles, found, strict=True) if not hit]}


# --- running ---------------------------------------------------------------------


def describe(client: ollama.Client, model: str, image: bytes, prompt: str) -> dict:
    """One description with the request OllamaVisionProvider sends, and the server's timings."""
    started = time.perf_counter()
    response = client.chat(model=model, messages=[{"role": "user", "content": prompt, "images": [image]}],
                           think=False, options={"temperature": 0, "seed": 0})
    wall = time.perf_counter() - started
    total, load = (response.get("total_duration") or 0) / 1e9, (response.get("load_duration") or 0) / 1e9
    return {"text": response["message"]["content"].strip(), "wall_seconds": round(wall, 2),
            "load_seconds": round(load, 2), "seconds": round(total - load, 2),
            "prompt_tokens": response.get("prompt_eval_count"), "output_tokens": response.get("eval_count")}


def load_frames(run: Path, extra_labels: Path | None) -> list[dict]:
    """Write the synthetic and probe frames under *run* and return every frame's label with its file."""
    folder = run / "frames"
    folder.mkdir(parents=True, exist_ok=True)
    frames = []
    for group, built in (("synthetic", count_frames()), ("probe", probe_frames())):
        for label, image in built:
            path = folder / f"{label['id']}.jpg"
            image.save(path, quality=92)
            frames.append({**label, "group": group, "file": str(path)})
    if extra_labels is not None:
        for label in json.loads(extra_labels.read_text()):
            frames.append({**label, "group": label.get("group", "extra")})
    return frames


def summarize(rows: list[dict]) -> dict:
    counted = [row for row in rows if "targets" in row]
    targets = [target for row in counted for target in row["targets"]]
    seconds = [row["seconds"] for row in rows]
    probes = [row for row in rows if "probe" in row]
    by_status = {status: sum(1 for target in targets if target["status"] == status)
                 for status in ("correct", "wrong", "mixed", "missing")}
    return {
        "frames": len(counted),
        "frames_correct": sum(1 for row in counted if all(t["status"] == "correct" for t in row["targets"])),
        "targets": len(targets), **{f"targets_{status}": value for status, value in by_status.items()},
        "uncertain_marked": sum(1 for row in counted if UNCERTAIN.search(row["text"].casefold())),
        "seconds_mean": round(statistics.mean(seconds), 2) if seconds else None,
        "seconds_median": round(statistics.median(seconds), 2) if seconds else None,
        "probe_needles_found": sum(row["probe"]["found"] for row in probes),
        "probe_needles": sum(row["probe"]["needles"] for row in probes),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True)
    parser.add_argument("--models", default="qwen3.5:9b")
    parser.add_argument("--versions", default=",".join(str(v) for v in sorted(DESCRIBE_PROMPTS)))
    parser.add_argument("--extra-labels", type=Path, help="labels of frames kept outside the repository")
    parser.add_argument("--max-calls", type=int, default=150, help="refuse a run that needs more model calls")
    parser.add_argument("--keep-loaded", action="store_true", help="leave each model loaded after its frames")
    parser.add_argument("--kind", default="photo", help="source kind the prompt names")
    parser.add_argument("--rescore", action="store_true",
                        help="score the run's recorded descriptions again with the current labels; no model call")
    args = parser.parse_args(argv)
    models = [model for model in args.models.split(",") if model]
    versions = [int(version) for version in args.versions.split(",")]
    run = RUNS / args.run
    frames = load_frames(run, args.extra_labels)
    if args.rescore:
        by_id = {frame["id"]: frame for frame in frames}
        rows = [score_row(json.loads(line), by_id[json.loads(line)["frame"]])
                for line in (run / "descriptions.jsonl").read_text().splitlines()]
        write_summary(run, models, versions, frames, rows)
        return
    calls = len(models) * len(versions) * len(frames)
    if calls > args.max_calls:
        raise SystemExit(f"{calls} model calls planned, more than --max-calls {args.max_calls}")
    print(f"{len(frames)} frames x {len(versions)} versions x {len(models)} models = {calls} calls", flush=True)
    client = ollama.Client(host=OLLAMA_HOST)
    rows: list[dict] = []
    raw = (run / "descriptions.jsonl").open("a")
    for model in models:
        for version in versions:
            prompt = describe_prompt(args.kind, version)
            for frame in frames:
                result = describe(client, model, Path(frame["file"]).read_bytes(), prompt)
                row = score_row({"model": model, "version": version, "frame": frame["id"], "group": frame["group"],
                                 **result}, frame)
                rows.append(row)
                raw.write(json.dumps(row) + "\n")
                raw.flush()
                marks = " ".join(f"{t['what']}={t['stated']}:{t['status']}" for t in row.get("targets", []))
                probe = f" probe {row['probe']['found']}/{row['probe']['needles']}" if "probe" in row else ""
                print(f"  {model} v{version} {frame['id']} {result['seconds']}s {marks}{probe}", flush=True)
        if not args.keep_loaded:
            client.generate(model=model, keep_alive=0)  # unloads the model; no image and no prompt
    raw.close()
    write_summary(run, models, versions, frames, rows)


def score_row(row: dict, frame: dict) -> dict:
    """*row* with the count and probe scores of its description against *frame*'s labels."""
    row = {key: value for key, value in row.items() if key not in ("targets", "probe")}
    if "targets" in frame:
        row["targets"] = [score_target(row["text"], target) for target in frame["targets"]]
    if "needles" in frame:
        row["probe"] = score_probe(row["text"], frame["needles"])
    return row


def write_summary(run: Path, models: list[str], versions: list[int], frames: list[dict], rows: list[dict]) -> None:
    summary = {
        "run": run.name, "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ollama_version": httpx.get(OLLAMA_HOST + "/api/version", timeout=5).json().get("version"),
        "calls": len(rows),
        "results": [{"model": model, "version": version, "all": summarize(selected),
                     **{group: summarize([row for row in selected if row["group"] == group])
                        for group in sorted({row["group"] for row in selected})}}
                    for model in models for version in versions
                    for selected in [[row for row in rows if row["model"] == model and row["version"] == version]]],
        "frames": [{key: value for key, value in frame.items() if key != "file"} for frame in frames],
        "per_frame": [{key: value for key, value in row.items() if key != "text"} for row in rows],
    }
    (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["results"], indent=2))


if __name__ == "__main__":
    main()

"""Stage "store-contextual": the store stage with a situating sentence written for each memory.

``store()`` keeps a memory's text as sent, so a memory that does not name its
subject ("They chose X and Y. They dropped Z to save time.") is found only by
a question that happens to use its words. Contextual retrieval writes one short
sentence per memory that says who and what it is about, from the memories that
precede it in the same session and nothing else, and stores the memory with
``retrieval_content = sentence + " " + content``. The node is embedded, word
indexed and shown with the sentence; the source event keeps the text as sent.

This stage measures whether that is worth building. It runs the store stage
over a fixture twice as ``store.py`` does, once plain and once with the
sentences, through a local Ollama model (``deepseek-v4.1-flash:cloud``), one
request per memory. The prompt is fixed, generic and names nothing from a
fixture. The model's replies are cached per fixture and run
(``fixtures/fast-cycle/contextual-cache``), so a rerun replays them and calls no
model; ``--no-cache`` asks the model again, which varies a little from run to run.

Ranks are read from each node's original text, never the sentence, so a
sentence that happens to hold an answer term cannot answer for another memory.

    uv run python -m benchmarks.fast_cycle run --label ctx --out /tmp/ctx --stages store,store-contextual
    uv run python -m benchmarks.fast_cycle.contextual lift --runs 3 --out /tmp/lift
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlsplit

import httpx

from benchmarks.fast_cycle import store

KIND = "prme-fast-cycle-contextual-cache"
CACHE_DIR = store.FIXTURES / "contextual-cache"
MODEL = "deepseek-v4.1-flash:cloud"
ENDPOINT = "http://127.0.0.1:11434/v1"
TEMPERATURE = 0
SEED = 20261019
WINDOW = 5
MAX_TOKENS = 120
# A sentence longer than this is not one short sentence; the memory is stored plain.
MAX_SENTENCE_CHARS = 240
ATTEMPTS = 3
CONCURRENCY = 4

# Two prompts, both generic: neither names anything from a fixture. "situate" asks for a sentence that says who and
# what topic; "label" asks for a shorter one that only names the people and the topic. Both are zero-shot.
SITUATE = """You situate one stored memory so that a search can find it later.

You are given the memory and up to 5 earlier memories from the same session, the context. Write ONE short sentence, at most 25 words, that says who the memory is about and what topic it belongs to. Use the names and terms of the context, so that a question about the person and the topic matches this memory even when the memory does not name them.

Rules:
- Use only facts that appear in the context or in the memory itself. Do not add, infer or guess anything.
- Do not restate the memory's details.
- If the memory already names who and what it is about, or the context adds nothing, answer with exactly: NONE

Context:
{context}

Memory:
{memory}

Sentence:"""
LABEL = """You label one stored memory so that a search can find it later.

You are given the memory and up to 5 earlier memories from the same session, the context. Write ONE short sentence, at most 15 words, that names the person or people the memory is about and the topic it belongs to. Copy the names and the topic's name from the context.

Rules:
- Name only people, places and topics that appear in the context or in the memory. Do not state a role, a relationship, a cause or a date unless the memory itself says it.
- Take only the subject and the topic from the context. Do not copy other details from it.
- Do not restate the memory.
- If the memory already names who and what it is about, or the context adds nothing, answer with exactly: NONE

Context:
{context}

Memory:
{memory}

Sentence:"""
PROMPTS = {"situate": SITUATE, "label": LABEL}
DEFAULT_PROMPT = "situate"
PROMPT_SHA256 = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in PROMPTS.items()}


# --- The prompt and the reply ----------------------------------------------------------------------


def context_window(memories: list[dict], index: int, size: int = WINDOW) -> list[str]:
    """The texts of up to ``size`` memories that precede memory ``index`` in its session, oldest first."""
    session = memories[index]["session"]
    earlier = [memory["content"] for memory in memories[:index] if memory["session"] == session]
    return earlier[-size:]


def build_prompt(context: list[str], memory: str, prompt: str = DEFAULT_PROMPT) -> str:
    return PROMPTS[prompt].format(context="\n".join(f"{number}. {text}" for number, text in enumerate(context, start=1)),
                         memory=memory)


def clean_sentence(raw: str) -> str | None:
    """The sentence a reply holds, or None when it says nothing, is empty or is not one short sentence."""
    text = " ".join(raw.split()).strip("\"'`").strip()
    if text.casefold().startswith("sentence:"):
        text = text[len("sentence:"):].strip().strip("\"'`").strip()
    if not text or text.casefold().rstrip(". ") == "none" or len(text) > MAX_SENTENCE_CHARS:
        return None
    return text


def retrieval_content(sentence: str | None, content: str) -> str | None:
    """What ``store()`` is given as ``retrieval_content``; None keeps the memory as written."""
    return f"{sentence} {content}" if sentence else None


# --- The model and its cache -----------------------------------------------------------------------


def check_endpoint(endpoint: str) -> None:
    """Only a loopback Ollama server's ``/v1`` URL, without credentials, may receive memories."""
    parts = urlsplit(endpoint)
    if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "::1"} or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or parts.path.rstrip("/") != "/v1"):
        raise ValueError("The endpoint must be a loopback Ollama server's /v1 URL without credentials")


def request_body(prompt: str, model: str = MODEL) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": TEMPERATURE,
            "seed": SEED, "max_tokens": MAX_TOKENS, "reasoning_effort": "none", "stream": False}


def cache_key(body: dict, run: int) -> str:
    return hashlib.sha256(json.dumps({"run": run, "body": body}, sort_keys=True).encode()).hexdigest()


class Cache:
    """The model's replies for one fixture and run, as one JSON file keyed by request."""

    def __init__(self, path: Path):
        self.path = path
        self.entries: dict[str, dict] = {}
        if path.is_file():
            value = json.loads(path.read_text())
            if value.get("kind") != KIND:
                raise ValueError(f"{path} is not a contextual retrieval cache")
            self.entries = value["entries"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps({"kind": KIND, "model": MODEL, "prompt_sha256": PROMPT_SHA256,
                           "entries": dict(sorted(self.entries.items()))}, indent=1)
        self.path.write_text(text + "\n")


async def ask(client: httpx.AsyncClient, body: dict) -> dict:
    """One chat completion, with a few attempts at a transient failure; returns the reply and its token counts."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = await client.post("/chat/completions", json=body)
            if response.status_code in {429, 500, 502, 503, 504, 529} and attempt < ATTEMPTS:
                await asyncio.sleep(2 ** attempt)
                continue
            response.raise_for_status()
            value = response.json()
            choice = value["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError(f"the reply ended with {choice['finish_reason']}")
            return {"raw": (choice["message"].get("content") or "").strip(),
                    "prompt_tokens": value["usage"]["prompt_tokens"],
                    "completion_tokens": value["usage"]["completion_tokens"]}
        except (httpx.TransportError, ValueError, KeyError) as error:
            if attempt == ATTEMPTS:
                raise RuntimeError(f"the model did not answer: {type(error).__name__}") from None
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


async def generate(fixture: dict, *, run: int, cache: Cache, live: bool, prompt: str = DEFAULT_PROMPT,
                   model: str = MODEL, endpoint: str = ENDPOINT) -> list[dict]:
    """A situating sentence for every memory of the fixture, replayed from the cache or asked of the model.

    Returns one record per memory: the prompt's context size, the raw reply, the cleaned sentence (None when
    the memory is stored plain), the tokens the request used and whether the model was asked.
    """
    check_endpoint(endpoint)
    memories = fixture["memories"]
    semaphore = asyncio.Semaphore(CONCURRENCY)
    records: list[dict | None] = [None] * len(memories)

    async def one(client: httpx.AsyncClient | None, index: int) -> None:
        context = context_window(memories, index)
        if not context:
            # The first memory of a session has nothing to situate it by.
            records[index] = {"context": 0, "raw": "", "sentence": None, "prompt_tokens": 0, "completion_tokens": 0,
                              "asked": False, "skipped": True}
            return
        body = request_body(build_prompt(context, memories[index]["content"], prompt), model)
        key = cache_key(body, run)
        entry = cache.entries.get(key)
        asked = entry is None
        if asked:
            if client is None:
                raise RuntimeError(f"No cached reply for memory {index} of run {run}; rerun with the model")
            async with semaphore:
                entry = await ask(client, body)
            cache.entries[key] = entry
        records[index] = {"context": len(context), "raw": entry["raw"], "sentence": clean_sentence(entry["raw"]),
                          "prompt_tokens": entry["prompt_tokens"], "completion_tokens": entry["completion_tokens"],
                          "asked": asked, "skipped": False}

    if live:
        async with httpx.AsyncClient(base_url=endpoint, timeout=httpx.Timeout(300, connect=30),
                                     trust_env=False) as client:
            await asyncio.gather(*(one(client, index) for index in range(len(memories))))
    else:
        await asyncio.gather(*(one(None, index) for index in range(len(memories))))
    return [record for record in records if record is not None]


# --- The stage -------------------------------------------------------------------------------------


def cache_path(fixture: dict, run: int, folder: Path = CACHE_DIR) -> Path:
    return folder / f"{fixture['name']}-run{run}.json"


def working_cache(work: Path, committed: Path) -> Path:
    """A copy of the committed cache for this run to read and add to."""
    working = work / "contextual-cache"
    shutil.rmtree(working, ignore_errors=True)
    if committed.is_dir():
        shutil.copytree(committed, working)
    else:
        working.mkdir(parents=True)
    return working


def update_cache(working: Path, committed: Path) -> int:
    """Copy the replies a run added to the committed cache; returns the number of replies added."""
    added = 0
    for path in sorted(working.glob("*.json")):
        target = Cache(committed / path.name)
        source = Cache(path)
        new = {key: value for key, value in source.entries.items() if key not in target.entries}
        if new:
            target.entries.update(new)
            target.save()
            added += len(new)
    return added


def summarize(records: list[dict]) -> dict:
    """What the generation cost and produced: model calls, tokens, sentences and the words they add."""
    asked = [record for record in records if record["asked"]]
    sentences = [record["sentence"] for record in records if record["sentence"]]
    return {"memories": len(records), "model_calls": len(asked),
            "replayed": sum(not record["asked"] and not record["skipped"] for record in records),
            "prompt_tokens": sum(record["prompt_tokens"] for record in records),
            "completion_tokens": sum(record["completion_tokens"] for record in records),
            "no_context": sum(record["skipped"] for record in records),
            "none_replies": sum(not record["skipped"] and not record["sentence"] and bool(record["raw"])
                                for record in records),
            "sentences": len(sentences),
            "words_added_mean": sum(len(sentence.split()) for sentence in sentences) / len(sentences)
            if sentences else 0.0}


async def run(work: Path, *, fixture_path: Path = store.DEFAULT_FIXTURE, run_number: int = 1,
              prompt: str = DEFAULT_PROMPT, cache_dir: Path = CACHE_DIR, use_cache: bool = True,
              overrides: dict | None = None, database_url: str | None = None, model: str = MODEL,
              endpoint: str = ENDPOINT) -> dict:
    """The store stage over the fixture with a situating sentence for each memory."""
    fixture = store.load_fixture(fixture_path)
    working = working_cache(work, cache_dir)
    cache = Cache(cache_path(fixture, run_number, working))
    if not use_cache:
        cache.entries.clear()
    # A reply missing from the cache is asked of the model; with a cache that holds all of them, none is.
    complete = all(_cached(fixture, run_number, cache, prompt, model))
    records = await generate(fixture, run=run_number, cache=cache, live=not complete, prompt=prompt, model=model,
                             endpoint=endpoint)
    cache.save()
    contents = [retrieval_content(record["sentence"], memory["content"])
                for record, memory in zip(records, fixture["memories"])]
    result = await store.run(work, fixture_path=fixture_path, overrides=overrides, database_url=database_url,
                             retrieval_contents=contents)
    result["contextual"] = {"model": model, "run": run_number, "prompt": prompt,
                            "prompt_sha256": PROMPT_SHA256[prompt], **summarize(records),
                            "sentences_by_memory": [record["sentence"] for record in records]}
    result["working_cache"] = str(working)
    return result


def _cached(fixture: dict, run_number: int, cache: Cache, prompt: str, model: str):
    """For each memory with a context, whether the cache holds the model's reply to its request."""
    memories = fixture["memories"]
    for index in range(len(memories)):
        context = context_window(memories, index)
        if context:
            yield cache_key(request_body(build_prompt(context, memories[index]["content"], prompt), model),
                            run_number) in cache.entries


# --- The lift command ------------------------------------------------------------------------------


def _ranks(result: dict) -> dict[str, int | None]:
    return {question["id"]: question["answer_rank"] for question in result["questions"]}


def lift_table(plain: dict, runs: list[dict]) -> str:
    """The plain store stage against each run, per question and in total, as Markdown."""
    def total(result: dict) -> dict:
        measures = result["measures"]
        return {"top1": measures["answers_in_top1"], "top3": measures["answers_in_top3"],
                "mrr": measures["mean_reciprocal_rank"], "stale": measures["stale_above_answer"],
                "tokens": measures["context_tokens_mean"]}

    ids = list(_ranks(plain))
    header = ["Question", "Plain", *(f"Run {number}" for number in range(1, len(runs) + 1))]
    rows = [[question_id, *(str(_ranks(result)[question_id] or "none") for result in (plain, *runs))]
            for question_id in ids]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    names = (("top1", "Answers ranked first"), ("top3", "Answers in the top 3"), ("mrr", "Mean reciprocal rank"),
             ("stale", "Stale value ranked above the answer"), ("tokens", "Context tokens per question"))
    for key, label in names:
        cells = [f"{total(result)[key]:.3f}" if key == "mrr" else f"{total(result)[key]:.0f}"
                 for result in (plain, *runs)]
        lines.append("| " + " | ".join([label, *cells]) + " |")
    worse = []
    for number, result in enumerate(runs, start=1):
        count = sum(1 for question_id in ids if _worse_by(_ranks(plain)[question_id], _ranks(result)[question_id]) > 1)
        worse.append(f"run {number}: {count}")
    lines += ["", "Questions that fell by more than one place: " + ", ".join(worse)]
    return "\n".join(lines) + "\n"


def _worse_by(before: int | None, after: int | None) -> float:
    before = float("inf") if before is None else before
    after = float("inf") if after is None else after
    return 0.0 if before == after else after - before


async def lift(args: argparse.Namespace) -> int:
    from benchmarks.fast_cycle import cycle

    cycle._quiet_logs()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    status = 0
    for fixture_path in args.fixture:
        fixture = store.load_fixture(fixture_path)
        plain = await store.run(out / fixture["name"] / "plain", fixture_path=fixture_path,
                                database_url=args.database_url)
        runs = []
        for number in range(1, args.runs + 1):
            work = out / fixture["name"] / f"run{number}"
            shutil.rmtree(work, ignore_errors=True)
            runs.append(await run(work, fixture_path=fixture_path, run_number=number, prompt=args.prompt,
                                  cache_dir=args.cache_dir, use_cache=not args.no_cache,
                                  database_url=args.database_url))
            if args.update_cache:
                update_cache(Path(runs[-1]["working_cache"]), args.cache_dir)
        (out / f"{fixture['name']}.json").write_text(json.dumps({"plain": plain, "runs": runs}, indent=1) + "\n")
        print(f"## {fixture['name']}\n")
        print(lift_table(plain, runs))
        for number, result in enumerate(runs, start=1):
            info = result["contextual"]
            print(f"run {number}: {info['model_calls']} model calls, {info['sentences']} sentences, "
                  f"{info['none_replies']} NONE, {info['no_context']} without context, "
                  f"{info['prompt_tokens']} prompt tokens, {info['completion_tokens']} completion tokens, "
                  f"{info['words_added_mean']:.1f} words added on average")
        print()
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.fast_cycle.contextual", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    lifting = commands.add_parser("lift", help="Plain store against the situated store, per question, over some runs")
    lifting.add_argument("--fixture", type=Path, action="append", default=[],
                         help="A store fixture (repeatable; default: store-v1 and store-contextual-v1)")
    lifting.add_argument("--runs", type=int, default=3, help="How many generations to compare (default: 3)")
    lifting.add_argument("--prompt", choices=sorted(PROMPTS), default=DEFAULT_PROMPT,
                         help=f"Which prompt writes the sentences (default: {DEFAULT_PROMPT})")
    lifting.add_argument("--out", type=Path, required=True)
    lifting.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    lifting.add_argument("--no-cache", action="store_true", help="Ask the model again for every memory")
    lifting.add_argument("--update-cache", action="store_true", help="Keep the replies in the committed cache")
    lifting.add_argument("--database-url", default=os.environ.get("PRME_FAST_CYCLE_DATABASE_URL"),
                         help="Run the stores on PostgreSQL (or set PRME_FAST_CYCLE_DATABASE_URL); DuckDB otherwise")
    args = parser.parse_args(argv)
    args.fixture = args.fixture or [store.DEFAULT_FIXTURE, store.FIXTURES / "store-contextual-v1.json"]
    return asyncio.run(lift(args))


if __name__ == "__main__":
    sys.exit(main())

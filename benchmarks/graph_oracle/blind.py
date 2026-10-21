"""A blind question set: questions about the three memory sets that nobody wrote while reading the gold graphs.

The gold graphs and their questions were written by one person with the
questions in view, so the oracle run is an upper bound. This module asks a
local model for new questions instead. The model sees only the memories (ids
and text) and a per type instruction; it never sees a gold graph, a query spec
or the cue words. A reviewer then drops the questions that cannot be answered
from the memories, that have no unique answer, or that repeat another, and the
frozen file is committed before any strategy runs on it. A second request writes
three paraphrases of each frozen question.

    uv run python -m benchmarks.graph_oracle blind-generate --out <scratch>
    uv run python -m benchmarks.graph_oracle blind-paraphrase --out <scratch>

Only a loopback Ollama server is used. Every request is counted in the output.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re

import httpx

from benchmarks.graph_oracle.gold import FIXTURES, Memory, _corpus_memories

KIND = "prme-graph-oracle-blind"
PARAPHRASE_KIND = "prme-graph-oracle-blind-paraphrases"
SCHEMA_VERSION = 1
BLIND_FILE = FIXTURES / "blind-v1.json"
PARAPHRASE_FILE = FIXTURES / "blind-paraphrases-v1.json"
MODEL = "deepseek-v4.1-flash:cloud"
MODEL_LABEL = "the fast tiers' extraction model through a local Ollama server (MODEL in benchmarks/graph_oracle/blind.py)"
ENDPOINT = "http://127.0.0.1:11434/v1"
SEED = 20261020
ATTEMPTS = 3
CORPORA = {
    "defects-v1": {"kind": "conversation", "path": "benchmarks/conversations/defects-v1.json"},
    "store-v1": {"kind": "store", "path": "benchmarks/fixtures/fast-cycle/store-v1.json"},
    "chat-v1": {"kind": "conversation", "path": "benchmarks/conversations/chat-v1.json"},
}
# The routing gate's new draw: a memory set written for it, with a gold graph and no earlier questions.
CORPORA_DRAW2 = {"draw2-v1": {"kind": "memories", "path": "benchmarks/fixtures/graph-oracle/draw2-memories-v1.json"}}
BLIND2_FILE = FIXTURES / "blind-v2.json"
CANDIDATES2_FILE = FIXTURES / "blind-candidates-v2.json"
REVIEW2_FILE = FIXTURES / "blind-review-v2.json"
# How many candidates to ask for, per type, in each corpus. A small chat gets fewer.
PER_TYPE = {"defects-v1": 3, "store-v1": 5, "chat-v1": 5}

INSTRUCTIONS = {
    "lookup": "a question whose answer is one fact that a single memory states.",
    "list": "a question whose answer is a list of two or more items. The items are stated in one memory or spread over "
            "several; put every memory that names an item in answer_ids.",
    "multi-hop": "a question that needs two or three memories chained through a shared person, place or thing, for "
                 "example a fact about someone who is only introduced in another memory. Put the memory that states "
                 "the final answer in answer_ids and the other memories of the chain in support_ids.",
    "temporal": "a question about when something happened or will happen, or about which of two things came first, "
                "using dates or times that the memories state.",
    "correction": "a question about a fact that a later memory changed or corrected. The answer is the current value: "
                  "answer_ids are the memories that state it, stale_ids the memories that state the outdated value. "
                  "Return fewer questions, or none, if the memories correct nothing.",
    "negation": "a question about something a person does not do, dislikes, rejected, declined, dropped or avoids; "
                "the answer says what is excluded.",
    "aggregation": "a question that counts or combines facts from several memories (how many, which of these, in "
                   "total). Put every memory the answer needs in answer_ids.",
}
TYPES = tuple(INSTRUCTIONS)

SYSTEM = """You write evaluation questions about a person's stored memories.

You are given a list of memories, each with an id. Write {count} new questions of the type described below, each answerable from the memories alone.

Type: {type}, {instruction}

Rules:
- Ask naturally, the way a user would ask an assistant, and do not copy the memory's wording more than needed.
- The answer must be unique and fully supported by the memories. Never ask about something the memories do not say.
- Every id you give must be an id from the list.
- Return only a JSON array of objects with the keys question, answer, answer_ids, support_ids, stale_ids. Use empty lists where a key does not apply."""

PARAPHRASE = """Rewrite each question below three times, so that a different person might have asked it. Keep the meaning and the answer exactly the same. Change the wording and the sentence shape: use other words for the verbs and nouns where a natural alternative exists, and you may leave a name out in favor of how the person would otherwise be referred to only if the question stays unambiguous. Do not add new facts.

Questions (id: text):
{questions}

Return only a JSON object that maps each id to a list of exactly three rewritten questions."""

PROMPT_SHA256 = hashlib.sha256((SYSTEM + json.dumps(INSTRUCTIONS, sort_keys=True) + PARAPHRASE).encode()).hexdigest()


def memories_of(name: str) -> list[Memory]:
    """A corpus's memories in storage order, read without opening its gold graph."""
    return _corpus_memories({**CORPORA, **CORPORA_DRAW2}[name])[2]


def memory_lines(memories: list[Memory]) -> str:
    return "\n".join(f"[{memory.id}] {memory.role}: {memory.text}" for memory in memories)


class Counter:
    """Every request made, for the report."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0


async def ask(client: httpx.AsyncClient, counter: Counter, system: str, user: str, max_tokens: int = 6000,
              seed: int = SEED) -> str:
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0, "seed": seed, "max_tokens": max_tokens, "reasoning_effort": "none", "stream": False}
    for attempt in range(1, ATTEMPTS + 1):
        counter.calls += 1
        try:
            response = await client.post("/chat/completions", json=body)
            response.raise_for_status()
            value = response.json()
            choice = value["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError(f"the reply ended with {choice['finish_reason']}")
            counter.prompt_tokens += value["usage"]["prompt_tokens"]
            counter.completion_tokens += value["usage"]["completion_tokens"]
            return (choice["message"].get("content") or "").strip()
        except (httpx.HTTPError, ValueError, KeyError) as error:
            if attempt == ATTEMPTS:
                raise RuntimeError(f"the model did not answer: {type(error).__name__}") from None
            await asyncio.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def parse_json(text: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = min((index for index in (text.find("["), text.find("{")) if index >= 0), default=0)
    return json.loads(text[start:])


def check_endpoint(endpoint: str) -> None:
    from urllib.parse import urlsplit

    parts = urlsplit(endpoint)
    if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "::1"} or parts.path.rstrip("/") != "/v1":
        raise ValueError("The endpoint must be a loopback Ollama server's /v1 URL")


async def generate(out: Path, *, endpoint: str = ENDPOINT, plan: list[tuple[str, str, int]] | None = None,
                   round: int = 1) -> dict:
    """Ask for candidate questions: one request per (corpus, type, count) of the plan. Writes candidates.json under ``out``.

    The default plan asks for ``PER_TYPE`` questions of every type in every corpus. A later ``round`` uses another
    seed and numbers its candidates apart, so a second request for the same corpus and type is a new draw.
    """
    check_endpoint(endpoint)
    counter = Counter()
    candidates = []
    plan = plan or [(name, kind, PER_TYPE[name]) for name in CORPORA for kind in TYPES]
    async with httpx.AsyncClient(base_url=endpoint, timeout=httpx.Timeout(600, connect=30)) as client:
        for name, kind, count in plan:
            listing = memory_lines(memories_of(name))
            known = {memory.id for memory in memories_of(name)}
            system = SYSTEM.format(count=count, type=kind, instruction=INSTRUCTIONS[kind])
            reply = await ask(client, counter, system, f"Memories:\n{listing}", seed=SEED + round - 1)
            try:
                items = parse_json(reply)
            except ValueError:
                items = []
            for number, item in enumerate(items, start=1):
                ids = [value for key in ("answer_ids", "support_ids", "stale_ids") for value in item.get(key, [])]
                candidates.append({
                    "id": f"{name}:{kind}:{'' if round == 1 else f'r{round}.'}{number}", "corpus": name, "type": kind,
                    "question": item.get("question", ""), "answer": item.get("answer", ""),
                    "answers": item.get("answer_ids", []), "support": item.get("support_ids", []),
                    "stale": item.get("stale_ids", []), "unknown_ids": sorted(set(ids) - known)})
    result = {"model": MODEL_LABEL, "round": round, "calls": counter.calls, "prompt_tokens": counter.prompt_tokens,
              "completion_tokens": counter.completion_tokens, "prompt_sha256": PROMPT_SHA256, "candidates": candidates}
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidates.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


async def paraphrase(out: Path, *, endpoint: str = ENDPOINT) -> dict:
    """Three paraphrases of each frozen question, one request per corpus. Writes paraphrases.json under ``out``."""
    check_endpoint(endpoint)
    frozen = load_blind()
    counter = Counter()
    rewritten: dict[str, list[str]] = {}
    async with httpx.AsyncClient(base_url=endpoint, timeout=httpx.Timeout(600, connect=30)) as client:
        for name in CORPORA:
            questions = [question for question in frozen["questions"] if question["corpus"] == name]
            listing = "\n".join(f"{question['id']}: {question['question']}" for question in questions)
            for attempt in range(ATTEMPTS):
                parsed = parse_json(await ask(client, counter, "You rewrite questions faithfully.",
                                              PARAPHRASE.format(questions=listing)))
                if all(isinstance(parsed.get(question["id"]), list) and len(parsed[question["id"]]) == 3
                       for question in questions):
                    break
            rewritten.update({question["id"]: [str(text).strip() for text in parsed.get(question["id"], [])]
                              for question in questions})
    result = {"kind": PARAPHRASE_KIND, "schema_version": SCHEMA_VERSION, "model": MODEL_LABEL,
              "blind_sha256": hashlib.sha256(BLIND_FILE.read_bytes()).hexdigest(), "calls": counter.calls,
              "prompt_tokens": counter.prompt_tokens, "completion_tokens": counter.completion_tokens,
              "paraphrases": rewritten}
    out.mkdir(parents=True, exist_ok=True)
    (out / "paraphrases.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


CANDIDATES_FILE = FIXTURES / "blind-candidates-v1.json"
REVIEW_FILE = FIXTURES / "blind-review-v1.json"
# Two questions are the same when this share of their words is shared.
SAME_QUESTION = 0.8


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def freeze(candidates: dict, review: dict, corpora: dict | None = None, name: str = "blind-v1") -> dict:
    """The frozen question file: the candidates the reviewer judged answerable, less the repeats.

    A candidate repeats when 80 percent of its words are shared with a question the gold files already ask (or the
    held-out store questions), or with a candidate kept before it, or when an earlier kept question asks for the
    same answer memories under the same type. A question that one memory answers is a lookup whatever the model
    called it. The result is a pure function of the candidates and the reviewer's decisions.
    """
    corpora = corpora or CORPORA
    gold = {corpus: json.loads((FIXTURES / f"{corpus}.json").read_text())["questions"] for corpus in corpora}
    known: dict[str, list[set[str]]] = {corpus: [_words(item["question"]) for item in gold[corpus]]
                                        + [_words(variant) for item in gold[corpus] for variant in item.get("variants", [])]
                                        for corpus in corpora}
    if "store-v1" in known:
        heldout = json.loads((FIXTURES.parent / "fast-cycle" / "store-heldout-v1.json").read_text())
        known["store-v1"] += [_words(item["question"]) for item in heldout["key"]["questions"]]
    dropped = {"not answerable, ambiguous or several answers": 0,
               "repeats an existing or earlier question (word overlap 0.8 or more)": 0,
               "asks the same fact and type as an earlier kept question": 0}
    seen: set[tuple] = set()
    kept = []
    for candidate in candidates["candidates"]:
        corpus, key = candidate["corpus"], candidate["id"].split(":", 1)[1]
        if key not in review["answerable"][corpus]:
            dropped["not answerable, ambiguous or several answers"] += 1
            continue
        words = _words(candidate["question"])
        if any(len(words & other) / len(words | other) >= SAME_QUESTION for other in known[corpus]):
            dropped["repeats an existing or earlier question (word overlap 0.8 or more)"] += 1
            continue
        fixed = review["ids_corrected"].get(candidate["id"])
        answers = fixed["answers"] if fixed else candidate["answers"]
        support = fixed["support"] if fixed else candidate["support"]
        kind = review["retag"].get(candidate["id"], candidate["type"])
        if kind == "multi-hop" and not set(support) - set(answers):
            kind = "lookup"
        slot = (corpus, kind, frozenset(answers))
        if slot in seen:
            dropped["asks the same fact and type as an earlier kept question"] += 1
            continue
        seen.add(slot)
        known[corpus].append(words)
        kept.append({"id": candidate["id"], "corpus": corpus, "type": kind, "question": candidate["question"],
                     "answer": candidate["answer"], "answers": answers, "support": support, "stale": candidate["stale"],
                     **({"retagged_from": candidate["type"]} if kind != candidate["type"] else {}),
                     **({"ids_corrected": True} if fixed else {})})
    requests = sum(item["requests"] for item in candidates["rounds"])
    return {"kind": KIND, "schema_version": SCHEMA_VERSION, "name": name,
            "description": "Questions a local model wrote about the memory sets from the memories alone, then "
                           "reviewed for answerability. See benchmarks/graph_oracle/BLIND.md and GATE.md.",
            "generator": {"model": candidates["model"], "requests": requests, "prompt_sha256": candidates["prompt_sha256"],
                          "rounds": len(candidates["rounds"])},
            "review": {"candidates": len(candidates["candidates"]), "kept": len(kept), "dropped": dropped,
                       "ids_corrected": sum("ids_corrected" in item for item in kept),
                       "retagged": sum("retagged_from" in item for item in kept)},
            "questions": kept}


def load_blind(path: Path = BLIND_FILE) -> dict:
    value = json.loads(path.read_text())
    if value.get("kind") != KIND or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a blind question file")
    known = {name: {memory.id for memory in memories_of(name)} for name in {**CORPORA, **CORPORA_DRAW2}}
    seen = set()
    for question in value["questions"]:
        if question["id"] in seen or question["corpus"] not in known or question["type"] not in TYPES:
            raise ValueError(f"{path}: bad question {question['id']}")
        seen.add(question["id"])
        if not question["answers"] or set(question["answers"] + question.get("support", []) + question.get("stale", [])) - known[question["corpus"]]:
            raise ValueError(f"{path}: question {question['id']} names no answer or an unknown memory")
    return value


def load_paraphrases(path: Path = PARAPHRASE_FILE, blind_path: Path = BLIND_FILE) -> dict[str, list[str]]:
    value = json.loads(path.read_text())
    if value.get("kind") != PARAPHRASE_KIND or value["blind_sha256"] != hashlib.sha256(blind_path.read_bytes()).hexdigest():
        raise ValueError(f"{path} is not the paraphrases of {blind_path}")
    return value["paraphrases"]


def blind_sha256(path: Path = BLIND_FILE) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = ["TYPES", "generate", "paraphrase", "freeze", "load_blind", "load_paraphrases", "blind_sha256"]

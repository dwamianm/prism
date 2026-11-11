"""The one decision primitive: ``decide(question, options) -> (index, confidence)``.

A closed choice, never free text. The options are listed under letters (A, B, C, ...) and the model is asked for
one letter. The model generates a single token with ``top_logprobs=20``; the choice is the option whose letter has
the highest log probability among the option letters, so an answer outside the options cannot happen. The
confidence is that letter's probability renormalized over the option letters. If no option letter is among the 20
most likely first tokens, one constrained retry asks for JSON ``{"answer": <letter>}`` with an Ollama ``format``
schema whose enum is the option letters (logged as ``fallback``; its confidence is ``None``).

Why letters and a first-token read instead of a JSON string enum: a schema of ``{"type": "string", "enum": [...]}``
forces a leading quote that the model itself rated at log probability -15, and the forced token moved the next
choice (on a mechanics probe, the unconstrained first token was "yes" at p=0.85 and the constrained answer was
"no"). A letter is one token and needs no forced prefix.

Backends, behind one function so a hosted model can be swapped in later:

- ``ollama``: a local Ollama model, thinking off, temperature 0, a fixed seed, one output token.
- ``random``: a seeded random option, a function of the seed and the prompt only. Same code path otherwise.
- ``systemone``: a model with Ollama's "decision" capability (clef-flash, clef) through its native endpoint,
  /v1/systemone, which returns a label and a probability per label (see ``systemone_request`` below).

Every decision is logged (one JSON line: tag, prompt hash, choice, confidence, milliseconds, cached or not) and
cached on disk by prompt hash, so a rerun with the same cache directory is free and exact. A decision with one
option makes no call and is not logged or counted.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import string
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434"
LABELS = string.ascii_uppercase
INSTRUCTION = "Answer with the letter of one option."
TEMPLATE_VERSION = 1


def render(question: str, options: list[str]) -> str:
    """The full prompt for a decision: the question, the lettered options, the instruction."""
    lines = [question.rstrip(), "", "Options:"]
    lines += [f"{LABELS[i]}. {option}" for i, option in enumerate(options)]
    lines += ["", INSTRUCTION]
    return "\n".join(lines)


@dataclass
class Decision:
    tag: str
    key: str
    choice: int
    confidence: float | None
    n_options: int
    cost_ms: float          # what the model took when this prompt was first answered (0 for random)
    ms: float               # what this call took now (about 0 on a cache hit)
    cached: bool
    fallback: bool


@dataclass
class Decider:
    backend: str                         # "ollama" or "random"
    model: str | None
    cache_dir: Path
    log_path: Path
    seed: int = 7
    decisions: list[Decision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.backend not in ("ollama", "random", "systemone"):
            raise ValueError(f"unknown backend {self.backend!r}")
        if self.backend in ("ollama", "systemone") and not self.model:
            raise ValueError(f"the {self.backend} backend needs a model")
        if len(LABELS) < 2:
            raise AssertionError
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    # The primitive ------------------------------------------------------------------------------------------------

    def decide(self, question: str, options: list[str], *, tag: str = "") -> tuple[int, float | None]:
        if not options:
            raise ValueError("decide() needs at least one option")
        if len(options) > len(LABELS):
            raise ValueError(f"at most {len(LABELS)} options")
        if len(options) == 1:
            return 0, 1.0
        if self.backend == "systemone":
            prompt = json.dumps(systemone_request(self.model, question, options), sort_keys=True)
        else:
            prompt = render(question, options)
        key = self._key(prompt, len(options))
        path = self.cache_dir / f"{key}.json"
        started = time.perf_counter()
        if path.exists():
            entry = json.loads(path.read_text())
            cached = True
        else:
            entry = self._ask(prompt, len(options), key)
            path.write_text(json.dumps(entry))
            cached = False
        ms = (time.perf_counter() - started) * 1000
        decision = Decision(tag=tag, key=key, choice=entry["choice"], confidence=entry["confidence"],
                            n_options=len(options), cost_ms=entry["ms"], ms=round(ms, 2), cached=cached,
                            fallback=entry["fallback"])
        self.decisions.append(decision)
        with self.log_path.open("a") as log:
            extra = {k: entry[k] for k in ("probabilities", "api_confidence", "choice_is_argmax") if k in entry}
            log.write(json.dumps({**decision.__dict__, "prompt_sha256": key, "options": options, **extra}) + "\n")
        return entry["choice"], entry["confidence"]

    # Internals ------------------------------------------------------------------------------------------------

    def _key(self, prompt: str, n: int) -> str:
        identity = {"backend": self.backend, "model": self.model, "seed": self.seed, "n": n,
                    "template": TEMPLATE_VERSION, "prompt": prompt}
        if self.backend == "systemone":
            identity["transport"] = SYSTEMONE_TRANSPORT
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def _ask(self, prompt: str, n: int, key: str) -> dict:
        if self.backend == "random":
            choice = random.Random(f"{self.seed}:{key}").randrange(n)
            return {"choice": choice, "confidence": round(1 / n, 4), "ms": 0.0, "fallback": False, "top": []}
        if self.backend == "systemone":
            return _systemone(json.loads(prompt), n)
        started = time.perf_counter()
        response = _chat(self.model, prompt, seed=self.seed, num_predict=1, top_logprobs=20)
        top = response.get("logprobs", [{}])[0].get("top_logprobs", [])
        scores: dict[int, float] = {}
        for item in top:
            token = item["token"].strip()
            if len(token) == 1 and token in LABELS[:n]:
                index = LABELS.index(token)
                scores[index] = _logaddexp(scores.get(index, -math.inf), item["logprob"])
        fallback = not scores
        if fallback:
            schema = {"type": "object", "properties": {"answer": {"type": "string", "enum": list(LABELS[:n])}},
                      "required": ["answer"]}
            retry = _chat(self.model, prompt + '\nReply as JSON: {"answer": "<letter>"}', seed=self.seed,
                          num_predict=16, format=schema)
            choice = LABELS.index(json.loads(retry["message"]["content"])["answer"])
            confidence = None
        else:
            best = max(scores.values())
            choice = min(i for i, s in scores.items() if s == best)      # ties go to the earlier option
            total = sum(math.exp(s - best) for s in scores.values())
            confidence = round(1 / total, 4)
        ms = (time.perf_counter() - started) * 1000
        return {"choice": choice, "confidence": confidence, "ms": round(ms, 2), "fallback": fallback,
                "top": [(item["token"], round(item["logprob"], 3)) for item in top[:5]]}

    # Accounting -----------------------------------------------------------------------------------------------

    def mark(self) -> int:
        """A position in the decision list; ``since(mark)`` gives the decisions made after it."""
        return len(self.decisions)

    def since(self, mark: int) -> list[Decision]:
        return self.decisions[mark:]


def _logaddexp(a: float, b: float) -> float:
    if a == -math.inf:
        return b
    high = max(a, b)
    return high + math.log(math.exp(a - high) + math.exp(b - high))


def _chat(model: str, prompt: str, *, seed: int, num_predict: int, top_logprobs: int = 0, format=None) -> dict:
    body: dict = {"model": model, "think": False, "stream": False, "keep_alive": "30m",
                  "messages": [{"role": "user", "content": prompt}],
                  "options": {"temperature": 0, "seed": seed, "num_predict": num_predict}}
    if top_logprobs:
        body["logprobs"] = True
        body["top_logprobs"] = top_logprobs
    if format is not None:
        body["format"] = format
    request = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as handle:
        return json.load(handle)


# The decision-model transport (models with the Ollama "decision" capability, such as clef-flash and clef) -------
#
# Found from the server, not guessed: `ollama run --help` lists --ask, --choice label="description" and --level for
# decision models, and running `ollama run clef-flash --ask ... --choice ...` through a logging relay showed the CLI
# posting to /v1/systemone:
#   {"model": M, "state": <the data>, "questions": {"q1": {"type": "choice", "instructions": <the question>,
#    "criteria": {<label>: <description>, ...}}}}
# and getting back {"answers": {"q1": {"type": "choice", "choice": <label>, "probabilities": {<label>: p, ...},
# "confidence": c}}, "usage": {"input_tokens": n, "output_tokens": 0}}. "criteria" must be an object; "state" is
# required. No token is generated (output_tokens 0): the answer is a score over the labels, not a sample.
#
# What changes from the chat transport, and nothing else: the frozen question text is split at its last blank line,
# the part before it (the question, records, statements) becomes "state" and the last paragraph (the question asked)
# becomes "instructions"; the options become "criteria" with letters as labels and each option's text, unchanged,
# as its description (the CLI's billing="Payments and refunds" form). The line "Answer with the letter of one
# option." is not sent: the interface returns a label itself. The choice is the returned label; the confidence is
# that label's probability. The response's own "confidence" field is logged as api_confidence.
SYSTEMONE_TRANSPORT = "systemone-v1"


def systemone_request(model: str, question: str, options: list[str]) -> dict:
    head, sep, tail = question.rstrip().rpartition("\n\n")
    state, instructions = (head, tail) if sep else ("", question.rstrip())
    return {"model": model, "state": state, "questions": {"q1": {
        "type": "choice", "instructions": instructions,
        "criteria": {LABELS[i]: option for i, option in enumerate(options)}}}}


def _systemone(request: dict, n: int) -> dict:
    started = time.perf_counter()
    http = urllib.request.Request(f"{OLLAMA_URL}/v1/systemone", data=json.dumps(request).encode(),
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(http, timeout=120) as handle:
        response = json.load(handle)
    ms = (time.perf_counter() - started) * 1000
    answer = response["answers"]["q1"]
    label = answer["choice"]
    if label not in LABELS[:n]:
        raise RuntimeError(f"the decision model returned {label!r}, not one of the option labels")
    probabilities = answer.get("probabilities", {})
    best = max(probabilities, key=probabilities.get) if probabilities else label
    return {"choice": LABELS.index(label), "confidence": round(probabilities.get(label, float("nan")), 4),
            "ms": round(ms, 2), "fallback": False, "api_confidence": answer.get("confidence"),
            "choice_is_argmax": best == label, "usage": response.get("usage"),
            "prompt_eval_cached_count": response.get("prompt_eval_cached_count"),
            "probabilities": {k: round(v, 4) for k, v in probabilities.items()}}


def ollama_identity(model: str | None) -> dict:
    """The server version and the model's digest, recorded with every run."""
    if model is None:
        return {}
    with urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=10) as handle:
        version = json.load(handle)["version"]
    with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=10) as handle:
        digest = next((m["digest"] for m in json.load(handle)["models"] if m["name"] == model), None)
    return {"ollama_version": version, "model_digest": digest}


def warm(decider: Decider) -> float:
    """Load the model before timing starts (not cached, not logged). Returns the milliseconds it took, which is
    the load time when the model was not loaded."""
    started = time.perf_counter()
    if decider.backend == "ollama":
        _chat(decider.model, "Say A.", seed=decider.seed, num_predict=1)
    elif decider.backend == "systemone":
        _systemone(systemone_request(decider.model, f"Warm up {time.time()}\n\nIs this a test?", ["yes", "no"]), 2)
    return round((time.perf_counter() - started) * 1000, 1)


def loaded_models() -> list[str]:
    with urllib.request.urlopen(f"{OLLAMA_URL}/api/ps", timeout=10) as handle:
        return [m["name"] for m in json.load(handle)["models"]]


# The module-level primitive, as the brief names it -------------------------------------------------------------

_current: Decider | None = None


def configure(decider: Decider) -> Decider:
    global _current
    _current = decider
    return decider


def decide(question: str, options: list[str], *, tag: str = "") -> tuple[int, float | None]:
    if _current is None:
        raise RuntimeError("call configure() first")
    return _current.decide(question, options, tag=tag)

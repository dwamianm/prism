"""Reader and judge calls through a local Ollama server, for the DeepSeek answer track.

The GPT-5.4 comparison sends its reader and judge to OpenAI's Responses API
through ``gpt54_budget``, whose source the registration pins, so this track has
its own client rather than a switch inside that module. It keeps the same
records and HTTP retry policy: the request, every HTTP attempt and the answer
are each written once and never overwritten; a transient 429 or 5xx is retried
up to four identical attempts; a malformed response and an ambiguous transport
failure are never retried. A truncated response, or a judge's empty one, is not
an answer either, unless the caller asks for it (``truncated_ok``,
``empty_ok``): the track's amended failure policy (#132, in ``gpt54_baselines``)
then records it as it is and decides whether to send the request once more.
Failure messages match ``gpt54_budget.call`` so the baseline arms classify them
the same way.

Calls go to Ollama's OpenAI-compatible chat completions endpoint. The endpoint
must be a loopback address and no credentials are sent, so this path cannot
reach a paid API. A ``:cloud`` model is served by Ollama's hosted service: the
prompts leave this machine and count against the Ollama account's usage limits,
but nothing is charged per request, so receipts record zero cost.

Only cloud models are accepted. The OpenAI-compatible endpoint cannot set a
context size, so a local model would run at the server's default context and
could cut long prompts short without saying so; a cloud model is served at its
full context.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from benchmarks.diagnostics.reader_judge import matches_response_model
from benchmarks.integrations.gpt54_budget import JUDGE_LIMIT, READER_LIMIT, sha, write_new
from benchmarks.integrations.run_longmemeval_v2 import _ollama_reader_identity

PROVIDER = "ollama"
MODEL = "deepseek-v4.1-flash:cloud"
ENDPOINT = "http://127.0.0.1:11434/v1"
TEMPERATURE = 0
# The registration's bootstrap seed. Earlier DeepSeek runs used other seeds; this
# track starts a new series, so it takes the comparison's own.
SEED = 20260923
# Thinking off, as in the repository's earlier DeepSeek runs. The reader prompts
# already ask for the evidence to be explained before the answer, and the judge
# must answer a bare yes or no.
REASONING_EFFORT = "none"
ATTEMPTS = 4
TIMEOUT_SECONDS = 900
TRANSIENT = frozenset({429, 500, 502, 503, 504, 529})
# Literal loopback addresses only: "localhost" depends on the hosts file.
_LOOPBACK = frozenset({"127.0.0.1", "::1"})
# Identity fields that name the model. The server version is left out, so an
# Ollama upgrade does not look like a different model.
_IDENTITY_KEYS = ("provider", "model", "resolved_model", "manifest_digest_sha256", "model_digest_sha256",
                  "remote_host", "remote_model")


def check_endpoint(endpoint: str) -> None:
    """Only a loopback Ollama server's ``/v1`` URL, without credentials, may receive prompts."""
    parts = urlsplit(endpoint)
    if (parts.scheme != "http" or parts.hostname not in _LOOPBACK or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment or parts.path.rstrip("/") != "/v1"):
        raise ValueError("The answer endpoint must be a loopback Ollama server's /v1 URL without credentials")


@dataclass(frozen=True)
class AnswerModel:
    """The reader and judge of an Ollama answer run, and the settings every request sends."""

    model: str = MODEL
    endpoint: str = ENDPOINT
    temperature: float = TEMPERATURE
    seed: int = SEED
    reasoning_effort: str = REASONING_EFFORT

    def __post_init__(self) -> None:
        check_endpoint(self.endpoint)
        if not self.model or any(character.isspace() for character in self.model):
            raise ValueError("The Ollama model must be a nonempty tag")

    @property
    def track(self) -> str:
        """The label this model's answers are filed under, apart from the GPT-5.4 track."""
        return "ollama-" + re.sub(r"[^a-z0-9.]+", "-", self.model.lower()).strip("-")

    def body(self, prompt: str, limit: int) -> dict:
        return {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature, "seed": self.seed, "max_tokens": limit,
                "reasoning_effort": self.reasoning_effort, "stream": False}

    def settings(self) -> dict:
        """What every result records about the reader and judge, including the HTTP retry settings.

        ``gpt54_baselines`` adds the failure policy the answers are scored under.
        """
        return {"provider": PROVIDER, "api": "chat.completions", "endpoint": self.endpoint, "model": self.model,
                "temperature": self.temperature, "seed": self.seed, "reasoning_effort": self.reasoning_effort,
                "stream": False, "reader_limit": READER_LIMIT, "judge_limit": JUDGE_LIMIT, "attempts": ATTEMPTS,
                "timeout_seconds": TIMEOUT_SECONDS}


def identity(model: AnswerModel) -> dict:
    """The Ollama server's description of the model. A cloud model's remote weights are not pinned.

    The lookup goes through ``run_longmemeval_v2``'s helper, which other
    registrations pin, so it keeps that helper's urllib defaults: unlike
    ``client_for`` it honors proxy settings. It carries no prompt or credential.
    """
    parts = urlsplit(model.endpoint)
    return _ollama_reader_identity(urlunsplit((parts.scheme, parts.netloc, "", "", "")), model.model)


def describe(model: AnswerModel) -> dict:
    """The model's settings and the server's identity for it. Only a cloud model is accepted."""
    found = identity(model)
    if found.get("provider") != "ollama_cloud":
        raise ValueError(f"{model.model} is not an Ollama cloud model. The OpenAI-compatible endpoint cannot set "
                         "a context size, so a local model could cut long prompts short.")
    return {**model.settings(), "identity": found}


def same_model(recorded: dict, current: dict) -> bool:
    """Whether two recorded settings name the same model, endpoint and sampling."""
    def key(value: dict) -> tuple:
        identity_value = value.get("identity") or {}
        return ({name: item for name, item in value.items() if name != "identity"},
                tuple(identity_value.get(name) for name in _IDENTITY_KEYS))
    return key(recorded) == key(current)


def client_for(model: AnswerModel) -> httpx.AsyncClient:
    """A client for the loopback endpoint. It sends no credentials and ignores proxy settings."""
    check_endpoint(model.endpoint)
    return httpx.AsyncClient(base_url=model.endpoint, timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=30),
                             trust_env=False)


def _completion(body: dict, model: AnswerModel) -> tuple[str, str]:
    """The text and finish reason of one chat completion from the requested model, with valid token accounting."""
    choices = body.get("choices")
    if (body.get("object") != "chat.completion" or not matches_response_model(model.model, body.get("model"))
            or not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get("finish_reason"), str)):
        raise ValueError("Incomplete response or changed model")
    counts = body.get("usage")
    if not isinstance(counts, dict) or any(type(counts.get(name)) is not int or counts[name] < 0
                                           for name in ("prompt_tokens", "completion_tokens")):
        raise ValueError("Missing token accounting")
    cached = (counts.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    if type(cached) is not int or not 0 <= cached <= counts["prompt_tokens"]:
        raise ValueError("Invalid cached token count")
    text = (choices[0].get("message") or {}).get("content")
    if text is not None and not isinstance(text, str):
        raise ValueError("Missing answer text")
    return (text or "").strip(), choices[0]["finish_reason"]


def response_text(body: dict, model: AnswerModel) -> str:
    """The answer of a complete chat completion from the requested model; anything else is not an answer."""
    return answer_record(body, model)["text"]


def answer_record(body: dict, model: AnswerModel, *, truncated_ok: bool = False, empty_ok: bool = False) -> dict:
    """What a response answered: its text, and ``truncated`` when it ended early.

    A response that ended for any reason other than ``stop`` counts only with
    ``truncated_ok``, and one that ended normally with no text only with
    ``empty_ok``; anything else that is not a whole answer raises.
    """
    text, finish = _completion(body, model)
    if finish != "stop":
        if truncated_ok:
            return {"text": text, "truncated": True}
        raise ValueError("Incomplete response or changed model")
    if not text and not empty_ok:
        raise ValueError("Missing answer text")
    return {"text": text}


def usage(body: dict) -> dict:
    """Provider token counts in the baseline arms' names. Ollama does not report reasoning tokens."""
    counts = body["usage"]
    return {"input_tokens": counts["prompt_tokens"],
            "cached_input_tokens": (counts.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            "output_tokens": counts["completion_tokens"]}


async def call(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, model: AnswerModel, prompt: str,
               limit: int, path: Path, *, truncated_ok: bool = False, empty_ok: bool = False) -> dict:
    """One reader or judge request, with every attempt kept. Returns the answer record written to ``path``.

    A response that ended for any reason other than ``stop`` is not an answer.
    With ``truncated_ok`` it is still recorded and returned, marked
    ``truncated``, with whatever text it had; otherwise it fails like any
    other incomplete response. With ``empty_ok``, a response that ended
    normally with no text is recorded with empty text (``answer_record``).
    """
    path = Path(path)
    body = model.body(prompt, limit)
    request_sha256 = sha(body)
    write_new(path.with_suffix(".request.json"), body)
    started = time.perf_counter()
    async with semaphore:
        for attempt in range(1, ATTEMPTS + 1):
            stamp = time.perf_counter()
            try:
                response = await client.post("/chat/completions", json=body)
            except Exception as exc:
                write_new(path.with_suffix(f".attempt-{attempt}.json"), {
                    "exception_type": type(exc).__name__, "request_sha256": request_sha256,
                    "endpoint": model.endpoint})
                raise RuntimeError("Ambiguous provider failure retained without retry") from None
            try:
                value = response.json()
            except ValueError:
                value = None
            record = {"http_status": response.status_code, "response": value, "seconds": time.perf_counter() - stamp,
                      "request_sha256": request_sha256, "endpoint": model.endpoint}
            if value is None:
                record["text"] = response.text[:2000]
            write_new(path.with_suffix(f".attempt-{attempt}.json"), record)
            if not response.is_success:
                if response.status_code in TRANSIENT and attempt < ATTEMPTS:
                    await asyncio.sleep(min(8, 2 ** attempt))
                    continue
                raise RuntimeError(f"Provider HTTP {response.status_code}; attempt retained")
            if not isinstance(value, dict):
                # A response arrived and was not an answer: final.
                raise ValueError("Malformed provider response")
            answer = answer_record(value, model, truncated_ok=truncated_ok, empty_ok=empty_ok)
            result = {**answer, "response": value, "attempts": attempt, "seconds": time.perf_counter() - started,
                      "request_sha256": request_sha256, "endpoint": model.endpoint}
            write_new(path, result)
            return result
    # Unreachable, as in gpt54_budget.call: the last attempt returns or raises.
    raise RuntimeError("Provider attempts exhausted")


def verify_call(path: Path, prompt: str, limit: int, *, model: AnswerModel, truncated_ok: bool = False,
                empty_ok: bool = False) -> dict:
    """The recorded call, after checking it against the request this prompt makes and the retry policy.

    The recorded text, and whether the call is marked truncated, must be what
    its response gives (``answer_record``), so a truncated or empty call is
    accepted only with ``truncated_ok`` or ``empty_ok``.
    """
    value = json.loads(path.read_text())
    expected = model.body(prompt, limit)
    if json.loads(path.with_suffix(".request.json").read_text()) != expected:
        raise ValueError("Provider request differs from the prompt and settings")
    answer = answer_record(value["response"], model, truncated_ok=truncated_ok, empty_ok=empty_ok)
    recorded = {key: value[key] for key in ("text", "truncated") if key in value}
    # Compared with types too, since 1 == True: only a real true marks a call as truncated.
    if (value.get("request_sha256") != sha(expected) or value.get("endpoint") != model.endpoint
            or recorded != answer or any(type(recorded[key]) is not type(answer[key]) for key in answer)):
        raise ValueError("Provider response binding differs")
    if not 1 <= value["attempts"] <= ATTEMPTS:
        raise ValueError("Attempt limit differs")
    attempts = [json.loads(path.with_suffix(f".attempt-{number}.json").read_text())
                for number in range(1, value["attempts"] + 1)]
    if any(attempt.get("request_sha256") != value["request_sha256"] or attempt.get("endpoint") != model.endpoint
           for attempt in attempts):
        raise ValueError("Provider attempt request differs")
    if any(attempt.get("http_status") not in TRANSIENT for attempt in attempts[:-1]):
        raise ValueError("Retry outside the registered policy")
    final = attempts[-1]
    if not 200 <= final.get("http_status", 0) < 300 or final["response"] != value["response"]:
        raise ValueError("Provider attempt record differs")
    value["verified_http_statuses"] = [str(attempt["http_status"]) for attempt in attempts]
    return value

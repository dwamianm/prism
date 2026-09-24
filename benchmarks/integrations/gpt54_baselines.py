"""Baseline arms for the registered GPT-5.4 LoCoMo and LongMemEval-S comparison.

The 2026-09-23 comparison has no reference points, so nobody can tell how much
of its gap belongs to the memory system. These arms add them:

- ``full-context`` (LoCoMo only): the whole conversation, one date header per
  session and one ``Speaker: text`` line per turn.
- ``plain-vector``, ``plain-bm25`` and ``plain-rrf``: plain RAG over the raw
  turns stored in the saved memory packs. Turns are ranked by the vector index,
  by the BM25 index, or by reciprocal rank fusion (k=60) of the two. They are
  then packed in rank order, one ``(date) speaker: text`` record per turn, up to
  PRME's budget (3,996 tokens). PRME's scoring, expansion, filters and renderer
  are not used.
- ``prme``: PRME's own ``retrieve()`` with the current defaults, replayed over
  the saved packs by the offline evidence gate, rendered as the product renders
  it, at the same budget. ``prme-<name>`` is a named variant: the same replay
  with the settings ``--set`` changes, so a variant can be answered next to the
  defaults and compared with them question by question (``compare``).

Every arm reuses the registered reader, judge, prompts, model settings and
question sets of ``run_gpt54_comparison``, and leaves that frozen module
unchanged. Its ``validate()`` cannot be reused, because it also pins every
``src/prme`` file and those have changed since registration, so
``registered_protocol`` checks the parts the arms depend on.

``prepare`` and ``estimate`` make no model calls. ``prepare`` for a plain or
``prme`` arm runs the offline evidence gate with the same ranking, so its gate
report describes exactly the contexts the reader will see. ``run`` makes paid
reader and judge calls. Start it only after the owner approves the spend: the
CLI asks for confirmation in an interactive terminal, and the approved
``--max-usd`` is the arm's spending cap, which the ledger enforces.

``--provider ollama`` answers with a separate track instead: the same prompts
and questions, with ``deepseek-v4.1-flash:cloud`` as reader and judge through
the local Ollama server (``ollama_answers``). It never builds an OpenAI client
or reads an API key, keeps its contexts and answers apart from the GPT-5.4
track, and needs its own ``calibrate`` before ``run``. The ``prme`` arms and
``--sample`` smoke checks run on this track only. Its scores are not comparable
with GPT-5.4 scores; compare DeepSeek runs only with each other.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
import fcntl
from functools import partial
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
from unittest.mock import patch

from benchmarks.diagnostics import product_packing as gate
from benchmarks.integrations import ollama_answers
from benchmarks.integrations import run_gpt54_comparison as study
from benchmarks.integrations.gpt54_budget import (
    JUDGE_LIMIT, MODEL, READER_LIMIT, BudgetExhausted, Ledger, call, client_for, digest, sha, usage_cost,
    write_new,
)
from benchmarks.integrations.gpt54_official_prompt_loader import load_prompt
from prme.retrieval.tokenization import count_tokens

# Private records live in the main checkout, like the saved run's archive, so
# every worktree shares one spending ledger, run lock and set of answers per arm.
DATA = study.ORIGINAL / "data" / "gpt54-baselines-v1"
# Each Ollama model's track keeps its own contexts and answers under here, so no
# DeepSeek answer can be read as a GPT-5.4 one, or the reverse.
OLLAMA_DATA = study.ORIGINAL / "data" / "ollama-answers-v1"
RESULTS = study.ROOT / "benchmarks" / "results" / "research"
_VARIANT = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
ARMS = {"prme": gate.GATE_BENCHMARKS, "full-context": ("locomo",),
        **{f"plain-{method}": gate.GATE_BENCHMARKS for method in gate.PLAIN_METHODS}}
# What gpt54_budget.call and client_for send for the registered GPT-5.4 track.
OPENAI_ANSWER_MODEL = {"provider": "openai", "api": "responses", "endpoint": "https://api.openai.com/v1",
                       "model": MODEL, "reasoning_effort": "medium", "service_tier": "flex",
                       "reader_limit": READER_LIMIT, "judge_limit": JUDGE_LIMIT}
# The registered calibration's authored cases. The frozen run_gpt54_comparison.calibrate
# keeps them inline, so the Ollama track's calibration repeats them here.
AUTHORED_CASES = (
    ("The blue box holds a brass key.", "What does the blue box hold?", "a brass key"),
    ("On 2024-01-01 I launched Cedar. On 2024-01-06 I delivered it.",
     "How many days passed between launch and delivery?", "5 days"),
)
AUTHORED_WRONG_ANSWER = "a red balloon"
# Registered sources that fix the request body, retries, prompts and question rows.
FROZEN_SOURCES = ("benchmarks/integrations/gpt54_budget.py", "benchmarks/integrations/run_gpt54_comparison.py",
                  "benchmarks/integrations/run_longmemeval_s_baseline.py")
RETRY_POLICY = (
    "Within a run, the registered policy applies: at most four identical HTTP attempts for transient 429/5xx, "
    "no retry of truncated, invalid or ambiguous responses, and the first failure stops new questions. A later "
    "run asks a question again, in a new attempt, only when no answer or verdict was received: a budget stop, "
    "a provider HTTP error, an ambiguous transport failure or an interrupted process. Truncated or malformed "
    "responses and invalid verdicts are final and leave the arm incomplete. Every attempt is kept."
)
# Messages from gpt54_budget.call and ollama_answers.call for failures that returned no answer text.
_NO_ANSWER_FAILURES = ("Provider HTTP ", "Ambiguous provider failure", "Provider attempts exhausted")
_ATTEMPT = re.compile(r"attempt-(\d+)")
# A judge prompt holds the question, the reference and a reader answer of at most READER_LIMIT tokens.
_JUDGE_BYTES = 4 * READER_LIMIT + 4096


# Contexts ------------------------------------------------------------------

def full_context(conversation: dict) -> str:
    """The whole conversation: a date header per session, one ``Speaker: text`` line per turn.

    Turns come from the registered source boundary (``source_turns``), so the
    reader sees the same text PRME stored: empty turns are left out and image
    captions are kept.
    """
    sessions: dict[int, list[str]] = {}
    for turn in study.source_turns(conversation):
        number = turn["metadata"]["source_session"]
        prefix = f"({conversation[f'session_{number}_date_time']}) "
        if not turn["content"].startswith(prefix):
            raise ValueError("A source turn does not start with its session date")
        sessions.setdefault(number, []).append(turn["content"][len(prefix):])
    return "\n\n".join(f"Session {number} ({conversation[f'session_{number}_date_time']})\n" + "\n".join(lines)
                       for number, lines in sessions.items())


# Registered protocol --------------------------------------------------------

def registered_protocol(benchmark: str) -> tuple[dict, list[dict]]:
    """The comparison's registration and this benchmark's questions, after checking what the arms reuse."""
    registration = json.loads(study.REG.read_text())
    for path in FROZEN_SOURCES:
        if digest(study.ROOT / path) != registration["sources"][path]:
            raise ValueError(f"{path} differs from the registered comparison")
    settings = ("model", "reasoning", "service_tier", "reader_limit", "judge_limit")
    if [registration[key] for key in settings] != [OPENAI_ANSWER_MODEL[key] for key in (
            "model", "reasoning_effort", "service_tier", "reader_limit", "judge_limit")]:
        raise ValueError("The reader or judge model settings differ from the registered comparison")
    if benchmark == "locomo":
        prompts = {"locomo_reader": study.LOCO_READER, "locomo_judge": study.LOCO_JUDGE}
        dataset = study.LOCOMO
    else:
        official = digest(study.OFFICIAL / "src/evaluation/evaluate_qa.py")
        prompts = {"longmemeval_reader": study.lme._reader_prompt("{context}", "{date}", "{question}"),
                   "longmemeval_judge_sha256": official}
        dataset = study.LONGMEM
        # The saved run loaded the official judge through this amended loader.
        amendment = json.loads((study.PUBLIC / "gpt54-official-loader-amendment.json").read_text())
        if (amendment["registration_sha256"] != digest(study.REG) or amendment["official_source_sha256"] != official
                or amendment["loader_sha256"] != digest(study.ROOT / "benchmarks/integrations/"
                                                        "gpt54_official_prompt_loader.py")):
            raise ValueError("The official judge or its loader differs from the registered amendment")
    if any(registration["prompts"][key] != value for key, value in prompts.items()):
        raise ValueError("The reader or judge prompts differ from the registered comparison")
    if digest(dataset) != registration["datasets"][benchmark]["sha256"]:
        raise ValueError(f"The {benchmark} dataset differs from the registered comparison")
    questions = study.question_rows(benchmark)
    if [row["question_id"] for row in questions] != registration["cohort_ids"][benchmark]:
        raise ValueError(f"The {benchmark} questions differ from the registered comparison")
    return registration, questions


def arm_name(arm: str, variant: str | None = None) -> str:
    """The folder and result name of an arm: ``prme-<variant>`` for a named PRME variant."""
    if variant is None:
        return arm
    if arm != "prme" or not _VARIANT.fullmatch(variant):
        raise ValueError("--variant names a prme variant: lowercase letters, digits and hyphens, at most 40")
    return f"prme-{variant}"


def is_prme(arm: str) -> bool:
    """PRME's own retrieval: the current defaults (``prme``) or a named variant of them."""
    return arm == "prme" or (arm.startswith("prme-") and bool(_VARIANT.fullmatch(arm.removeprefix("prme-"))))


def _check_arm(arm: str, benchmark: str) -> None:
    kind = "prme" if is_prme(arm) else arm
    if kind not in ARMS:
        raise ValueError(f"Choose an arm from: {', '.join(ARMS)}")
    if benchmark not in ARMS[kind]:
        raise ValueError(f"The {arm} arm covers {', '.join(ARMS[kind])} only")


def _check_overrides(arm: str, overrides: dict | None) -> None:
    """What each arm may change: nothing for full-context and the defaults, anything for a named variant."""
    if arm == "full-context" and overrides:
        raise ValueError("The full-context arm has no budget to override")
    if arm == "prme" and overrides:
        raise ValueError("The prme arm prepares the current defaults; name a variant with --variant to change "
                         "settings")
    if is_prme(arm) and arm != "prme" and not overrides:
        raise ValueError(f"The {arm} variant needs the settings it changes, as --set KEY=VALUE")
    if arm.startswith("plain-") and overrides:
        gate.check_plain(arm.removeprefix("plain-"), overrides)


def _module_identity() -> dict:
    return {path: digest(study.ROOT / path) for path in (
        "benchmarks/integrations/gpt54_baselines.py", "benchmarks/diagnostics/product_packing.py",
        "benchmarks/integrations/gpt54_official_prompt_loader.py",
        "benchmarks/integrations/analyze_gpt54_comparison.py", "benchmarks/integrations/ollama_answers.py",
        "benchmarks/integrations/run_longmemeval_v2.py", "benchmarks/diagnostics/reader_judge.py",
        "benchmarks/compare_evidence.py", "benchmarks/evidence.py", *FROZEN_SOURCES)}


def _provenance() -> dict:
    from benchmarks.retrieval_eval import provenance

    value = provenance(gate.gate_config(Path("{pack}")))
    return {key: value[key] for key in ("commit", "dirty", "worktree_sha256", "dependencies")}


@contextmanager
def _registered_judge(registration: dict, benchmark: str) -> Iterator[None]:
    """Build LongMemEval judge prompts as the saved run did, loading the verified official source once."""
    if benchmark != "longmemeval":
        yield
        return
    source = study.OFFICIAL / "src/evaluation/evaluate_qa.py"
    judge = load_prompt(study.OFFICIAL)
    if digest(source) != registration["prompts"]["longmemeval_judge_sha256"]:
        raise ValueError("The official judge changed while it was loaded")
    with patch.object(study.lme, "_load_official_prompt_function", lambda _root: judge):
        yield


# Prepared contexts ----------------------------------------------------------

def prepare(arm: str, benchmark: str, *, data: Path = DATA, archive: Path | None = None,
            overrides: dict | None = None, progress=None) -> dict:
    """Write every question's context and a manifest. No model calls."""
    _check_arm(arm, benchmark)
    _, questions = registered_protocol(benchmark)
    folder = data / arm / benchmark
    if folder.exists():
        raise ValueError(f"{folder} already exists; prepared contexts are never overwritten. Remove an "
                         "incomplete preparation before preparing again.")
    ledger = _ledger_path(data, arm, benchmark)
    if ledger.exists() and json.loads(ledger.read_text())["entries"]:
        raise ValueError(f"{arm} {benchmark} has already made paid calls ({ledger}); its contexts cannot be "
                         "prepared again")
    log = _run_log_path(data, arm, benchmark)
    if any(event["event"] == "finished" and event.get("complete") and event.get("sample") is None
           for event in _run_events(log)):
        raise ValueError(f"{arm} {benchmark} has a complete answer run ({log}); its contexts are never prepared "
                         "again")
    _check_overrides(arm, overrides)
    if log.exists():
        # Earlier runs ended without a score. Preparing again is allowed, and stays on record.
        _log_run(data, arm, benchmark, {"event": "prepared-again"})
    if arm == "full-context":
        entries, extra = _prepare_full_context(folder, benchmark, questions)
    else:
        entries, extra = _prepare_replay(folder, arm, benchmark, archive, overrides, progress)
    if [entry["question_id"] for entry in entries] != [question["question_id"] for question in questions]:
        raise ValueError("Prepared contexts do not cover the registered questions in order")
    prepared = {
        "kind": "gpt54-baseline-contexts", "complete": True, "arm": arm, "benchmark": benchmark,
        "questions": len(entries), "registration_sha256": digest(study.REG),
        "tokenizer": gate.gate_config(Path("{pack}"), overrides).packing.tokenizer, **extra,
        "modules": _module_identity(), "contexts": entries,
    }
    write_new(folder / "prepared.json", prepared)
    return prepared


def _prepare_full_context(folder: Path, benchmark: str, questions: list[dict]) -> tuple[list[dict], dict]:
    tokenizer = gate.gate_config(Path("{pack}")).packing.tokenizer
    files = {}
    for sample in json.loads(study.LOCOMO.read_text()):
        # Every question in a conversation reads the same context, so it is stored once.
        context = full_context(sample["conversation"])
        path = folder / "contexts" / benchmark / f"{sample['sample_id']}.json"
        write_new(path, {"conversation_id": sample["sample_id"], "context": context})
        files[sample["sample_id"]] = {
            "path": str(path.relative_to(folder)), "sha256": digest(path),
            "text_sha256": hashlib.sha256(context.encode()).hexdigest(),
            "context_tokens": count_tokens(context, tokenizer), "retrieval_seconds": None}
    entries = [{"question_id": question["question_id"], **files[question["conversation_id"]]}
               for question in questions]
    return entries, {"context_budget": None, "context_rule": "The whole conversation; no budget.",
                     "provenance": _provenance()}


def _prepare_replay(folder: Path, arm: str, benchmark: str, archive: Path | None, overrides: dict | None,
                    progress) -> tuple[list[dict], dict]:
    """Contexts from the evidence gate's replay of the saved packs: PRME's retrieve() or a plain ranking."""
    method = None if is_prme(arm) else arm.removeprefix("plain-")
    report = asyncio.run(gate.run_gate(benchmark, archive=archive, overrides=overrides,
                                       capture_dir=folder / "contexts", plain=method, progress=progress))
    report_path = folder / "gate.json"
    gate._write_report(report_path, report, gate.gate_markdown(report))
    entries = [{"question_id": row["question_id"], "path": f"contexts/{benchmark}/{row['question_id']}.json",
                "sha256": row["capture_sha256"], "text_sha256": row["context_sha256"],
                "context_tokens": row["context_tokens"], "retrieval_seconds": row["retrieval_seconds"]}
               for row in report["rows"]]
    extra = {"context_budget": gate.context_limit(gate.gate_config(Path("{pack}"), overrides).packing),
             "gate_report_sha256": digest(report_path), "provenance": report["provenance"]}
    if method is None:
        changed = "the current defaults" if not overrides else f"the current defaults and {json.dumps(overrides)}"
        # How many contexts are still byte for byte the ones the saved GPT-5.4 run answered.
        return entries, {**extra, "context_rule": f"PRME retrieve() with {changed}, as the product renders it.",
                         "contexts_matching_saved_run":
                             report["benchmarks"][benchmark]["summary"]["contexts_matching_saved"]}
    rule = {"vector": "vector similarity", "bm25": "BM25",
            "rrf": f"reciprocal rank fusion (k={gate.PLAIN_RRF_K}) of vector and BM25 ranks"}[method]
    return entries, {**extra, "context_rule": f"Stored turns ranked by {rule}, packed in rank order as plain lines."}


def load_prepared(arm: str, benchmark: str, questions: list[dict], *,
                  data: Path = DATA) -> tuple[Path, dict, dict[str, dict]]:
    """The prepared manifest, after checking every context file against it."""
    _check_arm(arm, benchmark)
    folder = data / arm / benchmark
    path = folder / "prepared.json"
    if not path.is_file():
        raise ValueError(f"No prepared contexts at {folder}; run prepare first")
    prepared = json.loads(path.read_text())
    if (not prepared.get("complete") or prepared.get("arm") != arm or prepared.get("benchmark") != benchmark
            or prepared.get("registration_sha256") != digest(study.REG)):
        raise ValueError("The prepared contexts are incomplete or belong to another arm or registration")
    entries = {entry["question_id"]: entry for entry in prepared["contexts"]}
    if list(entries) != [question["question_id"] for question in questions]:
        raise ValueError("The prepared contexts do not cover the registered questions in order")
    checked: dict[str, str] = {}
    for qid, entry in entries.items():
        if entry["path"] not in checked:
            checked[entry["path"]] = digest(_context_path(folder, entry))
        if checked[entry["path"]] != entry["sha256"]:
            raise ValueError(f"Prepared context {qid} changed")
    return folder, prepared, entries


def _context_path(folder: Path, entry: dict) -> Path:
    path = (folder / entry["path"]).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError(f"Prepared context {entry['question_id']} points outside its arm")
    return path


def _context(folder: Path, entry: dict) -> str:
    context = json.loads(_context_path(folder, entry).read_text())["context"]
    if hashlib.sha256(context.encode()).hexdigest() != entry["text_sha256"]:
        raise ValueError(f"Prepared context {entry['question_id']} changed")
    return context


def _token_summary(values: list[int]) -> dict:
    return {"mean": statistics.fmean(values), "p50": statistics.median(values), "max": max(values)}


# Cost estimate --------------------------------------------------------------

def estimate(arm: str, benchmark: str, *, data: Path = DATA, archive: Path | None = None) -> dict:
    """Scale each question's recorded 2026-09-23 reader and judge usage to this arm's context. No model calls."""
    questions = study.question_rows(benchmark)
    folder, prepared, entries = load_prepared(arm, benchmark, questions, data=data)
    saved = (archive or gate.default_archive()) / benchmark
    totals = {"uncached": 0, "prefix_cached": 0}
    seen: set[str] = set()
    for question in questions:
        qid = question["question_id"]
        record = saved / "execution" / qid
        reader = json.loads((record / "reader.json").read_text())["response"]["usage"]
        judge = json.loads((record / "judge.json").read_text())["response"]
        saved_tokens = json.loads((saved / "contexts" / f"{qid}.json").read_text())["context_tokens"]
        tokens = entries[qid]["context_tokens"]
        # Tokenizer counts stand in for the provider's; the instructions and question keep their recorded size.
        inputs = reader["input_tokens"] - saved_tokens + tokens
        # Every later question on the same full context can read it from the provider's prompt cache.
        cached = min(tokens, inputs) if arm == "full-context" and entries[qid]["text_sha256"] in seen else 0
        seen.add(entries[qid]["text_sha256"])
        for name, hits in (("uncached", 0), ("prefix_cached", cached)):
            totals[name] += usage_cost(judge) + usage_cost({"service_tier": "flex", "usage": {
                "input_tokens": inputs, "output_tokens": reader["output_tokens"],
                "input_tokens_details": {"cached_tokens": hits}}})
    tokens = [entry["context_tokens"] for entry in entries.values()]
    # The ledger reserves each request's worst case before sending it, and every worker can hold a reader
    # and then a judge reservation at once, so the cap needs that much room above the expected spend.
    reader_bytes = max(len(study.reader_prompt(benchmark, question, _context(folder, entries[question["question_id"]]))
                           .encode()) for question in questions)
    concurrency = json.loads(study.REG.read_text())["provider_concurrency"]
    headroom = concurrency * (_reservation(reader_bytes, READER_LIMIT) + _reservation(_JUDGE_BYTES, JUDGE_LIMIT))
    return {
        "arm": arm, "benchmark": benchmark, "questions": len(tokens), "context_budget": prepared["context_budget"],
        "context_tokens": {**_token_summary(tokens), "total": sum(tokens)},
        "estimated_usd": {name: value / 1e9 for name, value in totals.items()},
        "reservation_headroom_usd": headroom / 1e9,
        "minimum_cap_usd": (totals["uncached"] + headroom) / 1e9,
        "basis": ("Flex prices. Each question keeps its recorded 2026-09-23 reader output and judge usage; reader "
                  "input changes by the difference in context tokens. prefix_cached assumes every later question "
                  "in a conversation reads the shared full-context prefix from the provider cache, which is "
                  "not guaranteed. minimum_cap_usd adds the ledger's in-flight reservations to the uncached "
                  "estimate. A planning estimate, not a quote."),
    }


def _reservation(prompt_bytes: int, limit: int) -> int:
    """The worst case gpt54_budget.call reserves for one request, in nanodollars."""
    return (prompt_bytes + 1024) * 2500 + limit * 15000


# Paid run -------------------------------------------------------------------

def _attempts(folder: Path) -> list[Path]:
    found = [(int(match.group(1)), path) for path in folder.glob("attempt-*")
             if path.is_dir() and (match := _ATTEMPT.fullmatch(path.name))]
    return [path for _, path in sorted(found)]


def _next_attempt(folder: Path) -> Path:
    attempts = _attempts(folder)
    number = int(attempts[-1].name.removeprefix("attempt-")) + 1 if attempts else 1
    path = folder / f"attempt-{number}"
    path.mkdir(parents=True)
    return path


def _attempt_record(attempt: Path) -> dict:
    """The attempt's result or failure; an attempt with neither was interrupted and received nothing usable."""
    for name in ("result.json", "failure.json"):
        if (attempt / name).exists():
            return {"kind": name.removesuffix(".json"), **json.loads((attempt / name).read_text())}
    return {"kind": "failure", "attempt": attempt.name, "exception_type": "Interrupted", "retryable": True,
            "budget_stop": False}


def _status(folder: Path) -> str:
    records = [_attempt_record(attempt) for attempt in _attempts(folder)]
    if any(record["kind"] == "result" for record in records):
        return "answered"
    return "final" if any(not record["retryable"] for record in records) else "pending"


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, BudgetExhausted) or (
        isinstance(exc, RuntimeError) and str(exc).startswith(_NO_ANSWER_FAILURES))


def _write_json(path: Path, value: dict) -> None:
    """Write through a temporary file, so a crash never leaves a truncated record."""
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    os.replace(temp, path)


def _api_key() -> str:
    from dotenv import dotenv_values

    path = study.ORIGINAL / ".env"
    key = dotenv_values(path).get("OPENAI_API_KEY") if path.is_file() else None
    if not key:
        raise ValueError(f"OPENAI_API_KEY is not set in {path}")
    return key


def _ledger_path(data: Path, arm: str, benchmark: str) -> Path:
    # Kept outside the arm's folder, so removing a preparation never erases what the arm has spent.
    return data / "ledgers" / f"{arm}-{benchmark}.json"


def _ledger(path: Path, max_usd: float) -> Ledger:
    """The arm's spending ledger. A resumed run may raise the approved cap, never lower it."""
    cap = round(max_usd * 1e9)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The same lock file Ledger.update takes.
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            value = json.loads(path.read_text())
            recorded = value["cap_nanodollars"]
            if cap < recorded:
                raise ValueError(f"This arm's approved cap is ${recorded / 1e9:,.2f}; a resumed run cannot lower it")
            if cap > recorded:
                value.setdefault("cap_history", []).append(
                    {"from_nanodollars": recorded, "to_nanodollars": cap, "at": study.utc()})
                value["cap_nanodollars"] = cap
                _write_json(path, value)
    return Ledger(path, cap=cap)


@contextmanager
def _run_lock(folder: Path) -> Iterator[None]:
    with (folder / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"Another run of {folder.parent.name} {folder.name} is in progress") from None
        yield


def _check_calibration(archive: Path | None) -> None:
    """The saved run's reader and judge calibration, which this run reuses, must be complete and registered."""
    path = (archive or gate.default_archive()) / "authored-calibration" / "result.json"
    calibration = json.loads(path.read_text()) if path.is_file() else {}
    if not calibration.get("complete") or calibration.get("registration_sha256") != digest(study.REG):
        raise ValueError(f"No complete registered reader and judge calibration at {path}")


def data_root(model: ollama_answers.AnswerModel | None) -> Path:
    """Where a track keeps its contexts and answers: the GPT-5.4 track, or one folder per Ollama model."""
    return DATA if model is None else OLLAMA_DATA / model.track


def sample_questions(questions: list[dict], per_category: int) -> list[dict]:
    """A fixed smoke sample: the first ``per_category`` questions of each category, in registered order."""
    if per_category < 1:
        raise ValueError("A sample needs at least one question per category")
    taken: Counter = Counter()
    chosen = []
    for question in questions:
        if taken[question["question_type"]] < per_category:
            taken[question["question_type"]] += 1
            chosen.append(question)
    return chosen


def _run_log_path(data: Path, arm: str, benchmark: str) -> Path:
    # Kept outside the arm's folder, like the ledgers, so removing a preparation never erases that the arm
    # was answered.
    return data / "runs" / f"{arm}-{benchmark}.jsonl"


def _run_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _run_history(data: Path, arm: str, benchmark: str) -> dict:
    """What the arm's run log holds, so a result shows every earlier run and preparation."""
    path = _run_log_path(data, arm, benchmark)
    events = _run_events(path)
    return {"sha256": digest(path), "runs_started": sum(event["event"] == "started" for event in events),
            "prepared_again": sum(event["event"] == "prepared-again" for event in events)}


def _log_run(data: Path, arm: str, benchmark: str, event: dict) -> None:
    """Append one event to the arm's run log, which is never rewritten."""
    path = _run_log_path(data, arm, benchmark)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"at": study.utc(), **event}, sort_keys=True) + "\n")


# Ollama calibration ------------------------------------------------------------

def _calibration_folder(data: Path) -> Path:
    return data / "authored-calibration"


def _calibrations(data: Path) -> list[tuple[Path, dict]]:
    """Every calibration attempt of a track, oldest first, with its record."""
    return [(attempt, json.loads((attempt / "result.json").read_text()) if (attempt / "result.json").exists()
             else {"complete": False, "error": "Interrupted"})
            for attempt in _attempts(_calibration_folder(data))]


def _passed_calibration(data: Path, settings: dict) -> dict | None:
    """The first attempt that passed with this model and these settings, and how many attempts there were."""
    attempts = _calibrations(data)
    for attempt, record in attempts:
        if (record.get("complete") and record.get("registration_sha256") == digest(study.REG)
                and ollama_answers.same_model(record["answer_model"], settings)):
            return {"attempt": attempt.name, "sha256": digest(attempt / "result.json"),
                    "attempts_before_pass": int(attempt.name.removeprefix("attempt-")) - 1,
                    "attempts": len(attempts)}
    return None


async def calibrate(model: ollama_answers.AnswerModel, *, data: Path | None = None) -> dict:
    """The registered authored calibration, for an Ollama reader and judge.

    For each authored case and each benchmark's prompts, the judge must accept
    the reader's answer and the reference, and reject a wrong answer. ``run``
    on the Ollama track needs this to have passed for the same model and
    settings. Every attempt is kept, passed or not, and results report how
    many there were.
    """
    data = data or data_root(model)
    folder = _calibration_folder(data)
    folder.mkdir(parents=True, exist_ok=True)
    with _run_lock(folder):
        settings = ollama_answers.describe(model)
        if _passed_calibration(data, settings) is not None:
            raise ValueError(f"{model.model} with these settings is already calibrated ({folder})")
        protocols = {benchmark: registered_protocol(benchmark)[0] for benchmark in ("longmemeval", "locomo")}
        attempt = _next_attempt(folder)
        cases: list[dict] = []
        error = None
        try:
            async with ollama_answers.client_for(model) as client:
                for benchmark, registration in protocols.items():
                    with _registered_judge(registration, benchmark):
                        for number, (context, question, answer) in enumerate(AUTHORED_CASES):
                            cases.append(await _calibration_case(client, model, attempt, benchmark, number,
                                                                 context, question, answer))
            if not ollama_answers.same_model(settings, ollama_answers.describe(model)):
                error = "The model identity changed during calibration"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
        record = {"kind": "ollama-authored-calibration",
                  "complete": error is None and len(cases) == 2 * len(AUTHORED_CASES)
                  and all(case["passed"] for case in cases),
                  "registration_sha256": digest(study.REG), "answer_model": settings, "attempt": attempt.name,
                  "calibrated_at": study.utc(), "cases": cases, "error": error}
        _write_json(attempt / "result.json", record)
    if not record["complete"]:
        raise RuntimeError(f"{model.model} failed the authored calibration; see {attempt}")
    return record


async def _calibration_case(client, model: ollama_answers.AnswerModel, attempt: Path, benchmark: str,
                            number: int, context: str, question: str, answer: str) -> dict:
    """One authored case: the reader answers, then the judge sees that answer, the reference and a wrong one."""
    row = {"question": question, "answer": answer, "question_type": "single-session-user",
           "question_id": f"authored-{number}", "question_date": "2024/02/01 (Thu) 00:00"}
    semaphore = asyncio.Semaphore(1)
    reader = await ollama_answers.call(client, semaphore, model, study.reader_prompt(benchmark, row, context),
                                       READER_LIMIT, attempt / f"{benchmark}-{number}-reader.json")
    labels = []
    # The registered calibration's names, so both tracks' records line up.
    for kind, response in (("reader", reader["text"]), ("positive", answer), ("negative", AUTHORED_WRONG_ANSWER)):
        judged = await ollama_answers.call(client, semaphore, model, study.judge_prompt(benchmark, row, response),
                                           JUDGE_LIMIT, attempt / f"{benchmark}-{number}-{kind}-judge.json")
        labels.append(study.verdict(judged["text"]))
    return {"case": number, "benchmark": benchmark, "labels": labels, "passed": labels == [True, True, False]}


def _ollama_answer_model(model: ollama_answers.AnswerModel, data: Path) -> tuple[dict, dict]:
    """The model's settings and identity, and the calibration it passed with exactly these."""
    settings = ollama_answers.describe(model)
    calibration = _passed_calibration(data, settings)
    if calibration is None:
        raise ValueError(f"{model.model} has not passed calibration with these settings and this Ollama model "
                         f"identity ({_calibration_folder(data)}); run calibrate first")
    return settings, calibration


def _bind_answer_model(folder: Path, settings: dict) -> dict:
    """The arm's reader and judge. Once an answer exists, answers from another model are never mixed in.

    Returns the bound settings, which answered every question in the arm.
    """
    path = folder / "answer-model.json"
    if path.exists():
        bound = json.loads(path.read_text())
        if ollama_answers.same_model(bound, settings):
            return bound
        if any((folder / "execution").glob("*/attempt-*/result.json")):
            raise ValueError(f"This arm was answered by another reader and judge or other settings ({path})")
    _write_json(path, settings)
    return settings


async def run(arm: str, benchmark: str, *, max_usd: float | None = None,
              model: ollama_answers.AnswerModel | None = None, data: Path | None = None, results: Path = RESULTS,
              archive: Path | None = None, api_key: str | None = None, sample: int | None = None) -> dict:
    """Answer and judge every question that has no result yet, then report the whole arm.

    ``RETRY_POLICY`` says which failures a later run asks again. The arm is
    complete only when every question has an authenticated answer and verdict.
    With ``model``, the Ollama track answers instead of GPT-5.4: no API key,
    ledger or cap. Only that track runs the ``prme`` arms and ``sample``, which
    asks only ``sample_questions`` as a smoke check, not a score; a later full
    run reuses its answers.
    """
    if model is None:
        if not (max_usd is not None and math.isfinite(max_usd) and max_usd > 0):
            raise ValueError("Pass the owner-approved spending cap as --max-usd")
        if is_prme(arm) or sample is not None:
            raise ValueError("The prme arms and samples run on the Ollama track only")
    elif max_usd is not None or api_key is not None:
        raise ValueError("An Ollama run makes no paid calls; it takes no spending cap or API key")
    data = data or data_root(model)
    registration, questions = registered_protocol(benchmark)
    folder, prepared, entries = load_prepared(arm, benchmark, questions, data=data)
    if sample is not None:
        questions = sample_questions(questions, sample)
    run_name = f"{arm} {benchmark}" if sample is None else f"{arm} {benchmark} sample of {sample}"
    private = folder / ("result.json" if sample is None else f"sample-{sample}-result.json")
    if model is None:
        if (folder / "answer-model.json").exists():
            raise ValueError(f"{run_name} was answered on the Ollama track; GPT-5.4 answers are never mixed in")
        _check_calibration(archive)
        api_key = api_key or _api_key()
    started = study.utc()
    with _run_lock(folder), _registered_judge(registration, benchmark):
        if private.exists() and json.loads(private.read_text()).get("complete"):
            raise ValueError(f"{run_name} is already complete; a finished arm or sample is never rerun")
        if model is None:
            ledger = _ledger(_ledger_path(data, arm, benchmark), max_usd)
            ask = partial(call, ledger=ledger)
            opened = client_for(api_key)
            answer_model, extra = OPENAI_ANSWER_MODEL, {"max_usd": max_usd}
        else:
            settings, calibration = _ollama_answer_model(model, data)
            answer_model = _bind_answer_model(folder, settings)
            ledger = None
            ask = partial(ollama_answers.call, model=model)
            opened = ollama_answers.client_for(model)
            extra = {"calibration": calibration}
            _log_run(data, arm, benchmark, {"event": "started", "sample": sample,
                                            "answer_model_sha256": sha(answer_model)})
        async with opened as client:
            await _answer(arm, benchmark, folder, questions, entries, registration["provider_concurrency"],
                          client, ask, ledger)
        if model is not None and not ollama_answers.same_model(answer_model, ollama_answers.describe(model)):
            _log_run(data, arm, benchmark, {"event": "model-changed", "sample": sample})
            raise RuntimeError(f"The Ollama model identity changed during {run_name}; nothing is reported. Its "
                               "answers stay in the arm, which refuses another model.")
        result = report(arm, benchmark, folder, questions, prepared, entries, ledger, model=model)
        result.update(started_at=started, finished_at=study.utc(), retry_policy=RETRY_POLICY,
                      provenance=_provenance(), modules=_module_identity(), answer_model=answer_model, **extra,
                      prepared=_prepared_summary(prepared))
        if sample is not None:
            # A smoke check only: the count of accepted answers, with no accuracy or intervals to cite.
            for key in ("accuracy", "ci95_questions", "ci95_source_clusters", "categories"):
                result.pop(key, None)
            result.update(kind=result["kind"] + "-sample", sample={
                "per_category": sample, "question_ids": [row["question_id"] for row in questions],
                "note": "A fixed smoke sample: the first questions of each category in registered order. "
                        "Not a benchmark score."})
        _write_json(private, result)
        if model is not None:
            _log_run(data, arm, benchmark, {"event": "finished", "sample": sample, "complete": result["complete"],
                                            "completed": result["completed"], "total": result["total"]})
            result["run_log"] = _run_history(data, arm, benchmark)
        if not result["complete"]:
            raise RuntimeError(
                f"{run_name} is incomplete: {result['completed']}/{result['total']} answered, "
                f"{result['final_failures']} final failures. Run it again to retry the rest; no partial score "
                "is reported.")
        prefix = "gpt54-baseline" if model is None else model.track
        suffix = "" if sample is None else f"-sample-{sample}"
        published = results / started[:10] / f"{prefix}-{arm}-{benchmark}{suffix}-result.json"
        # Failure messages can name local paths; the private copy keeps them.
        write_new(published, {**result, "failures": [
            {key: value for key, value in failure.items() if key != "message"} for failure in result["failures"]]})
    return result


def _prepared_summary(prepared: dict) -> dict:
    """What built the contexts: the commit, whether the tree was clean, and the settings a variant changed."""
    provenance = prepared.get("provenance") or {}
    return {"commit": provenance.get("commit"), "dirty": provenance.get("dirty"),
            "worktree_sha256": provenance.get("worktree_sha256"), "overrides": provenance.get("overrides", {}),
            "context_rule": prepared["context_rule"],
            "contexts_matching_saved_run": prepared.get("contexts_matching_saved_run")}


def compare(before: dict, after: dict, *, samples: int = 2000) -> dict:
    """Pair two complete answer results on the same questions, answered by the same reader and judge.

    ``before`` is the baseline. The difference and its 95% interval resample
    questions, as the evidence gate's comparisons do.
    """
    from benchmarks.compare_evidence import paired_statistics

    for side, result in (("before", before), ("after", after)):
        if not result.get("complete") or "sample" in result or result.get("kind", "").endswith("-sample"):
            raise ValueError(f"The {side} result is not a complete answer run")
    for field in ("kind", "benchmark", "model", "registration_sha256"):
        if before.get(field) != after.get(field):
            raise ValueError(f"The results differ in {field}")
    if not ollama_answers.same_model(before["answer_model"], after["answer_model"]):
        raise ValueError("The results were answered by different readers and judges or settings")
    old, new = ({row["question_id"]: row for row in result["rows"]} for result in (before, after))
    if list(old) != list(new):
        raise ValueError("The results answer different questions")

    def paired(keys: list[str]) -> dict:
        return paired_statistics([(float(old[key]["correct"]), float(new[key]["correct"])) for key in keys],
                                 samples=samples, seed=42)

    categories = sorted({row["question_type"] for row in old.values()})
    prepared = {side: result.get("prepared") or {} for side, result in (("before", before), ("after", after))}
    warnings = [f"The {side} contexts were prepared from a tree with uncommitted changes"
                for side, value in prepared.items() if value.get("dirty")]
    if prepared["before"].get("commit") != prepared["after"].get("commit"):
        warnings.append("The contexts were prepared from different commits, so code changes are part of the "
                        "difference")
    return {
        "kind": "answer-comparison", "benchmark": before["benchmark"], "model": before["model"],
        "answer_model": before["answer_model"], "bootstrap_samples": samples, "bootstrap_seed": 42,
        "interval_note": ("Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, so "
                          "they are narrower than conversation-level intervals."),
        "arms": {"before": before["arm"], "after": after["arm"]},
        "prepared": prepared, "warnings": warnings,
        "accuracy": paired(list(old)),
        "categories": {category: paired([key for key, row in old.items() if row["question_type"] == category])
                       for category in categories},
        "gained": [key for key in old if not old[key]["correct"] and new[key]["correct"]],
        "lost": [key for key in old if old[key]["correct"] and not new[key]["correct"]],
    }


async def _answer(arm: str, benchmark: str, folder: Path, questions: list[dict], entries: dict[str, dict],
                  concurrency: int, client, ask, ledger: Ledger | None) -> None:
    """Ask every pending question through ``ask``, the provider's call with its ledger or model bound."""
    execution = folder / "execution"
    pending = iter([question for question in questions
                    if _status(execution / question["question_id"]) == "pending"])
    errors: list[dict] = []
    answered = 0

    async def worker(semaphore):
        nonlocal answered
        while not errors:
            question = next(pending, None)
            if question is None:
                return
            qid = question["question_id"]
            attempt = None
            try:
                attempt = _next_attempt(execution / qid)
                context = _context(folder, entries[qid])
                reader = await ask(client, semaphore, prompt=study.reader_prompt(benchmark, question, context),
                                   limit=READER_LIMIT, path=attempt / "reader.json")
                judged = await ask(client, semaphore, prompt=study.judge_prompt(benchmark, question, reader["text"]),
                                   limit=JUDGE_LIMIT, path=attempt / "judge.json")
                _write_json(attempt / "result.json", {
                    "question_id": qid, "question_type": question["question_type"],
                    "cluster": question.get("conversation_id") or sha(question["haystack_sessions"]),
                    "correct": study.verdict(judged["text"]), "context_sha256": entries[qid]["sha256"],
                    "context_tokens": entries[qid]["context_tokens"],
                    "retrieval_seconds": entries[qid]["retrieval_seconds"],
                    "reader_sha256": digest(attempt / "reader.json"),
                    "judge_sha256": digest(attempt / "judge.json")})
                answered += 1
                if answered % 25 == 0:
                    cost = "" if ledger is None else "; arm cost ${:.3f}".format(
                        sum(entry["charge"] for entry in ledger.update()["entries"].values()) / 1e9)
                    print(f"{arm} {benchmark}: {answered} answered this run{cost}", file=sys.stderr, flush=True)
            except Exception as exc:
                error = {"question_id": qid, "attempt": attempt.name if attempt else None,
                         "exception_type": type(exc).__name__, "message": str(exc)[:500],
                         "retryable": _retryable(exc), "budget_stop": isinstance(exc, BudgetExhausted)}
                errors.append(error)
                if attempt is not None:
                    # Without its record the attempt reads as interrupted, which is retried.
                    with suppress(OSError):
                        _write_json(attempt / "failure.json", error)

    semaphore = asyncio.Semaphore(concurrency)
    await asyncio.gather(*(worker(semaphore) for _ in range(concurrency)))


def report(arm: str, benchmark: str, folder: Path, questions: list[dict], prepared: dict,
           entries: dict[str, dict], ledger: Ledger | None, *, model: ollama_answers.AnswerModel | None = None) -> dict:
    """Authenticate every answered question and summarize the arm with its cost, tokens and budget.

    Without ``model`` the calls are GPT-5.4 calls checked against ``ledger``;
    with it they are that Ollama model's calls, which cost nothing.
    """
    from benchmarks.integrations.analyze_gpt54_comparison import verify_call

    verify = verify_call if model is None else partial(ollama_answers.verify_call, model=model)
    tally = _tally_gpt54 if model is None else _tally_ollama
    rows, failures = [], []
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "successful_calls": 0,
             "http_attempts": 0, "http_status_counts": Counter()}
    if model is None:
        usage.update(reasoning_tokens=0, observed_nanodollars=0)
    for question in questions:
        qid = question["question_id"]
        records = [(attempt, _attempt_record(attempt)) for attempt in _attempts(folder / "execution" / qid)]
        answer = next((attempt for attempt, record in records if record["kind"] == "result"), None)
        failures += [{key: value for key, value in record.items() if key != "kind"} | {"replaced": answer is not None}
                     for _, record in records if record["kind"] == "failure"]
        if answer is not None:
            rows.append(_verified_row(verify, tally, benchmark, question, folder, prepared, entries[qid], answer,
                                      usage))
    if model is None:
        charges = list(ledger.update()["entries"].values())
        if sum(entry["charge"] for entry in charges) < usage["observed_nanodollars"]:
            raise ValueError("The spending ledger records less than the verified calls cost; it is not this arm's "
                             "complete ledger")
        cost = {"usd": sum(entry["charge"] for entry in charges) / 1e9,
                "unsettled_reservations": sum(not entry["settled"] for entry in charges),
                "ledger_sha256": digest(ledger.path),
                "note": "Every provider attempt of this arm, including failed ones; an unsettled request keeps "
                        "its full reservation."}
    else:
        cost = {"usd": 0, "note": "Local Ollama server; no API charge. A :cloud model's calls count against the "
                                  "Ollama account's usage limits instead."}
    chosen = [entries[question["question_id"]] for question in questions]
    result = {
        "kind": "gpt54-baseline-result" if model is None else "ollama-answer-result", "arm": arm,
        "benchmark": benchmark, "model": MODEL if model is None else model.model,
        "registration_sha256": digest(study.REG), "prepared_sha256": digest(folder / "prepared.json"),
        "context_budget": prepared["context_budget"], "context_rule": prepared["context_rule"],
        "complete": len(rows) == len(questions), "total": len(questions), "completed": len(rows),
        "final_failures": sum(not failure["retryable"] and not failure["replaced"] for failure in failures),
        "unreplaced_failures": sum(not failure["replaced"] for failure in failures),
        "failures": failures,
        "cost": cost,
        "provider_tokens": {**usage, "http_status_counts": dict(usage["http_status_counts"]),
                            "note": "Successful reader and judge calls of answered questions."},
        "context_tokens": _token_summary([entry["context_tokens"] for entry in chosen]),
        "retrieval_seconds": _seconds_summary([entry["retrieval_seconds"] for entry in chosen]),
        "rows": rows,
    }
    if result["complete"]:
        correct = sum(row["correct"] for row in rows)
        result.update(correct=correct, accuracy=correct / len(rows), ci95_questions=study.confidence(rows),
                      ci95_source_clusters=study.confidence(rows, True), categories={
                          category: {"correct": sum(row["correct"] for row in rows if row["question_type"] == category),
                                     "total": sum(row["question_type"] == category for row in rows)}
                          for category in sorted({row["question_type"] for row in rows})})
    return result


def _verified_row(verify, tally, benchmark: str, question: dict, folder: Path, prepared: dict, entry: dict,
                  attempt: Path, usage: dict) -> dict:
    """The attempt's result row, after checking it against its context, calls and verdict."""
    qid = question["question_id"]
    row = json.loads((attempt / "result.json").read_text())
    context = _context(folder, entry)
    tokens = count_tokens(context, prepared["tokenizer"])
    budget = prepared["context_budget"]
    reader = verify(attempt / "reader.json", study.reader_prompt(benchmark, question, context), READER_LIMIT)
    judge = verify(attempt / "judge.json", study.judge_prompt(benchmark, question, reader["text"]), JUDGE_LIMIT)
    expected = {"question_id": qid, "question_type": question["question_type"],
                "cluster": question.get("conversation_id") or sha(question["haystack_sessions"]),
                "correct": study.verdict(judge["text"]), "context_sha256": entry["sha256"],
                "context_tokens": entry["context_tokens"], "retrieval_seconds": entry["retrieval_seconds"],
                "reader_sha256": digest(attempt / "reader.json"), "judge_sha256": digest(attempt / "judge.json")}
    if row != expected or tokens != entry["context_tokens"] or (budget is not None and tokens > budget):
        raise ValueError(f"The recorded result for {qid} does not match its context, calls or verdict")
    for value in (reader, judge):
        tally(usage, value["response"])
        usage["successful_calls"] += 1
        usage["http_attempts"] += value["attempts"]
        usage["http_status_counts"].update(value["verified_http_statuses"])
    return row


def _tally_gpt54(usage: dict, response: dict) -> None:
    counts = response["usage"]
    usage["input_tokens"] += counts["input_tokens"]
    usage["cached_input_tokens"] += counts.get("input_tokens_details", {}).get("cached_tokens", 0)
    usage["output_tokens"] += counts["output_tokens"]
    usage["reasoning_tokens"] += counts.get("output_tokens_details", {}).get("reasoning_tokens", 0)
    usage["observed_nanodollars"] += usage_cost(response)


def _tally_ollama(usage: dict, response: dict) -> None:
    for key, count in ollama_answers.usage(response).items():
        usage[key] += count


def _seconds_summary(values: list[float | None]) -> dict:
    if any(value is None for value in values):
        return {"p50": None, "p95": None, "note": "The full-context arm has no retrieval step."}
    return {"p50": statistics.median(values), "p95": statistics.quantiles(values, n=20, method="inclusive")[-1]
            if len(values) > 1 else values[0],
            "note": "Time to rank and pack each context during prepare; not model-response latency."}


# CLI ------------------------------------------------------------------------

def _confirm_spend(parser: argparse.ArgumentParser, arm: str, benchmark: str, max_usd: float) -> None:
    """Paid runs need the owner at an interactive terminal, so nothing unattended can start one."""
    if not sys.stdin.isatty():
        parser.error("run makes paid calls; start it from an interactive terminal so the owner can confirm")
    typed = input(f"This can spend up to ${max_usd:,.2f} on {arm} {benchmark} with {MODEL}. "
                  "Type the arm name to confirm: ")
    if typed.strip() != arm:
        parser.error("not confirmed; nothing was sent")


def _on_main() -> bool:
    """Whether the checked-out commit is on the main branch, so the defaults it prepares are the shipped ones."""
    import subprocess

    return subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], cwd=study.ROOT,
                          capture_output=True).returncode == 0


def _check_args(parser: argparse.ArgumentParser, args: argparse.Namespace,
                model: ollama_answers.AnswerModel | None) -> tuple[str, str, dict]:
    """The arm's name, its benchmark and its overrides, after refusing any combination that does not apply."""
    command = args.command
    if command == "calibrate" and model is None:
        parser.error("calibrate is for the ollama provider; the GPT-5.4 track reuses the saved calibration")
    if command in {"calibrate", "compare"}:
        given = [flag for flag, value in (("arm", args.arm), ("--benchmark", args.benchmark),
                                          ("--set", args.overrides), ("--variant", args.variant),
                                          ("--max-usd", args.max_usd), ("--sample", args.sample),
                                          ("--archive", args.archive)) if value]
        if given:
            parser.error(f"{command} takes none of: {', '.join(given)}")
    if (command == "compare") != (args.before is not None and args.after is not None) or (
            command != "compare" and (args.before or args.after)):
        parser.error("compare takes --before and --after, the result files to pair; nothing else does")
    if command in {"calibrate", "compare"}:
        return "", "", {}
    if args.arm is None:
        parser.error(f"{command} needs an arm")
    benchmark = args.benchmark or ("locomo" if args.arm == "full-context" else None)
    if benchmark is None:
        parser.error("plain and prme arms need --benchmark")
    if command != "prepare" and args.overrides:
        parser.error("--set applies to prepare only; run and estimate find a variant by --variant")
    if model is None:
        if args.arm == "prme" or args.sample is not None:
            parser.error("the prme arms and --sample run on --provider ollama only")
        if (command == "run") != (args.max_usd is not None):
            parser.error("--max-usd is required for run, which makes paid calls, and applies to run only")
        if command == "run" and not (math.isfinite(args.max_usd) and args.max_usd > 0):
            parser.error("--max-usd must be a positive amount")
    else:
        if args.max_usd is not None or command == "estimate":
            parser.error("the ollama provider makes no paid calls; --max-usd and estimate apply to openai only")
        if command == "run" and args.archive is not None:
            parser.error("--archive applies to prepare only on the ollama provider")
    if args.sample is not None and (command != "run" or args.sample < 1):
        parser.error("--sample applies to run only and needs a positive count")
    try:
        arm = arm_name(args.arm, args.variant)
        overrides = gate.parse_overrides(args.overrides)
        _check_arm(arm, benchmark)
        if command == "prepare":
            _check_overrides(arm, overrides)
    except ValueError as exc:
        parser.error(str(exc))
    return arm, benchmark, overrides


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.integrations.gpt54_baselines",
                                     description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["prepare", "estimate", "calibrate", "run", "compare"])
    parser.add_argument("arm", nargs="?", choices=list(ARMS),
                        help="Every command but calibrate and compare needs an arm")
    parser.add_argument("--benchmark", choices=gate.GATE_BENCHMARKS,
                        help="Required for plain and prme arms; full-context covers LoCoMo only")
    parser.add_argument("--provider", choices=["openai", "ollama"], default="openai",
                        help=f"Reader and judge: the registered GPT-5.4 (default, paid) or {ollama_answers.MODEL} "
                             f"through the local Ollama server at {ollama_answers.ENDPOINT} (a separate track, no "
                             "API cost)")
    parser.add_argument("--variant", metavar="NAME",
                        help="prme arm on the ollama provider: a named variant of the defaults, prepared with --set "
                             "and answered as the arm prme-NAME")
    parser.add_argument("--archive", type=Path,
                        help="Saved 2026-09-23 run archive (default: the main checkout's data/gpt54-comparison-v1)")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="prepare only: packing.token_budget or packing.overhead_tokens for a plain arm, or "
                             "the settings a prme variant changes (any the evidence gate accepts)")
    parser.add_argument("--max-usd", type=float,
                        help="run on the openai provider only: the owner-approved spending cap for this arm")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="run on the ollama provider only: a smoke check of the first N questions of each "
                             "category")
    parser.add_argument("--before", type=Path, help="compare only: the baseline's result file")
    parser.add_argument("--after", type=Path, help="compare only: the variant's result file")
    args = parser.parse_args(argv)
    model = ollama_answers.AnswerModel() if args.provider == "ollama" else None
    arm, benchmark, overrides = _check_args(parser, args, model)
    data = data_root(model)
    if args.command == "calibrate":
        print(json.dumps(asyncio.run(calibrate(model)), indent=2))
    elif args.command == "compare":
        try:
            result = compare(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(result, indent=2))
    elif args.command == "prepare":
        if _provenance()["dirty"]:
            parser.error("prepare records the code that builds the contexts; commit your changes first")
        if arm == "prme" and not _on_main():
            parser.error("the prme arm prepares the shipped defaults; prepare it from a commit on main, or name a "
                         "variant")
        gate._quiet_offline_cli()
        reported = 0

        def progress(done: int) -> None:
            nonlocal reported
            if done // 100 > reported // 100:
                print(f"{done} questions prepared", file=sys.stderr, flush=True)
            reported = done

        prepared = prepare(arm, benchmark, data=data, archive=args.archive, overrides=overrides or None,
                           progress=progress)
        print(json.dumps({key: prepared[key] for key in ("arm", "benchmark", "questions", "context_budget",
                                                         "contexts_matching_saved_run") if key in prepared}))
    elif args.command == "estimate":
        print(json.dumps(estimate(arm, benchmark, data=data, archive=args.archive), indent=2))
    elif model is None:
        _confirm_spend(parser, arm, benchmark, args.max_usd)
        result = asyncio.run(run(arm, benchmark, max_usd=args.max_usd, archive=args.archive))
        print(json.dumps({key: value for key, value in result.items() if key not in {"rows", "failures"}}))
    else:
        print(f"Reader and judge: {model.model} through {model.endpoint}. A :cloud model sends the prompts to "
              "Ollama's hosted service and uses the account's usage limits.", file=sys.stderr, flush=True)
        result = asyncio.run(run(arm, benchmark, model=model, sample=args.sample))
        print(json.dumps({key: value for key, value in result.items() if key not in {"rows", "failures"}}))


if __name__ == "__main__":
    main()

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

Every arm reuses the registered reader, judge, prompts, model settings and
question sets of ``run_gpt54_comparison``, and leaves that frozen module
unchanged. Its ``validate()`` cannot be reused, because it also pins every
``src/prme`` file and those have changed since registration, so
``registered_protocol`` checks the parts the arms depend on.

``prepare`` and ``estimate`` make no model calls. ``prepare`` for a plain arm
runs the offline evidence gate with the same ranking, so its gate report
describes exactly the contexts the reader will see. ``run`` makes paid reader and
judge calls. Start it only after the owner approves the spend: the CLI asks for
confirmation in an interactive terminal, and the approved ``--max-usd`` is the
arm's spending cap, which the ledger enforces.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
import fcntl
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
RESULTS = study.ROOT / "benchmarks" / "results" / "research"
ARMS = {"full-context": ("locomo",), **{f"plain-{method}": gate.GATE_BENCHMARKS for method in gate.PLAIN_METHODS}}
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
# Messages from gpt54_budget.call for failures that returned no answer text.
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
    if [registration[key] for key in settings] != [MODEL, "medium", "flex", READER_LIMIT, JUDGE_LIMIT]:
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


def _check_arm(arm: str, benchmark: str) -> None:
    if arm not in ARMS:
        raise ValueError(f"Choose an arm from: {', '.join(ARMS)}")
    if benchmark not in ARMS[arm]:
        raise ValueError(f"The {arm} arm covers {', '.join(ARMS[arm])} only")


def _module_identity() -> dict:
    return {path: digest(study.ROOT / path) for path in (
        "benchmarks/integrations/gpt54_baselines.py", "benchmarks/diagnostics/product_packing.py",
        "benchmarks/integrations/gpt54_official_prompt_loader.py",
        "benchmarks/integrations/analyze_gpt54_comparison.py", "benchmarks/evidence.py", *FROZEN_SOURCES)}


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
    if arm == "full-context":
        if overrides:
            raise ValueError("The full-context arm has no budget to override")
        entries, extra = _prepare_full_context(folder, benchmark, questions)
    else:
        entries, extra = _prepare_plain(folder, arm, benchmark, archive, overrides, progress)
    if [entry["question_id"] for entry in entries] != [question["question_id"] for question in questions]:
        raise ValueError("Prepared contexts do not cover the registered questions in order")
    prepared = {
        "kind": "gpt54-baseline-contexts", "complete": True, "arm": arm, "benchmark": benchmark,
        "questions": len(entries), "registration_sha256": digest(study.REG),
        "tokenizer": gate.gate_config(Path("{pack}")).packing.tokenizer, **extra,
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


def _prepare_plain(folder: Path, arm: str, benchmark: str, archive: Path | None, overrides: dict | None,
                   progress) -> tuple[list[dict], dict]:
    method = arm.removeprefix("plain-")
    report = asyncio.run(gate.run_gate(benchmark, archive=archive, overrides=overrides,
                                       capture_dir=folder / "contexts", plain=method, progress=progress))
    report_path = folder / "gate.json"
    gate._write_report(report_path, report, gate.gate_markdown(report))
    entries = [{"question_id": row["question_id"], "path": f"contexts/{benchmark}/{row['question_id']}.json",
                "sha256": row["capture_sha256"], "text_sha256": row["context_sha256"],
                "context_tokens": row["context_tokens"], "retrieval_seconds": row["retrieval_seconds"]}
               for row in report["rows"]]
    rule = {"vector": "vector similarity", "bm25": "BM25",
            "rrf": f"reciprocal rank fusion (k={gate.PLAIN_RRF_K}) of vector and BM25 ranks"}[method]
    return entries, {
        "context_budget": gate.context_limit(gate.gate_config(Path("{pack}"), overrides).packing),
        "context_rule": f"Stored turns ranked by {rule}, packed in rank order as plain lines.",
        "gate_report_sha256": digest(report_path), "provenance": report["provenance"]}


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


async def run(arm: str, benchmark: str, *, max_usd: float, data: Path = DATA, results: Path = RESULTS,
              archive: Path | None = None, api_key: str | None = None) -> dict:
    """Answer and judge every question that has no result yet, then report the whole arm.

    ``RETRY_POLICY`` says which failures a later run asks again. The arm is
    complete only when every question has an authenticated answer and verdict.
    """
    if not (math.isfinite(max_usd) and max_usd > 0):
        raise ValueError("Pass the owner-approved spending cap as --max-usd")
    registration, questions = registered_protocol(benchmark)
    folder, prepared, entries = load_prepared(arm, benchmark, questions, data=data)
    _check_calibration(archive)
    api_key = api_key or _api_key()
    started = study.utc()
    with _run_lock(folder), _registered_judge(registration, benchmark):
        private = folder / "result.json"
        if private.exists() and json.loads(private.read_text()).get("complete"):
            raise ValueError(f"{arm} {benchmark} is already complete; a finished arm is never rerun")
        ledger = _ledger(_ledger_path(data, arm, benchmark), max_usd)
        await _answer(arm, benchmark, folder, questions, entries, ledger, registration["provider_concurrency"],
                      api_key)
        result = report(arm, benchmark, folder, questions, prepared, entries, ledger)
        result.update(started_at=started, finished_at=study.utc(), max_usd=max_usd, retry_policy=RETRY_POLICY,
                      provenance=_provenance(), modules=_module_identity())
        _write_json(private, result)
        if not result["complete"]:
            raise RuntimeError(
                f"{arm} {benchmark} is incomplete: {result['completed']}/{result['total']} answered, "
                f"{result['final_failures']} final failures. Run it again to retry the rest; no partial score "
                "is reported.")
        published = results / started[:10] / f"gpt54-baseline-{arm}-{benchmark}-result.json"
        # Failure messages can name local paths; the private copy keeps them.
        write_new(published, {**result, "failures": [
            {key: value for key, value in failure.items() if key != "message"} for failure in result["failures"]]})
    return result


async def _answer(arm: str, benchmark: str, folder: Path, questions: list[dict], entries: dict[str, dict],
                  ledger: Ledger, concurrency: int, api_key: str) -> None:
    execution = folder / "execution"
    pending = iter([question for question in questions
                    if _status(execution / question["question_id"]) == "pending"])
    errors: list[dict] = []
    answered = 0

    async def worker(client, semaphore):
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
                reader = await call(client, semaphore, ledger, study.reader_prompt(benchmark, question, context),
                                    READER_LIMIT, attempt / "reader.json")
                judged = await call(client, semaphore, ledger,
                                    study.judge_prompt(benchmark, question, reader["text"]),
                                    JUDGE_LIMIT, attempt / "judge.json")
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
                    spent = sum(entry["charge"] for entry in ledger.update()["entries"].values()) / 1e9
                    print(f"{arm} {benchmark}: {answered} answered this run; arm cost ${spent:.3f}",
                          file=sys.stderr, flush=True)
            except Exception as exc:
                error = {"question_id": qid, "attempt": attempt.name if attempt else None,
                         "exception_type": type(exc).__name__, "message": str(exc)[:500],
                         "retryable": _retryable(exc), "budget_stop": isinstance(exc, BudgetExhausted)}
                errors.append(error)
                if attempt is not None:
                    # Without its record the attempt reads as interrupted, which is retried.
                    with suppress(OSError):
                        _write_json(attempt / "failure.json", error)

    async with client_for(api_key) as client:
        semaphore = asyncio.Semaphore(concurrency)
        await asyncio.gather(*(worker(client, semaphore) for _ in range(concurrency)))


def report(arm: str, benchmark: str, folder: Path, questions: list[dict], prepared: dict,
           entries: dict[str, dict], ledger: Ledger) -> dict:
    """Authenticate every answered question and summarize the arm with its cost, tokens and budget."""
    from benchmarks.integrations.analyze_gpt54_comparison import verify_call

    rows, failures = [], []
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
             "successful_calls": 0, "http_attempts": 0, "http_status_counts": Counter(), "observed_nanodollars": 0}
    for question in questions:
        qid = question["question_id"]
        records = [(attempt, _attempt_record(attempt)) for attempt in _attempts(folder / "execution" / qid)]
        answer = next((attempt for attempt, record in records if record["kind"] == "result"), None)
        failures += [{key: value for key, value in record.items() if key != "kind"} | {"replaced": answer is not None}
                     for _, record in records if record["kind"] == "failure"]
        if answer is not None:
            rows.append(_verified_row(verify_call, benchmark, question, folder, prepared, entries[qid], answer,
                                      usage))
    charges = list(ledger.update()["entries"].values())
    if sum(entry["charge"] for entry in charges) < usage["observed_nanodollars"]:
        raise ValueError("The spending ledger records less than the verified calls cost; it is not this arm's "
                         "complete ledger")
    result = {
        "kind": "gpt54-baseline-result", "arm": arm, "benchmark": benchmark, "model": MODEL,
        "registration_sha256": digest(study.REG), "prepared_sha256": digest(folder / "prepared.json"),
        "context_budget": prepared["context_budget"], "context_rule": prepared["context_rule"],
        "complete": len(rows) == len(questions), "total": len(questions), "completed": len(rows),
        "final_failures": sum(not failure["retryable"] and not failure["replaced"] for failure in failures),
        "unreplaced_failures": sum(not failure["replaced"] for failure in failures),
        "failures": failures,
        "cost": {"usd": sum(entry["charge"] for entry in charges) / 1e9,
                 "unsettled_reservations": sum(not entry["settled"] for entry in charges),
                 "ledger_sha256": digest(ledger.path),
                 "note": "Every provider attempt of this arm, including failed ones; an unsettled request keeps "
                         "its full reservation."},
        "provider_tokens": {**usage, "http_status_counts": dict(usage["http_status_counts"]),
                            "note": "Successful reader and judge calls of answered questions."},
        "context_tokens": _token_summary([entry["context_tokens"] for entry in entries.values()]),
        "retrieval_seconds": _seconds_summary([entry["retrieval_seconds"] for entry in entries.values()]),
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


def _verified_row(verify_call, benchmark: str, question: dict, folder: Path, prepared: dict, entry: dict,
                  attempt: Path, usage: dict) -> dict:
    """The attempt's result row, after checking it against its context, calls and verdict."""
    qid = question["question_id"]
    row = json.loads((attempt / "result.json").read_text())
    context = _context(folder, entry)
    tokens = count_tokens(context, prepared["tokenizer"])
    budget = prepared["context_budget"]
    reader = verify_call(attempt / "reader.json", study.reader_prompt(benchmark, question, context), READER_LIMIT)
    judge = verify_call(attempt / "judge.json", study.judge_prompt(benchmark, question, reader["text"]), JUDGE_LIMIT)
    expected = {"question_id": qid, "question_type": question["question_type"],
                "cluster": question.get("conversation_id") or sha(question["haystack_sessions"]),
                "correct": study.verdict(judge["text"]), "context_sha256": entry["sha256"],
                "context_tokens": entry["context_tokens"], "retrieval_seconds": entry["retrieval_seconds"],
                "reader_sha256": digest(attempt / "reader.json"), "judge_sha256": digest(attempt / "judge.json")}
    if row != expected or tokens != entry["context_tokens"] or (budget is not None and tokens > budget):
        raise ValueError(f"The recorded result for {qid} does not match its context, calls or verdict")
    for value in (reader, judge):
        counts = value["response"]["usage"]
        usage["input_tokens"] += counts["input_tokens"]
        usage["cached_input_tokens"] += counts.get("input_tokens_details", {}).get("cached_tokens", 0)
        usage["output_tokens"] += counts["output_tokens"]
        usage["reasoning_tokens"] += counts.get("output_tokens_details", {}).get("reasoning_tokens", 0)
        usage["successful_calls"] += 1
        usage["http_attempts"] += value["attempts"]
        usage["http_status_counts"].update(value["verified_http_statuses"])
        usage["observed_nanodollars"] += usage_cost(value["response"])
    return row


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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.integrations.gpt54_baselines",
                                     description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["prepare", "estimate", "run"])
    parser.add_argument("arm", choices=list(ARMS))
    parser.add_argument("--benchmark", choices=gate.GATE_BENCHMARKS,
                        help="Required for plain arms; full-context covers LoCoMo only")
    parser.add_argument("--archive", type=Path,
                        help="Saved 2026-09-23 run archive (default: the main checkout's data/gpt54-comparison-v1)")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="prepare only: packing.token_budget or packing.overhead_tokens for a plain arm")
    parser.add_argument("--max-usd", type=float, help="run only: the owner-approved spending cap for this arm")
    args = parser.parse_args(argv)
    benchmark = args.benchmark or ("locomo" if args.arm == "full-context" else None)
    if benchmark is None:
        parser.error("plain arms need --benchmark")
    if args.command != "prepare" and args.overrides:
        parser.error("--set applies to prepare only")
    if (args.command == "run") != (args.max_usd is not None):
        parser.error("--max-usd is required for run, which makes paid calls, and applies to run only")
    if args.command == "run" and not (math.isfinite(args.max_usd) and args.max_usd > 0):
        parser.error("--max-usd must be a positive amount")
    try:
        overrides = gate.parse_overrides(args.overrides)
        _check_arm(args.arm, benchmark)
        if overrides and args.arm != "full-context":
            gate.check_plain(args.arm.removeprefix("plain-"), overrides)
    except ValueError as exc:
        parser.error(str(exc))
    if args.command == "prepare":
        if _provenance()["dirty"]:
            parser.error("prepare records the code that builds the contexts; commit your changes first")
        gate._quiet_offline_cli()
        reported = 0

        def progress(done: int) -> None:
            nonlocal reported
            if done // 100 > reported // 100:
                print(f"{done} questions prepared", file=sys.stderr, flush=True)
            reported = done

        prepared = prepare(args.arm, benchmark, archive=args.archive, overrides=overrides or None,
                           progress=progress)
        print(json.dumps({key: prepared[key] for key in ("arm", "benchmark", "questions", "context_budget")}))
    elif args.command == "estimate":
        print(json.dumps(estimate(args.arm, benchmark, archive=args.archive), indent=2))
    else:
        _confirm_spend(parser, args.arm, benchmark, args.max_usd)
        result = asyncio.run(run(args.arm, benchmark, max_usd=args.max_usd, archive=args.archive))
        print(json.dumps({key: value for key, value in result.items() if key not in {"rows", "failures"}}))


if __name__ == "__main__":
    main()

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
  defaults and compared with them question by question (``compare``). Once
  ``prme`` has a complete answer run, the defaults at another commit on main
  are a new baseline, ``prme@<commit>`` (the commit's first 8 characters),
  which ``prepare prme`` and ``run prme`` choose from the checked-out commit.
  A later baseline also answers the defaults a second time: paired with an
  earlier baseline that read the same context text, ``compare`` reports the
  two as a repeat, which measures run-to-run variation (#118).

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
track, and needs its own ``calibrate`` before ``run`` or ``run-pair``. The ``prme`` arms and
``--sample`` smoke checks run on this track only. Its scores are not comparable
with GPT-5.4 scores; compare DeepSeek runs only with each other.

On this track a variant is answered only by ``run-pair``, together with a fresh
answer run of a prepared defaults baseline, in one session and interleaved
question by question, so drift and the time of day reach both sides equally
(#129). ``compare`` pairs a variant only with the defaults run answered
alongside it. ``run-pair`` with the ``prme`` arm and no variant answers the
baseline against itself: the A/A check of the paired test. A plain or
full-context arm can be paired with the defaults the same way.

The Ollama track answers under an amendment to the registered failure policy
(#132, recorded in ``FAILURE_AMENDMENT``), so one looping reader answer or
garbled verdict no longer stops a run or a pair: a truncated reader answer is
asked once more and then scored incorrect as ``truncated``, and a verdict that
does not read yes or no after the stray characters around it are removed is
judged once more and then scored incorrect as ``verdict_unresolved``. Every
result counts its retries and those outcomes, and ``compare`` refuses a result
with more than 1% of its questions scored that way. The GPT-5.4 track keeps
the registered policy unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
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
import uuid

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
# A defaults baseline recorded after the first one: prme@ and the first 8 characters of its commit.
SHORT_COMMIT = 8
_BASELINE = re.compile(rf"prme@([0-9a-f]{{{SHORT_COMMIT}}})")
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
# The Ollama track's amendment to RETRY_POLICY (#132, 2026-09-24). It applies to that track's standalone runs and
# to both sides of every pair alike; the GPT-5.4 track keeps RETRY_POLICY. Every binding and result on the Ollama
# track names the policy its answers were given under (answer_model.failure_policy), so none mixes two.
FAILURE_POLICY = "ollama-failure-policy-2026-09-24"
FAILURE_AMENDMENT = RESULTS / "2026-09-24" / "ollama-failure-policy-amendment.json"
OLLAMA_RETRY_POLICY = (
    "The registered policy as amended for the Ollama track on 2026-09-24 (#132). Within a run: at most four "
    "identical HTTP attempts for transient 429/5xx. A reader answer that ends for any reason other than stop is "
    "sent once more as the same request; if that answer also ends early, the question is scored incorrect without "
    "calling the judge and recorded as truncated. A verdict is accepted when what is left after removing leading "
    "and trailing characters other than the letters A to Z reads yes or no, in any case. Otherwise, or when the "
    "judge's response ends early, the judge is called once more, and if that verdict is not accepted either, the "
    "question is scored incorrect and recorded as verdict_unresolved. At most one retry per reader call and one per "
    "judge call. Any other invalid or ambiguous response is final, and the first failure stops new questions. A "
    "later run asks a question again, in a new attempt, only when no answer or verdict was received: a provider "
    "HTTP error, an ambiguous transport failure or an interrupted process. compare refuses a result in which more "
    "than 1% of the questions are truncated or verdict_unresolved. Every attempt is kept."
)
UNSCORED_PERCENT = 1
# The calls of one question attempt under the amended policy, in order: the call, then its one retry.
READER_CALLS = ("reader.json", "reader-retry.json")
JUDGE_CALLS = ("judge.json", "judge-retry.json")
# Outcomes scored incorrect without an accepted verdict; compare refuses a result with more than UNSCORED_PERCENT.
UNSCORED = ("truncated", "verdict_unresolved")
# A verdict under the amended policy: yes or no, in any case, with only characters other than the letters A to Z
# around it. The stray characters seen before verdicts so far were CJK characters, which Unicode counts as
# letters, so "letters" means the ones a yes or no is written in; ASCII matching keeps case folding to A to Z.
_VERDICT = re.compile(r"[^A-Za-z]*(yes|no)[^A-Za-z]*", re.IGNORECASE | re.ASCII | re.DOTALL)
# Besides this module, the code that sends, checks and judges the reader and judge calls. A repeat of the
# defaults must have answered with the same versions (compare).
ANSWER_MODULES = ("benchmarks/integrations/ollama_answers.py", "benchmarks/diagnostics/reader_judge.py",
                  "benchmarks/integrations/gpt54_official_prompt_loader.py",
                  "benchmarks/integrations/run_longmemeval_v2.py", *FROZEN_SOURCES)
# Messages from gpt54_budget.call and ollama_answers.call for failures that returned no answer text.
_NO_ANSWER_FAILURES = ("Provider HTTP ", "Ambiguous provider failure", "Provider attempts exhausted")
_ATTEMPT = re.compile(r"attempt-(\d+)")
# A judge prompt holds the question, the reference and a reader answer of at most READER_LIMIT tokens.
_JUDGE_BYTES = 4 * READER_LIMIT + 4096
# The two answer runs of a pair (#129): the defaults baseline, and the arm answered alongside it.
PAIR_SIDES = ("before", "after")
PAIR_ORDER = ("Interleaved question by question in registered order: each question's after side, then its before "
              "side, from one queue that the registration's concurrent requests share.")
# The order PAIR_ORDER describes: variant, defaults, variant, defaults.
_ANSWER_ORDER = ("after", "before")
# What both sides of one pair record identically, so compare can tell they were answered together.
_PAIR_KEYS = ("id", "number", "before", "after", "sha256")
_PAIR = re.compile(r"pair-(\d+)")
# Run log events that record the live Ollama server version.
_VERSION_EVENTS = frozenset({"started", "finished", "model-changed"})
# compare's paired bootstrap, as the evidence gate's comparisons draw it.
BOOTSTRAP_SEED = 42
# LoCoMo's questions come from 10 conversations, so its intervals resample conversations. Each LongMemEval-S
# question has its own history, so its intervals resample questions.
CONVERSATION_INTERVALS = frozenset({"locomo"})


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


def is_baseline(arm: str) -> bool:
    """The current defaults: the first baseline (``prme``) or a later one filed under its commit (``prme@...``)."""
    return arm == "prme" or bool(_BASELINE.fullmatch(arm))


def is_prme(arm: str) -> bool:
    """PRME's own retrieval: the current defaults (``is_baseline``) or a named variant of them."""
    return is_baseline(arm) or (arm.startswith("prme-") and bool(_VARIANT.fullmatch(arm.removeprefix("prme-"))))


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
    if is_baseline(arm) and overrides:
        raise ValueError(f"The {arm} arm prepares the current defaults; name a variant with --variant to change "
                         "settings")
    if is_prme(arm) and not is_baseline(arm) and not overrides:
        raise ValueError(f"The {arm} variant needs the settings it changes, as --set KEY=VALUE")
    if arm.startswith("plain-") and overrides:
        gate.check_plain(arm.removeprefix("plain-"), overrides)


def _module_identity() -> dict:
    return {path: digest(study.ROOT / path) for path in (
        "benchmarks/integrations/gpt54_baselines.py", "benchmarks/diagnostics/product_packing.py",
        "benchmarks/integrations/gpt54_official_prompt_loader.py",
        "benchmarks/integrations/analyze_gpt54_comparison.py", "benchmarks/integrations/ollama_answers.py",
        "benchmarks/integrations/run_longmemeval_v2.py", "benchmarks/diagnostics/reader_judge.py",
        "benchmarks/compare_evidence.py", "benchmarks/diagnostics/compare_public_captures.py",
        "benchmarks/evidence.py", *FROZEN_SOURCES)}


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
    events = _run_events(log)
    if any(_complete_run(event) for event in events):
        raise ValueError(f"{arm} {benchmark} has a complete answer run ({log}); its contexts are never prepared "
                         "again")
    paired = {path: _run_events(path) for path in _pair_logs(data, arm, benchmark)}
    for path, history in paired.items():
        if any(map(_complete_run, history)):
            raise ValueError(f"{arm} {benchmark} has a complete answer run in a pair ({path}); its contexts are "
                             "never prepared again")
    _check_overrides(arm, overrides)
    later = _BASELINE.fullmatch(arm) is not None
    if later:
        # The CLI names a later baseline after the checked-out commit; check a direct call before any work.
        _check_baseline_name(arm, benchmark, _provenance()["commit"], data)
    if any(event["event"] == "started" for history in (events, *paired.values()) for event in history):
        # Earlier runs, alone or in a pair, ended without a score. Preparing again is allowed, and stays on record.
        _log_run(data, arm, benchmark, {"event": "prepared-again"})
    if arm == "full-context":
        entries, extra = _prepare_full_context(folder, benchmark, questions)
    else:
        entries, extra = _prepare_replay(folder, arm, benchmark, archive, overrides, progress)
    if [entry["question_id"] for entry in entries] != [question["question_id"] for question in questions]:
        raise ValueError("Prepared contexts do not cover the registered questions in order")
    commit = extra["provenance"].get("commit")
    if later:
        _check_baseline_name(arm, benchmark, commit, data)
    prepared = {
        "kind": "gpt54-baseline-contexts", "complete": True, "arm": arm, "benchmark": benchmark,
        "questions": len(entries), "registration_sha256": digest(study.REG),
        "tokenizer": gate.gate_config(Path("{pack}"), overrides).packing.tokenizer, **extra,
        "modules": _module_identity(), "contexts": entries,
    }
    write_new(folder / "prepared.json", prepared)
    if later and not any(event["event"] == "new-baseline" for event in events):
        # Any other later baseline without a complete run was given up (baseline_arm refuses while one is still
        # prepared), so the new baseline's record names it.
        abandoned = [{"arm": name, "runs_started": sum(event["event"] == "started" for event in history)}
                     for name, history in _later_baselines(data, benchmark).items()
                     if name != arm and history and not any(_complete_run(event) for event in history)]
        _log_run(data, arm, benchmark, {
            "event": "new-baseline", "prepared_commit": commit,
            "first_baseline_commit": _complete_run_commit(data, "prme", benchmark), "abandoned": abandoned})
    return prepared


def baseline_arm(benchmark: str, commit: str | None, *, data: Path) -> str:
    """The arm that records the current defaults' baseline at ``commit``.

    The first baseline is the ``prme`` arm. Once it has a complete answer run,
    the defaults at any other commit (after a default or the model identity
    changes, or to answer the defaults again for #118) are a new baseline in
    their own arm, ``prme@`` and the commit's
    first ``SHORT_COMMIT`` characters, with their own contexts, answers and run
    log. No earlier baseline or its record is touched, and each commit has at
    most one baseline. While another later baseline is prepared and not
    complete, there is none: answer that one from its commit, or move its
    folder aside to give it up, which the next baseline's run log records.
    """
    first = _complete_run_commit(data, "prme", benchmark)
    if first is None or first == commit:
        return "prme"
    arm = f"prme@{(commit or '')[:SHORT_COMMIT]}"
    if not _BASELINE.fullmatch(arm):
        raise ValueError(f"The prme {benchmark} baseline is complete, and the checked-out commit ({commit}) cannot "
                         "name a new one; run from a git checkout")
    for name, history in _later_baselines(data, benchmark).items():
        folder = data / name / benchmark
        if name != arm and (folder / "prepared.json").is_file() and not any(map(_complete_run, history)):
            raise ValueError(f"{name} {benchmark} is a baseline that is prepared and not complete. Check out commit "
                             f"{name.removeprefix('prme@')} to answer it, or move {folder} aside to give it up.")
    return arm


def _check_baseline_name(arm: str, benchmark: str, commit: str | None, data: Path) -> None:
    """A later baseline must be the one ``baseline_arm`` names for the commit that prepares it."""
    expected = baseline_arm(benchmark, commit, data=data)
    if expected != arm:
        raise ValueError(f"The defaults at {commit} are the {expected} {benchmark} baseline, not {arm}")


def _later_baselines(data: Path, benchmark: str) -> dict[str, list[dict]]:
    """Every later baseline of the defaults with a folder or a run log for this benchmark, with its run log."""
    names = {path.name for path in data.glob("prme@*") if (path / benchmark).is_dir()}
    logs = (data / "runs").glob(f"prme@*-{benchmark}.jsonl")
    names |= {path.name.removesuffix(f"-{benchmark}.jsonl") for path in logs}
    return {name: _run_events(_run_log_path(data, name, benchmark)) for name in sorted(names)
            if _BASELINE.fullmatch(name)}


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

def _numbered(folder: Path, pattern: re.Pattern) -> list[tuple[int, Path]]:
    """The folders whose names match ``pattern``, with the number it captures, in numeric order."""
    return sorted((int(match.group(1)), path) for path in folder.glob("*")
                  if path.is_dir() and (match := pattern.fullmatch(path.name)))


def _attempts(folder: Path) -> list[Path]:
    return [path for _, path in _numbered(folder, _ATTEMPT)]


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
def _run_lock(folder: Path, name: str | None = None) -> Iterator[None]:
    with (folder / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"Another run of {name or f'{folder.parent.name} {folder.name}'} is in progress") from None
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


def _complete_run(event: dict) -> bool:
    return event["event"] == "finished" and bool(event.get("complete")) and event.get("sample") is None


def _complete_run_commit(data: Path, arm: str, benchmark: str) -> str | None:
    """The commit that prepared the contexts of the arm's complete answer run, or None before it has one."""
    finished = [event for event in _run_events(_run_log_path(data, arm, benchmark)) if _complete_run(event)]
    if not finished:
        return None
    commit = finished[-1].get("prepared_commit")
    manifest = data / arm / benchmark / "prepared.json"
    if commit is None and manifest.is_file():
        # Run logs written before the finished event recorded the commit: a complete arm keeps its manifest.
        commit = (json.loads(manifest.read_text()).get("provenance") or {}).get("commit")
    if not commit:
        raise ValueError(f"{arm} {benchmark} has a complete answer run, but neither its run log nor {manifest} "
                         "records the commit that prepared it")
    return commit


def _run_history(data: Path, arm: str, benchmark: str) -> dict:
    """What the arm's run log holds, so a result shows every earlier run and preparation."""
    path = _run_log_path(data, arm, benchmark)
    events = _run_events(path)
    # An arm answered only in pairs has no run log of its own until it is prepared again.
    history = {"sha256": digest(path) if path.exists() else None,
               "runs_started": sum(event["event"] == "started" for event in events),
               "prepared_again": sum(event["event"] == "prepared-again" for event in events)}
    for event in events:
        if event["event"] == "new-baseline":
            # A later baseline of the defaults: its commit, the first baseline's, and any it replaced unfinished.
            history["new_baseline"] = {key: event.get(key) for key in (
                "at", "prepared_commit", "first_baseline_commit", "abandoned")}
    return history


def _log_run(data: Path, arm: str, benchmark: str, event: dict) -> None:
    """Append one event to the arm's run log, which is never rewritten."""
    _append_event(_run_log_path(data, arm, benchmark), event)


def _append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"at": study.utc(), **event}, sort_keys=True) + "\n")


def _pair_root(data: Path, before: str, after: str, benchmark: str) -> Path:
    """Where the pairs of a baseline and the arm answered alongside it keep their answers, one folder per pair."""
    return data / "pairs" / before / after / benchmark


def _pair_log_path(data: Path, before: str, after: str, benchmark: str) -> Path:
    # Outside the pair folders, like the arms' run logs, so removing a pair never erases that it was started.
    return data / "runs" / "pairs" / before / f"{after}-{benchmark}.jsonl"


def _pair_logs(data: Path, arm: str, benchmark: str) -> list[Path]:
    """The run logs of every pair with the arm on either side, for this benchmark."""
    patterns = (_pair_log_path(data, arm, "*", benchmark), _pair_log_path(data, "*", arm, benchmark))
    return sorted({path for pattern in patterns for path in data.glob(str(pattern.relative_to(data)))})


def _version_of(settings: dict) -> str | None:
    """The Ollama server version in an answer model's settings: its identity at the lookup that recorded them."""
    return (settings.get("identity") or {}).get("server_version")


def _sorted_versions(versions) -> list[str | None]:
    return sorted(set(versions), key=lambda version: (version is None, version or ""))


def _server_versions(bound: dict, events: list[dict], end: str | None) -> list[str | None]:
    """Every Ollama server version the answers were given under: at binding, at each start and at the end.

    ``events`` are the run log events of the answer runs whose answers the
    result reports. A start or finish logged before server versions were
    recorded counts as unknown (None).
    """
    return _sorted_versions([_version_of(bound), end, *(event.get("server_version") for event in events
                                                          if event["event"] in _VERSION_EVENTS)])


def _current_preparation(events: list[dict]) -> list[dict]:
    """The events since the arm was last prepared again; earlier runs answered contexts that were removed."""
    marks = [number for number, event in enumerate(events) if event["event"] == "prepared-again"]
    return events[marks[-1] + 1:] if marks else events


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
    """The model's settings and identity under the amended failure policy, and the calibration it passed.

    The calibration is matched on the model's own settings: the failure policy
    decides how answers are scored, not what the reader and judge are sent.
    """
    failure_amendment()
    settings = ollama_answers.describe(model)
    calibration = _passed_calibration(data, settings)
    if calibration is None:
        raise ValueError(f"{model.model} has not passed calibration with these settings and this Ollama model "
                         f"identity ({_calibration_folder(data)}); run calibrate first")
    return _under_policy(settings), calibration


def _under_policy(settings: dict) -> dict:
    """An Ollama model's settings with the failure policy its answers are given under (#132)."""
    return {**settings, "failure_policy": FAILURE_POLICY}


def _bind_answer_model(folder: Path, settings: dict) -> dict:
    """The arm's reader and judge. Once an answer exists, answers from another model are never mixed in.

    Returns the bound settings, which answered every question in the arm. An
    arm is never moved to another failure policy once any question was asked,
    since an earlier attempt's final failure would then stand under rules that
    would have asked it again (#132).
    """
    path = folder / "answer-model.json"
    if path.exists():
        bound = json.loads(path.read_text())
        if ollama_answers.same_model(bound, settings):
            return bound
        if _policy_of(bound) != _policy_of(settings) and any((folder / "execution").glob("*/attempt-*")):
            raise ValueError(f"This arm was answered under another failure policy ({path}), and no arm mixes two "
                             "policies (#132). An incomplete arm can be moved aside and prepared again.")
        if any((folder / "execution").glob("*/attempt-*/result.json")):
            raise ValueError(f"This arm was answered by another reader and judge or other settings ({path})")
    _write_json(path, settings)
    return settings


# Amended failure policy (#132) ---------------------------------------------------

def policy_parameters() -> dict:
    """The values that decide how the amended policy scores, which its recorded amendment pins."""
    return {"verdict_pattern": _VERDICT.pattern, "verdict_flags": int(_VERDICT.flags),
            "reader_calls": list(READER_CALLS), "judge_calls": list(JUDGE_CALLS), "unscored_outcomes": list(UNSCORED),
            "unscored_limit_percent": UNSCORED_PERCENT}


def failure_amendment() -> dict:
    """The recorded amendment behind ``FAILURE_POLICY``, after checking it amends this registration with these rules.

    The rules are pinned twice: as the policy text, and as the values that
    score answers under it (``policy_parameters``), so a change to either
    needs a new amendment.
    """
    value = json.loads(FAILURE_AMENDMENT.read_text())
    if (value.get("kind") != "ollama-failure-policy-amendment" or value.get("id") != FAILURE_POLICY
            or value.get("registration_sha256") != digest(study.REG)
            or value.get("retry_policy") != OLLAMA_RETRY_POLICY or value.get("parameters") != policy_parameters()
            or not isinstance(value.get("issue"), int) or not isinstance(value.get("registered_at"), str)):
        raise ValueError(f"{FAILURE_AMENDMENT} does not state the failure policy this module applies to this "
                         "registration")
    return {"id": FAILURE_POLICY, "issue": value["issue"], "registered_at": value["registered_at"],
            "sha256": digest(FAILURE_AMENDMENT)}


def _policy_of(answer_model: dict) -> str | None:
    """The failure policy an answer model names: ``FAILURE_POLICY``, or None for the registered one."""
    policy = answer_model.get("failure_policy")
    if policy not in (None, FAILURE_POLICY):
        raise ValueError(f"Unknown failure policy {policy!r}")
    return policy


def normalized_verdict(text: str) -> bool | None:
    """Yes or no, in any case, once every character but A to Z is removed from both ends; otherwise None."""
    match = _VERDICT.fullmatch(text)
    return None if match is None else match.group(1).lower() == "yes"


def _judge_verdict(judge: dict) -> bool | None:
    """A judge call's verdict under the amended policy, or None when the call was truncated or is not accepted."""
    return None if judge.get("truncated") is True else normalized_verdict(judge["text"])


def _registered_verdict(text: str) -> bool | None:
    """The registered verdict rule's reading of ``text``, or None where it has none."""
    try:
        return study.verdict(text)
    except ValueError:
        return None


def _registered_scored(attempt: Path, judge: dict) -> dict:
    """A question's scoring under the registered policy: one reader call and one judge call, both final."""
    return {"correct": study.verdict(judge["text"]), "reader_sha256": digest(attempt / READER_CALLS[0]),
            "judge_sha256": digest(attempt / JUDGE_CALLS[0])}


def _outside_policy(detail: str) -> ValueError:
    return ValueError(f"The recorded calls do not follow the amended failure policy: {detail}")


def _scored(attempt: Path, readers: list[dict], judges: list[dict]) -> dict:
    """A question's scoring under the amended policy, from its reader and judge calls in the order they were made.

    Refuses calls the policy does not make: a retry after an answer that was
    not truncated or after an accepted verdict, a judge call after a reader
    answer that stayed truncated, or a missing retry.
    """
    truncated = [reader.get("truncated") is True for reader in readers]
    verdicts = [_judge_verdict(judge) for judge in judges]
    if not readers:
        raise _outside_policy("no reader call")
    if len(readers) > len(READER_CALLS) or not all(truncated[:-1]):
        raise _outside_policy("a reader retry without a truncated answer before it")
    if truncated[-1] and len(readers) < len(READER_CALLS):
        raise _outside_policy("a truncated answer that was not asked again")
    if truncated[-1] and judges:
        raise _outside_policy("a judge call after an answer that stayed truncated")
    if not truncated[-1] and not judges:
        raise _outside_policy("an answer that was never judged")
    if len(judges) > len(JUDGE_CALLS) or any(verdict is not None for verdict in verdicts[:-1]):
        raise _outside_policy("a judge retry after an accepted verdict")
    if judges and verdicts[-1] is None and len(judges) < len(JUDGE_CALLS):
        raise _outside_policy("a verdict that was not accepted and not judged again")
    outcome = "truncated" if truncated[-1] else "judged" if verdicts[-1] is not None else "verdict_unresolved"
    return {"correct": verdicts[-1] if outcome == "judged" else False, "outcome": outcome,
            "reader_sha256": digest(attempt / READER_CALLS[0]),
            "judge_sha256": digest(attempt / JUDGE_CALLS[0]) if judges else None,
            "reader_retry_sha256": digest(attempt / READER_CALLS[1]) if len(readers) > 1 else None,
            "judge_retry_sha256": digest(attempt / JUDGE_CALLS[1]) if len(judges) > 1 else None,
            # Accepted only because stray characters were removed around it.
            "verdict_normalized": outcome == "judged" and _registered_verdict(judges[-1]["text"]) is None}


async def _ask_amended(ask, client, semaphore, benchmark: str, question: dict, context: str, attempt: Path) -> dict:
    """Ask and judge one question under the amended policy: each reader and judge call is sent at most twice."""
    prompt = study.reader_prompt(benchmark, question, context)
    readers: list[dict] = []
    for name in READER_CALLS:
        readers.append(await ask(client, semaphore, prompt=prompt, limit=READER_LIMIT, path=attempt / name,
                                 truncated_ok=True))
        if readers[-1].get("truncated") is not True:
            break
    judges: list[dict] = []
    if readers[-1].get("truncated") is not True:
        prompt = study.judge_prompt(benchmark, question, readers[-1]["text"])
        for name in JUDGE_CALLS:
            # An empty verdict is a verdict the policy does not accept, so the judge is asked again.
            judges.append(await ask(client, semaphore, prompt=prompt, limit=JUDGE_LIMIT, path=attempt / name,
                                    truncated_ok=True, empty_ok=True))
            if _judge_verdict(judges[-1]) is not None:
                break
    return _scored(attempt, readers, judges)


def _recorded_calls(attempt: Path, names: tuple[str, ...]) -> list[Path]:
    """The recorded calls among ``names``, which must be the first of them in order: a retry never stands alone."""
    present = [name for name in names if (attempt / name).exists()]
    if present != list(names[:len(present)]):
        raise _outside_policy("a retry recorded without the call it repeats")
    return [attempt / name for name in present]


def _within_limit(unscored: int, total: int) -> bool:
    return unscored * 100 <= UNSCORED_PERCENT * total


def _outcome_counts(rows: list[dict], total: int) -> dict:
    """How many questions the amended policy asked again, accepted after removing stray characters, or left unscored."""
    keys = ("outcome", "reader_retry_sha256", "judge_retry_sha256", "verdict_normalized")
    if any(key not in row for row in rows for key in keys):
        raise ValueError("A result under the amended failure policy has rows without their outcome and retries")
    counts = {outcome: sum(row["outcome"] == outcome for row in rows) for outcome in UNSCORED}
    return {"reader_retries": sum(row["reader_retry_sha256"] is not None for row in rows),
            "judge_retries": sum(row["judge_retry_sha256"] is not None for row in rows),
            "verdicts_normalized": sum(row["verdict_normalized"] for row in rows), **counts,
            "unscored": sum(counts.values()), "unscored_limit_percent": UNSCORED_PERCENT,
            "within_limit": _within_limit(sum(counts.values()), total)}


def _invalid_reason(results: dict[str, dict], total: int) -> str | None:
    """Why complete results under the amended policy are ones compare will refuse, or None when they are not."""
    over = {name: result["failure_policy"]["unscored"] for name, result in results.items()
            if not result["failure_policy"]["within_limit"]}
    if not over:
        return None
    counts = ", ".join(f"{name} {count}" for name, count in over.items())
    return (f"more than {UNSCORED_PERCENT}% of the questions were truncated or left without a verdict ({counts} of "
            f"{total})")


def _invalid(label: str, results: dict[str, dict], total: int, complete: bool) -> dict:
    """What a finished event records about unscored questions, with a warning when compare will refuse the results.

    Results answered under the registered policy have nothing to record.
    """
    if any("failure_policy" not in result for result in results.values()):
        return {}
    fields: dict = {"unscored": {name: result["failure_policy"]["unscored"] for name, result in results.items()}}
    reason = _invalid_reason(results, total) if complete else None
    if reason:
        fields["invalid"] = reason
        print(f"{label} is complete but invalid: {reason}. It is published and stays on record, and compare refuses "
              "it (#132).", file=sys.stderr, flush=True)
    return fields


def _bound_policy(folder: Path) -> str | None:
    """The failure policy an Ollama arm or pair side is bound to: ``FAILURE_POLICY``, or None for the registered one.

    Answers given before the amendment keep the registered rules, so their records still verify as they were.
    """
    path = folder / "answer-model.json"
    if not path.is_file():
        raise ValueError(f"No answer model is bound at {path}")
    try:
        return _policy_of(json.loads(path.read_text()))
    except ValueError as exc:
        raise ValueError(f"{exc} ({path})") from None


async def run(arm: str, benchmark: str, *, max_usd: float | None = None,
              model: ollama_answers.AnswerModel | None = None, data: Path | None = None, results: Path = RESULTS,
              archive: Path | None = None, api_key: str | None = None, sample: int | None = None) -> dict:
    """Answer and judge every question that has no result yet, then report the whole arm.

    ``RETRY_POLICY`` says which failures a later run asks again. The arm is
    complete only when every question has an authenticated answer and verdict.
    With ``model``, the Ollama track answers instead of GPT-5.4, under
    ``OLLAMA_RETRY_POLICY`` (#132): no API key,
    ledger or cap. Only that track runs the ``prme`` arms and ``sample``, which
    asks only ``sample_questions`` as a smoke check, not a score; a later full
    run reuses its answers. A named variant of the defaults is answered only by
    ``run_pair``, alongside a fresh run of the defaults (#129).
    """
    if model is None:
        if not (max_usd is not None and math.isfinite(max_usd) and max_usd > 0):
            raise ValueError("Pass the owner-approved spending cap as --max-usd")
        if is_prme(arm) or sample is not None:
            raise ValueError("The prme arms and samples run on the Ollama track only")
    elif max_usd is not None or api_key is not None:
        raise ValueError("An Ollama run makes no paid calls; it takes no spending cap or API key")
    elif is_prme(arm) and not is_baseline(arm):
        raise ValueError(f"{arm} is a variant of the defaults, which is answered only alongside a fresh run of the "
                         "defaults: use run-pair with a prepared baseline (#129)")
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
        if _complete_result(private):
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
                                            "answer_model_sha256": sha(answer_model),
                                            "server_version": _version_of(settings),
                                            "failure_policy": _policy_of(answer_model)})
        amended = _policy_of(answer_model) is not None
        async with opened as client:
            await _answer(run_name, benchmark, [_Side(folder, folder, entries)], questions,
                          registration["provider_concurrency"], client, ask, ledger, amended=amended)
        end_version = None
        if model is not None:
            ended = _under_policy(ollama_answers.describe(model))
            end_version = _version_of(ended)
            if not ollama_answers.same_model(answer_model, ended):
                _log_run(data, arm, benchmark, {"event": "model-changed", "sample": sample,
                                                "server_version": end_version})
                raise RuntimeError(f"The Ollama model identity changed during {run_name}; nothing is reported. Its "
                                   "answers stay in the arm, which refuses another model.")
            events = _current_preparation(_run_events(_run_log_path(data, arm, benchmark)))
            extra["server_versions"] = _server_versions(answer_model, events, end_version)
        result = report(arm, benchmark, folder, questions, prepared, entries, ledger, model=model)
        result.update(started_at=started, finished_at=study.utc(),
                      retry_policy=OLLAMA_RETRY_POLICY if amended else RETRY_POLICY,
                      provenance=_provenance(), modules=_module_identity(), answer_model=answer_model, **extra,
                      prepared=_prepared_summary(prepared))
        if sample is not None:
            _label_sample(result, sample, questions)
        _write_json(private, result)
        if model is not None:
            invalid = _invalid(run_name, {arm: result}, len(questions), result["complete"] and sample is None)
            _log_run(data, arm, benchmark, {"event": "finished", "sample": sample, "complete": result["complete"],
                                            "completed": result["completed"], "total": result["total"],
                                            "prepared_commit": result["prepared"]["commit"],
                                            "server_version": end_version, **invalid})
            result["run_log"] = _run_history(data, arm, benchmark)
        if not result["complete"]:
            raise RuntimeError(
                f"{run_name} is incomplete: {result['completed']}/{result['total']} answered, "
                f"{result['final_failures']} final failures. Run it again to retry the rest; no partial score "
                "is reported.")
        prefix = "gpt54-baseline" if model is None else model.track
        suffix = "" if sample is None else f"-sample-{sample}"
        _publish(results / started[:10] / f"{prefix}-{arm}-{benchmark}{suffix}-result.json", result)
    return result


def _complete_result(path: Path) -> bool:
    return path.is_file() and bool(json.loads(path.read_text()).get("complete"))


def _label_sample(result: dict, sample: int, questions: list[dict]) -> None:
    """A smoke check only: the count of accepted answers, with no accuracy or intervals to cite."""
    for key in ("accuracy", "ci95_questions", "ci95_source_clusters", "categories"):
        result.pop(key, None)
    result.update(kind=result["kind"] + "-sample", sample={
        "per_category": sample, "question_ids": [row["question_id"] for row in questions],
        "note": "A fixed smoke sample: the first questions of each category in registered order. "
                "Not a benchmark score."})


def _publish(path: Path, result: dict) -> None:
    # Failure messages can name local paths; the private copy keeps them.
    write_new(path, {**result, "failures": [
        {key: value for key, value in failure.items() if key != "message"} for failure in result["failures"]]})


# Interleaved pairs (#129) -------------------------------------------------------

async def run_pair(before: str, after: str, benchmark: str, *, model: ollama_answers.AnswerModel,
                   data: Path | None = None, results: Path = RESULTS, sample: int | None = None) -> dict:
    """Answer ``after`` together with a fresh run of the defaults baseline ``before``, in one session.

    Both arms must be prepared, and ``before`` must have a complete answer run
    of its own (``run``), which records the baseline. ``after`` is a variant of
    the defaults, another arm, or ``before`` itself for the A/A check. The two
    answer runs share one queue, interleaved question by question
    (``PAIR_ORDER``), so drift and the time of day reach both sides equally,
    and each side is reported as its own result, marked with the pair.
    ``compare`` pairs a variant only with the defaults run answered in the same
    pair. Both sides answer under ``OLLAMA_RETRY_POLICY`` (#132). A call resumes
    the latest pair of these arms while it can still finish
    (``_pair_blocker``), and otherwise starts the next one, which is
    how a confirmation redraws both sides; every pair stays on record.
    ``sample`` works as in ``run``. Returns the pair's mark and both results.
    """
    if not isinstance(model, ollama_answers.AnswerModel):
        raise ValueError("Pairs are answered on the Ollama track only")
    _check_pair_baseline(before)
    data = data or data_root(model)
    if _complete_run_commit(data, before, benchmark) is None:
        raise ValueError(f"The {before} {benchmark} baseline has no complete answer run of its own; answer it with "
                         "run first, which records the baseline")
    registration, questions = registered_protocol(benchmark)
    arms = {"before": before, "after": after}
    # The A/A check reads one prepared arm on both sides, so it is loaded and checked once.
    prepared_arms = {arm: load_prepared(arm, benchmark, questions, data=data) for arm in dict.fromkeys(arms.values())}
    loaded = {side: prepared_arms[arm] for side, arm in arms.items()}
    if sample is not None:
        questions = sample_questions(questions, sample)
    prepared_sha256 = {side: digest(folder / "prepared.json") for side, (folder, _, _) in loaded.items()}
    root = _pair_root(data, before, after, benchmark)
    root.mkdir(parents=True, exist_ok=True)
    log = _pair_log_path(data, before, after, benchmark)
    started = study.utc()
    with _run_lock(root, f"the {before} and {after} {benchmark} pairs"), _registered_judge(registration, benchmark):
        # Checked before a pair is opened, so a failed check leaves no pair on record.
        settings, calibration = _ollama_answer_model(model, data)
        folder, record = _open_pair(root, log, arms, benchmark, prepared_sha256, settings)
        number = record["number"]
        run_name = f"{before} and {after} {benchmark} pair {number}" + (
            "" if sample is None else f" sample of {sample}")
        private = "result.json" if sample is None else f"sample-{sample}-result.json"
        # A complete pair is never opened again, so only a sample of the open pair can already be complete.
        if sample is not None and all(_complete_result(folder / side / private) for side in PAIR_SIDES):
            raise ValueError(f"{run_name} is already complete; a finished sample is never rerun")
        for side in PAIR_SIDES:
            (folder / side).mkdir(exist_ok=True)
        bound = {side: _bind_answer_model(folder / side, settings) for side in PAIR_SIDES}
        _append_event(log, {"event": "started", "pair": number, "sample": sample,
                            "answer_model_sha256": {side: sha(value) for side, value in bound.items()},
                            "server_version": _version_of(settings), "failure_policy": _policy_of(settings)})
        sides = []
        for side in _ANSWER_ORDER:
            prepared_folder, _, entries = loaded[side]
            sides.append(_Side(prepared_folder, folder / side, entries))
        async with ollama_answers.client_for(model) as client:
            await _answer(run_name, benchmark, sides, questions, registration["provider_concurrency"], client,
                          partial(ollama_answers.call, model=model), None, amended=_policy_of(settings) is not None)
        ended = _under_policy(ollama_answers.describe(model))
        end_version = _version_of(ended)
        if not all(ollama_answers.same_model(value, ended) for value in bound.values()):
            _append_event(log, {"event": "model-changed", "pair": number, "sample": sample,
                                "server_version": end_version})
            raise RuntimeError(f"The Ollama model identity changed during {run_name}; nothing is reported. Its "
                               "answers stay in the pair, and the next run starts a new pair.")
        events = _pair_events(log, number)
        mark = {**{key: record[key] for key in _PAIR_KEYS if key != "sha256"}, "sha256": digest(folder / "pair.json"),
                "order": PAIR_ORDER, "pairs_started": len(_pair_numbers(root, log)),
                "earlier_pairs": _earlier_pairs(log, number)}
        shared = {"started_at": started, "finished_at": study.utc(), "retry_policy": OLLAMA_RETRY_POLICY,
                  "provenance": _provenance(), "modules": _module_identity(), "calibration": calibration}
        outcome = {}
        for side in PAIR_SIDES:
            prepared_folder, prepared, entries = loaded[side]
            result = report(arms[side], benchmark, prepared_folder, questions, prepared, entries, None, model=model,
                            answers=folder / side)
            result.update(**shared, answer_model=bound[side],
                          server_versions=_server_versions(bound[side], events, end_version),
                          prepared=_prepared_summary(prepared), pair={**mark, "side": side})
            if sample is not None:
                _label_sample(result, sample, questions)
            outcome[side] = result
        # Both sides are verified before either is written, so a failed check leaves neither side's result.
        for side, result in outcome.items():
            _write_json(folder / side / private, result)
        versions = [version for version in _sorted_versions(
            version for result in outcome.values() for version in result["server_versions"]) if version]
        # compare refuses a pair whose server version changed, so such a pair is never published as complete.
        changed = f"the Ollama server version changed ({', '.join(versions)})" if len(versions) > 1 else None
        complete = changed is None and all(result["complete"] for result in outcome.values())
        invalid = _invalid(run_name, outcome, len(questions), complete and sample is None)
        _append_event(log, {"event": "finished", "pair": number, "sample": sample, "complete": complete,
                            "completed": {side: result["completed"] for side, result in outcome.items()},
                            "total": len(questions), "server_version": end_version,
                            "prepared_commit": {side: result["prepared"]["commit"]
                                                for side, result in outcome.items()},
                            **({"reason": changed} if changed else {}), **invalid})
        for side, result in outcome.items():
            # This pair's own starts, and the history of the arm whose contexts the side read.
            result["run_log"] = {"sha256": digest(log),
                                 "runs_started": sum(event["event"] == "started" for event in events),
                                 "arm": _run_history(data, arms[side], benchmark)}
        if changed:
            raise RuntimeError(f"{run_name}: {changed} while it was answered, so nothing is published. Its answers "
                               "stay on record, and the next run starts a new pair.")
        if not complete:
            counts = "; ".join(f"{side} {result['completed']}/{result['total']} answered, "
                               f"{result['final_failures']} final failures" for side, result in outcome.items())
            raise RuntimeError(f"{run_name} is incomplete: {counts}. Run it again: it asks the questions that got no "
                               "answer, or starts the next pair if a final failure means this one can never finish. "
                               "No partial score is reported.")
        suffix = "" if sample is None else f"-sample-{sample}"
        for side, result in outcome.items():
            _publish(results / started[:10] / f"{model.track}-{before}-vs-{after}-{benchmark}-pair-{number}-{side}"
                                              f"{suffix}-result.json", result)
    return {"pair": mark, **outcome}


def _check_pair_baseline(arm: str) -> None:
    if not is_baseline(arm):
        raise ValueError(f"The before side of a pair is a baseline of the defaults (prme or prme@<commit>), not {arm}")


def _pair_events(log: Path, number: int) -> list[dict]:
    return [event for event in _run_events(log) if event.get("pair") == number]


def _pair_numbers(root: Path, log: Path) -> list[int]:
    """Every pair of these arms on record: each folder, and each pair its run log names."""
    return sorted({number for number, _ in _numbered(root, _PAIR)}
                  | {event["pair"] for event in _run_events(log) if "pair" in event})


def _open_pair(root: Path, log: Path, arms: dict[str, str], benchmark: str, prepared_sha256: dict[str, str],
               settings: dict) -> tuple[Path, dict]:
    """The pair to answer: the latest one while it can still finish, otherwise a new one after every other.

    An unfinished pair that cannot be finished is given up on record: its run
    log gets an ``abandoned`` event with the reason, and its folder stays.
    """
    numbers = _pair_numbers(root, log)
    if numbers:
        latest = root / f"pair-{numbers[-1]}"
        events = _pair_events(log, numbers[-1])
        reason = _pair_blocker(latest, events, prepared_sha256, settings)
        if reason is None:
            return latest, json.loads((latest / "pair.json").read_text())
        if reason != "complete" and not any(event["event"] == "abandoned" for event in events):
            _append_event(log, {"event": "abandoned", "pair": numbers[-1], "reason": reason})
            print(f"Pair {numbers[-1]} cannot be finished ({reason}). It stays on record, and pair "
                  f"{numbers[-1] + 1} starts.", file=sys.stderr, flush=True)
    number = numbers[-1] + 1 if numbers else 1
    folder = root / f"pair-{number}"
    # A new folder claims the number; the record is then written whole.
    folder.mkdir()
    record = {"kind": "ollama-answer-pair", "id": uuid.uuid4().hex, "number": number, **arms,
              "benchmark": benchmark, "prepared_sha256": prepared_sha256, "order": PAIR_ORDER,
              "failure_policy": _policy_of(settings), "created_at": study.utc()}
    _write_json(folder / "pair.json", record)
    return folder, record


def _pair_blocker(folder: Path, events: list[dict], prepared_sha256: dict[str, str], settings: dict) -> str | None:
    """Why a pair cannot be finished as one session of these arms, under this model and server version, or None.

    It cannot when both sides are complete, it was started under another
    failure policy (#132), its record is missing, the arms' contexts were
    prepared again, a question failed finally, a side was answered by another
    model or settings, or the pair recorded another Ollama server version
    (``compare`` refuses such a pair). The next pair starts instead, and this
    one stays on record. The failure policy is read from the run log, so it
    is found even when the pair's folder was moved aside.
    """
    if all(_complete_result(folder / side / "result.json") for side in PAIR_SIDES):
        return "complete"
    if any(event["event"] == "started" and event.get("failure_policy") != _policy_of(settings) for event in events):
        return "it was started under another failure policy"
    path = folder / "pair.json"
    if not path.is_file():
        return "its pair.json record is missing"
    if json.loads(path.read_text()).get("prepared_sha256") != prepared_sha256:
        return "an arm was prepared again"
    if any(_status(question) == "final" for side in PAIR_SIDES
           for question in (folder / side / "execution").glob("*") if question.is_dir()):
        return "a question failed finally"
    bindings = [folder / side / "answer-model.json" for side in PAIR_SIDES]
    bound = [json.loads(binding.read_text()) for binding in bindings if binding.is_file()]
    if not all(ollama_answers.same_model(value, settings) for value in bound):
        return "it was answered by another model identity or settings"
    recorded = {_version_of(value) for value in bound} | {
        event.get("server_version") for event in events if event["event"] in _VERSION_EVENTS}
    if not recorded <= {_version_of(settings)}:
        return "it was answered under another Ollama server version"
    return None


def _earlier_pairs(log: Path, number: int) -> list[dict]:
    """Every earlier pair of these arms and how it ended, so a result shows the pairs it was not."""
    states = {}
    for event in _run_events(log):
        pair = event.get("pair")
        if pair is None or pair >= number:
            continue
        if event["event"] == "finished" and event.get("sample") is None and event.get("complete"):
            states[pair] = f"complete but invalid: {event['invalid']}" if event.get("invalid") else "complete"
        elif event["event"] == "abandoned":
            states[pair] = f"abandoned: {event['reason']}"
        elif event["event"] == "finished" and event.get("reason"):
            states[pair] = f"not published: {event['reason']}"
        else:
            states.setdefault(pair, "unfinished")
    return [{"number": pair, "state": states[pair]} for pair in sorted(states)]


def _prepared_summary(prepared: dict) -> dict:
    """What built the contexts: the commit, whether the tree was clean, and the settings a variant changed."""
    provenance = prepared.get("provenance") or {}
    return {"commit": provenance.get("commit"), "dirty": provenance.get("dirty"),
            "worktree_sha256": provenance.get("worktree_sha256"), "overrides": provenance.get("overrides", {}),
            "context_rule": prepared["context_rule"],
            "contexts_matching_saved_run": prepared.get("contexts_matching_saved_run")}


def _server_version(result: dict) -> str | None:
    return _version_of(result["answer_model"])


def _same_inputs(before: dict, after: dict) -> bool:
    """Whether two results sent the reader and judge the same inputs: context text, budget, code and server.

    A row's context hash covers the whole capture file, which includes a random
    receipt id, so two preparations never share one (#125). The text is shown
    to be the same when both preparations reproduce the saved 2026-09-23 run's
    context on every question, which holds only while the defaults still do.
    The modules that send, check and judge the calls must match too; this
    module's own digest changes with every edit to it, so it is left out.
    """
    return (before.get("context_budget") == after.get("context_budget")
            and all((result.get("prepared") or {}).get("contexts_matching_saved_run") == len(result["rows"])
                    for result in (before, after))
            and all(before.get("modules", {}).get(path) is not None
                    and before["modules"][path] == after.get("modules", {}).get(path) for path in ANSWER_MODULES)
            and _server_version(before) == _server_version(after))


def _recorded_versions(result: dict) -> list[str | None]:
    """The Ollama server versions a result recorded; results from before #129 recorded only the one at binding."""
    return result.get("server_versions") or [_server_version(result)]


def _pair_of(before: dict, after: dict) -> dict | None:
    """The pair both results were answered in, or None when each was answered on its own."""
    first, second = before.get("pair"), after.get("pair")
    if first is None and second is None:
        return None
    rule = "A variant is compared only with the defaults run answered alongside it in one pair (run-pair)."
    if first is None or second is None:
        raise ValueError(f"Only one of these results was answered in a pair. {rule}")
    if (first.get("side"), second.get("side")) != PAIR_SIDES:
        raise ValueError(f"Pass the pair's before side (the defaults) as before and its after side as after. {rule}")
    if any(first.get(key) is None or first.get(key) != second.get(key) for key in _PAIR_KEYS):
        raise ValueError(f"These results come from different pairs. {rule}")
    if (first["before"], first["after"]) != (before.get("arm"), after.get("arm")) or not is_baseline(first["before"]):
        raise ValueError(f"The results' arms are not the pair's, with a defaults baseline as its before side. {rule}")
    return {key: value for key, value in first.items() if key != "side"}


def _paired(pairs: list[tuple[str | None, float, float]], *, samples: int, clustered: bool) -> dict:
    """The paired difference and its 95% interval, resampling questions or, with ``clustered``, conversations too.

    Each pair is (conversation, before, after). The conversation bootstrap is
    the one the public-capture comparisons use: a draw keeps all of each
    chosen conversation's questions, and with fewer than two conversations
    there is no interval. A percentile bootstrap over only LoCoMo's 10
    conversations covers less than it claims (for a variant with no effect it
    excluded zero about 9% of the time instead of 5%), and it came out narrower
    than the question-level interval for the #118 repeat. So with
    ``clustered``, ``interval_95``, the interval the default-change rule reads,
    spans both: a difference excludes zero only when the conversation-level and
    the question-level intervals both do.
    """
    from benchmarks.compare_evidence import paired_statistics

    stats = paired_statistics([(before, after) for _, before, after in pairs], samples=samples, seed=BOOTSTRAP_SEED)
    if not clustered or not pairs:
        return stats
    # It draws with seed 42, BOOTSTRAP_SEED.
    from benchmarks.diagnostics.compare_public_captures import cluster_statistics

    conversations = cluster_statistics([(before, after) for _, before, after in pairs],
                                       [cluster for cluster, _, _ in pairs], samples=samples)["interval_95"]
    questions = stats["interval_95"]
    spanned = None if conversations is None else [min(conversations[0], questions[0]),
                                                  max(conversations[1], questions[1])]
    return {**stats, "groups": len({cluster for cluster, _, _ in pairs}), "interval_95": spanned,
            "interval_95_conversations": conversations, "interval_95_questions": questions}


def compare(before: dict, after: dict, *, samples: int = 2000) -> dict:
    """Pair two complete answer results on the same questions, answered by the same reader and judge.

    ``before`` is the baseline. On the Ollama track the two results must be the
    sides of one ``run_pair`` pair (#129). The one exception is a repeat: two
    answer runs of the defaults, each answered on its own, that sent the reader
    and judge the same inputs (``_same_inputs``), which keeps the #118
    measurement checkable. Every Ollama server version the two results recorded
    must be the same; a pair must record one at every start, and a repeat that
    did not gets a warning. The difference's 95% interval resamples LoCoMo's
    conversations, keeping each conversation's questions together, and
    LongMemEval-S's questions (``CONVERSATION_INTERVALS``). When both results
    are baselines of the defaults with the same inputs, the pairing is a
    repeat: its difference is run-to-run variation alone, and ``repeat``
    reports how many verdicts changed and whether the interval excludes zero.
    Both results must have been answered under the same failure policy, and
    under the amended one (#132) neither may have more than
    ``UNSCORED_PERCENT`` of its questions truncated or without an accepted
    verdict: such a pair is invalid.
    """
    for side, result in (("before", before), ("after", after)):
        if not result.get("complete") or "sample" in result or result.get("kind", "").endswith("-sample"):
            raise ValueError(f"The {side} result is not a complete answer run")
    for field in ("kind", "benchmark", "model", "registration_sha256"):
        if before.get(field) != after.get(field):
            raise ValueError(f"The results differ in {field}")
    before_policy, after_policy = (_policy_of(result.get("answer_model") or {}) for result in (before, after))
    if before_policy != after_policy:
        raise ValueError(f"The results were answered under different failure policies ({before_policy or 'registered'}"
                         f" and {after_policy or 'registered'}). Pair only runs answered under one policy (#132).")
    if before_policy is None and before["kind"] == "ollama-answer-result":
        # Only a checkout from before #132 answers under the registered rules once the amendment exists.
        amended_at = datetime.fromisoformat(failure_amendment()["registered_at"])
        for side, result in (("before", before), ("after", after)):
            if result.get("started_at") and datetime.fromisoformat(result["started_at"]) >= amended_at:
                raise ValueError(f"The {side} result was answered under the registered failure policy after the "
                                 "Ollama track's amendment was registered, so it came from a checkout without #132. "
                                 "Answer it again under the amendment.")
    outcomes = None
    if before_policy is not None:
        amendments = {(result.get("failure_policy") or {}).get("sha256") for result in (before, after)}
        if len(amendments) != 1 or None in amendments:
            raise ValueError("The results do not record the same failure policy amendment")
        outcomes = {}
        for side, result in (("before", before), ("after", after)):
            outcomes[side] = counts = _outcome_counts(result["rows"], len(result["rows"]))
            if not counts["within_limit"]:
                raise ValueError(f"{counts['unscored']} of the {side} result's {len(result['rows'])} questions were "
                                 f"truncated or left without a verdict, more than the {UNSCORED_PERCENT}% the failure "
                                 "policy allows, so the pair is invalid (#132)")
    if not ollama_answers.same_model(before["answer_model"], after["answer_model"]):
        raise ValueError("The results were answered by different readers and judges, settings or model identities. "
                         "Pair a run only with a defaults run answered by the same model identity and settings.")
    old, new = ({row["question_id"]: row for row in result["rows"]} for result in (before, after))
    if list(old) != list(new):
        raise ValueError("The results answer different questions")
    if before["rows"] == after["rows"]:
        raise ValueError("The before and after results are the same answer run")
    pair = _pair_of(before, after)
    both_baselines = all(is_baseline(result["arm"]) for result in (before, after))
    unpaired = ("On the Ollama track a result is compared only with the run answered alongside it in one pair "
                "(run-pair), or, for two answer runs of the defaults with the same inputs, as a repeat (#129)")
    if pair is None and before["kind"] == "ollama-answer-result" and not both_baselines:
        raise ValueError(unpaired)
    versions = set(_recorded_versions(before)) | set(_recorded_versions(after))
    known = sorted(version for version in versions if version is not None)
    if len(known) > 1:
        raise ValueError(f"The Ollama server version changed during or between these runs ({', '.join(known)}). "
                         "Pair only runs answered under one server version.")
    if pair is not None and None in versions:
        raise ValueError("A pair's results must record the Ollama server version at every start")
    clustered = before["benchmark"] in CONVERSATION_INTERVALS
    if clustered and any(not old[key].get("cluster") or old[key]["cluster"] != new[key].get("cluster")
                         for key in old):
        raise ValueError("Every LoCoMo row must name its conversation, the same one on both sides")

    def paired(keys: list[str]) -> dict:
        return _paired([(old[key].get("cluster"), float(old[key]["correct"]), float(new[key]["correct"]))
                        for key in keys], samples=samples, clustered=clustered)

    categories = sorted({row["question_type"] for row in old.values()})
    prepared = {side: result.get("prepared") or {} for side, result in (("before", before), ("after", after))}
    same_contexts = pair is not None and before.get("prepared_sha256") == after.get("prepared_sha256")
    repeat = both_baselines and (same_contexts or _same_inputs(before, after))
    if pair is None and before["kind"] == "ollama-answer-result" and not repeat:
        raise ValueError(unpaired)
    warnings = [f"The {side} contexts were prepared from a tree with uncommitted changes"
                for side, value in prepared.items() if value.get("dirty")]
    if prepared["before"].get("commit") != prepared["after"].get("commit") and not repeat:
        warnings.append("The contexts were prepared from different commits, so code changes are part of the "
                        "difference")
    if known and None in versions:
        warnings.append("The Ollama server version was not recorded at every start of these runs")
    if both_baselines and not repeat:
        warnings.append("Both results are baselines of the defaults, but they are not shown to have sent the same "
                        "context text, budget, answering code and server version, so this is not a repeat")
    accuracy = paired(list(old))
    # None with fewer than two LoCoMo conversations, where no conversation-level interval exists.
    interval = accuracy["interval_95"]
    how = ("answered together as one interleaved pair (#129)" if pair is not None
           else "each answered on its own, one after the other (#118)")
    return {
        "kind": "answer-comparison", "benchmark": before["benchmark"], "model": before["model"],
        "answer_model": before["answer_model"], "bootstrap_samples": samples, "bootstrap_seed": BOOTSTRAP_SEED,
        "interval_unit": "conversations and questions" if clustered else "questions",
        "interval_note": ("LoCoMo intervals resample its conversations, keeping each conversation's questions "
                          "together, and its interval_95 spans that interval and the question-level one, because "
                          "a bootstrap over 10 conversations covers less than 95%. LongMemEval-S intervals resample "
                          "questions, since each question has its own history."),
        "arms": {"before": before["arm"], "after": after["arm"]}, "pair": pair,
        "server_versions": _sorted_versions(versions),
        # Each side's retries and unscored questions under the amended policy; None under the registered one.
        "failure_policy": None if outcomes is None else {
            "id": before_policy, "sha256": before["failure_policy"]["sha256"], **outcomes},
        "prepared": prepared, "warnings": warnings,
        "accuracy": accuracy,
        "categories": {category: paired([key for key, row in old.items() if row["question_type"] == category])
                       for category in categories},
        "gained": [key for key in old if not old[key]["correct"] and new[key]["correct"]],
        "lost": [key for key in old if old[key]["correct"] and not new[key]["correct"]],
        "repeat": None if not repeat else {
            "changed_verdicts": accuracy["wins"] + accuracy["losses"],
            "interval_excludes_zero": None if interval is None else interval[0] > 0 or interval[1] < 0,
            "interleaved": pair is not None,
            "note": (f"Two answer runs of the defaults with the same inputs and the same model identity and settings, "
                     f"{how}, so the difference is run-to-run variation alone. If an interleaved A/A interval "
                     "excludes zero, a variant's gain must also be larger than the largest A/A difference measured "
                     "so far (the default-change rule in CLAUDE.md)."),
        },
    }


@dataclass(frozen=True)
class _Side:
    """One answer run: the arm folder whose prepared contexts it reads, their entries, and where answers go."""

    prepared: Path
    answers: Path
    entries: dict[str, dict]


async def _answer(label: str, benchmark: str, sides: list[_Side], questions: list[dict], concurrency: int,
                  client, ask, ledger: Ledger | None, *, amended: bool = False) -> None:
    """Ask every pending question of every side through ``ask``, the provider's call with its ledger or model bound.

    The sides share one queue, interleaved question by question in the order
    given, so a pair's two answer runs move through the questions together.
    With ``amended`` (the Ollama track), each question is asked and judged
    under ``OLLAMA_RETRY_POLICY``; otherwise under the registered policy.
    """
    jobs = iter([(side, question) for question in questions for side in sides
                 if _status(side.answers / "execution" / question["question_id"]) == "pending"])
    errors: list[dict] = []
    answered = 0

    async def worker(semaphore):
        nonlocal answered
        while not errors:
            job = next(jobs, None)
            if job is None:
                return
            side, question = job
            qid = question["question_id"]
            entry = side.entries[qid]
            attempt = None
            try:
                attempt = _next_attempt(side.answers / "execution" / qid)
                context = _context(side.prepared, entry)
                if amended:
                    scored = await _ask_amended(ask, client, semaphore, benchmark, question, context, attempt)
                else:
                    reader = await ask(client, semaphore, prompt=study.reader_prompt(benchmark, question, context),
                                       limit=READER_LIMIT, path=attempt / READER_CALLS[0])
                    judged = await ask(client, semaphore,
                                       prompt=study.judge_prompt(benchmark, question, reader["text"]),
                                       limit=JUDGE_LIMIT, path=attempt / JUDGE_CALLS[0])
                    scored = _registered_scored(attempt, judged)
                _write_json(attempt / "result.json", _row(question, entry, scored))
                answered += 1
                if answered % 25 == 0:
                    cost = "" if ledger is None else "; arm cost ${:.3f}".format(
                        sum(charge["charge"] for charge in ledger.update()["entries"].values()) / 1e9)
                    print(f"{label}: {answered} answered this run{cost}", file=sys.stderr, flush=True)
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
           entries: dict[str, dict], ledger: Ledger | None, *, model: ollama_answers.AnswerModel | None = None,
           answers: Path | None = None) -> dict:
    """Authenticate every answered question and summarize the arm with its cost, tokens and budget.

    Without ``model`` the calls are GPT-5.4 calls checked against ``ledger``;
    with it they are that Ollama model's calls, which cost nothing. The answers
    are in the arm's folder, or in ``answers`` for one side of a pair. Ollama
    answers are checked under the failure policy they are bound to, and under
    the amended one the result counts its retries and unscored questions.
    """
    from benchmarks.integrations.analyze_gpt54_comparison import verify_call

    verify = verify_call if model is None else partial(ollama_answers.verify_call, model=model)
    tally = _tally_gpt54 if model is None else _tally_ollama
    policy = None if model is None else _bound_policy(answers or folder)
    rows, failures = [], []
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "successful_calls": 0,
             "http_attempts": 0, "http_status_counts": Counter()}
    if model is None:
        usage.update(reasoning_tokens=0, observed_nanodollars=0)
    execution = (answers or folder) / "execution"
    for question in questions:
        qid = question["question_id"]
        records = [(attempt, _attempt_record(attempt)) for attempt in _attempts(execution / qid)]
        answer = next((attempt for attempt, record in records if record["kind"] == "result"), None)
        failures += [{key: value for key, value in record.items() if key != "kind"} | {"replaced": answer is not None}
                     for _, record in records if record["kind"] == "failure"]
        if answer is not None:
            rows.append(_verified_row(verify, tally, benchmark, question, folder, prepared, entries[qid], answer,
                                      usage, amended=policy is not None))
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
        **({} if policy is None else {"failure_policy": {**failure_amendment(),
                                                         **_outcome_counts(rows, len(questions))}}),
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


def _row(question: dict, entry: dict, scored: dict) -> dict:
    """A question's result row: the question, the context it was asked on, and how its answer was scored."""
    # "correct" stays where rows have always had it; the scoring's own fields follow.
    return {"question_id": question["question_id"], "question_type": question["question_type"],
            "cluster": question.get("conversation_id") or sha(question["haystack_sessions"]),
            "correct": scored["correct"], "context_sha256": entry["sha256"], "context_tokens": entry["context_tokens"],
            "retrieval_seconds": entry["retrieval_seconds"], **scored}


def _verified_row(verify, tally, benchmark: str, question: dict, folder: Path, prepared: dict, entry: dict,
                  attempt: Path, usage: dict, *, amended: bool = False) -> dict:
    """The attempt's result row, after checking it against its context, calls and verdict.

    With ``amended``, every call the amended failure policy made is checked,
    retries and truncated answers included, and so is the order they came in.
    """
    qid = question["question_id"]
    row = json.loads((attempt / "result.json").read_text())
    context = _context(folder, entry)
    tokens = count_tokens(context, prepared["tokenizer"])
    budget = prepared["context_budget"]
    prompt = study.reader_prompt(benchmark, question, context)
    if amended:
        readers = [verify(path, prompt, READER_LIMIT, truncated_ok=True)
                   for path in _recorded_calls(attempt, READER_CALLS)]
        if not readers:
            raise ValueError(f"The recorded result for {qid} has no reader call")
        judges = [verify(path, study.judge_prompt(benchmark, question, readers[-1]["text"]), JUDGE_LIMIT,
                         truncated_ok=True, empty_ok=True) for path in _recorded_calls(attempt, JUDGE_CALLS)]
        scored, calls = _scored(attempt, readers, judges), readers + judges
    else:
        reader = verify(attempt / READER_CALLS[0], prompt, READER_LIMIT)
        judge = verify(attempt / JUDGE_CALLS[0], study.judge_prompt(benchmark, question, reader["text"]), JUDGE_LIMIT)
        scored, calls = _registered_scored(attempt, judge), [reader, judge]
    if row != _row(question, entry, scored) or tokens != entry["context_tokens"] or (
            budget is not None and tokens > budget):
        raise ValueError(f"The recorded result for {qid} does not match its context, calls or verdict")
    for value in calls:
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


def _checked_out_baseline(parser: argparse.ArgumentParser, benchmark: str, commit: str | None, data: Path) -> str:
    """The defaults' baseline arm for the checked-out commit, which prepare fills and run answers."""
    try:
        arm = baseline_arm(benchmark, commit, data=data)
    except ValueError as exc:
        parser.error(str(exc))
    if arm != "prme":
        print(f"prme {benchmark} has a complete answer run from another commit, so the defaults at this commit are "
              f"the baseline {arm}", file=sys.stderr, flush=True)
    return arm


def _check_args(parser: argparse.ArgumentParser, args: argparse.Namespace,
                model: ollama_answers.AnswerModel | None) -> tuple[str, str, dict]:
    """The arm's name, its benchmark and its overrides, after refusing any combination that does not apply."""
    command = args.command
    if command == "calibrate" and model is None:
        parser.error("calibrate is for the ollama provider; the GPT-5.4 track reuses the saved calibration")
    if command == "run-pair" and model is None:
        parser.error("run-pair is for the ollama provider; the GPT-5.4 track has no defaults arm to pair with")
    if command in {"calibrate", "compare"}:
        given = [flag for flag, value in (("arm", args.arm), ("--benchmark", args.benchmark),
                                          ("--set", args.overrides), ("--variant", args.variant),
                                          ("--max-usd", args.max_usd), ("--sample", args.sample),
                                          ("--archive", args.archive), ("--baseline", args.baseline)) if value]
        if given:
            parser.error(f"{command} takes none of: {', '.join(given)}")
    if (command == "compare") != (args.before is not None and args.after is not None) or (
            command != "compare" and (args.before or args.after)):
        parser.error("compare takes --before and --after, the result files to pair; nothing else does")
    if (command == "run-pair") != (args.baseline is not None):
        parser.error("run-pair takes --baseline, the prepared defaults baseline to answer alongside the arm; nothing "
                     "else does")
    if command in {"calibrate", "compare"}:
        return "", "", {}
    if args.arm is None:
        parser.error(f"{command} needs an arm")
    benchmark = args.benchmark or ("locomo" if args.arm == "full-context" else None)
    if benchmark is None:
        parser.error("plain and prme arms need --benchmark")
    if command != "prepare" and args.overrides:
        parser.error("--set applies to prepare only; run-pair and estimate find a variant by --variant")
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
        if command in {"run", "run-pair"} and args.archive is not None:
            parser.error("--archive applies to prepare only on the ollama provider")
        if command == "run" and args.variant is not None:
            parser.error("a prme variant is answered only alongside a fresh run of the defaults: use run-pair with "
                         "--baseline")
    if args.sample is not None and (command not in {"run", "run-pair"} or args.sample < 1):
        parser.error("--sample applies to run and run-pair only and needs a positive count")
    try:
        arm = arm_name(args.arm, args.variant)
        overrides = gate.parse_overrides(args.overrides)
        _check_arm(arm, benchmark)
        if command == "prepare":
            _check_overrides(arm, overrides)
        if command == "run-pair":
            _check_pair_baseline(args.baseline)
            _check_arm(args.baseline, benchmark)
    except ValueError as exc:
        parser.error(str(exc))
    return arm, benchmark, overrides


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.integrations.gpt54_baselines",
                                     description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["prepare", "estimate", "calibrate", "run", "run-pair", "compare"])
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
                             "and answered with run-pair as the arm prme-NAME")
    parser.add_argument("--baseline", metavar="ARM",
                        help="run-pair only: the prepared baseline of the defaults (prme or prme@<commit>) answered "
                             "again alongside the arm. With the prme arm and no --variant, the baseline is paired "
                             "with itself: the A/A check.")
    parser.add_argument("--archive", type=Path,
                        help="Saved 2026-09-23 run archive (default: the main checkout's data/gpt54-comparison-v1)")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="prepare only: packing.token_budget or packing.overhead_tokens for a plain arm, or "
                             "the settings a prme variant changes (any the evidence gate accepts)")
    parser.add_argument("--max-usd", type=float,
                        help="run on the openai provider only: the owner-approved spending cap for this arm")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="run and run-pair on the ollama provider only: a smoke check of the first N questions "
                             "of each category")
    parser.add_argument("--before", type=Path, help="compare only: the defaults side's result file")
    parser.add_argument("--after", type=Path,
                        help="compare only: the other side's result file from the same pair, or a later "
                             "baseline's for a repeat")
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
        provenance = _provenance()
        if provenance["dirty"]:
            parser.error("prepare records the code that builds the contexts; commit your changes first")
        if arm == "prme":
            if not _on_main():
                parser.error("the prme arm prepares the shipped defaults; prepare it from a commit on main, or name "
                             "a variant")
            arm = _checked_out_baseline(parser, benchmark, provenance.get("commit"), data)
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
    elif args.command == "run" and model is None:
        _confirm_spend(parser, arm, benchmark, args.max_usd)
        result = asyncio.run(run(arm, benchmark, max_usd=args.max_usd, archive=args.archive))
        print(json.dumps(_summary(result)))
    elif args.command == "run-pair":
        _announce(model)
        # The prme arm without a variant is the defaults, so the baseline is answered against itself.
        after = args.baseline if arm == "prme" else arm
        outcome = asyncio.run(run_pair(args.baseline, after, benchmark, model=model, sample=args.sample))
        print(json.dumps({"pair": outcome["pair"], **{side: _summary(outcome[side]) for side in PAIR_SIDES}}))
    else:
        _announce(model)
        if arm == "prme":
            arm = _checked_out_baseline(parser, benchmark, _provenance().get("commit"), data)
        result = asyncio.run(run(arm, benchmark, model=model, sample=args.sample))
        print(json.dumps(_summary(result)))


def _announce(model: ollama_answers.AnswerModel) -> None:
    print(f"Reader and judge: {model.model} through {model.endpoint}. A :cloud model sends the prompts to "
          "Ollama's hosted service and uses the account's usage limits.", file=sys.stderr, flush=True)


def _summary(result: dict) -> dict:
    """A result without its rows and failures, for the terminal."""
    return {key: value for key, value in result.items() if key not in {"rows", "failures"}}


if __name__ == "__main__":
    main()

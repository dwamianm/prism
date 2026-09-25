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
  That commit must descend from the commit of every complete baseline on
  either benchmark, so the current baseline, the one whose own answer run
  completed last, holds the newest defaults recorded for its benchmark (#127).
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
answer run of the current baseline of the defaults, in one session and
interleaved question by question, so drift and the time of day reach both sides
equally (#129). ``compare`` pairs a variant only with the defaults run answered
alongside it, and only while that run's baseline is still the current one
(#127). ``run-pair`` with the ``prme`` arm and no variant answers the
baseline against itself: the A/A check of the paired test. A plain or
full-context arm can be paired with the defaults the same way. The
default-change rule reads only the 4K budget, so ``run-pair`` and ``compare``
refuse the defaults or a variant prepared at any other budget or tokenizer, and
``compare`` reports how many questions the two sides asked on different context
text, from the hash of each row's context text (#125).

A variant's baseline may have been prepared at another commit, so the text
a pair differs on could come from the variant's settings or from any code
that changed between the two preparations. ``prepare`` therefore replays the
defaults at the variant's commit too, without captures, and records the hash
of their text on every question (#139). ``compare`` counts the questions on
which those defaults read other text than the baseline, and refuses a
variant's pair with any, which ``run-pair`` never answers: after main changes
the defaults, a new baseline is recorded before any further variant pair
counts. A variant pair without that count is refused too, unless it started
before #139, as the ``prme-reader-rrf`` pairs did.

A variant is identified by what it changes, not by its arm name (#130): the
settings whose values differ from the defaults (``variant_settings``) and its
context text on every question (``contexts_sha256``). ``prepare`` logs every
variant preparation with that identity and refuses a variant that changes
nothing, and each start of a variant's pair records it. A variant's pairs
count together under any arm name, but only alongside one baseline and on one
context text (#143): its first pair is the first of those to complete, and
its confirmation the next to complete that started after the first completed
and repeats the first pair's settings. A new baseline starts the count again,
and a code change that alters the variant's context text makes a new variant,
with its own first pair and confirmation. Other settings on the same context
text stay in the count, so they cannot buy another first pair. ``compare``
reports which of the two a pair is, lists every arm and pair with the
variant's settings or context text alongside any baseline, and refuses any
other pair, which the default-change rule never reads; ``run-pair`` answers
none. A pair's ``earlier_pairs`` lists the arm's pairs alongside every
baseline.

The Ollama track answers under an amendment to the registered failure policy
(#132, recorded in ``FAILURE_AMENDMENT``), so one looping reader answer or
garbled verdict no longer stops a run or a pair: a truncated reader answer is
asked once more and then scored incorrect as ``truncated``, and a verdict that
does not read yes or no after the stray characters around it are removed is
judged once more and then scored incorrect as ``verdict_unresolved``. Every
result counts its retries and those outcomes, and ``compare`` refuses a result
with more than 1% of its questions scored that way. The GPT-5.4 track keeps
the registered policy unchanged.

Each A/A pair ``run-pair`` publishes is added to the track's tracked A/A
record (``record_aa_check``, #137): the conditions it was answered under
(model identity, answer settings, failure policy, Ollama server version and
context budget), its interval, and whether it is the first A/A pair under
those conditions that ``compare`` accepted, which is the A/A check the
default-change rule reads for them. ``compare`` refuses a variant's pair
unless an A/A check under the same conditions is recorded on both
benchmarks, so an Ollama update needs a new A/A pair on both before any
further variant pair counts, and ``run-pair`` answers no variant pair it
would refuse. It names the check it relied on and every other A/A pair under
those conditions. The record must list exactly the A/A pairs the track's run
logs show complete, each line matching its published results.
``record-aa-check`` adds an A/A pair published before the record existed, or
one ``run-pair`` could not add.

Whether a variant passed is recorded, not worked out by hand (#144). When
the ``compare`` command accepts a variant's first pair or confirmation, or
an A/A pair, it appends a ``compared`` event to the pair's run log and a
line to the track's tracked verdict record (``record_pair_verdict``): the
role, the baseline, the variant's identity, the difference, its 95% interval
and whether it excludes zero, the A/A check it relied on, and the published
results with their digests. ``verdict`` reads those lines for one variant on
both benchmarks, checks them against the published results and, on the
machine that answered the pairs, against the run logs, and prints ``pass``,
``fail`` or ``incomplete`` with the numbers behind it, applying the
default-change rule's A/A margin where an A/A pair under a pair's
conditions excluded zero. A failed first pair fails the variant, and the
pull request that flips a default cites the output.
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
from functools import cache, partial
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
# A full commit name (SHA-1 or SHA-256), the only kind the harness passes to git.
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
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
# Why run_pair gives up a pair stopped by a final failure, which the default-change rule counts as neither a pass
# nor a fail.
_FINAL_FAILURE = "a question failed finally"
# What identifies a named variant's pairs across arm names (#130): the settings it changes (variant_settings) and
# the hash of its context text on every question (_contexts_sha256). Alongside one baseline, a first pair and its
# confirmation must record the same of both (#143).
_VARIANT_KEYS = ("variant_settings", "contexts_sha256")
# Run log events that record the live Ollama server version.
_VERSION_EVENTS = frozenset({"started", "finished", "model-changed"})
# compare's paired bootstrap, as the evidence gate's comparisons draw it.
BOOTSTRAP_SEED = 42
BOOTSTRAP_SAMPLES = 2000
# LoCoMo's questions come from 10 conversations, so its intervals resample conversations. Each LongMemEval-S
# question has its own history, so its intervals resample questions.
CONVERSATION_INTERVALS = frozenset({"locomo"})
# The 4K budget the default-change rule in CLAUDE.md reads, as the registered run packed it: 4,096 tokens less the
# 100 reserved, counted by its tokenizer. compare and run-pair refuse PRME's arms prepared any other way (#125).
RULE_BUDGET = 3996
RULE_TOKENIZER = "cl100k_base"
# Reference points rather than PRME settings, which are paired with the defaults at any budget.
REFERENCE_ARMS = frozenset(ARMS) - {"prme"}
# What an A/A check measured, which a variant's pair must share for the check to cover it (#137).
AA_CONDITIONS = ("model identity", "answer settings", "failure policy", "Ollama server version", "context budget")
# The roles compare records a pair in for the verdict step (#144): a variant's first pair and its confirmation (#130),
# and an A/A pair (#137).
VERDICT_ROLES = ("first", "confirmation")
RECORDED_ROLES = (*VERDICT_ROLES, "aa")
_ROLE_LABELS = {"first": "first pair", "confirmation": "confirmation"}
# What a tracked record keeps of a pair's mark, and of a LoCoMo interval's two halves.
_MARK_KEYS = ("id", "number", "sha256")
_SPLIT_INTERVALS = ("interval_95_conversations", "interval_95_questions")
# The #118 sequential repeat of the defaults: the day its results were published, the first baseline and its repeat,
# each answered on its own. The default-change rule's A/A margin counts its difference with the A/A pairs' (#144).
SEQUENTIAL_REPEAT = ("2026-09-24", "prme", "prme@46647825")
# What a variant's replay and the defaults' replay at its commit must share, so the two differ by settings alone (#139).
_REPLAY_INPUTS = ("commit", "dirty", "worktree_sha256", "python", "dependencies", "archive", "datasets")
# Since when every variant pair must record the defaults' text at the variant's commit (#139). Every variant pair on
# record started before it: the last, pair 2 of prme-reader-rrf, at 14:35 UTC that day. That variant was prepared at
# 7b36b2b7, and between the prme@46647825 baseline's commit and 7b36b2b7 only this module's answering and pairing code
# and ollama_answers.py changed, neither of which builds contexts, so the defaults read the same text at both commits.
# compare accepts those pairs unsplit, with a warning, and refuses any later variant pair without the split.
DEFAULTS_REPLAYED_SINCE = datetime.fromisoformat("2026-09-24T18:00:00+00:00")


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


def is_variant(arm: str) -> bool:
    """A named variant of the defaults, ``prme-<name>``: the defaults with the settings it was prepared with."""
    return is_prme(arm) and not is_baseline(arm)


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
    if is_variant(arm) and not overrides:
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
    settings = variant_settings(overrides) if is_variant(arm) and overrides else None
    if settings == {}:
        raise ValueError(f"The settings of the {arm} variant are the defaults' at this commit, so its pairs would "
                         "answer the defaults again; set a value that differs from the default (#130)")
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
    if is_variant(arm):
        entries, extra = _replay_defaults(folder, benchmark, archive, entries, extra, progress)
    commit = extra["provenance"].get("commit")
    if later:
        _check_baseline_name(arm, benchmark, commit, data)
    prepared = {
        "kind": "gpt54-baseline-contexts", "complete": True, "arm": arm, "benchmark": benchmark,
        "questions": len(entries), "registration_sha256": digest(study.REG),
        "tokenizer": gate.gate_config(Path("{pack}"), overrides).packing.tokenizer,
        **({} if settings is None else {"variant_settings": settings}), **extra,
        "modules": _module_identity(), "contexts": entries,
    }
    write_new(folder / "prepared.json", prepared)
    if settings is not None:
        _log_prepared_variant(data, arm, benchmark, prepared, digest(folder / "prepared.json"))
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


def _log_prepared_variant(data: Path, arm: str, benchmark: str, prepared: dict, prepared_sha256: str) -> None:
    """Record a variant's preparation and its identity in its run log, and name the other arms it shares them with.

    The run log lives outside the arm's folder, so every arm prepared with
    these settings or this context text stays on record for ``compare`` to
    list, even once its folder is moved aside (#130). Alongside one baseline,
    an arm with the same context text counts with this one, and only one with
    the first pair's settings can confirm it; an arm with the same settings on
    other context text is another variant, whose pairs ``compare`` lists
    (#143).
    """
    identity = _prepared_identity(prepared)
    this = {**identity, "settings_recorded": prepared.get("variant_settings") is not None}
    groups: dict[tuple[str, ...], list[str]] = {}
    for name, found in _variant_preparations(data, benchmark).items():
        differences = [_identity_differences(entry, this) for entry in found if _shares_identity(entry, identity)]
        if name != arm and differences:
            groups.setdefault(tuple(min(differences, key=len)), []).append(name)
    commit = (prepared.get("provenance") or {}).get("commit")
    _log_run(data, arm, benchmark, {"event": "prepared", **identity, "prepared_commit": commit,
                                    "prepared_sha256": prepared_sha256})
    notices = {
        (): (f"with the same settings and context text, so {arm} is the same variant: alongside one baseline, its "
             "pairs and theirs count together, and compare reads only the first pair and the confirmation among them "
             "(#130, #143)."),
        ("settings",): (f"on the same context text under other settings, so {arm}'s pairs count with theirs: "
                        "alongside one baseline, whichever completes first is the first pair, and only a pair with "
                        "its settings can confirm it (#143)."),
        ("context text",): (f"with the same settings on other context text, so their pairs count apart from {arm}'s: "
                            "other context text makes another variant, with its own first pair and confirmation, and "
                            f"compare lists their pairs beside {arm}'s (#143)."),
    }
    for differs, notice in notices.items():
        names = groups.get(differs, [])
        if names:
            print(f"{', '.join(names)} {benchmark} {'was' if len(names) == 1 else 'were'} prepared {notice}",
                  file=sys.stderr, flush=True)


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
    Nor is there one, first or later, at a commit that does not descend from
    the commit of every complete baseline on either benchmark
    (``_check_descends``), so the current baseline holds the newest defaults
    recorded (#127).
    """
    first = _complete_run_commit(data, "prme", benchmark)
    if first == commit:
        return "prme"
    if first is None:
        _check_descends("prme", benchmark, commit, data)
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
    _check_descends(arm, benchmark, commit, data)
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


def _complete_baselines(data: Path, benchmark: str) -> list[dict]:
    """Every baseline of the defaults with a complete answer run of its own, in the order those runs finished.

    Each entry is the arm, the commit that prepared it (None when neither its
    run log nor its manifest records one) and when its complete run finished.
    A baseline's own run is complete once at most, since neither ``prepare``
    nor ``run`` starts over after it. The last entry is the current baseline
    (``current_baseline``).
    """
    found = []
    histories = {"prme": _run_events(_run_log_path(data, "prme", benchmark)), **_later_baselines(data, benchmark)}
    for arm, events in histories.items():
        finished = next((event for event in events if _complete_run(event)), None)
        if finished is None:
            continue
        when = _recorded_time(finished.get("at"), f"The {arm} {benchmark} run log does not record when its complete "
                                                  "run finished, with a time zone")
        found.append((when, arm, {"arm": arm, "commit": _recorded_commit(data, arm, benchmark, finished),
                                  "finished_at": finished["at"]}))
    return [baseline for *_, baseline in sorted(found, key=lambda item: item[:2])]


def _recorded_time(value, missing: str) -> datetime:
    """A time a run log recorded, which must name its time zone; ``missing`` is the error when it does not."""
    when = None
    with suppress(TypeError, ValueError):
        when = datetime.fromisoformat(value)
    if when is None or when.tzinfo is None:
        raise ValueError(missing)
    return when


def current_baseline(data: Path, benchmark: str) -> str | None:
    """The current baseline of the defaults: the one whose own answer run completed last, or None before any.

    No baseline is recorded at a commit older than a complete one on either
    benchmark (``_check_descends``), so it holds the newest defaults recorded
    for the benchmark. Pairs are answered alongside it only, and ``compare``
    accepts no other as a pair's before side, so the choice of baseline is
    never left open (#127).
    """
    found = _complete_baselines(data, benchmark)
    return found[-1]["arm"] if found else None


def _descends(commit: str, ancestor: str) -> bool:
    """Whether ``commit`` is ``ancestor`` or descends from it, in this checkout's git history."""
    import subprocess

    for value in (commit, ancestor):
        # Only a full commit name reaches git, so a recorded value can never read as an option, a branch or a tag.
        if not isinstance(value, str) or not _COMMIT.fullmatch(value):
            raise ValueError(f"{value!r} is not a full commit name")
    try:
        found = subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, commit], cwd=study.ROOT,
                               capture_output=True, text=True)
    except OSError as exc:
        raise ValueError(f"git is needed to check which commit a baseline descends from ({exc})") from None
    if found.returncode not in (0, 1):
        raise ValueError(f"git cannot tell whether {commit} descends from {ancestor} ({found.stderr.strip()}). Run "
                         "git fetch first, with the full history in a shallow clone.")
    return found.returncode == 0


def _check_descends(arm: str, benchmark: str, commit: str | None, data: Path) -> None:
    """A baseline not yet recorded must be at a commit that descends from every complete baseline's (#127).

    Any ancestor of ``origin/main`` is on main, so without this an older
    commit, whose defaults may have changed since, could become the current
    baseline. The complete baselines of both benchmarks count, so neither
    benchmark's newest baseline holds older defaults than the other's. A
    complete baseline is left to ``prepare`` and ``run``, which refuse it
    themselves.
    """
    recorded = {name: _complete_baselines(data, name) for name in gate.GATE_BENCHMARKS}
    if any(baseline["arm"] == arm for baseline in recorded[benchmark]):
        return
    for name, found in recorded.items():
        for baseline in found:
            if commit is not None and baseline["commit"] == commit:
                continue
            if commit is None or baseline["commit"] is None:
                missing = (f"The {arm} {benchmark} baseline" if commit is None
                           else f"The {baseline['arm']} {name} baseline")
                raise ValueError(f"{missing} records no commit, so nothing shows that {arm} {benchmark} descends "
                                 f"from the complete {baseline['arm']} {name} baseline. Prepare the baseline from a "
                                 "git checkout of main (#127).")
            if not _descends(commit, baseline["commit"]):
                raise ValueError(f"The defaults at {commit} do not descend from {baseline['commit']}, which prepared "
                                 f"the complete {baseline['arm']} {name} baseline, so they cannot be the newest "
                                 "baseline. Record a new baseline only at a commit on main that descends from every "
                                 "complete one (#127).")


def _check_current_baseline(arm: str, benchmark: str, found: list[dict], commit: str | None = None) -> None:
    """A pair counts only with the current baseline as its before side (#127), and the same baseline's contexts.

    ``found`` is ``_complete_baselines`` for the benchmark. With ``commit``,
    the before side must also have read contexts prepared at the current
    baseline's recorded commit, so a result from another data root with the
    same arm name is not taken for it.
    """
    if not found:
        raise ValueError(f"No {benchmark} baseline of the defaults has a complete answer run on record, so nothing "
                         f"shows that {arm} is the current one. The run logs live in the main checkout that answered "
                         "the pair (#127).")
    current = found[-1]
    if arm != current["arm"]:
        raise ValueError(f"{arm} is not the current {benchmark} baseline of the defaults: {current['arm']} is, the "
                         "most recent to complete its own answer run. Pairs are answered and compared only alongside "
                         "the current baseline, and every earlier pair of an arm stays on record for the pull "
                         "request to list (#127, #130).")
    if commit is not None and current["commit"] is not None and commit != current["commit"]:
        raise ValueError(f"The before side read contexts prepared at {commit}, but the current {benchmark} baseline, "
                         f"{arm}, was prepared at {current['commit']} (#127)")


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


def _replay_defaults(folder: Path, benchmark: str, archive: Path | None, entries: list[dict], extra: dict,
                     progress) -> tuple[list[dict], dict]:
    """A variant's entries with the hash of the defaults' context text at the same commit, from a second replay.

    The variant's pairs read their before side from a baseline that may have
    been prepared at another commit, so the text they differ on can come from
    the variant's settings or from code that changed between the two
    preparations. This
    replays the defaults through the evidence gate on the same code and saved
    run, writing no captures, and keeps its report as ``defaults-gate.json``.
    Each entry gains ``defaults_text_sha256``, which ``compare`` and
    ``run_pair`` read against the baseline's text (#139).
    """
    print(f"Replaying the defaults at this commit as well, so compare can tell the variant's own {benchmark} context "
          "changes from other code's (#139)", file=sys.stderr, flush=True)
    report = asyncio.run(gate.run_gate(benchmark, archive=archive, progress=progress))
    changed = [name for name in _REPLAY_INPUTS if report["provenance"].get(name) != extra["provenance"].get(name)]
    if changed:
        raise ValueError(f"The {', '.join(changed)} changed between the variant's replay and the defaults' replay, so "
                         "the defaults' contexts do not show what the variant's settings changed. Remove "
                         f"{folder} and prepare the variant again from an unchanged checkout.")
    texts = {row["question_id"]: row["context_sha256"] for row in report["rows"]}
    if list(texts) != [entry["question_id"] for entry in entries]:
        raise ValueError(f"The defaults' replay does not cover the variant's questions in order. Remove {folder} and "
                         "prepare the variant again.")
    report_path = folder / "defaults-gate.json"
    gate._write_report(report_path, report, gate.gate_markdown(report))
    return ([{**entry, "defaults_text_sha256": texts[entry["question_id"]]} for entry in entries],
            {**extra, "defaults_gate_report_sha256": digest(report_path)})


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
    commit = _recorded_commit(data, arm, benchmark, finished[-1])
    if not commit:
        raise ValueError(f"{arm} {benchmark} has a complete answer run, but neither its run log nor "
                         f"{data / arm / benchmark / 'prepared.json'} records the commit that prepared it")
    return commit


def _recorded_commit(data: Path, arm: str, benchmark: str, finished: dict) -> str | None:
    """The commit a complete run's ``finished`` event records, or else the arm's manifest; None when neither does."""
    commit = finished.get("prepared_commit")
    manifest = data / arm / benchmark / "prepared.json"
    if commit is None and manifest.is_file():
        # Run logs written before the finished event recorded the commit: a complete arm keeps its manifest.
        commit = (json.loads(manifest.read_text()).get("provenance") or {}).get("commit")
    return commit or None


def _run_history(data: Path, arm: str, benchmark: str) -> dict:
    """What the arm's run log holds, so a result shows every earlier run and preparation."""
    path = _run_log_path(data, arm, benchmark)
    events = _run_events(path)
    # An arm answered only in pairs has no run log of its own until it is prepared again, unless it is a variant,
    # whose every preparation is logged (#130).
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
              model: ollama_answers.AnswerModel | None = None, data: Path | None = None,
              results: Path | None = None, archive: Path | None = None, api_key: str | None = None,
              sample: int | None = None) -> dict:
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
    elif is_variant(arm):
        raise ValueError(f"{arm} is a variant of the defaults, which is answered only alongside a fresh run of the "
                         "defaults: use run-pair with a prepared baseline (#129)")
    data = data or data_root(model)
    results = results or RESULTS
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
        if is_baseline(arm) and sample is None:
            # The CLI answers the baseline of the checked-out commit (baseline_arm); a direct call is checked against
            # the commit that prepared it, so an older baseline never completes after a newer one (#127).
            _check_descends(arm, benchmark, (prepared.get("provenance") or {}).get("commit"), data)
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
                   data: Path | None = None, results: Path | None = None, sample: int | None = None) -> dict:
    """Answer ``after`` together with a fresh run of the defaults baseline ``before``, in one session.

    Both arms must be prepared, and ``before`` must have a complete answer run
    of its own (``run``), which records the baseline, and be the current
    baseline (``current_baseline``), the only one ``compare`` pairs a variant
    with (#127). ``after`` is a variant of
    the defaults, another arm, or ``before`` itself for the A/A check. The two
    answer runs share one queue, interleaved question by question
    (``PAIR_ORDER``), so drift and the time of day reach both sides equally,
    and each side is reported as its own result, marked with the pair.
    ``compare`` pairs a variant only with the defaults run answered in the same
    pair. Both sides answer under ``OLLAMA_RETRY_POLICY`` (#132). A call resumes
    the latest pair of these arms while it can still finish
    (``_pair_blocker``), and otherwise starts the next one, which is
    how a confirmation redraws both sides; every pair stays on record.
    Each result lists every other pair of ``after`` on record, alongside this
    baseline or an earlier one (``earlier_pairs``). Each start of a variant's
    pair records the pair's id and the variant's identity, so ``compare`` can
    tell a first pair from its confirmation across arm names, and no pair
    alongside this baseline that could count as neither is answered
    (``_check_uncounted``, #130, #143). Each start records the pair's
    id, and an A/A pair's published results are added to the track's A/A
    record (``record_aa_check``, #137). A full pair starts only when the
    record could take its results or cover them (``_check_aa_ready``): an
    A/A pair needs the record to list every complete A/A pair on its
    benchmark, and a variant's pair a recorded A/A check under the current
    conditions on both benchmarks. A variant's pair is answered only when its
    preparation replayed the defaults at its commit and those defaults read
    the baseline's text on every question (``_check_defaults_replayed``, #139).
    ``sample`` works as in ``run``. Returns the pair's mark and both results.
    """
    if not isinstance(model, ollama_answers.AnswerModel):
        raise ValueError("Pairs are answered on the Ollama track only")
    _check_pair_baseline(before)
    data = data or data_root(model)
    results = results or RESULTS
    if _complete_run_commit(data, before, benchmark) is None:
        raise ValueError(f"The {before} {benchmark} baseline has no complete answer run of its own; answer it with "
                         "run first, which records the baseline")
    _check_current_baseline(before, benchmark, _complete_baselines(data, benchmark))
    registration, questions = registered_protocol(benchmark)
    arms = {"before": before, "after": after}
    # The A/A check reads one prepared arm on both sides, so it is loaded and checked once.
    prepared_arms = {arm: load_prepared(arm, benchmark, questions, data=data) for arm in dict.fromkeys(arms.values())}
    loaded = {side: prepared_arms[arm] for side, arm in arms.items()}
    for side, (_, prepared, _) in loaded.items():
        # compare would refuse the pair, so it is never answered (#125).
        _check_rule_budget(f"The {side} arm", arms[side], prepared["context_budget"], prepared["tokenizer"])
    if sample is not None:
        questions = sample_questions(questions, sample)
    prepared_sha256 = {side: digest(folder / "prepared.json") for side, (folder, _, _) in loaded.items()}
    # Each start of a variant's pair records the variant's identity, and a pair the default-change rule would never
    # read is never answered (#130, #143).
    identity = _prepared_identity(loaded["after"][1]) if is_variant(after) else None
    if identity is not None:
        _check_uncounted(data, before, after, benchmark, loaded["after"][1])
        # compare would refuse the pair, so it is never answered (#139).
        _check_defaults_replayed(loaded["before"][1], *loaded["after"][:2])
    root = _pair_root(data, before, after, benchmark)
    root.mkdir(parents=True, exist_ok=True)
    log = _pair_log_path(data, before, after, benchmark)
    started = study.utc()
    with _run_lock(root, f"the {before} and {after} {benchmark} pairs"), _registered_judge(registration, benchmark):
        # Checked before a pair is opened, so a failed check leaves no pair on record.
        settings, calibration = _ollama_answer_model(model, data)
        if sample is None:
            _check_aa_ready(data, results, model, benchmark, before, after, settings, loaded["after"][1])
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
        # The pair id binds the run log to the pair's results: a variant's pair for compare (#130), and an A/A pair
        # for the A/A record (#137).
        _append_event(log, {"event": "started", "pair": number, "sample": sample,
                            "answer_model_sha256": {side: sha(value) for side, value in bound.items()},
                            "server_version": _version_of(settings), "failure_policy": _policy_of(settings),
                            "id": record["id"], **({} if identity is None else identity)})
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
        earlier = _earlier_pairs(_pairs_on_record(data, after, benchmark), before, number)
        mark = {**{key: record[key] for key in _PAIR_KEYS if key != "sha256"}, "sha256": digest(folder / "pair.json"),
                "order": PAIR_ORDER, "pairs_started": len(earlier) + 1, "earlier_pairs": earlier}
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
        # compare refuses a pair whose server version changed, or whose baseline is no longer the current one
        # (#127), so such a pair is never published as complete.
        changed = f"the Ollama server version changed ({', '.join(versions)})" if len(versions) > 1 else None
        superseded = current_baseline(data, benchmark)
        if changed is None and superseded != before:
            changed = f"{superseded} became the current {benchmark} baseline"
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
            then = ("the next run starts a new pair" if superseded == before
                    else f"pairs are answered alongside {superseded} from now on")
            raise RuntimeError(f"{run_name}: {changed} while it was answered, so nothing is published. Its answers "
                               f"stay on record, and {then}.")
        if not complete:
            counts = "; ".join(f"{side} {result['completed']}/{result['total']} answered, "
                               f"{result['final_failures']} final failures" for side, result in outcome.items())
            raise RuntimeError(f"{run_name} is incomplete: {counts}. Run it again: it asks the questions that got no "
                               "answer, or starts the next pair if a final failure means this one can never finish. "
                               "No partial score is reported.")
        suffix = "" if sample is None else f"-sample-{sample}"
        published = {side: results / started[:10] / f"{model.track}-{before}-vs-{after}-{benchmark}-pair-{number}-"
                                                    f"{side}{suffix}-result.json" for side in PAIR_SIDES}
        for side, result in outcome.items():
            _publish(published[side], result)
        if before == after and sample is None:
            # An A/A pair: compare reads the A/A record for every variant's pair (#137).
            try:
                record_aa_check(published["before"], published["after"], data=data, results=results)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"{run_name} is complete and published, but it was not added to the A/A record: "
                                   f"{exc} Once that is resolved, add it with record-aa-check --before "
                                   f"{published['before']} --after {published['after']} (#137).") from None
    return {"pair": mark, **outcome}


def _check_uncounted(data: Path, before: str, arm: str, benchmark: str, prepared: dict) -> None:
    """Refuse a variant's pair alongside ``before`` whose count is already decided as neither (#130, #143).

    ``prepared`` is the variant's manifest. ``compare`` refuses a pair that
    is neither the first pair nor the confirmation, since the default-change
    rule never reads one, so none is answered that could only be neither,
    under this arm name or any other: a pair of a variant whose first pair
    and confirmation alongside this baseline are complete (#130), or a pair
    on the context text of a first pair that was answered under other
    settings, which it could not confirm (#143).
    """
    identity = {**_prepared_identity(prepared), "settings_recorded": prepared.get("variant_settings") is not None}
    pairs = _variant_pairs(data, benchmark, _variant_preparations(data, benchmark))
    counted = _counted(_counting(pairs, before, identity["contexts_sha256"]))
    if len(counted) > 1:
        raise ValueError(f"The {arm} {benchmark} variant already has its first pair ({_named(counted[0])}) and its "
                         f"confirmation ({_named(counted[1])}) alongside {before}, counting every arm that read its "
                         "context text. The default-change rule reads no later pair, so none is answered (#130, #143).")
    if counted and not _same_test(identity, counted[0]):
        first = counted[0]
        raise ValueError(f"The {arm} {benchmark} variant reads the context text of {_named(first)}, its first pair "
                         f"alongside {before}, under other settings ({_shown_settings(identity['variant_settings'])}; "
                         f"the first pair's are {_shown_settings(first['variant_settings'])}). A confirmation must "
                         "repeat the first pair's settings, so no pair of these settings on that text could count, "
                         f"and none is answered. Answer the confirmation under {first['arm']}, or another arm "
                         "prepared with its settings (#143).")


def _check_defaults_replayed(baseline: dict, folder: Path, variant: dict) -> None:
    """Refuse a variant's pair that compare could not split, or would refuse for other code's changes (#139).

    ``baseline`` and ``variant`` are the two manifests, as ``load_prepared``
    checked them, and ``folder`` the variant's. The variant's must hold the
    defaults' text at its commit on every question, as ``prepare`` records
    it since #139, matching the defaults' gate report it kept, and that text
    must be the baseline's on every question; otherwise the pair would credit
    other code to the variant.
    """
    name = f"{variant['arm']} {variant['benchmark']}"
    defaults = [entry.get("defaults_text_sha256") for entry in variant["contexts"]]
    changed = _other_code_changes([entry["text_sha256"] for entry in baseline["contexts"]], defaults)
    if changed is None:
        raise ValueError(f"The {name} preparation does not record the defaults' context text at its commit, which "
                         "prepare records since #139, so nothing would show which of its contexts other code changed. "
                         "Prepare the variant again from a checkout with #139, under a new name with the same "
                         "settings, and answer that arm. With the same context text it stays the same variant (#130); "
                         "if newer code changed its text, it is a new variant with its own first pair and "
                         "confirmation (#143).")
    report_path = folder / "defaults-gate.json"
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    provenance = report.get("provenance") or {}
    if (not report or digest(report_path) != variant.get("defaults_gate_report_sha256")
            or provenance.get("overrides") != {} or provenance.get("commit") != variant["provenance"].get("commit")
            or [row.get("context_sha256") for row in report.get("rows", [])] != defaults):
        raise ValueError(f"The defaults' context text the {name} preparation records does not match the defaults' "
                         "gate report it kept (defaults-gate.json), so it is not shown to be the defaults' at its "
                         "commit. Prepare the variant again under a new name with the same settings (#139).")
    if changed:
        inputs = tuple(key for key in ("dirty", "python", "dependencies", "archive", "datasets")
                       if baseline["provenance"].get(key) != variant["provenance"].get(key))
        raise _other_code_refusal(changed, len(defaults), baseline["arm"], baseline["provenance"].get("commit"),
                                  variant["arm"], variant["provenance"].get("commit"), inputs)


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
    # A pair whose run log records it complete stays complete, even once its folder is moved aside (#130).
    if any(map(_complete_run, events)) or all(_complete_result(folder / side / "result.json") for side in PAIR_SIDES):
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
        return _FINAL_FAILURE
    bindings = [folder / side / "answer-model.json" for side in PAIR_SIDES]
    bound = [json.loads(binding.read_text()) for binding in bindings if binding.is_file()]
    if not all(ollama_answers.same_model(value, settings) for value in bound):
        return "it was answered by another model identity or settings"
    recorded = {_version_of(value) for value in bound} | {
        event.get("server_version") for event in events if event["event"] in _VERSION_EVENTS}
    if not recorded <= {_version_of(settings)}:
        return "it was answered under another Ollama server version"
    return None


def _pair_states(root: Path, log: Path) -> dict[int, dict]:
    """How each pair of one baseline and arm went, by number, from its run log and its folders.

    ``state`` is ``complete``, ``complete but invalid: <why>``,
    ``abandoned: <why>``, ``not published: <why>`` or ``unfinished``; a pair
    folder that its run log never names is unfinished. A pair keeps what its
    first complete full run recorded: neither an ``abandoned`` event, logged
    once its folder was moved aside, nor another ``finished`` event changes
    it (#130). ``finished_at``, ``commit`` (the after side's prepared commit)
    and ``server_version`` come from that run, and the pair's ``id`` and the
    variant identity (``_VARIANT_KEYS``) from its first start that recorded
    them.
    """
    pairs: dict[int, dict] = {}

    def pair(number: int) -> dict:
        return pairs.setdefault(number, {"state": "unfinished", "started_at": None, "finished_at": None, "commit": None,
                                         "server_version": None, "id": None, **dict.fromkeys(_VARIANT_KEYS)})

    for number, _ in _numbered(root, _PAIR):
        pair(number)
    for event in _run_events(log):
        if event.get("pair") is None:
            continue
        found = pair(event["pair"])
        if event["event"] == "started":
            found["started_at"] = found["started_at"] or event.get("at")
            for key in ("id", *_VARIANT_KEYS):
                found[key] = event.get(key) if found[key] is None else found[key]
        if found["finished_at"] is not None:
            continue
        if _complete_run(event):
            found.update(state=f"complete but invalid: {event['invalid']}" if event.get("invalid") else "complete",
                         finished_at=event.get("at"), commit=(event.get("prepared_commit") or {}).get("after"),
                         server_version=event.get("server_version"))
        elif event["event"] == "abandoned":
            found["state"] = f"abandoned: {event['reason']}"
        elif event["event"] == "finished" and event.get("reason"):
            found["state"] = f"not published: {event['reason']}"
    return dict(sorted(pairs.items()))


def _pairs_on_record(data: Path, after: str, benchmark: str) -> dict[tuple[str, str], dict[int, dict]]:
    """Every pair whose after side is ``after``, an arm or a glob of arms, alongside any baseline.

    Keyed by (baseline, arm), then by pair number, to ``_pair_states``, and
    found from both the pair run logs and the pair folders.
    """
    logs, roots = (data.glob(str(path.relative_to(data))) for path in (_pair_log_path(data, "*", after, benchmark),
                                                                      _pair_root(data, "*", after, benchmark)))
    sides = {(path.parent.name, path.name.removesuffix(f"-{benchmark}.jsonl")) for path in logs}
    sides |= {(path.parent.parent.name, path.parent.name) for path in roots if path.is_dir()}
    return {(before, arm): _pair_states(_pair_root(data, before, arm, benchmark),
                                        _pair_log_path(data, before, arm, benchmark))
            for before, arm in sorted(sides)}


def _earlier_pairs(pairs: dict[tuple[str, str], dict[int, dict]], before: str, number: int) -> list[dict]:
    """Every other pair of the arm and how it ended, so a result shows the pairs it was not.

    ``pairs`` is ``_pairs_on_record`` for the arm: this baseline's earlier
    pairs, and the arm's pairs alongside any other baseline, since a new
    baseline numbers the arm's pairs from 1 again (#130). Listed in the order
    they started.
    """
    earlier = [(state["started_at"] or "", baseline, pair, state["state"])
               for (baseline, _), numbered in pairs.items() for pair, state in numbered.items()
               if baseline != before or pair < number]
    return [{"baseline": baseline, "number": pair, "state": state} for _, baseline, pair, state in sorted(earlier)]


# Variant identity (#130) ---------------------------------------------------------

def variant_settings(overrides: dict) -> dict:
    """What a variant's ``--set`` overrides change in the configuration, by dotted setting name.

    Only the settings whose values differ from the defaults' configuration at
    this commit are kept, as the configuration reads them. So neither another
    spelling (``x=true`` and ``x=True``) nor an override that repeats a
    default makes a new variant, and a setting the configuration fills in
    because of another (``scoring.rrf_k`` under rank fusion) is kept too. A
    setting the variant's configuration leaves out is recorded as None.
    """
    changed, defaults = (_dotted(gate.gate_config(Path("{pack}"), values).model_dump(mode="json"))
                         for values in (overrides, None))
    return {name: changed.get(name) for name in sorted(changed.keys() | defaults.keys())
            if name not in changed or name not in defaults or changed[name] != defaults[name]}


def _dotted(values: dict, prefix: str = "") -> dict:
    found = {}
    for key, value in values.items():
        if isinstance(value, dict) and value:
            found.update(_dotted(value, f"{prefix}{key}."))
        else:
            found[f"{prefix}{key}"] = value
    return found


def _contexts_sha256(entries) -> str:
    """The hash of a preparation's context text on every question, in registered order.

    Two preparations with the same hash send the reader the same text on
    every question, so their pairs are pairs of one variant even when their
    settings differ, for example by a setting that retrieval never reads.
    """
    return sha([[entry["question_id"], entry["text_sha256"]] for entry in entries])


def _prepared_identity(prepared: dict) -> dict:
    """A variant manifest's identity, which its pairs record at every start (``_VARIANT_KEYS``).

    A manifest written before #130 records only its overrides, which are
    read against this checkout's defaults, or not at all (None) when this
    checkout's configuration no longer accepts them.
    """
    settings = prepared.get("variant_settings")
    overrides = (prepared.get("provenance") or {}).get("overrides")
    if settings is None and overrides:
        with suppress(ValueError):
            settings = variant_settings(overrides)
    return {"variant_settings": settings, "contexts_sha256": _contexts_sha256(prepared["contexts"])}


def _shares_identity(found: dict, identity: dict) -> bool:
    """Whether a pair or preparation records the same settings or the same context text as ``identity``.

    ``compare`` lists every such arm and pair with a variant (#130), but only
    those alongside one baseline on one context text count together
    (``_counting``, #143).
    """
    return any(found[key] is not None and found[key] == identity[key] for key in _VARIANT_KEYS)


def _variant_preparations(data: Path, benchmark: str) -> dict[str, list[dict]]:
    """Every preparation of a named variant on the track for this benchmark, by arm, with its identity.

    Each comes from a ``prepared`` event in the arm's run log, which stays
    when the arm's folder is moved aside, or, for an arm prepared before
    those events were logged (#130), from its complete manifest.
    ``settings_recorded`` says whether the settings were recorded when it
    was prepared, rather than read now from its overrides (#143).
    """
    logs = data.glob(str(_run_log_path(data, "prme-*", benchmark).relative_to(data)))
    names = {path.name.removesuffix(f"-{benchmark}.jsonl") for path in logs}
    names |= {path.parent.parent.name for path in data.glob(f"prme-*/{benchmark}/prepared.json")}
    found = {}
    for name in sorted(filter(is_variant, names)):
        events = [event for event in _run_events(_run_log_path(data, name, benchmark)) if event["event"] == "prepared"]
        found[name] = [{"at": event["at"], "commit": event.get("prepared_commit"),
                        **{key: event.get(key) for key in _VARIANT_KEYS},
                        "settings_recorded": event.get("variant_settings") is not None} for event in events]
        manifest = data / name / benchmark / "prepared.json"
        if manifest.is_file() and digest(manifest) not in {event.get("prepared_sha256") for event in events}:
            try:
                prepared = json.loads(manifest.read_text())
            except ValueError:
                continue  # A manifest that was never written whole was never answered.
            if prepared.get("complete") and prepared.get("arm") == name:
                found[name].append({"at": None, "commit": (prepared.get("provenance") or {}).get("commit"),
                                    **_prepared_identity(prepared),
                                    "settings_recorded": prepared.get("variant_settings") is not None})
    return found


def _variant_pairs(data: Path, benchmark: str, preparations: dict[str, list[dict]]) -> list[dict]:
    """Every pair of a named variant on the track for this benchmark, alongside any baseline, with its identity.

    A pair's identity is what its start recorded (#130). A pair started
    before starts recorded it takes its arm's, when every preparation of the
    arm on record has the same identity, and otherwise has none.
    ``settings_recorded`` says whether its settings were recorded, by its
    start or by that preparation (#143).
    """
    pairs = []
    for (baseline, arm), numbered in _pairs_on_record(data, "prme-*", benchmark).items():
        if not is_variant(arm):
            continue
        known = {sha([entry[key] for key in _VARIANT_KEYS]): entry for entry in preparations.get(arm, [])}
        fallback = (next(iter(known.values())) if len(known) == 1
                    else {**dict.fromkeys(_VARIANT_KEYS), "settings_recorded": False})
        for number, state in numbered.items():
            if all(state[key] is None for key in _VARIANT_KEYS):
                identity = {key: fallback[key] for key in (*_VARIANT_KEYS, "settings_recorded")}
            else:
                identity = {"settings_recorded": state["variant_settings"] is not None}
            pairs.append({**state, **identity, "arm": arm, "baseline": baseline, "number": number})
    return pairs


def _counting(pairs: list[dict], baseline: str, contexts_sha256: str | None) -> list[dict]:
    """The pairs that count together: those alongside ``baseline`` that read the context text ``contexts_sha256``.

    A new baseline starts every variant's count again, and a change to a
    variant's context text makes a new variant, with its own first pair and
    confirmation (#143). Pairs with the same context text under other settings
    stay in the count, as #130 made them, so a setting that retrieval never
    reads does not buy the variant another first pair.
    """
    return [pair for pair in pairs if pair["baseline"] == baseline
            and contexts_sha256 is not None and pair["contexts_sha256"] == contexts_sha256]


def _identity_differences(found: dict, pair: dict) -> list[str]:
    """What ``found`` does not repeat of ``pair``'s context text and settings, which a confirmation must (#143).

    Both are pairs or preparations alongside one baseline. The context text
    differs unless both record the same hash. Settings are compared only when
    both recorded them (``settings_recorded``): a pair started before #130
    recorded none, and its arm's manifest gives them only as this checkout's
    configuration reads its overrides, which a later default change alters,
    so its context text decides alone.
    """
    differs = []
    if found["contexts_sha256"] is None or found["contexts_sha256"] != pair["contexts_sha256"]:
        differs.append("context text")
    recorded = found["settings_recorded"] and pair["settings_recorded"]
    if recorded and found["variant_settings"] != pair["variant_settings"]:
        differs.append("settings")
    return differs


def _same_test(found: dict, pair: dict) -> bool:
    """Whether ``found`` repeats ``pair``'s test alongside the same baseline (``_identity_differences``, #143)."""
    return not _identity_differences(found, pair)


def _shown_settings(settings: dict | None) -> str:
    """A variant's settings as ``--set`` names them, for messages."""
    if settings is None:
        return "settings not recorded"
    return ", ".join(f"{key}={value if isinstance(value, str) else json.dumps(value)}"
                     for key, value in settings.items())


def _counted(pairs: list[dict]) -> list[dict]:
    """Of one variant's pairs, the ones the default-change rule reads: its first pair, then its confirmation.

    ``pairs`` are the pairs that count together (``_counting``). The first
    pair is the first to complete. The confirmation is the next to complete
    that started after the first completed, so it is a fresh pair, and that
    repeats the first pair's settings (``_same_test``, #143). A complete pair
    that is invalid under the 1% limit, and a pair given up, not published
    or unfinished, count as neither.
    """
    complete = sorted((pair for pair in pairs if pair["state"] == "complete"),
                      key=lambda pair: (_pair_time(pair, "finished_at"), pair["baseline"], pair["arm"], pair["number"]))
    if not complete:
        return []
    first = complete[0]
    first_ended = _pair_time(first, "finished_at")
    fresh = [pair for pair in complete[1:] if _same_test(pair, first)
             and pair["started_at"] and _pair_time(pair, "started_at") > first_ended]
    return [first, *fresh[:1]]


def _pair_time(pair: dict, key: str) -> datetime:
    return _recorded_time(pair[key], f"The {pair['baseline']} and {pair['arm']} pair run log does not record when "
                                     f"pair {pair['number']} {key.removesuffix('_at')}, with a time zone")


def _named(pair: dict) -> str:
    return f"pair {pair['number']} of {pair['baseline']} and {pair['arm']}"


def _listed(pair: dict) -> dict:
    return {key: pair[key] for key in ("arm", "baseline", "number", "state", "finished_at", "commit",
                                        "server_version")}


def _arms_of(pairs: list[dict], preparations: dict[str, list[dict]], wanted) -> list[dict]:
    """Every arm with a preparation or a pair that ``wanted`` accepts, with those preparations and pairs."""
    arms = []
    for name in sorted({pair["arm"] for pair in pairs} | set(preparations)):
        prepared = [{"at": entry["at"], "commit": entry["commit"]} for entry in preparations.get(name, [])
                    if wanted(entry)]
        answered = [{key: value for key, value in _listed(pair).items() if key != "arm"} for pair in pairs
                    if pair["arm"] == name and wanted(pair)]
        if prepared or answered:
            arms.append({"arm": name, "preparations": prepared, "pairs": answered})
    return arms


def _variant_record(data: Path, benchmark: str, mark: dict) -> dict:
    """Where a variant's pair stands among every pair with its settings or its context text under ``data``.

    The default-change rule in CLAUDE.md reads a variant's first pair and its
    confirmation (``_counted``). Pairs that count together can sit under
    another arm name, so this reads every variant preparation and pair on
    record for the benchmark (#130), and lists each that records the
    variant's settings (``variant_settings``) or its context text on every
    question (``_contexts_sha256``), alongside any baseline. Only those
    alongside this pair's baseline that read its context text count together,
    and a confirmation must also repeat the first pair's settings
    (``_counting``, #143).

    The pair ``mark`` names must be on record as complete, with the pair id
    and identity its start recorded, and must be the first pair or the
    confirmation: any other pair is refused, since the rule never reads it.
    Returns its ``role``, the ``first`` pair and the ``confirmation``, every
    arm with the variant's settings or its context text, with each of its
    preparations and pairs (``arms``), the settings of the other variants
    that change any of the same settings (``related``), every variant pair
    that records no identity (``unknown``), the pairs with its settings or
    its context text that started before this one finished and never
    completed for a reason other than a final failure (``dropped``), the
    complete pairs alongside this baseline with its settings or its context
    text that differ in the other, with what differs (``other_identities``),
    and the complete pairs with its settings or its context text alongside
    other baselines, with whether each of those baselines prepared the same
    defaults' context text as this one (``other_baselines``, #143).
    """
    preparations = _variant_preparations(data, benchmark)
    pairs = _variant_pairs(data, benchmark, preparations)
    this = next((pair for pair in pairs if (pair["arm"], pair["baseline"], pair["number"])
                 == (mark["after"], mark["before"], mark["number"])), None)
    named = f"Pair {mark['number']} of {mark['before']} and {mark['after']}"
    if this is None:
        raise ValueError(f"{named} is not in the track's pair run logs, so nothing shows whether it is the variant's "
                         "first pair or its confirmation. Compare a variant's pair on the machine that answered it "
                         "(#130).")
    if this["state"] != "complete":
        raise ValueError(f"{named} is on record as {this['state']}, not complete (#130)")
    if this["id"] is not None and this["id"] != mark["id"]:
        raise ValueError(f"{named} on record has another pair id than these results, so they were not answered in it "
                         "(#130)")
    if all(this[key] is None for key in _VARIANT_KEYS):
        raise ValueError(f"Nothing records the settings or the contexts {named[0].lower()}{named[1:]} was answered "
                         "with, so nothing shows which variant it belongs to (#130)")
    if this["contexts_sha256"] is None:
        raise ValueError(f"Nothing records the context text {named[0].lower()}{named[1:]} was answered on, so nothing "
                         "shows which pairs it counts with: a variant's pairs count together alongside one baseline "
                         "on one context text (#143)")
    identity = {key: this[key] for key in _VARIANT_KEYS}
    same = [pair for pair in pairs if _shares_identity(pair, identity)]
    counted = _counted(_counting(pairs, this["baseline"], this["contexts_sha256"]))
    role = next((VERDICT_ROLES[place] for place, pair in enumerate(counted) if pair is this), None)
    if role is None:
        which = " and ".join(f"{name} is {_named(pair)}" for name, pair in zip(("the first", "the confirmation"),
                                                                             counted))
        first_ended = _pair_time(counted[0], "finished_at")
        if not _same_test(this, counted[0]):
            why = (f"a confirmation must repeat the first pair's settings "
                   f"({_shown_settings(identity['variant_settings'])} here; the first pair's are "
                   f"{_shown_settings(counted[0]['variant_settings'])})")
        elif len(counted) > 1 and this["started_at"] and _pair_time(this, "started_at") > first_ended:
            why = "the confirmation is already complete, and the rule reads no later pair"
        else:
            why = "a confirmation must start after the first pair completed"
        raise ValueError(f"{named} is neither the variant's first pair nor its confirmation alongside "
                         f"{this['baseline']} on its context text: {which}, and {why}. The default-change rule reads "
                         "only those two, so compare pairs no other one. Every pair stays on record, and compare "
                         "lists it with the two that count (#130, #143).")
    changed = set(this["variant_settings"] or ())
    related: dict[str, dict] = {}
    for found in [*pairs, *(entry for entries in preparations.values() for entry in entries)]:
        settings = found["variant_settings"]
        if settings and changed & set(settings) and not _shares_identity(found, identity):
            related.setdefault(sha(settings), settings)
    ended = _pair_time(this, "finished_at")
    # Pairs with the variant's settings or its context text alongside this baseline, other than this one (#143).
    others = [pair for pair in same if pair is not this]
    differing = [(pair, _identity_differences(pair, this)) for pair in others
                 if pair["baseline"] == this["baseline"] and pair["state"] == "complete"]
    elsewhere = [pair for pair in others if pair["baseline"] != this["baseline"] and pair["state"] == "complete"]
    texts = {name: _baseline_text(data, benchmark, name) for name in {pair["baseline"] for pair in elsewhere}}
    current = _baseline_text(data, benchmark, this["baseline"]) if texts else None
    # Whether each other baseline read this one's defaults' text, None where either is unknown (#143).
    unchanged = {name: None if None in (text, current) else text == current for name, text in texts.items()}
    return {
        **identity, "role": role, "first": _listed(counted[0]),
        "confirmation": _listed(counted[1]) if len(counted) > 1 else None,
        "arms": _arms_of(same, preparations, partial(_shares_identity, identity=identity)),
        "related": [{"variant_settings": settings,
                     "arms": _arms_of(pairs, preparations, lambda found, settings=settings: (
                         found["variant_settings"] == settings and not _shares_identity(found, identity)))}
                    for _, settings in sorted(related.items())],
        "unknown": [_listed(pair) for pair in pairs if all(pair[key] is None for key in _VARIANT_KEYS)],
        "dropped": [_listed(pair) for pair in others if pair["finished_at"] is None
                    and pair["state"] != f"abandoned: {_FINAL_FAILURE}"
                    and (pair["started_at"] is None or _pair_time(pair, "started_at") < ended)],
        "other_identities": [{**_listed(pair), "differs": differs} for pair, differs in differing if differs],
        "other_baselines": [{**_listed(pair), "same_defaults_text": unchanged[pair["baseline"]]}
                            for pair in elsewhere],
    }


def _baseline_text(data: Path, benchmark: str, baseline: str) -> str | None:
    """The hash of a baseline's prepared context text on every question (``_contexts_sha256``), or None if unknown.

    Two baselines with the same hash sent the reader the same defaults' text, so the defaults did not change
    between them for this benchmark (#143).
    """
    with suppress(OSError, ValueError, KeyError, TypeError):
        return _contexts_sha256(json.loads((data / baseline / benchmark / "prepared.json").read_text())["contexts"])
    return None


def _variant_warnings(variant: dict) -> list[str]:
    """What compare warns about in a variant's ``_variant_record``: pairs that could hide the result that counts,
    and pairs with the variant's settings or its context text that count apart from it (#130, #143)."""
    this = variant["first"] if variant["role"] == "first" else variant["confirmation"]
    ended = _pair_time(this, "finished_at")

    def names(pairs: list[dict]) -> str:
        return ", ".join(f"{_named(pair)} ({pair['state']})" for pair in pairs)

    warnings = []
    if variant["dropped"]:
        warnings.append(f"Pairs with this variant's settings or its context text started before this one finished and "
                        f"never completed, for a reason other than a final failure: {names(variant['dropped'])}. A "
                        "pair left unfinished or given up by hand, or one whose baseline stopped being current while "
                        "it was answered, can hide a result (#130, #143)")
    unknown = [pair for pair in variant["unknown"]
               if pair["state"] == "complete" and _pair_time(pair, "finished_at") < ended]
    if unknown:
        warnings.append(f"Variant pairs that completed before this one record neither settings nor contexts, so any of "
                        f"them may be an earlier pair of this variant: {names(unknown)} (#130)")
    if variant["other_baselines"]:
        warnings.append(f"Complete pairs with this variant's settings or its context text were answered alongside "
                        f"other baselines: {', '.join(_named(pair) for pair in variant['other_baselines'])}. Each "
                        "baseline counts from zero, so they do not count toward this decision (#143)")
    unchanged = sorted({pair["baseline"] for pair in variant["other_baselines"] if pair["same_defaults_text"]})
    if unchanged:
        warnings.append(f"{', '.join(unchanged)} prepared the same defaults' context text as {this['baseline']}, so "
                        "the defaults did not change between them, and this variant's pairs alongside "
                        f"{'it' if len(unchanged) == 1 else 'them'} ran the same test as this one. Weigh their results "
                        "with this one before the default flips (#143)")
    if variant["other_identities"]:
        differing = ", ".join(f"{_named(pair)} (differs in {' and '.join(pair['differs'])})"
                              for pair in variant["other_identities"])
        warnings.append(f"Complete pairs alongside {this['baseline']} have this variant's settings or its context "
                        f"text but differ in the other, so they do not count with this pair: {differing}. One on other "
                        "context text is a variant of its own, with its own first pair and confirmation, and one on "
                        "this context text under other settings counts as neither (#143)")
    related = [f"pair {pair['number']} of {pair['baseline']} and {arm['arm']}" for group in variant["related"]
               for arm in group["arms"] for pair in arm["pairs"] if pair["state"] == "complete"]
    if related:
        warnings.append(f"Other variants that change some of the same settings have complete pairs: "
                        f"{', '.join(related)}. Each is a variant of its own, with its own first pair and confirmation "
                        "(#130)")
    return warnings


# A/A checks (#137) ----------------------------------------------------------------

def _aa_record_path(results: Path, model: str) -> Path:
    """The A/A record under ``results`` of the Ollama model named ``model``: one line per complete A/A pair.

    Each benchmark's lines are in the order its pairs finished. The record is
    tracked, so it is committed with the A/A pairs' published results.
    """
    return results / f"{ollama_answers.AnswerModel(model=model).track}-aa-checks.jsonl"


def _aa_named(baseline: str, number: int, benchmark: str | None = None) -> str:
    return f"A/A pair {number} of {baseline}" + ("" if benchmark is None else f" on {benchmark}")


def _pair_conditions(before: dict, after: dict) -> dict:
    """What a pair's two sides were answered under, as the A/A record keeps it and compare matches it (#137).

    ``run_pair`` binds both sides of a pair to one answer model, and
    ``compare`` refuses a pair whose sides differ, so the after side's values
    stand for both. A result that names no tokenizer was packed with the
    registered one (#125).
    """
    return {"answer_model": after["answer_model"],
            "server_versions": _sorted_versions([*_recorded_versions(before), *_recorded_versions(after)]),
            "failure_policy": {"id": _policy_of(after["answer_model"]),
                               "sha256": (after.get("failure_policy") or {}).get("sha256")},
            "context_budget": after.get("context_budget"),
            "tokenizer": (after.get("prepared") or {}).get("tokenizer") or RULE_TOKENIZER}


def _condition_differences(recorded: dict, current: dict) -> list[str]:
    """Which of ``AA_CONDITIONS`` differ between two sets of pair conditions (``_pair_conditions``); none when equal.

    The model identity is compared as ``ollama_answers.same_model`` compares
    it, and the answer settings are every other field of the answer model but
    its failure policy, which is compared with the amendment it names.
    """
    def settings(conditions: dict) -> dict:
        return {key: value for key, value in conditions["answer_model"].items()
                if key not in ("identity", "failure_policy")}

    differs = {
        "model identity": not ollama_answers.same_model({"identity": recorded["answer_model"].get("identity")},
                                                        {"identity": current["answer_model"].get("identity")}),
        "answer settings": settings(recorded) != settings(current),
        "failure policy": recorded["failure_policy"] != current["failure_policy"],
        "Ollama server version": recorded["server_versions"] != current["server_versions"],
        "context budget": [recorded["context_budget"], recorded["tokenizer"]]
                          != [current["context_budget"], current["tokenizer"]],
    }
    return [name for name in AA_CONDITIONS if differs[name]]


def _complete_aa_pairs(data: Path, benchmark: str) -> dict[tuple[str, int], dict]:
    """Every A/A pair the track's run logs record as complete, valid or not, by baseline and number (``_pair_states``)."""
    return {(before, number): state for (before, arm), numbered in _pairs_on_record(data, "prme*", benchmark).items()
            if before == arm and is_baseline(arm) for number, state in numbered.items()
            if state["state"].startswith("complete")}


def _finished(state: dict, named: str) -> datetime:
    """When a pair or a record line says the pair finished, which must name its time zone."""
    return _recorded_time(state.get("finished_at"), f"Nothing records when {named} finished, with a time zone (#137)")


def _published_aa_pair(before: Path, after: Path, results: Path) -> tuple[dict[str, dict], dict, dict[str, dict]]:
    """Both sides of one published A/A pair, its pair mark, and where each side is published under ``results``."""
    sides = {side: json.loads(path.read_text()) for side, path in zip(PAIR_SIDES, (before, after))}
    for side, result in sides.items():
        if result.get("kind") != "ollama-answer-result" or not result.get("complete"):
            raise ValueError(f"The {side} result is not a complete answer run on the Ollama track (#137)")
    pair = _pair_of(sides["before"], sides["after"])
    if pair is None or pair["after"] != pair["before"]:
        raise ValueError("An A/A check is the two sides of one pair of a baseline of the defaults with itself "
                         "(run-pair prme --baseline <baseline>) (#137)")
    benchmark = sides["before"].get("benchmark")
    if benchmark not in gate.GATE_BENCHMARKS or sides["after"].get("benchmark") != benchmark:
        raise ValueError(f"The results name an unknown benchmark, {benchmark!r}")
    return sides, pair, _published(dict(zip(PAIR_SIDES, (before, after))), results, "#137")


def _published(paths: dict[str, Path], results: Path, issue: str) -> dict[str, dict]:
    """Where each side's result is published under ``results``, with its digest, as a tracked record names it."""
    found = {}
    for side, path in paths.items():
        try:
            found[side] = {"path": str(path.resolve().relative_to(results.resolve())), "sha256": digest(path)}
        except ValueError:
            raise ValueError(f"The {side} result is not published under {results}. Copy the pair's published "
                             "results into this checkout's results, where they are committed with the record "
                             f"({issue}).") from None
    return found


def _recorded_sides(results: Path, entry: dict, record: str, named: str, issue: str) -> dict[str, dict]:
    """Both sides' results a tracked record's line names, which must be published under ``results`` with the
    digests the line records."""
    sides = {}
    for side in PAIR_SIDES:
        found = entry["results"][side]
        path = (results / found["path"]).resolve()
        if not path.is_relative_to(results.resolve()) or not path.is_file() or digest(path) != found["sha256"]:
            raise ValueError(f"The {record} names {found['path']} as the {side} result of {named}, which is not "
                             f"published in this checkout with the recorded digest ({issue})")
        sides[side] = json.loads(path.read_text())
    return sides


def record_aa_check(before: Path, after: Path, *, data: Path | None = None, results: Path | None = None) -> dict:
    """Add a published A/A pair to the track's A/A record, which compare reads for every variant's pair (#137).

    ``before`` and ``after`` are the two sides of one A/A pair (a baseline of
    the defaults answered against itself by ``run_pair``), published under
    ``results``. The track's run logs under ``data`` must record the pair as
    complete, with the same pair id when its start recorded one. The record
    must already list every complete A/A pair on the benchmark that finished
    before this one, and none that finished after it, so it keeps them in the
    order they finished and ``first`` is decided against all of them.
    ``first`` marks the first accepted A/A pair on the benchmark under its
    conditions (``_pair_conditions``): the A/A check the default-change rule
    reads for them, which a later pair never replaces. The pair is accepted
    when ``compare`` accepts it as a repeat, which gives its interval; a pair
    its run log records complete but invalid (#132) is recorded as refused.
    Any other refusal by ``compare`` records nothing and is raised, so a
    passing problem elsewhere never turns into a permanent refusal.
    ``run_pair`` records every A/A pair it publishes; this also records one
    published before the record existed, or one ``run_pair`` could not.
    Returns the line it appended.
    """
    results = results or RESULTS
    sides, pair, paths = _published_aa_pair(before, after, results)
    benchmark, baseline, number = sides["before"]["benchmark"], pair["before"], pair["number"]
    named = _aa_named(baseline, number, benchmark)
    data = _track_data(sides["before"], data)
    conditions = _pair_conditions(sides["before"], sides["after"])
    record = _aa_record_path(results, sides["before"]["model"])
    record.parent.mkdir(parents=True, exist_ok=True)
    # The record's own file is locked, rather than a lock file next to it, so nothing untracked is left beside it.
    with record.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        # Read under the lock, so a pair recorded at the same moment on this benchmark is seen.
        on_record = _complete_aa_pairs(data, benchmark)
        state = on_record.pop((baseline, number), None)
        if state is None:
            raise ValueError(f"{named} is not on record as complete in the track's run logs. Record an A/A pair on "
                             "the machine that answered it (#137).")
        if state["id"] is not None and state["id"] != pair["id"]:
            raise ValueError(f"{named} on record has another pair id than these results (#137)")
        finished = _finished(state, named)
        handle.seek(0)
        listed = [entry for entry in map(json.loads, handle.read().splitlines()) if entry.get("benchmark") == benchmark]
        if any(entry["pair"]["id"] == pair["id"] or (entry["baseline"], entry["pair"]["number"]) == (baseline, number)
               for entry in listed):
            raise ValueError(f"{named} is already in the A/A record ({record.name}) (#137)")
        later = [entry for entry in listed if _finish_time(entry) > finished]
        if later:
            raise ValueError(f"{named} finished before {_aa_named(later[0]['baseline'], later[0]['pair']['number'])}, "
                             f"which the A/A record ({record.name}) already lists; it keeps the A/A pairs of a "
                             "benchmark in the order they finished (#137)")
        _check_aa_record(record, benchmark, listed, {key: value for key, value in on_record.items()
                                                     if _finished(value, _aa_named(*key)) <= finished})
        try:
            comparison = compare(sides["before"], sides["after"], data=data, results=results)
        except ValueError as exc:
            if state["state"] == "complete":
                raise ValueError(f"compare refuses {named}, which its run log records complete, so nothing was "
                                 f"recorded: {exc}") from None
            comparison = None
        if comparison is not None and comparison["repeat"] is None:
            raise ValueError(f"compare does not show the two sides of {named} as a repeat of the defaults, so nothing "
                             "was recorded (#137)")
        accepted = comparison is not None and state["state"] == "complete"
        accuracy = comparison["accuracy"] if accepted else {}
        entry = {
            "kind": "ollama-aa-check", "benchmark": benchmark, "baseline": baseline,
            "pair": {key: pair[key] for key in ("id", "number", "sha256")}, "finished_at": state["finished_at"],
            "recorded_at": study.utc(), "results": paths, "conditions": conditions, "accepted": accepted,
            # Only the run log's own reason is kept: compare's messages can name local paths.
            "refused": None if accepted else f"its run log records it {state['state']}",
            "first": accepted and not any(earlier["accepted"]
                                          and not _condition_differences(earlier["conditions"], conditions)
                                          for earlier in listed),
            "questions": len(sides["before"]["rows"]),
            "correct": {side: result.get("correct") for side, result in sides.items()},
            "difference": accuracy.get("delta"), "interval_95": accuracy.get("interval_95"),
            **{key: accuracy[key] for key in ("interval_95_conversations", "interval_95_questions") if key in accuracy},
            "interval_excludes_zero": comparison["repeat"]["interval_excludes_zero"] if accepted else None,
            "changed_verdicts": comparison["repeat"]["changed_verdicts"] if accepted else None,
            "warnings": comparison["warnings"] if accepted else [],
        }
        handle.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")
    return entry


def _finish_time(entry: dict) -> datetime:
    return _finished(entry, _aa_named(entry["baseline"], entry["pair"]["number"]))


def _aa_lines(path: Path, benchmark: str) -> list[dict]:
    return [entry for entry in _run_events(path) if entry.get("benchmark") == benchmark]


def _check_aa_record(path: Path, benchmark: str, listed: list[dict], on_record: dict[tuple[str, int], dict]) -> None:
    """The A/A record must list exactly the A/A pairs ``on_record`` shows complete on the benchmark, in finish order.

    ``on_record`` is ``_complete_aa_pairs`` for the benchmark, from the track's
    run logs. So no A/A pair can be left out of the record, redrawn out of
    sight, or taken from another machine's run logs (#137).
    """
    keys = [(entry["baseline"], entry["pair"]["number"]) for entry in listed]
    for key, state in on_record.items():
        if key not in keys:
            raise ValueError(f"{_aa_named(*key, benchmark)} is on record in the track's run logs as {state['state']}, "
                             f"but the A/A record ({path.name}) does not list it. run-pair adds every A/A pair it "
                             "publishes: add one it could not, or one published before the record existed, with "
                             "record-aa-check, or use a checkout whose A/A record lists it (#137).")
    for key, count in Counter(keys).items():
        if count > 1:
            raise ValueError(f"The A/A record ({path.name}) lists {_aa_named(*key, benchmark)} {count} times (#137)")
    for entry, key in zip(listed, keys):
        state = on_record.get(key)
        if (state is None or entry["finished_at"] != state["finished_at"]
                or (state["id"] is not None and state["id"] != entry["pair"]["id"])):
            raise ValueError(f"The A/A record ({path.name}) lists {_aa_named(*key, benchmark)}, which the track's run "
                             "logs do not show complete with that pair id and finish time. Use the machine that "
                             "answered the pairs, whose run logs record them (#137).")
    times = list(map(_finish_time, listed))
    if times != sorted(times):
        raise ValueError(f"The A/A record ({path.name}) does not list the A/A pairs on {benchmark} in the order they "
                         "finished (#137)")


def _check_aa_results(results: Path, entry: dict) -> dict[str, dict]:
    """A record line must name the pair's published results with their digests, and the conditions they record.

    So an edited line cannot move an A/A check to conditions it was not
    answered under, or hide a pair under others (#137). Returns both sides.
    """
    named = _aa_named(entry["baseline"], entry["pair"]["number"], entry["benchmark"])
    sides = _recorded_sides(results, entry, "A/A record", named, "#137")
    if (any((result.get("pair") or {}).get("id") != entry["pair"]["id"] for result in sides.values())
            or _pair_conditions(sides["before"], sides["after"]) != entry["conditions"]):
        raise ValueError(f"The A/A record's line for {named} does not match the pair and conditions its published "
                         "results record (#137)")
    return sides


def _checked_aa_lines(results: Path, model: str, benchmark: str,
                      on_record: dict[tuple[str, int], dict] | None) -> list[tuple[dict, dict[str, dict]]]:
    """The A/A record's lines on the benchmark, each with both sides of its published pair (``_check_aa_results``).

    With ``on_record``, the complete A/A pairs in the track's run logs
    (``_complete_aa_pairs``), the record must list exactly them
    (``_check_aa_record``). Without the run logs, None, nothing can show it.
    """
    path = _aa_record_path(results, model)
    listed = _aa_lines(path, benchmark)
    if on_record is not None:
        _check_aa_record(path, benchmark, listed, on_record)
    return [(entry, _check_aa_results(results, entry)) for entry in listed]


def _aa_listed(entry: dict) -> dict:
    return {"baseline": entry["baseline"], "number": entry["pair"]["number"], "pair_id": entry["pair"]["id"],
            **{key: entry[key] for key in ("finished_at", "accepted", "refused", "difference", "interval_95",
                                           "interval_excludes_zero", "changed_verdicts", "results")}}


def _aa_coverage(data: Path, results: Path, model: str, conditions: dict) -> dict:
    """The A/A check that covers ``conditions`` on each benchmark, or a refusal when one does not (#137).

    The default-change rule in CLAUDE.md relies on a variant's pair only under
    the conditions of a recorded A/A check (``AA_CONDITIONS``) on both
    benchmarks, so after an Ollama update a new A/A pair is needed on both
    before any further variant pair counts. On each benchmark the check is the
    first A/A pair under those conditions that completed and that compare
    accepted; ``other_pairs`` lists every other A/A pair under them, in the
    order they finished. The A/A record under ``results`` must list exactly
    the A/A pairs the track's run logs under ``data`` show complete
    (``_check_aa_record``), and each line must match its published results
    (``_check_aa_results``).
    """
    path = _aa_record_path(results, model)
    checks = {}
    for benchmark in gate.GATE_BENCHMARKS:
        on_record = _complete_aa_pairs(data, benchmark)
        listed = [entry for entry, _ in _checked_aa_lines(results, model, benchmark, on_record)]
        covering = [entry for entry in listed if not _condition_differences(entry["conditions"], conditions)]
        accepted = [entry for entry in covering if entry["accepted"]
                    and on_record[(entry["baseline"], entry["pair"]["number"])]["state"] == "complete"]
        if not accepted:
            found = []
            for entry in listed:
                differs = _condition_differences(entry["conditions"], conditions)
                why = f"differs in {', '.join(differs)}" if differs else "refused"
                found.append(f"{_aa_named(entry['baseline'], entry['pair']['number'])} ({why})")
            raise ValueError(f"No A/A check on {benchmark} was answered under the conditions of this pair (model "
                             "identity, answer settings, failure policy, Ollama server version "
                             f"{', '.join(map(str, conditions['server_versions']))} and context budget), so the "
                             f"default-change rule cannot rely on it. A/A pairs recorded on {benchmark}: "
                             f"{'; '.join(found) or 'none'}. Answer an A/A pair under these conditions on both "
                             "benchmarks first (run-pair prme --baseline <current baseline>) (#137).")
        check = accepted[0]
        if [entry for entry in covering if entry["first"]] != [check]:
            raise ValueError(f"The A/A record ({path.name}) must mark "
                             f"{_aa_named(check['baseline'], check['pair']['number'], benchmark)}, the first A/A pair "
                             "under its conditions that compare accepted, and no other, as the first A/A check under "
                             "them (#137)")
        checks[benchmark] = {"check": _aa_listed(check),
                             "other_pairs": [_aa_listed(entry) for entry in covering if entry is not check]}
    return {"conditions": {"manifest_digest_sha256": (conditions["answer_model"].get("identity") or {}).get(
                               "manifest_digest_sha256"),
                           **{key: conditions[key] for key in ("server_versions", "failure_policy", "context_budget",
                                                               "tokenizer")}},
            "record": {"path": path.name, "sha256": digest(path)}, "checks": checks}


def _aa_warnings(coverage: dict) -> list[str]:
    """What compare warns about in ``_aa_coverage``: other A/A pairs under the same conditions, and any that exclude
    zero, which brings in the default-change rule's extra margin."""
    warnings = []
    for benchmark, found in coverage["checks"].items():
        check = found["check"]
        if found["other_pairs"]:
            warnings.append(f"Other A/A pairs on {benchmark} were answered under the conditions of this pair: "
                            + ", ".join(f"{_aa_named(entry['baseline'], entry['number'])} ("
                                        + ("accepted" if entry["accepted"] else "refused") + ")"
                                        for entry in found["other_pairs"])
                            + f". The A/A check is the first that compare accepted, "
                              f"{_aa_named(check['baseline'], check['number'])}, and a later pair never replaces it "
                              "(#137)")
        excluding = [_aa_named(entry["baseline"], entry["number"]) for entry in (check, *found["other_pairs"])
                     if entry["interval_excludes_zero"]]
        if excluding:
            warnings.append(f"On {benchmark}, {', '.join(excluding)} under the conditions of this pair "
                            f"{'excludes' if len(excluding) == 1 else 'exclude'} zero, so under the default-change "
                            "rule in CLAUDE.md a variant's gain there must also be larger than the largest absolute "
                            "A/A difference measured so far on that benchmark, the #118 repeat included (#137)")
    return warnings


def _check_aa_ready(data: Path, results: Path, model: ollama_answers.AnswerModel, benchmark: str, before: str,
                    after: str, settings: dict, prepared: dict) -> None:
    """Refuse to start a pair whose results the A/A record could not take or cover (#137).

    An A/A pair is added to the record once published, so the record must
    already list every complete A/A pair on its benchmark. A variant's pair
    needs a recorded A/A check under the model, settings, failure policy,
    Ollama server version and budget it is about to be answered under, on both
    benchmarks: compare would refuse it otherwise, and it would still count
    as the variant's first pair or confirmation (#130).
    """
    if before == after:
        record = _aa_record_path(results, model.model)
        _check_aa_record(record, benchmark, _aa_lines(record, benchmark), _complete_aa_pairs(data, benchmark))
    elif is_variant(after):
        now = {"answer_model": settings, "server_versions": [_version_of(settings)],
               "failure_policy": {"sha256": failure_amendment()["sha256"]} if _policy_of(settings) else None,
               "context_budget": prepared["context_budget"], "prepared": {"tokenizer": prepared.get("tokenizer")}}
        _aa_coverage(data, results, model.model, _pair_conditions(now, now))


# Pair verdicts (#144) -------------------------------------------------------------

def _verdict_record_path(results: Path, model: str) -> Path:
    """The verdict record under ``results`` of the Ollama model named ``model``.

    One line per pair the ``compare`` command accepted as a variant's first
    pair or confirmation, or as an A/A pair, in the order they were first
    compared. The record is tracked, so it is committed with the pairs'
    published results, like the A/A record.
    """
    return results / f"{ollama_answers.AnswerModel(model=model).track}-pair-verdicts.jsonl"


def _pair_named(entry: dict) -> str:
    """A verdict record line's pair, for messages."""
    return f"{_named({'number': entry['pair']['number'], **{key: entry[key] for key in ('baseline', 'arm')}})} " \
           f"on {entry['benchmark']}"


def _role_of(comparison: dict) -> str | None:
    """The role a pair compare accepted has under the default-change rule (``RECORDED_ROLES``), or None.

    A variant's pair is its first pair or its confirmation (#130), and a pair
    of a baseline with itself that compare shows as a repeat is an A/A pair
    (#137). A reference arm's pair, and a repeat answered as two runs on
    their own, have no role.
    """
    if comparison.get("variant") is not None:
        return comparison["variant"]["role"]
    pair = comparison.get("pair")
    if pair is not None and comparison.get("repeat") is not None and pair["before"] == pair["after"]:
        return "aa"
    return None


def record_pair_verdict(comparison: dict, before: Path, after: Path, *, data: Path | None = None,
                        results: Path | None = None) -> tuple[dict, bool] | None:
    """Record a pair compare accepted, for the verdict step (``verdict``, #144).

    ``comparison`` is compare's output for the results at ``before`` and
    ``after``, which must be that pair's. A variant's first pair or
    confirmation, and an A/A pair (``_role_of``), get a ``compared`` event
    in the pair's run log under ``data`` and a line in the track's verdict
    record under ``results`` (``_verdict_record_path``): the role, the
    baseline, the variant's identity and the first pair it counts with, when
    the pair started and finished, the difference, ``interval_95`` and
    whether it excludes zero, the A/A check a variant's pair relied on,
    compare's warnings, and both results' published paths and digests, so a
    reviewer can check the line without the track's private data. The line
    is checked against those results before it is written
    (``_check_verdict_results``), and the pair must be on record as complete
    in its run log with their pair id.

    Returns the line and whether it was added. The record keeps one line per
    pair: comparing a pair again adds an event to its run log but no line,
    and a line that records other values for the pair is refused. None means
    nothing was recorded: the pair has no role, or the track's run logs are
    not on this machine, where only the pair's numbers can be checked.
    """
    role = _role_of(comparison)
    if role is None:
        return None
    if (comparison["bootstrap_samples"], comparison["bootstrap_seed"]) != (BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED):
        raise ValueError(f"The verdict record keeps comparisons with compare's own bootstrap ({BOOTSTRAP_SAMPLES} "
                         f"samples, seed {BOOTSTRAP_SEED}) only (#144)")
    results = results or RESULTS
    pair, benchmark = comparison["pair"], comparison["benchmark"]
    paths = _published({"before": before, "after": after}, results, "#144")
    sides = {side: json.loads(path.read_text()) for side, path in zip(PAIR_SIDES, (before, after))}
    marked = _pair_of(sides["before"], sides["after"])
    if marked != pair or sides["before"].get("benchmark") != benchmark:
        raise ValueError("The comparison is not of the pair these results were answered in (#144)")
    data = _track_data(sides["before"], data)
    if current_baseline(data, benchmark) is None:
        return None
    log = _pair_log_path(data, pair["before"], pair["after"], benchmark)
    state = _pair_states(_pair_root(data, pair["before"], pair["after"], benchmark), log).get(pair["number"])
    named = f"pair {pair['number']} of {pair['before']} and {pair['after']} on {benchmark}"
    if state is None or state["state"] != "complete" or (state["id"] is not None and state["id"] != pair["id"]):
        raise ValueError(f"{named[0].upper()}{named[1:]} is not on record as complete in the track's pair run log, "
                         "with these results' pair id, so it was not recorded for the verdict. Compare a pair on the "
                         "machine that answered it (#144).")
    accuracy, variant, aa_check = comparison["accuracy"], comparison["variant"], comparison["aa_check"]
    entry = {
        "kind": "ollama-pair-verdict", "benchmark": benchmark, "role": role, "baseline": pair["before"],
        "arm": pair["after"], "pair": {key: pair[key] for key in _MARK_KEYS},
        "started_at": state["started_at"], "finished_at": state["finished_at"],
        "variant": None if variant is None else {
            **{key: variant[key] for key in _VARIANT_KEYS},
            "first": {key: variant["first"][key] for key in ("arm", "baseline", "number")}},
        "questions": accuracy["queries"], "correct": {side: result.get("correct") for side, result in sides.items()},
        "bootstrap_samples": BOOTSTRAP_SAMPLES, "bootstrap_seed": BOOTSTRAP_SEED, "difference": accuracy["delta"],
        "interval_95": accuracy["interval_95"], **{key: accuracy[key] for key in _SPLIT_INTERVALS if key in accuracy},
        "interval_excludes_zero": _excludes_zero(accuracy["interval_95"]),
        # The check itself, which a later A/A pair never replaces; other_pairs grows, so it is left to compare.
        "aa_check": None if aa_check is None else {
            "conditions": aa_check["conditions"],
            "checks": {name: {key: found["check"][key] for key in ("baseline", "number", "pair_id")}
                       for name, found in aa_check["checks"].items()}},
        "warnings": comparison["warnings"], "results": paths,
    }
    # As JSON reads it back (lists, not tuples), so a line already on record compares like with like.
    entry = json.loads(json.dumps(entry, allow_nan=False))
    _check_verdict_results(results, entry)
    record = _verdict_record_path(results, comparison["model"])
    record.parent.mkdir(parents=True, exist_ok=True)
    # The record's own file is locked, as the A/A record is, so nothing untracked is left beside it.
    with record.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        same = [line for line in map(json.loads, handle.read().splitlines())
                if line.get("benchmark") == benchmark and (line["pair"]["id"] == pair["id"] or (
                    line["baseline"], line["arm"], line["pair"]["number"]) == (entry["baseline"], entry["arm"],
                                                                               pair["number"]))]
        for line in same:
            # compare's warnings can grow later, as more pairs complete, so only the recorded result must repeat.
            differs = sorted(key for key in {*line, *entry} - {"recorded_at", "warnings"}
                             if line.get(key) != entry.get(key))
            if differs:
                raise ValueError(f"The verdict record ({record.name}) already lists {named} with other values "
                                 f"({', '.join(differs)}), so nothing was recorded (#144)")
        _append_event(log, {"event": "compared", "pair": pair["number"], "id": pair["id"],
                            **{key: value for key, value in entry.items() if key not in ("kind", "pair")}})
        if same:
            return same[0], False
        entry["recorded_at"] = study.utc()
        handle.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")
    return entry, True


def _check_numbers(entry: dict, sides: dict[str, dict], record: str, named: str) -> None:
    """A tracked record's line must give the numbers its published results give, recomputed with compare's bootstrap.

    So an edited line cannot turn a result into another (#144). The A/A
    record and the verdict record both keep them.
    """
    accuracy = _accuracy(sides["before"], sides["after"])
    found = {"questions": accuracy["queries"], "difference": accuracy["delta"], "interval_95": accuracy["interval_95"],
             **{key: accuracy[key] for key in _SPLIT_INTERVALS if key in accuracy},
             "interval_excludes_zero": _excludes_zero(accuracy["interval_95"]),
             "correct": {side: result.get("correct") for side, result in sides.items()}}
    if json.loads(json.dumps(found)) != {key: entry.get(key) for key in found}:
        raise ValueError(f"The {record}'s line for {named} gives other numbers than this checkout recomputes from its "
                         "published results: the line was edited, or the paired bootstrap code changed since it was "
                         "recorded (#144)")


def _check_verdict_results(results: Path, entry: dict) -> dict[str, dict]:
    """A verdict record line must name its pair's published results with their digests, and match what they record.

    Its role must be one the record keeps, its numbers compare's own for
    those results (``_check_numbers``), and a variant's identity the one its
    after side records, where it records one: the settings it was prepared
    with and the hash of its context text on every question. Returns both
    sides.
    """
    named = _pair_named(entry)
    if entry.get("kind") != "ollama-pair-verdict" or entry.get("role") not in RECORDED_ROLES:
        raise ValueError(f"The verdict record's line for {named} is not a pair verdict with a known role (#144)")
    if (entry.get("bootstrap_samples"), entry.get("bootstrap_seed")) != (BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED):
        raise ValueError(f"The verdict record's line for {named} names another bootstrap than compare's "
                         f"({BOOTSTRAP_SAMPLES} samples, seed {BOOTSTRAP_SEED}) (#144)")
    sides = _recorded_sides(results, entry, "verdict record", named, "#144")
    pair = _pair_of(sides["before"], sides["after"])
    if (pair is None or {key: pair[key] for key in _MARK_KEYS} != entry["pair"]
            or (pair["before"], pair["after"]) != (entry["baseline"], entry["arm"])
            or sides["before"].get("benchmark") != entry["benchmark"]
            or (entry["role"] == "aa") != (entry["variant"] is None)):
        raise ValueError(f"The verdict record's line for {named} does not match the pair its published results "
                         "record (#144)")
    _check_numbers(entry, sides, "verdict record", named)
    if entry["variant"] is not None:
        after = sides["after"]
        settings = (after.get("prepared") or {}).get("variant_settings")
        texts = [{"question_id": row["question_id"], "text_sha256": row.get("context_text_sha256")}
                 for row in after["rows"]]
        if ((settings is not None and settings != entry["variant"]["variant_settings"])
                or (all(_has_hash(row, "context_text_sha256") for row in after["rows"])
                    and _contexts_sha256(texts) != entry["variant"]["contexts_sha256"])):
            raise ValueError(f"The verdict record's line for {named} names another variant than its published "
                             "results record (#144)")
    return sides


def _aa_refused(sides: dict[str, dict]) -> bool:
    """Whether an A/A pair's published results show why compare refused it: a side over the 1% limit (#132)."""
    with suppress(KeyError, TypeError, ValueError):
        return any(not _outcome_counts(result["rows"], len(result["rows"]))["within_limit"]
                   for result in sides.values())
    return False


def _accepted_aa_lines(results: Path, model: str, benchmark: str, data: Path, *, logs: bool) -> list[dict]:
    """The accepted A/A pairs on the benchmark in the track's A/A record, each checked against its published pair.

    The verdict reads them for the A/A margin, so each accepted line's
    numbers are recomputed from its published results (``_check_numbers``),
    and whether a line is accepted must be shown too: with the track's run
    logs on this machine (``logs``), by the pair's state there, which also
    requires the record to list exactly the complete A/A pairs they show,
    so no A/A pair that excluded zero can be left out; without them, by a
    refused pair's published results being over the failure policy's limit.
    """
    on_record = _complete_aa_pairs(data, benchmark) if logs else None
    accepted = []
    for entry, sides in _checked_aa_lines(results, model, benchmark, on_record):
        named = _aa_named(entry["baseline"], entry["pair"]["number"], benchmark)
        shown = (on_record[(entry["baseline"], entry["pair"]["number"])]["state"] == "complete"
                 if on_record is not None else not _aa_refused(sides))
        if entry["accepted"] != shown:
            shows = "the track's run logs" if logs else "its published results"
            raise ValueError(f"The A/A record marks {named} {'accepted' if entry['accepted'] else 'refused'}, which "
                             f"{shows} do not show (#144)")
        if entry["accepted"]:
            _check_numbers(entry, sides, "A/A record", named)
            accepted.append(entry)
    return accepted


def _sequential_repeat(results: Path, model: str, benchmark: str) -> float:
    """The paired difference of the #118 sequential repeat on the benchmark, as compare gives it for its results."""
    day, *arms = SEQUENTIAL_REPEAT
    track = ollama_answers.AnswerModel(model=model).track
    paths = [results / day / f"{track}-{arm}-{benchmark}-result.json" for arm in arms]
    missing = [str(path.relative_to(results)) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"The #118 repeat's published results are missing ({', '.join(missing)}), and the A/A margin "
                         "counts its difference (#144)")
    comparison = compare(*(json.loads(path.read_text()) for path in paths))
    if comparison["repeat"] is None:
        raise ValueError("compare does not show the #118 repeat's published results as a repeat of the defaults, "
                         "and the A/A margin counts its difference (#144)")
    return comparison["accuracy"]["delta"]


def _aa_margin(accepted: dict[str, list[dict]], conditions: dict, benchmark: str, relied_on: dict, repeat) -> dict:
    """Whether the default-change rule's A/A margin applies to a variant's pair on the benchmark, and how large it is.

    ``accepted`` is ``_accepted_aa_lines`` on each benchmark, ``conditions``
    the pair's (``_pair_conditions``) and ``relied_on`` the A/A check on this
    benchmark the pair recorded, which must be one of them under those
    conditions. The margin applies when an accepted A/A pair under the same
    conditions excluded zero, on either benchmark, as the rule reads ("if
    either excludes zero"), and a gain on this benchmark must then also be
    larger than the largest absolute A/A difference recorded on it, the #118
    repeat included (``repeat``, called only then).
    """
    def covering(entries: list[dict]) -> list[dict]:
        return [entry for entry in entries if not _condition_differences(entry["conditions"], conditions)]

    if not any((entry["baseline"], entry["pair"]["number"], entry["pair"]["id"])
               == (relied_on["baseline"], relied_on["number"], relied_on["pair_id"])
               for entry in covering(accepted[benchmark])):
        raise ValueError(f"The A/A record holds no accepted A/A check on {benchmark} under the conditions of this pair "
                         f"that is the one it relied on, {_aa_named(relied_on['baseline'], relied_on['number'])} "
                         "(#137, #144)")
    excluding = [_aa_named(entry["baseline"], entry["pair"]["number"], name) for name, entries in accepted.items()
                 for entry in covering(entries) if entry["interval_excludes_zero"]]
    if not excluding:
        return {"applies": False, "excluding": [], "largest_aa_difference": None, "from": None,
                "case": "No accepted A/A pair under this pair's conditions excludes zero on either benchmark, so no "
                        "margin applies: a gain counts when its 95% interval excludes zero"}
    sources = [(abs(entry["difference"]), _aa_named(entry["baseline"], entry["pair"]["number"]))
               for entry in accepted[benchmark]]
    sources.append((abs(repeat()), "the #118 sequential repeat"))
    largest, source = max(sources, key=lambda item: item[0])
    return {"applies": True, "excluding": excluding, "largest_aa_difference": largest, "from": source,
            "case": f"{', '.join(excluding)} under this pair's conditions "
                    f"{'excludes' if len(excluding) == 1 else 'exclude'} zero, so a gain on {benchmark} counts only "
                    f"when it is also larger than {largest:.4f}, the largest absolute A/A difference recorded on "
                    f"{benchmark} ({source})"}


def _judged(entry: dict, margin: dict) -> dict:
    """What a recorded pair shows on its benchmark under the default-change rule, with the A/A margin applied.

    A gain or a loss is one whose 95% interval excludes zero, on that side of it.
    """
    difference, interval = entry["difference"], entry["interval_95"]
    outcome = ("gain" if interval and interval[0] > 0 else "loss" if interval and interval[1] < 0
               else "no difference shown")
    counts = outcome == "gain" and (not margin["applies"] or difference > margin["largest_aa_difference"])
    return {"pair": {"arm": entry["arm"], "baseline": entry["baseline"], **entry["pair"]},
            "variant_settings": entry["variant"]["variant_settings"],
            **{key: entry.get(key) for key in ("started_at", "finished_at", "correct", "questions")},
            "difference": difference, "interval_95": interval,
            **{key: entry[key] for key in _SPLIT_INTERVALS if key in entry},
            "interval_excludes_zero": entry["interval_excludes_zero"], "outcome": outcome, "margin": margin,
            "gain_counts": counts, "aa_check": entry["aa_check"], "results": entry["results"],
            "recorded_at": entry.get("recorded_at")}


def _verdict_lines(benchmark: str, arm: str, lines: list[dict], data: Path) -> dict:
    """The variant's first pair and confirmation lines on one benchmark, found without recomputing anything.

    The count is the variant's pairs alongside the current baseline on its
    context text (``_counting``, #143). With the track's run logs on this
    machine, the current baseline is theirs, the context text that of the
    arm's latest preparation, and each recorded role must be the pair they
    count in it (``_counted``). Without them, the baseline is that of the
    last first pair or confirmation recorded on the benchmark, since compare
    records a variant's pair only while its baseline is current, and the
    text that of the arm's last line alongside it.
    """
    listed = [line for line in lines if line.get("benchmark") == benchmark]
    for line in listed:
        if line.get("kind") != "ollama-pair-verdict" or line.get("role") not in RECORDED_ROLES:
            raise ValueError(f"The verdict record lists a line on {benchmark} that is not a pair verdict with a known "
                             "role (#144)")
    variants = [line for line in listed if line["role"] in VERDICT_ROLES]
    baseline, source, text, pairs = current_baseline(data, benchmark), "run logs", None, []
    if baseline is not None:
        preparations = _variant_preparations(data, benchmark)
        pairs = _variant_pairs(data, benchmark, preparations)
        text = (preparations.get(arm) or [{}])[-1].get("contexts_sha256")
    else:
        baseline = next((line["baseline"] for line in reversed(variants)), None)
        source = None if baseline is None else "verdict record"
    own = [line for line in variants if line["arm"] == arm]
    if text is None:
        text = next((line["variant"]["contexts_sha256"] for line in reversed(own) if line["baseline"] == baseline),
                    None)
    counting = [line for line in variants if line["baseline"] == baseline and text is not None
                and line["variant"]["contexts_sha256"] == text]
    recorded = {}
    for role in VERDICT_ROLES:
        found = {line["pair"]["id"]: line for line in counting if line["role"] == role}
        if len(found) > 1:
            raise ValueError(f"The verdict record lists more than one {_ROLE_LABELS[role]} of the variant alongside "
                             f"{baseline} on {benchmark}: {', '.join(map(_pair_named, found.values()))} (#144)")
        recorded[role] = next(iter(found.values()), None)
    counted = dict(zip(VERDICT_ROLES, _counted(_counting(pairs, baseline, text)))) if pairs and text else {}
    first, confirmation = recorded["first"], recorded["confirmation"]
    for role, line in recorded.items():
        pair = counted.get(role)
        if source == "run logs" and line is not None and (
                pair is None or (pair["arm"], pair["baseline"], pair["number"])
                != (line["arm"], line["baseline"], line["pair"]["number"])
                or (pair["id"] is not None and pair["id"] != line["pair"]["id"])
                or any(pair[key] != line["variant"][key] for key in _VARIANT_KEYS)):
            raise ValueError(f"The verdict record lists {_pair_named(line)} as the variant's {_ROLE_LABELS[role]}, "
                             "but the track's run logs do not count it as that (#144)")
    if confirmation is not None:
        named = confirmation["variant"]["first"]
        if first is not None and named != {"arm": first["arm"], "baseline": first["baseline"],
                                           "number": first["pair"]["number"]}:
            raise ValueError(f"The verdict record's line for {_pair_named(confirmation)} names another first pair than "
                             f"{_pair_named(first)} (#144)")
        if first is not None and (
                _recorded_time(confirmation["started_at"], f"The verdict record's line for {_pair_named(confirmation)} "
                                                           "does not record when it started, with a time zone (#144)")
                <= _recorded_time(first["finished_at"], f"The verdict record's line for {_pair_named(first)} does "
                                                        "not record when it finished, with a time zone (#144)")):
            raise ValueError(f"The verdict record's line for {_pair_named(confirmation)} started before its first pair "
                             "finished, so it is not a confirmation (#130, #144)")
    # With the run logs, compare's own view of each recorded pair as it stands now: its role, and its warnings about
    # pairs that could hide a result, such as pairs alongside an earlier baseline with the same defaults' text (#143).
    warnings = []
    for line in recorded.values():
        if source == "run logs" and line is not None:
            mark = {"before": line["baseline"], "after": line["arm"], "number": line["pair"]["number"],
                    "id": line["pair"]["id"]}
            warnings += [f"{_pair_named(line)}: {text}" for text in _variant_warnings(
                _variant_record(data, benchmark, mark))]
        elif line is not None:
            warnings += [f"compare warned about {_pair_named(line)}: {text}" for text in line.get("warnings", [])]
    missing = []
    if baseline is None:
        missing.append(f"nothing on {benchmark} is in the verdict record, and the track's run logs are not on this "
                       "machine")
    for role in VERDICT_ROLES if baseline is not None else ():
        if recorded[role] is not None:
            continue
        # The pair that holds the role, from the confirmation's line or the run logs, when either names it.
        pair = confirmation["variant"]["first"] if role == "first" and confirmation else counted.get(role)
        if pair is not None:
            missing.append(f"{_named(pair)} is the variant's {_ROLE_LABELS[role]} on {benchmark}, but compare has "
                           "not recorded it: compare its published results")
        else:
            missing.append(f"no {_ROLE_LABELS[role]} of the variant alongside {baseline} on {benchmark} is in the "
                           "verdict record")
    return {"baseline": baseline, "baseline_from": source, "contexts_sha256": text, "recorded": recorded,
            "missing": missing, "warnings": warnings,
            "other_baseline_pairs": [_pair_named(line) for line in own if line["baseline"] != baseline],
            "other_text_pairs": [_pair_named(line) for line in own if line["baseline"] == baseline
                                 and line["variant"]["contexts_sha256"] != text],
            "baselines_recorded": list(dict.fromkeys(line["baseline"] for line in variants))}


def _same_answers(found: dict[str, dict], sides: dict[str, dict[str, dict]]) -> None:
    """A variant's first pair and its confirmation on one benchmark must share the model identity and answer settings.

    The default-change rule in CLAUDE.md reads the two as one test repeated
    (#144); ``sides`` are both pairs' published results.
    """
    if len(sides) < 2:
        return
    conditions = [_pair_conditions(pair["before"], pair["after"]) for pair in sides.values()]
    differs = [name for name in _condition_differences(*conditions) if name in ("model identity", "answer settings")]
    if differs:
        lines = " and ".join(_pair_named(found["recorded"][role]) for role in sides)
        raise ValueError(f"{lines[0].upper()}{lines[1:]} were answered under different {' and '.join(differs)}, so "
                         "the confirmation does not repeat the first pair's test (#144)")


def _role_verdict(role: str, found: dict[str, dict | None]) -> dict:
    """Whether a variant's first pair or confirmation passed, over both benchmarks, or None while it is undecided.

    It passes with a gain on at least one benchmark whose 95% interval
    excludes zero (and, where the A/A margin applies, is larger than it) and
    no loss on either benchmark whose interval excludes zero. A loss decides
    it as soon as it is recorded, on either benchmark.
    """
    label = _ROLE_LABELS[role]
    losses = [name for name, value in found.items() if value is not None and value["outcome"] == "loss"]
    if losses:
        return {"passed": False, "why": f"A loss on {' and '.join(losses)} whose 95% interval excludes zero",
                "benchmarks": found}
    if any(value is None for value in found.values()):
        return {"passed": None, "why": f"The {label} is not recorded on both benchmarks", "benchmarks": found}
    gains = [name for name, value in found.items() if value["gain_counts"]]
    short = [name for name, value in found.items() if value["outcome"] == "gain" and not value["gain_counts"]]
    if not gains:
        why = "No gain on either benchmark whose 95% interval excludes zero" + (
            f"; the gain on {' and '.join(short)} excludes zero but is not larger than the A/A margin" if short else "")
    else:
        why = (f"A gain on {' and '.join(gains)} whose 95% interval excludes zero, and no loss on either benchmark "
               "whose interval excludes zero")
    return {"passed": bool(gains), "why": why, "benchmarks": found}


def verdict(arm: str, *, model: ollama_answers.AnswerModel | None = None, data: Path | None = None,
            results: Path | None = None) -> dict:
    """Whether a variant passed the default-change rule in CLAUDE.md, from the track's verdict record (#144).

    ``arm`` is a named variant. On each benchmark, its pairs count together
    alongside the current baseline on its context text (#143), and the
    verdict reads the first pair and the confirmation compare recorded among
    them (``_verdict_lines``), each checked against its published results
    (``_check_verdict_results``). A first pair or confirmation passes with a
    gain on at least one benchmark whose 95% interval excludes zero and no
    loss on either whose interval excludes zero (``_role_verdict``); where an
    accepted A/A pair under the pair's conditions excluded zero, the gain
    must also be larger than the largest absolute A/A difference recorded on
    that benchmark, the #118 repeat included (``_aa_margin``). ``pass`` needs
    both to pass. A failed first pair fails the variant, whatever its
    confirmation shows, and so does a failed confirmation. Anything else is
    ``incomplete``, with what is missing: a pair compare refused was never
    recorded, so it counts as neither, and a new baseline starts the count
    again. The pairs must record the same settings on both benchmarks, when
    they record any, and a first pair and its confirmation the same model
    identity and answer settings. ``checked_against_run_logs`` says whether
    the track's run logs were on this machine for both benchmarks, which the
    pull request that flips a default needs.
    """
    if not is_variant(arm):
        raise ValueError(f"{arm} is not a named variant of the defaults (prme-<name>), which is what the verdict "
                         "decides on (#144)")
    model = model or ollama_answers.AnswerModel()
    data = data or data_root(model)
    results = results or RESULTS
    path = _verdict_record_path(results, model.model)
    lines = _run_events(path)
    found = {benchmark: _verdict_lines(benchmark, arm, lines, data) for benchmark in gate.GATE_BENCHMARKS}
    # The settings are compared first, since recomputing the pairs' numbers takes a while.
    recorded = [line for record in found.values() for line in record["recorded"].values() if line is not None]
    settings = {sha(line["variant"]["variant_settings"]): line["variant"]["variant_settings"] for line in recorded
                if line["variant"]["variant_settings"] is not None}
    if len(settings) > 1:
        raise ValueError(f"The variant's recorded pairs were answered under different settings "
                         f"({'; '.join(map(_shown_settings, settings.values()))}), so they are not one variant's "
                         "(#130, #144)")
    repeat = cache(partial(_sequential_repeat, results, model.model))
    # The margin reads the A/A pairs on both benchmarks, so both are checked once any pair is recorded.
    accepted = {benchmark: _accepted_aa_lines(results, model.model, benchmark, data,
                                              logs=record["baseline_from"] == "run logs")
                for benchmark, record in found.items()} if recorded else {}
    judged: dict[str, dict[str, dict | None]] = {role: {} for role in VERDICT_ROLES}
    for benchmark, record in found.items():
        sides = {}
        for role, line in record["recorded"].items():
            judged[role][benchmark] = None
            if line is None:
                continue
            sides[role] = _check_verdict_results(results, line)
            margin = _aa_margin(accepted, _pair_conditions(sides[role]["before"], sides[role]["after"]), benchmark,
                                line["aa_check"]["checks"][benchmark], partial(repeat, benchmark))
            judged[role][benchmark] = _judged(line, margin)
        _same_answers(record, sides)
    roles = {role: _role_verdict(role, judged[role]) for role in VERDICT_ROLES}
    first, confirmation = roles["first"]["passed"], roles["confirmation"]["passed"]
    missing = [item for record in found.values() for item in record["missing"]]
    if first is False:
        outcome, reason = "fail", (f"The first pair failed: {roles['first']['why']}. A failed first pair fails the "
                                   "variant, whatever its confirmation shows.")
    elif confirmation is False:
        outcome, reason = "fail", (f"The confirmation failed: {roles['confirmation']['why']}. A failed confirmation "
                                   "fails the variant.")
    elif first and confirmation:
        outcome, reason = "pass", "The first pair and the confirmation both passed."
    else:
        outcome, reason = "incomplete", f"Not decided yet: {'; '.join(missing)}."
    logs = all(record["baseline_from"] == "run logs" for record in found.values())
    warnings = []
    for benchmark, record in found.items():
        if record["baseline_from"] == "verdict record":
            named = ", ".join(record["baselines_recorded"])
            several = (f"; the record's first pairs and confirmations on it name {named}"
                       if len(record["baselines_recorded"]) > 1 else "")
            warnings.append(f"The track's run logs are not on this machine, so the current {benchmark} baseline is "
                            f"taken from the verdict record ({record['baseline']}{several}), and nothing checks the "
                            "record against the run logs. Run verdict on the machine that answered the pairs before a "
                            "default flips (#144)")
        if record["other_baseline_pairs"]:
            warnings.append(f"Recorded pairs of {arm} alongside other baselines do not count: "
                            f"{', '.join(record['other_baseline_pairs'])}. A new baseline starts every count again "
                            "(#143)")
        if record["other_text_pairs"]:
            warnings.append(f"Recorded pairs of {arm} on other context text do not count with its current text: "
                            f"{', '.join(record['other_text_pairs'])}. Each context text is a variant of its own "
                            "(#143)")
        warnings += record["warnings"]
    if settings and any(line["variant"]["variant_settings"] is None for line in recorded):
        warnings.append("Some of the variant's recorded pairs record no settings, so their settings are not compared "
                        "with the others' (#130)")
    aa_path = _aa_record_path(results, model.model)
    return {
        "kind": "variant-verdict", "arm": arm, "verdict": outcome, "reason": reason,
        "checked_against_run_logs": logs, "variant_settings": next(iter(settings.values()), None),
        "benchmarks": {benchmark: {key: record[key] for key in ("baseline", "baseline_from", "contexts_sha256",
                                                                "missing", "other_baseline_pairs", "other_text_pairs")}
                       for benchmark, record in found.items()},
        "first": roles["first"], "confirmation": roles["confirmation"],
        "record": {"path": path.name, "sha256": digest(path) if path.exists() else None},
        "aa_record": {"path": aa_path.name, "sha256": digest(aa_path) if aa_path.exists() else None},
        "warnings": warnings,
        "note": "The default-change rule in CLAUDE.md, applied to the pairs compare recorded in the track's verdict "
                "record: the variant passes only when its first pair and its confirmation each show a gain on at least "
                "one benchmark whose 95% interval excludes zero and no loss on either whose interval excludes zero. "
                "Where an accepted A/A pair under a pair's conditions excluded zero, that pair's gain must also be "
                "larger than the largest absolute A/A difference recorded on its benchmark, the #118 repeat included "
                "(margin). A failed first pair fails the variant, whatever its confirmation shows, and so does a "
                "failed confirmation. A pair compare refused was never recorded and counts as neither, and a new "
                "baseline starts the count again (#143). checked_against_run_logs is true only when the track's run "
                "logs were on this machine for both benchmarks; the pull request that flips a default cites this "
                "output with it true.",
    }


def _prepared_summary(prepared: dict) -> dict:
    """What built the contexts: the commit, whether the tree was clean, and the settings a variant changed."""
    provenance = prepared.get("provenance") or {}
    return {"commit": provenance.get("commit"), "dirty": provenance.get("dirty"),
            "worktree_sha256": provenance.get("worktree_sha256"), "overrides": provenance.get("overrides", {}),
            "variant_settings": prepared.get("variant_settings"),
            "tokenizer": prepared.get("tokenizer"), "context_rule": prepared["context_rule"],
            "contexts_matching_saved_run": prepared.get("contexts_matching_saved_run")}


def _server_version(result: dict) -> str | None:
    return _version_of(result["answer_model"])


def _check_rule_budget(label: str, arm: str, budget: int | None, tokenizer: str | None) -> None:
    """Refuse an arm prepared outside the default-change rule's 4K budget, unless it is a reference arm (#125).

    The rule in CLAUDE.md reads paired runs only at the 4K budget:
    ``RULE_BUDGET`` tokens counted by ``RULE_TOKENIZER``, as the registered run
    packed them. Only the plain and full-context reference arms, which are not
    PRME settings, are paired with the defaults at any budget; PRME's defaults
    and variants, and any arm name the harness does not make, are not. A
    result published before its summary recorded the tokenizer (#125) names
    none, and every such result used the registered one.
    """
    if arm in REFERENCE_ARMS:
        return
    if budget != RULE_BUDGET:
        packed = "no context budget" if budget is None else f"a context budget of {budget!r} tokens"
    elif tokenizer not in (None, RULE_TOKENIZER):
        packed = f"its budget counted by the {tokenizer!r} tokenizer"
    else:
        return
    raise ValueError(f"{label}, {arm}, was prepared with {packed}. The default-change rule in CLAUDE.md reads PRME's "
                     f"arms only at the 4K budget ({RULE_BUDGET:,} {RULE_TOKENIZER} tokens), so compare refuses any "
                     "other: prepare the variant without changing packing.token_budget, packing.overhead_tokens or "
                     "packing.tokenizer (#125).")


def _context_changes(before: dict, after: dict) -> dict:
    """How many questions the two results asked on different context text (``differing``), and what shows it.

    Each row's ``context_sha256`` covers the whole capture file, which
    includes a random receipt id, so two preparations never share one. Rows
    reported since #125 also carry ``context_text_sha256``, the hash of the
    text alone, and when every row on both sides does, ``shown_by`` is
    ``text hashes``. For results published before, the text is shown to be the
    same only when both read ``one preparation``, or both preparations
    reproduce the ``saved run``'s 2026-09-23 context on every question, which
    holds only while the defaults still do; otherwise both values are None.
    Both results must list the same questions in order, once each.

    A variant prepared since #139 also replayed the defaults at its own
    commit, and each of its rows carries that text's hash
    (``defaults_text_sha256``). ``changed_by_other_code`` counts the
    questions on which those defaults read other text than the before side:
    what changed between the two preparations, such as code on main or on
    the variant's branch (``_other_code_changes``). At 0, every question in
    ``differing`` differs by the variant's settings alone. It is None when
    the after side's rows do not carry the hash or the before side's rows
    carry no text hashes.
    """
    rows = [result["rows"] for result in (before, after)]
    hashed = [[_has_hash(row, "context_text_sha256") for row in side] for side in rows]
    if any(any(side) and not all(side) for side in hashed):
        raise ValueError("A result has context text hashes on some of its rows only")
    replayed = [_has_hash(row, "defaults_text_sha256") for row in rows[1]]
    if any(replayed) and not all(replayed):
        raise ValueError("A result has hashes of the defaults' context text on some of its rows only")
    if all(map(all, hashed)):
        other = _other_code_changes([row["context_text_sha256"] for row in rows[0]],
                                    [row.get("defaults_text_sha256") for row in rows[1]])
        return {"differing": sum(old["context_text_sha256"] != new["context_text_sha256"]
                                 for old, new in zip(*rows, strict=True)), "shown_by": "text hashes",
                "changed_by_other_code": other}
    unsplit = {"changed_by_other_code": None}
    if before.get("prepared_sha256") is not None and before["prepared_sha256"] == after.get("prepared_sha256"):
        return {"differing": 0, "shown_by": "one preparation", **unsplit}
    if all((result.get("prepared") or {}).get("contexts_matching_saved_run") == len(result["rows"])
           for result in (before, after)):
        return {"differing": 0, "shown_by": "saved run", **unsplit}
    return {"differing": None, "shown_by": None, **unsplit}


def _has_hash(record: dict, key: str) -> bool:
    return isinstance(record.get(key), str) and bool(record[key])


def _other_code_changes(texts: list, defaults: list) -> int | None:
    """On how many questions the defaults replayed at a variant's commit read other text than its baseline (#139).

    ``texts`` are the baseline's context text hashes and ``defaults`` the
    variant's hashes of the defaults' text, question by question in
    registered order. None when a hash is missing on either side, since
    nothing then shows it. ``run_pair`` reads them from the two manifests
    and ``compare`` from the two results' rows.
    """
    if not all(isinstance(value, str) and value for value in (*texts, *defaults)):
        return None
    return sum(old != new for old, new in zip(texts, defaults, strict=True))


def _other_code_refusal(changed: int, total: int, before: str, before_commit: str | None, after: str,
                        after_commit: str | None, inputs: tuple[str, ...] = ()) -> ValueError:
    """Why a variant's pair is refused when the defaults at its commit read other text than its baseline (#139).

    ``inputs`` names what else the two manifests show was prepared differently, when ``run_pair`` knows it.
    """
    also = f" They were also prepared with different {', '.join(inputs)}." if inputs else ""
    return ValueError(
        f"On {changed} of {total} questions the defaults at the commit that prepared {after} "
        f"({after_commit or 'an unrecorded commit'}) read other context text than {before}, prepared at "
        f"{before_commit or 'an unrecorded commit'}.{also} That difference comes from what changed between the two "
        "preparations, such as code on main or on the variant's branch, the dependencies or the saved run, and the "
        "pair would credit it to the variant. If main changed the defaults, record a new baseline at a commit on "
        "main that includes the change, and prepare the variant again, under a new name with the same settings, "
        "from a commit that descends from it; alongside that baseline its pairs count from zero (#143). If the "
        "variant's own branch changes the defaults, put that change behind its settings (#139).")


def _check_other_code(before: dict, after: dict, changed: int | None, prepared: dict[str, dict]) -> None:
    """Refuse a variant's pair whose split shows other code's changes, or that no split covers (#139).

    ``changed`` is ``_context_changes``'s ``changed_by_other_code``. A pair
    without the split is accepted only when it started before
    ``DEFAULTS_REPLAYED_SINCE``, as every variant pair on record did, and
    ``compare`` warns that nothing splits it. Any later one was answered on
    a preparation, or by a checkout, without #139.
    """
    if changed is None:
        started = after.get("started_at")
        if started and datetime.fromisoformat(started) >= DEFAULTS_REPLAYED_SINCE:
            raise ValueError(f"The {after['arm']} result's rows do not record the defaults' context text at the "
                             "variant's commit, so nothing shows which of its contexts other code changed. It started "
                             f"at or after {DEFAULTS_REPLAYED_SINCE.isoformat()}, so it was prepared or answered by a checkout "
                             "without #139. Prepare the variant again from a checkout with #139, under a new name with "
                             "the same settings, and answer its pair there (#139).")
    elif changed:
        raise _other_code_refusal(changed, len(after["rows"]), before["arm"], prepared["before"].get("commit"),
                                  after["arm"], prepared["after"].get("commit"))


def _same_inputs(before: dict, after: dict) -> bool:
    """Whether two results sent the reader and judge the same inputs: context text, budget, code and server.

    The context text must be shown to be the same on every question
    (``_context_changes``). The modules that send, check and judge the calls
    must match too; this module's own digest changes with every edit to it,
    so it is left out.
    """
    return (before.get("context_budget") == after.get("context_budget")
            and _context_changes(before, after)["differing"] == 0
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


def _baseline_record(before: dict, data: Path | None) -> list[dict] | None:
    """Every complete baseline of the defaults on the before result's track and benchmark (``_complete_baselines``).

    Read from the track's run logs under ``data``, or the result's own track,
    when ``compare`` runs, so a baseline recorded after a pair was answered
    counts (#127). None for a GPT-5.4 result: that track answers no baseline
    of the defaults.
    """
    if before["kind"] != "ollama-answer-result":
        return None
    if before.get("benchmark") not in gate.GATE_BENCHMARKS:
        raise ValueError(f"The results name an unknown benchmark, {before.get('benchmark')!r}")
    return _complete_baselines(_track_data(before, data), before["benchmark"])


def _track_data(result: dict, data: Path | None) -> Path:
    """Where compare reads an Ollama result's track records: ``data``, or else the result's own track."""
    return data or data_root(ollama_answers.AnswerModel(model=result["model"]))


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


def _paired_questions(old: dict[str, dict], new: dict[str, dict], keys, *, samples: int, clustered: bool) -> dict:
    """``_paired`` over the questions ``keys`` of two results' rows, each keyed by question id."""
    return _paired([(old[key].get("cluster"), float(old[key]["correct"]), float(new[key]["correct"])) for key in keys],
                   samples=samples, clustered=clustered)


def _accuracy(before: dict, after: dict, *, samples: int = BOOTSTRAP_SAMPLES) -> dict:
    """The paired accuracy difference of two results on every question and its 95% interval, as compare gives it.

    The verdict step recomputes a tracked record's numbers with it from the
    published results the record names, so an edited line is refused (#144).
    """
    old, new = ({row["question_id"]: row for row in result["rows"]} for result in (before, after))
    if list(old) != list(new):
        raise ValueError("The results answer different questions")
    return _paired_questions(old, new, list(old), samples=samples,
                             clustered=before.get("benchmark") in CONVERSATION_INTERVALS)


def _excludes_zero(interval: list | None) -> bool | None:
    """Whether a 95% interval excludes zero; None when there is none (fewer than two LoCoMo conversations)."""
    return None if interval is None else interval[0] > 0 or interval[1] < 0


def compare(before: dict, after: dict, *, samples: int = BOOTSTRAP_SAMPLES, data: Path | None = None,
            results: Path | None = None) -> dict:
    """Pair two complete answer results on the same questions, answered by the same reader and judge.

    ``before`` is the baseline. On the Ollama track the two results must be the
    sides of one ``run_pair`` pair (#129). The one exception is a repeat: two
    answer runs of the defaults, each answered on its own, that sent the reader
    and judge the same inputs (``_same_inputs``), which keeps the #118
    measurement checkable. Unless the two results are a repeat, the pair's
    before side must be the current baseline of the defaults, prepared at its
    recorded commit (``_check_current_baseline``), read from the track's run
    logs under ``data`` (by default the before result's track) when compare
    runs, so a pair answered before a newer baseline completed no longer
    counts (#127). ``baseline`` reports every complete baseline and which is
    current. A variant's pair must be its first pair or its confirmation
    alongside its baseline on its context text, a confirmation with the
    first pair's settings too, on record as complete in the same run logs,
    and ``variant`` lists every arm and pair of the variant
    (``_variant_record``, #130, #143), with warnings for pairs that could
    hide a result or count apart from it (``_variant_warnings``). A variant's pair
    must also have been answered under the model identity, answer settings,
    failure policy, Ollama server version and context budget of an A/A check
    in the track's A/A record under ``results`` (by default this checkout's),
    on both benchmarks, and ``aa_check`` names that check and every other A/A
    pair under those conditions (``_aa_coverage``, #137). Every Ollama
    server version the two results recorded
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
    verdict: such a pair is invalid. A result of PRME's arms, the defaults or
    a variant, must have been prepared at the default-change rule's 4K budget
    (``_check_rule_budget``); the plain and full-context reference arms
    compare at any budget (#125). ``contexts`` reports how many questions the
    two sides asked on different context text, and what shows it
    (``_context_changes``). For a variant prepared since #139 it also counts
    the questions on which the defaults replayed at the variant's commit
    read other text than the before side (``changed_by_other_code``), and a
    variant's pair with any is refused, since that change would be credited
    to the variant; so is a variant's pair without the count that started
    after ``DEFAULTS_REPLAYED_SINCE`` (``_check_other_code``).
    """
    for side, result in (("before", before), ("after", after)):
        if not result.get("complete") or "sample" in result or result.get("kind", "").endswith("-sample"):
            raise ValueError(f"The {side} result is not a complete answer run")
    for field in ("kind", "benchmark", "model", "registration_sha256"):
        if before.get(field) != after.get(field):
            raise ValueError(f"The results differ in {field}")
    for side, result in (("before", before), ("after", after)):
        _check_rule_budget(f"The {side} result", result["arm"], result.get("context_budget"),
                           (result.get("prepared") or {}).get("tokenizer"))
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
    if len(old) != len(before["rows"]) or len(new) != len(after["rows"]):
        raise ValueError("A result lists a question more than once")
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
        return _paired_questions(old, new, keys, samples=samples, clustered=clustered)

    categories = sorted({row["question_type"] for row in old.values()})
    prepared = {side: result.get("prepared") or {} for side, result in (("before", before), ("after", after))}
    contexts = _context_changes(before, after)
    same_contexts = pair is not None and contexts["differing"] == 0
    repeat = both_baselines and (same_contexts or _same_inputs(before, after))
    if pair is None and before["kind"] == "ollama-answer-result" and not repeat:
        raise ValueError(unpaired)
    recorded = _baseline_record(before, data)
    # A repeat measures two runs of the defaults, so it may pair an earlier baseline with a later one.
    if recorded is not None and not repeat:
        _check_current_baseline(before["arm"], before["benchmark"], recorded, prepared["before"].get("commit"))
    if is_variant(after["arm"]):
        _check_other_code(before, after, contexts["changed_by_other_code"], prepared)
    variant = aa_check = None
    if recorded is not None and pair is not None and is_variant(after["arm"]):
        track = _track_data(before, data)
        variant = _variant_record(track, before["benchmark"], pair)
        aa_check = _aa_coverage(track, results or RESULTS, before["model"], _pair_conditions(before, after))
    warnings = [f"The {side} contexts were prepared from a tree with uncommitted changes"
                for side, value in prepared.items() if value.get("dirty")]
    if variant is not None:
        warnings += _variant_warnings(variant) + _aa_warnings(aa_check)
    if contexts["differing"] is None:
        warnings.append("Nothing shows which questions the two sides asked on different context text: a result was "
                        "published before rows carried text hashes (#125)")
    # With the same text on every question, the code that built it changed nothing the reader saw, and when the
    # defaults at the after side's commit read the before side's text, every difference is the after side's settings.
    if prepared["before"].get("commit") != prepared["after"].get("commit") and not repeat \
            and contexts["differing"] != 0 and contexts["changed_by_other_code"] != 0:
        counted = ""
        if contexts["differing"] is not None:
            counted = (f": {contexts['differing']} of {len(old)} questions read different context text, from the "
                       "after side's settings and from any other change between the commits")
            if is_variant(after["arm"]):
                # Other code's changes to a variant's contexts are refused above, and so is a pair without the
                # count that started since #139, so this is a variant pair from before it.
                counted += (". The variant was prepared before prepare replayed the defaults at its commit, so "
                            "nothing shows how many of them other code changed (#139)")
        warnings.append("The contexts were prepared from different commits, so code changes are part of the "
                        f"difference{counted}")
    if known and None in versions:
        warnings.append("The Ollama server version was not recorded at every start of these runs")
    if both_baselines and not repeat:
        warnings.append("Both results are baselines of the defaults, but they are not shown to have sent the same "
                        "context text, budget, answering code and server version, so this is not a repeat")
    # The verdict step recomputes a recorded pair's numbers with the same function (#144).
    accuracy = _accuracy(before, after, samples=samples)
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
        "baseline": None if recorded is None else {
            "current": recorded[-1]["arm"] if recorded else None, "complete": recorded,
            "note": "The baselines of the defaults with a complete answer run of their own on this benchmark, in "
                    "the order those runs finished, from the track's run logs when compare ran. The last is the "
                    "current baseline. A pair counts only with it as the before side, so a pair can be compared "
                    "again only while its baseline is current; a repeat of the defaults may pair any two (#127)."},
        "variant": None if variant is None else {
            **variant,
            "note": "Every arm and pair with this variant's settings (variant_settings) or its context text on "
                    "every question (contexts_sha256): every such preparation and pair on the track's run logs when "
                    "compare ran, under any arm name and alongside any baseline. Only the pairs alongside this pair's "
                    "baseline that read its context text count together (#143): a new baseline starts every count "
                    "again, and other context text, such as after a code change, makes a new variant with its own "
                    "first pair and confirmation. The first pair is the first of them to complete, and the "
                    "confirmation the next to complete that started after it and repeats the first pair's settings; "
                    "the default-change rule in CLAUDE.md reads only those two, compare refuses any other pair, and a "
                    "failed first pair or confirmation fails the variant. A complete pair that is invalid under the 1% "
                    "limit, and a pair given up, not published or unfinished, count as neither. Settings recorded "
                    "only as this checkout reads an arm's overrides, for pairs started before #130, are not compared. "
                    "dropped lists the pairs with the variant's settings or its context text, alongside any baseline, "
                    "that started before this one finished and never completed, other than for a final failure; "
                    "other_identities lists the complete pairs alongside this baseline with the variant's settings or "
                    "its context text that differ in the other, and what differs, which never count with this pair; "
                    "other_baselines lists the complete pairs with the variant's settings or its context text "
                    "alongside other baselines, and whether each of those baselines prepared the same defaults' "
                    "context text as this one (same_defaults_text, None when unknown): where it did, the defaults did "
                    "not change, and those pairs ran the same test; "
                    "related lists the other variants that change any of the same settings, each with its own first "
                    "pair and confirmation; unknown lists the variant pairs that record neither settings nor contexts "
                    "(#130, #143)."},
        "aa_check": None if aa_check is None else {
            **aa_check,
            "note": "The A/A check this pair relies on, on each benchmark: the first A/A pair in the track's A/A "
                    "record that was answered under the same model identity, answer settings, failure policy, Ollama "
                    "server version and context budget as this pair and that compare accepted. The default-change "
                    "rule in CLAUDE.md reads a variant's pair only under the conditions of an A/A check on both "
                    "benchmarks, so compare refuses any other, and a later A/A pair never replaces the check. "
                    "other_pairs lists every other A/A pair recorded under these conditions, accepted or refused, in "
                    "the order they finished. If any A/A pair under them excludes zero, a variant's gain on that "
                    "benchmark must also be larger than the largest absolute A/A difference measured so far there "
                    "(#137), which the verdict step applies (#144)."},
        "server_versions": _sorted_versions(versions),
        # Each side's retries and unscored questions under the amended policy; None under the registered one.
        "failure_policy": None if outcomes is None else {
            "id": before_policy, "sha256": before["failure_policy"]["sha256"], **outcomes},
        "prepared": prepared, "warnings": warnings,
        "contexts": {"questions": len(old), **contexts,
                     "note": "differing counts the questions the two sides asked on different context text. "
                             "shown_by names what shows it: each row's context_text_sha256 (text hashes), or, for "
                             "results published before rows carried it (#125), both results reading one "
                             "preparation or both reproducing every saved 2026-09-23 context (saved run). Both are "
                             "None when nothing shows it. A variant prepared since #139 also replayed the defaults at "
                             "its own commit, and changed_by_other_code counts the questions on which those defaults "
                             "read other text than the before side: what changed between the two preparations, such "
                             "as code on main or on the variant's branch. compare refuses a variant's pair with any, "
                             "so at 0 every differing question differs by the variant's settings alone. It is None "
                             "for any other after side, when the before side's rows carry no text hashes, and for "
                             "the variant pairs that started before #139, which compare accepts with a warning."},
        "accuracy": accuracy,
        "categories": {category: paired([key for key, row in old.items() if row["question_type"] == category])
                       for category in categories},
        "gained": [key for key in old if not old[key]["correct"] and new[key]["correct"]],
        "lost": [key for key in old if old[key]["correct"] and not new[key]["correct"]],
        "repeat": None if not repeat else {
            "changed_verdicts": accuracy["wins"] + accuracy["losses"],
            "interval_excludes_zero": _excludes_zero(interval),
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
    async def answer(job: tuple[_Side, dict], attempt: Path, semaphore: asyncio.Semaphore) -> dict:
        side, question = job
        entry = side.entries[question["question_id"]]
        context = _context(side.prepared, entry)
        if amended:
            scored = await _ask_amended(ask, client, semaphore, benchmark, question, context, attempt)
        else:
            reader = await ask(client, semaphore, prompt=study.reader_prompt(benchmark, question, context),
                               limit=READER_LIMIT, path=attempt / READER_CALLS[0])
            judged = await ask(client, semaphore, prompt=study.judge_prompt(benchmark, question, reader["text"]),
                               limit=JUDGE_LIMIT, path=attempt / JUDGE_CALLS[0])
            scored = _registered_scored(attempt, judged)
        return _row(question, entry, scored)

    await drain_pending(label, [(side.answers / "execution" / question["question_id"], (side, question))
                                for question in questions for side in sides], concurrency, answer, ledger)


async def drain_pending(label: str, jobs: list[tuple[Path, object]], concurrency: int, handle,
                        ledger: Ledger | None, *, done: str = "answered", every: int = 25) -> None:
    """Run ``handle`` for each job whose question folder is still pending, as a new attempt, under the retry rules.

    Each job is the question's folder and what ``handle(job, attempt,
    semaphore)`` needs; it returns the row written as the attempt's
    ``result.json``. The first failure stops new jobs, and each failure is
    written as the attempt's ``failure.json`` with whether a later run may ask
    it again (``_retryable``). The lenient judge (#96) grades saved answers
    through this loop too, so both follow one set of rules.
    """
    pending = iter([(folder, job) for folder, job in jobs if _status(folder) == "pending"])
    errors: list[dict] = []
    finished = 0

    async def worker(semaphore):
        nonlocal finished
        while not errors:
            item = next(pending, None)
            if item is None:
                return
            folder, job = item
            attempt = None
            try:
                attempt = _next_attempt(folder)
                _write_json(attempt / "result.json", await handle(job, attempt, semaphore))
                finished += 1
                if finished % every == 0:
                    spent = "" if ledger is None else "; cost so far ${:.3f}".format(
                        sum(charge["charge"] for charge in ledger.update()["entries"].values()) / 1e9)
                    print(f"{label}: {finished} {done} this run{spent}", file=sys.stderr, flush=True)
            except Exception as exc:
                error = {"question_id": folder.name, "attempt": attempt.name if attempt else None,
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
    usage = new_usage(paid=model is None)
    execution = (answers or folder) / "execution"
    for question in questions:
        qid = question["question_id"]
        answer, found = recorded_attempts(execution / qid)
        failures += found
        if answer is not None:
            rows.append(_verified_row(verify, tally, benchmark, question, folder, prepared, entries[qid], answer,
                                      usage, amended=policy is not None))
    chosen = [entries[question["question_id"]] for question in questions]
    result = {
        "kind": "gpt54-baseline-result" if model is None else "ollama-answer-result", "arm": arm,
        "benchmark": benchmark, "model": MODEL if model is None else model.model,
        "registration_sha256": digest(study.REG), "prepared_sha256": digest(folder / "prepared.json"),
        "context_budget": prepared["context_budget"], "context_rule": prepared["context_rule"],
        **coverage(rows, failures, len(questions)),
        **({} if policy is None else {"failure_policy": {**failure_amendment(),
                                                         **_outcome_counts(rows, len(questions))}}),
        "cost": provider_cost(ledger, usage, "arm"),
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


def _row(question: dict, entry: dict, scored: dict, *, reported: bool = False) -> dict:
    """A question's result row: the question, the context it was asked on, and how its answer was scored.

    ``context_sha256`` is the capture file's hash, which covers the retrieval
    receipt's random request id. A ``reported`` row also carries
    ``context_text_sha256``, the hash of the text alone (#125), and a
    variant's, ``defaults_text_sha256``, the hash of the defaults' text at the
    variant's commit (#139); the attempt's own record does not, so answers
    recorded before them still verify.
    """
    text = {}
    if reported:
        text["context_text_sha256"] = entry["text_sha256"]
        if "defaults_text_sha256" in entry:
            text["defaults_text_sha256"] = entry["defaults_text_sha256"]
    # "correct" stays where rows have always had it; the scoring's own fields follow.
    return {"question_id": question["question_id"], "question_type": question["question_type"],
            "cluster": question.get("conversation_id") or sha(question["haystack_sessions"]),
            "correct": scored["correct"], "context_sha256": entry["sha256"], **text,
            "context_tokens": entry["context_tokens"], "retrieval_seconds": entry["retrieval_seconds"], **scored}


def _verified_row(verify, tally, benchmark: str, question: dict, folder: Path, prepared: dict, entry: dict,
                  attempt: Path, usage: dict, *, amended: bool = False) -> dict:
    """The attempt's result row, after checking it against its context, calls and verdict, as reported.

    With ``amended``, every call the amended failure policy made is checked,
    retries and truncated answers included, and so is the order they came in.
    The reported row adds the context text's hash, which ``_context`` has
    just checked (#125), and a variant's the manifest's hash of the defaults'
    text at its commit (#139), so answers recorded before them gain them too.
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
    count_calls(usage, calls, tally)
    return _row(question, entry, scored, reported=True)


# Shared by the answer runs' report and the lenient judge's (#96) ------------------

def new_usage(*, paid: bool) -> dict:
    """Empty provider token counts; GPT-5.4 calls also count reasoning tokens and what they cost."""
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "successful_calls": 0,
             "http_attempts": 0, "http_status_counts": Counter()}
    if paid:
        usage.update(reasoning_tokens=0, observed_nanodollars=0)
    return usage


def count_calls(usage: dict, calls: list[dict], tally) -> None:
    """Add verified calls to ``usage``: their provider tokens (``tally``), attempts and HTTP statuses."""
    for value in calls:
        tally(usage, value["response"])
        usage["successful_calls"] += 1
        usage["http_attempts"] += value["attempts"]
        usage["http_status_counts"].update(value["verified_http_statuses"])


def recorded_attempts(folder: Path) -> tuple[Path | None, list[dict]]:
    """A question's attempt with a result, if any, and its failures, each marked replaced when a result exists."""
    records = [(attempt, _attempt_record(attempt)) for attempt in _attempts(folder)]
    answered = next((attempt for attempt, record in records if record["kind"] == "result"), None)
    return answered, [{key: value for key, value in record.items() if key != "kind"} | {"replaced": answered is not None}
                      for _, record in records if record["kind"] == "failure"]


def coverage(rows: list[dict], failures: list[dict], total: int) -> dict:
    """Whether every question has a verified result, and the failures on the way, as every result reports them."""
    return {"complete": len(rows) == total, "total": total, "completed": len(rows),
            "final_failures": sum(not failure["retryable"] and not failure["replaced"] for failure in failures),
            "unreplaced_failures": sum(not failure["replaced"] for failure in failures), "failures": failures}


def provider_cost(ledger: Ledger | None, usage: dict, what: str) -> dict:
    """What the calls cost: the ledger's charges on GPT-5.4, which must cover the verified calls, or nothing on Ollama."""
    if ledger is None:
        return {"usd": 0, "note": "Local Ollama server; no API charge. A :cloud model's calls count against the "
                                  "Ollama account's usage limits instead."}
    charges = list(ledger.update()["entries"].values())
    if sum(entry["charge"] for entry in charges) < usage["observed_nanodollars"]:
        raise ValueError(f"The spending ledger records less than the verified calls cost; it is not this {what}'s "
                         "complete ledger")
    return {"usd": sum(entry["charge"] for entry in charges) / 1e9,
            "unsettled_reservations": sum(not entry["settled"] for entry in charges),
            "ledger_sha256": digest(ledger.path),
            "note": f"Every provider attempt of this {what}, including failed ones; an unsettled request keeps its "
                    "full reservation."}


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
    return {**gate._latency(values),
            "note": "Time to rank and pack each context during prepare; not model-response latency."}


# CLI ------------------------------------------------------------------------

def _confirm_spend(parser: argparse.ArgumentParser, arm: str, benchmark: str, max_usd: float, *,
                   kind: str = "arm") -> None:
    """Paid runs need the owner at an interactive terminal, so nothing unattended can start one.

    The owner types ``arm``, the name of what the cap pays for: an arm, or a lenient judge pass (#96).
    """
    if not sys.stdin.isatty():
        parser.error("run makes paid calls; start it from an interactive terminal so the owner can confirm")
    typed = input(f"This can spend up to ${max_usd:,.2f} on {arm} {benchmark} with {MODEL}. "
                  f"Type the {kind} name, {arm}, to confirm: ")
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
    if command == "verdict" and model is None:
        parser.error("verdict is for the ollama provider, whose variants the default-change rule reads")
    # Commands that read or record results rather than answering an arm.
    reading = {"calibrate", "compare", "record-aa-check"}
    if command in reading:
        given = [flag for flag, value in (("arm", args.arm), ("--benchmark", args.benchmark),
                                          ("--set", args.overrides), ("--variant", args.variant),
                                          ("--max-usd", args.max_usd), ("--sample", args.sample),
                                          ("--archive", args.archive), ("--baseline", args.baseline)) if value]
        if given:
            parser.error(f"{command} takes none of: {', '.join(given)}")
    paired = command in {"compare", "record-aa-check"}
    if paired != (args.before is not None and args.after is not None) or (
            not paired and (args.before or args.after)):
        parser.error("compare takes --before and --after, the result files to pair, and record-aa-check the two "
                     "sides of a published A/A pair; nothing else does")
    if (command == "run-pair") != (args.baseline is not None):
        parser.error("run-pair takes --baseline, the current baseline of the defaults to answer alongside the arm; "
                     "nothing else does")
    if command in reading:
        return "", "", {}
    if command == "verdict":
        # It reads both benchmarks' recorded pairs, and answers nothing.
        given = [flag for flag, value in (("--benchmark", args.benchmark), ("--set", args.overrides or None),
                                          ("--max-usd", args.max_usd), ("--sample", args.sample),
                                          ("--archive", args.archive)) if value is not None]
        if given:
            parser.error(f"verdict takes none of: {', '.join(given)}")
        if args.arm != "prme" or args.variant is None:
            parser.error("verdict takes the prme arm and --variant, the variant to decide on, over both benchmarks")
        try:
            return arm_name(args.arm, args.variant), "", {}
        except ValueError as exc:
            parser.error(str(exc))
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
    parser.add_argument("command", choices=["prepare", "estimate", "calibrate", "run", "run-pair", "compare",
                                            "record-aa-check", "verdict"])
    parser.add_argument("arm", nargs="?", choices=list(ARMS),
                        help="Every command but calibrate, compare and record-aa-check needs an arm; verdict takes "
                             "prme with --variant")
    parser.add_argument("--benchmark", choices=gate.GATE_BENCHMARKS,
                        help="Required for plain and prme arms; full-context covers LoCoMo only. verdict reads both "
                             "benchmarks and takes none")
    parser.add_argument("--provider", choices=["openai", "ollama"], default="openai",
                        help=f"Reader and judge: the registered GPT-5.4 (default, paid) or {ollama_answers.MODEL} "
                             f"through the local Ollama server at {ollama_answers.ENDPOINT} (a separate track, no "
                             "API cost)")
    parser.add_argument("--variant", metavar="NAME",
                        help="prme arm on the ollama provider: a named variant of the defaults, prepared with --set "
                             "and answered with run-pair as the arm prme-NAME. Its preparation also replays the "
                             "defaults at the same commit, and run-pair and compare refuse it while those defaults "
                             "read other text than the baseline (#139). verdict decides on it from the pairs compare "
                             "recorded (#144).")
    parser.add_argument("--baseline", metavar="ARM",
                        help="run-pair only: the current baseline of the defaults (prme or prme@<commit>, the one "
                             "whose own answer run completed last), answered again alongside the arm. With the prme "
                             "arm and no --variant, the baseline is paired with itself: the A/A check.")
    parser.add_argument("--archive", type=Path,
                        help="Saved 2026-09-23 run archive (default: the main checkout's data/gpt54-comparison-v1)")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="prepare only: packing.token_budget or packing.overhead_tokens for a plain arm, or "
                             "the settings a prme variant changes (any the evidence gate accepts). run-pair and "
                             "compare refuse a prme variant that changes the 4K budget or its tokenizer (#125).")
    parser.add_argument("--max-usd", type=float,
                        help="run on the openai provider only: the owner-approved spending cap for this arm")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="run and run-pair on the ollama provider only: a smoke check of the first N questions "
                             "of each category")
    parser.add_argument("--before", type=Path,
                        help="compare and record-aa-check only: the defaults side's result file")
    parser.add_argument("--after", type=Path,
                        help="compare and record-aa-check only: the other side's result file from the same pair, "
                             "or a later baseline's for a repeat. record-aa-check adds a published A/A pair to the "
                             "track's A/A record (#137). The compare command records a variant's first pair or "
                             "confirmation, or an A/A pair, in the track's verdict record, which verdict reads "
                             "(#144).")
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
        # A pair the default-change rule reads is recorded for the verdict step (#144).
        try:
            recorded = record_pair_verdict(result, args.before, args.after)
        except (OSError, ValueError) as exc:
            parser.error(f"compare accepted the pair, but did not record it for the verdict: {exc}")
        record = _verdict_record_path(RESULTS, result["model"])
        # The record belongs to this checkout, while the run log is shared by every worktree.
        shown = record.relative_to(study.ROOT) if record.is_relative_to(study.ROOT) else record
        if recorded is not None:
            line, added = recorded
            print(f"{'Recorded' if added else 'Already recorded'} {_pair_named(line)} as {line['role']} in {shown}"
                  + ("; commit it with the pair's published results (#144)" if added else " (#144)"),
                  file=sys.stderr, flush=True)
        elif _role_of(result) is not None:
            print("Not recorded for the verdict: the track's run logs are not on this machine, so only the machine "
                  "that answered the pair records it (#144)", file=sys.stderr, flush=True)
    elif args.command == "verdict":
        try:
            found = verdict(arm, model=model)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(found, indent=2))
    elif args.command == "record-aa-check":
        try:
            entry = record_aa_check(args.before, args.after)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(entry, indent=2))
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
            # A variant's preparation replays twice, once with its settings and once with the defaults (#139).
            reported = 0 if done < reported else reported
            if done // 100 > reported // 100:
                print(f"{done} questions replayed", file=sys.stderr, flush=True)
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
        try:
            _check_current_baseline(args.baseline, benchmark, _complete_baselines(data, benchmark))
        except ValueError as exc:
            parser.error(str(exc))
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


def _announce(model: ollama_answers.AnswerModel, role: str = "Reader and judge") -> None:
    print(f"{role}: {model.model} through {model.endpoint}. A :cloud model sends the prompts to "
          "Ollama's hosted service and uses the account's usage limits.", file=sys.stderr, flush=True)


def _summary(result: dict) -> dict:
    """A result without its rows and failures, for the terminal."""
    return {key: value for key, value in result.items() if key not in {"rows", "failures"}}


if __name__ == "__main__":
    main()

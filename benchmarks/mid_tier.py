"""A mid-tier benchmark: all of LoCoMo and a fixed LongMemEval-S sample, in under an hour.

The full benchmarks take too long to check a change against: a LongMemEval-S
build alone is about 15 hours. The mid tier sits between the fast-cycle checks
and the twice-weekly full run. It builds packs through ``ingest()`` with the
DeepSeek extraction model on the local Ollama server, replays retrieval over
them with the offline evidence gate, and writes one report with the gate's
headline measures, their intervals, and the calls, tokens and wall time of each
stage. It makes no reader or judge call, so it measures evidence coverage, not
answers.

    python -m benchmarks.mid_tier run --label main --out /tmp/mid-main
    python -m benchmarks.mid_tier run --label try --out /tmp/mid-try --set packing.token_budget=8192
    python -m benchmarks.mid_tier compare /tmp/mid-main /tmp/mid-try

Choices, and why:

- The extraction cache lives in one stable folder, shared by every label, so a
  rerun with the same prompt and model replays every response. A change to
  retrieval or packing then costs the pack builds' ingestion and the gate; only
  a change to the extraction request calls the model.
- The sample is ``benchmarks/slices/mid-v1.json``. LoCoMo is whole. Ollama's
  cloud served about 25 requests at once and refused the rest, and its
  completion rate (about 1,300 to 1,700 tokens per second in all) bounds a
  cold build, so the LongMemEval-S questions are a prefix of a fixed draw
  order that takes the types in turn: 12 questions, two of each type, by
  default. ``select`` derives the file from the dataset and ``check`` confirms
  that the committed file still follows from it.
- The builds are one set of packs for each benchmark, under ``<label>-locomo``
  and ``<label>-longmemeval``. Ingestion is CPU-bound Python (a warm pack
  takes minutes), so each set is built by several processes at the same time,
  four packs each (``extracted_packs build --shard``), that divide the cap on
  live calls. Each
  benchmark's gate starts when its build ends, as separate processes (LoCoMo's
  split by conversation), so a stage reports its own time.
- ``compare`` pairs two reports question by question with the gate's paired
  bootstrap, and for LoCoMo also resamples whole conversations, as the answer
  track does. It refuses reports over different questions, datasets or
  tokenizers.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
SAMPLE_PATH = REPO / "benchmarks" / "slices" / "mid-v1.json"
SAMPLE_KIND = "prme-mid-tier-sample"
REPORT_KIND = "prme-mid-tier-report"
COMPARISON_KIND = "prme-mid-tier-comparison"
SCHEMA_VERSION = 1
BENCHMARKS = ("locomo", "longmemeval")
SEED = 20261019
PER_TYPE = 6
LME_QUESTIONS = 12
MODEL = "deepseek-v4.1-flash:cloud"
# Ollama's cloud refused requests beyond about 25 at once (a 429) on 2026-10-19.
IN_FLIGHT = 24
# Packs one build process holds at most: ingestion is CPU-bound Python, so more packs than this slow each other.
PACKS_PER_PROCESS = 4
GATE_SHARDS = 3
# Seconds to wait before building again after packs paused (a rate limit), and how many builds to try.
RETRY_WAIT = 300.0
ATTEMPTS = 4
SAMPLES = 2000
# Fewer groups than this give no honest bootstrap interval.
MIN_GROUPS = 5
FIELDS = ["question_id", "category", "group", "annotated", "packed", "top_25", "all_packed", "all_returned",
          "projected"]
# An evidence turn this near the top of the candidates counts for the rank measure.
TOP = 25
LIMITS = [
    "The LongMemEval-S sample is small (a few questions per type), so its intervals are wide. It flags a type "
    "that collapses, not a small change. LoCoMo carries most of the signal, and its conversation-level "
    "interval rests on 10 conversations.",
    "It measures evidence coverage, not answers: no reader or judge runs. The projected accuracy is a planning "
    "estimate from the 2026-09-23 GPT-5.4 run, not an answer score.",
    "It is a screen between the fast-cycle checks and the twice-weekly full run. A change that looks good here "
    "still needs the full benchmarks, and the default-change rule still needs the answer track.",
    "A cold run's model output is close to repeatable but not exact, so two cold builds of the same code differ "
    "a little. Compare a change with a baseline that replayed the same cache.",
]


# The sample ---------------------------------------------------------------------------------------------------


def _rank(seed: int, question_id: str) -> str:
    return hashlib.sha256(f"{seed}:{question_id}".encode()).hexdigest()


def choose(pool: dict[str, list[str]], *, seed: int = SEED, per_type: int = PER_TYPE) -> list[dict]:
    """The draw order of a stratified sample: each type's questions in the order of a seeded hash, types in turn.

    Any prefix of the result has the types in turn too, so a smaller sample keeps every type that
    its size allows. Hashing the ID with the seed gives the same draw on every Python version.
    """
    types = sorted(pool)
    if not types:
        raise ValueError("No question types to draw from")
    drawn = {}
    for kind in types:
        ids = sorted(set(pool[kind]), key=lambda question_id: _rank(seed, question_id))
        if len(ids) < per_type:
            raise ValueError(f"Only {len(ids)} {kind} questions to draw {per_type} from")
        drawn[kind] = ids[:per_type]
    return [{"question_id": drawn[kind][index], "type": kind} for index in range(per_type) for kind in types]


def longmemeval_pool() -> tuple[dict[str, list[str]], dict[str, int], str]:
    """Annotated, non-abstention LongMemEval-S questions by type, the pool's sizes and the dataset digest.

    Abstention questions cite no evidence, so the gate cannot score their evidence.
    """
    from benchmarks.diagnostics import extracted_packs
    from benchmarks.diagnostics.product_packing import _harness

    gpt54 = _harness()
    kinds = {row["question_id"]: row["question_type"] for row in gpt54.question_rows("longmemeval")}
    pool: dict[str, list[str]] = defaultdict(list)
    for unit in extracted_packs.longmemeval_units():
        if unit.annotated:
            pool[kinds[unit.unit_id]].append(unit.unit_id)
    return dict(pool), {kind: len(ids) for kind, ids in sorted(pool.items())}, gpt54.lme.DATASET_SHA256


def locomo_conversations() -> tuple[dict[str, list[str]], str]:
    """Each LoCoMo conversation's question IDs, in dataset order, and the dataset digest."""
    from benchmarks.diagnostics.product_packing import _harness

    gpt54 = _harness()
    questions: dict[str, list[str]] = defaultdict(list)
    for row in gpt54.question_rows("locomo"):
        questions[row["conversation_id"]].append(row["question_id"])
    return dict(questions), gpt54.LOCOMO_SHA


def select() -> dict:
    """Derive the sample file from the datasets and the seed."""
    pool, sizes, lme_sha = longmemeval_pool()
    conversations, locomo_sha = locomo_conversations()
    return {
        "kind": SAMPLE_KIND, "schema_version": SCHEMA_VERSION, "name": "mid-v1", "seed": SEED,
        "rules": {
            "locomo": "every conversation and every question",
            "longmemeval": f"{PER_TYPE} annotated, non-abstention questions of each type, ordered by "
                           "sha256 of 'seed:question_id'; the order below takes the types in turn, so the "
                           f"first N questions are stratified. The default is the first {LME_QUESTIONS}.",
            "default_longmemeval_questions": LME_QUESTIONS,
        },
        "datasets": {"locomo": locomo_sha, "longmemeval": lme_sha},
        "locomo": {"conversations": sorted(conversations),
                   "questions": {name: len(ids) for name, ids in sorted(conversations.items())}},
        "longmemeval": {"pool": sizes, "order": choose(pool)},
    }


def load_sample(path: Path = SAMPLE_PATH) -> dict:
    """The committed sample with the digest of its file, after checking its shape."""
    raw = path.read_bytes()
    sample = json.loads(raw)
    if sample.get("kind") != SAMPLE_KIND or sample.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a version {SCHEMA_VERSION} mid-tier sample")
    order = sample["longmemeval"]["order"]
    ids = [item["question_id"] for item in order]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path} repeats a LongMemEval-S question")
    types = sorted({item["type"] for item in order})
    if [item["type"] for item in order] != [kind for _ in range(len(order) // len(types)) for kind in types]:
        raise ValueError(f"{path} does not take the question types in turn")
    return {**sample, "file_sha256": hashlib.sha256(raw).hexdigest()}


def plan_sample(sample: dict, locomo_questions: dict[str, list[str]], *, lme_questions: int = LME_QUESTIONS,
                conversations: list[str] | None = None) -> dict:
    """The questions and packs of one run: all of LoCoMo, or the named conversations of a trial, and a prefix."""
    order = sample["longmemeval"]["order"]
    if not 1 <= lme_questions <= len(order):
        raise ValueError(f"Choose from 1 to {len(order)} LongMemEval-S questions")
    chosen = conversations if conversations else sample["locomo"]["conversations"]
    unknown = sorted(set(chosen) - locomo_questions.keys())
    if unknown:
        raise ValueError(f"Unknown LoCoMo conversations: {', '.join(unknown)}")
    lme = [item["question_id"] for item in order[:lme_questions]]
    plan = {
        "name": sample["name"], "sample_sha256": sample["file_sha256"], "trial": bool(conversations),
        "benchmarks": {
            "locomo": {"units": list(chosen), "questions": [q for name in chosen for q in locomo_questions[name]]},
            "longmemeval": {"units": lme, "questions": lme},
        },
        "longmemeval_types": {item["question_id"]: item["type"] for item in order[:lme_questions]},
    }
    plan["sha256"] = hashlib.sha256(json.dumps({"benchmarks": plan["benchmarks"]}, sort_keys=True).encode()).hexdigest()
    return plan


def slice_document(name: str, locomo: dict | None = None, longmemeval: dict | None = None) -> dict:
    """The gate's ``--slice`` file for some of the plan's questions (``dev_slice`` format)."""
    from benchmarks.diagnostics import dev_slice

    empty = {"units": [], "questions": []}
    return {"kind": dev_slice.KIND, "schema_version": dev_slice.SCHEMA_VERSION, "name": name,
            "benchmarks": {"locomo": locomo or empty, "longmemeval": longmemeval or empty}}


def shard_conversations(sizes: dict[str, int], shards: int) -> list[list[str]]:
    """Conversations in at most ``shards`` groups of similar question counts, largest first."""
    bins: list[list[str]] = [[] for _ in range(max(1, shards))]
    loads = [0] * len(bins)
    for name in sorted(sizes, key=lambda conversation: (-sizes[conversation], conversation)):
        index = loads.index(min(loads))
        bins[index].append(name)
        loads[index] += sizes[name]
    return [sorted(group) for group in bins if group]


# Intervals ----------------------------------------------------------------------------------------------------


def wilson(count: int, total: int, z: float = 1.959964) -> list[float] | None:
    """The Wilson score interval of a share; it stays honest at a handful of questions."""
    if total <= 0:
        return None
    share = count / total
    scale = 1 + z * z / total
    centre = (share + z * z / (2 * total)) / scale
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / scale
    return [max(0.0, centre - half), min(1.0, centre + half)]


def cluster_interval(values: list[float], groups: list[str], *, samples: int = SAMPLES, seed: int = 42):
    """A percentile bootstrap of the pooled mean that resamples whole groups, or None below MIN_GROUPS groups."""
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for value, group in zip(values, groups, strict=True):
        totals[group][0] += value
        totals[group][1] += 1
    keys = sorted(totals)
    if len(keys) < MIN_GROUPS:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        total = count = 0
        for key in rng.choices(keys, k=len(keys)):
            total += totals[key][0]
            count += totals[key][1]
        means.append(total / count)
    means.sort()
    return [means[int(0.025 * (samples - 1))], means[int(0.975 * (samples - 1))]]


# Measures -----------------------------------------------------------------------------------------------------


def compact_row(row: dict) -> dict:
    """What a gate row contributes to the report: the fields the measures and the comparison need."""
    from benchmarks.diagnostics.product_packing import _all_within

    evidence = row["evidence"]
    question_id = row["question_id"]
    group = question_id.rsplit("-q", 1)[0] if row["benchmark"] == "locomo" else question_id
    return {
        "question_id": question_id, "category": row["category"], "group": group,
        "annotated": None if evidence is None else evidence["annotated"],
        "packed": None if evidence is None else evidence["packed"],
        "top_25": None if evidence is None else sum(rank is not None and rank <= TOP
                                                     for rank in evidence["ranks"].values()),
        "all_packed": None if evidence is None else evidence["all_packed"],
        "all_returned": None if evidence is None else _all_within(evidence, math.inf),
        "projected": row["projected_correct"],
    }


def _share(rows: list[dict], key: str, clustered: bool) -> dict:
    count = sum(bool(row[key]) for row in rows)
    result = {"count": count, "of": len(rows), "share": count / len(rows) if rows else None,
              "interval_95_questions": wilson(count, len(rows))}
    if clustered:
        result["interval_95_conversations"] = cluster_interval([float(bool(row[key])) for row in rows],
                                                               [row["group"] for row in rows])
    result["interval_95"] = result["interval_95_conversations" if clustered else "interval_95_questions"]
    return result


def _mean(rows: list[dict], value: Callable[[dict], float], clustered: bool) -> dict:
    values = [value(row) for row in rows]
    result = {"mean": sum(values) / len(values) if values else None,
              "interval_95_questions": cluster_interval(values, [row["question_id"] for row in rows])}
    if clustered:
        result["interval_95_conversations"] = cluster_interval(values, [row["group"] for row in rows])
    result["interval_95"] = result["interval_95_conversations" if clustered else "interval_95_questions"]
    return result


def measure(rows: list[dict], *, clustered: bool) -> dict:
    """The headline measures over some questions: shares and means with 95% intervals.

    ``clustered`` (LoCoMo) adds the interval that resamples whole conversations and makes it the headline one.
    """
    annotated = [row for row in rows if row["annotated"] is not None]
    return {
        "questions": len(rows), "annotated_questions": len(annotated),
        "all_evidence_packed": _share(annotated, "all_packed", clustered),
        "all_evidence_returned": _share(annotated, "all_returned", clustered),
        "evidence_recall": _mean(annotated, lambda row: row["packed"] / row["annotated"], clustered),
        "evidence_in_top_25": _mean(annotated, lambda row: row["top_25"] / row["annotated"], clustered),
        "projected_accuracy": _mean(rows, lambda row: row["projected"], clustered),
    }


def benchmark_measures(rows: list[dict], *, clustered: bool) -> dict:
    return {"overall": measure(rows, clustered=clustered),
            "categories": {category: measure([row for row in rows if row["category"] == category],
                                             clustered=clustered)
                           for category in sorted({row["category"] for row in rows})}}


# The report ---------------------------------------------------------------------------------------------------


def stage_totals(stages: dict[str, dict]) -> dict:
    """Live calls, tokens and cache hits over the build stages."""
    builds = [stage for name, stage in stages.items() if name.startswith("build-")]
    calls = sum(stage["calls"] for stage in builds)
    hits = sum(stage["cache_hits"] for stage in builds)
    return {"calls": calls, "prompt_tokens": sum(stage["prompt_tokens"] for stage in builds),
            "completion_tokens": sum(stage["completion_tokens"] for stage in builds), "cache_hits": hits,
            "cache": "warm" if calls == 0 else ("cold" if hits == 0 else "partly warm")}


def assemble_report(*, label: str, plan: dict, gate_rows: dict[str, list[dict]], stages: dict[str, dict],
                    total: dict, code: dict, model: dict, settings: dict, projection: dict, tokenizer: str,
                    command: list[str]) -> dict:
    """One report from the gate's rows, the stages' records and the run's identity."""
    rows = {name: [compact_row(row) for row in gate_rows[name]] for name in BENCHMARKS}
    for name in BENCHMARKS:
        expected = plan["benchmarks"][name]["questions"]
        found = [row["question_id"] for row in rows[name]]
        if sorted(found) != sorted(expected):
            raise ValueError(f"The {name} gate did not replay exactly the sample's {len(expected)} questions")
        rank = {question: index for index, question in enumerate(expected)}
        rows[name].sort(key=lambda row: rank[row["question_id"]])
    return {
        "kind": REPORT_KIND, "schema_version": SCHEMA_VERSION, "label": label, "created_at": now(),
        "command": command,
        "sample": {"name": plan["name"], "file_sha256": plan["sample_sha256"], "plan_sha256": plan["sha256"],
                   "trial": plan["trial"],
                   "locomo_conversations": plan["benchmarks"]["locomo"]["units"],
                   "locomo_questions": len(plan["benchmarks"]["locomo"]["questions"]),
                   "longmemeval_questions": plan["benchmarks"]["longmemeval"]["questions"],
                   "longmemeval_types": plan["longmemeval_types"]},
        "settings": settings, "code": code, "model": model, "projection": projection, "tokenizer": tokenizer,
        "stages": stages, "total": {**total, **stage_totals(stages)},
        "measures": {name: benchmark_measures(rows[name], clustered=name == "locomo") for name in BENCHMARKS},
        "limits": LIMITS, "fields": FIELDS,
        "rows": {name: [[row[field] for field in FIELDS] for row in rows[name]] for name in BENCHMARKS},
    }


def report_rows(report: dict, benchmark: str) -> dict[str, dict]:
    """A report's compact rows by question ID."""
    fields = report["fields"]
    return {values[0]: dict(zip(fields, values, strict=True)) for values in report["rows"][benchmark]}


def dump_json(value, depth: int = 0) -> str:
    """JSON with objects spread over lines and lists of plain values (a question's row) on one line each."""
    pad = "  " * (depth + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{pad}{json.dumps(key)}: {dump_json(item, depth + 1)}" for key, item in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + "  " * depth + "}"
    if isinstance(value, list):
        if all(not isinstance(item, dict | list) for item in value):
            return json.dumps(value, allow_nan=False)
        return "[\n" + ",\n".join(pad + dump_json(item, depth + 1) for item in value) + "\n" + "  " * depth + "]"
    return json.dumps(value, allow_nan=False)


def _percent(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.1f}" if signed else f"{value * 100:.1f}%"


def _interval(interval: list[float] | None, signed: bool = False) -> str:
    if interval is None:
        return "n/a"
    low, high = (_percent(value, signed).rstrip("%") for value in interval)
    return f"{low} to {high}"


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)} min {rest:02.0f} s" if minutes else f"{rest:.0f} s"


def _tokens(count: int) -> str:
    return f"{count / 1e6:.2f} M" if count >= 1e5 else f"{count:,}"


def _load(stage: dict) -> str:
    load = stage.get("load_average_1m")
    return f"{load['start']:.2f} / {load['end']:.2f}" if load else "n/a"


def stage_table(report: dict) -> list[str]:
    lines = ["| Stage | Wall time | Live calls | Prompt tokens | Completion tokens | Cache hits | Load, 1 min (start / end) |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for name, stage in report["stages"].items():
        calls = f"{stage['calls']:,}" if "calls" in stage else ""
        prompt = _tokens(stage["prompt_tokens"]) if "calls" in stage else ""
        completion = _tokens(stage["completion_tokens"]) if "calls" in stage else ""
        hits = f"{stage['cache_hits']:,}" if "calls" in stage else ""
        lines.append(f"| {name} | {_clock(stage['seconds'])} | {calls} | {prompt} | {completion} | {hits} | "
                     f"{_load(stage)} |")
    total = report["total"]
    lines.append(f"| **Whole run** | **{_clock(total['seconds'])}** | {total['calls']:,} | "
                 f"{_tokens(total['prompt_tokens'])} | {_tokens(total['completion_tokens'])} | "
                 f"{total['cache_hits']:,} | {_load(total)} |")
    return lines


def report_markdown(report: dict) -> str:
    sample, code, total = report["sample"], report["code"], report["total"]
    model = report["model"]
    lines = [
        f"# Mid tier: {report['label']}", "",
        f"Commit `{code['commit']}`{' (working tree changed)' if code['dirty'] else ''}, "
        f"engine source tree `{code['src_tree']}`. Settings: "
        f"{json.dumps(report['settings']['overrides']) if report['settings']['overrides'] else 'the defaults'}. "
        f"Extraction: `{model['model']}` on Ollama {', '.join(model['server_versions'])}.", "",
        f"Sample `{sample['name']}`{' (trial: not the mid tier)' if sample['trial'] else ''}: "
        f"{len(sample['locomo_conversations'])} LoCoMo conversations ({sample['locomo_questions']:,} questions) and "
        f"{len(sample['longmemeval_questions'])} LongMemEval-S questions. Extraction was **{total['cache']}** "
        f"({total['calls']:,} live calls, {total['cache_hits']:,} cache hits; the cache held "
        f"{total['cache_entries']['start']:,} responses before and {total['cache_entries']['end']:,} after). "
        f"The run took **{_clock(total['seconds'])}** on a machine with {report['settings']['machine']['cpus']} "
        "cores.", "",
        *stage_table(report), "",
        "Stages overlap: each benchmark's gate starts when its build ends, and the builds run together.", ""]
    for name, title in (("locomo", "LoCoMo"), ("longmemeval", "LongMemEval-S")):
        measures = report["measures"][name]
        clustered = name == "locomo"
        lines += [f"## {title}", "",
                  "Intervals are 95%: `questions` treats questions as independent"
                  + ("; `conversations` resamples whole conversations and is the one to trust." if clustered else
                     ", and with this few questions they are wide."), "",
                  "| Category | Questions | All evidence packed | 95% questions | "
                  + ("95% conversations | " if clustered else "")
                  + "Evidence recall | Evidence in top 25 | Projected accuracy | All evidence among candidates |",
                  "|---|---:|---:|---:|" + ("---:|" if clustered else "") + "---:|---:|---:|---:|"]
        for category, item in [("**All**", measures["overall"]), *sorted(measures["categories"].items())]:
            packed = item["all_evidence_packed"]
            lines.append(
                f"| {category} | {item['questions']:,} | {packed['count']}/{packed['of']} ({_percent(packed['share'])}) | "
                f"{_interval(packed['interval_95_questions'])} | "
                + (f"{_interval(packed['interval_95_conversations'])} | " if clustered else "")
                + f"{_percent(item['evidence_recall']['mean'])} | {_percent(item['evidence_in_top_25']['mean'])} | "
                f"{_percent(item['projected_accuracy']['mean'])} | "
                f"{item['all_evidence_returned']['count']}/{item['all_evidence_returned']['of']} |")
        overall = measures["overall"]
        lines += ["", f"All {title} questions, each measure with its intervals:", "",
                  "| Measure | Value | 95% questions |" + (" 95% conversations |" if clustered else ""),
                  "|---|---:|---:|" + ("---:|" if clustered else "")]
        for measure_name, key in (("All evidence packed", "all_evidence_packed"),
                                  ("All evidence among the candidates", "all_evidence_returned"),
                                  ("Evidence recall", "evidence_recall"), ("Evidence in top 25", "evidence_in_top_25"),
                                  ("Projected accuracy", "projected_accuracy")):
            item = overall[key]
            value = item["share"] if "share" in item else item["mean"]
            lines.append(f"| {measure_name} | {_percent(value)} | {_interval(item['interval_95_questions'])} |"
                         + (f" {_interval(item['interval_95_conversations'])} |" if clustered else ""))
        lines.append("")
    lines += ["## Limits", "", *[f"- {limit}" for limit in report["limits"]], ""]
    return "\n".join(lines)


# Comparison ---------------------------------------------------------------------------------------------------


METRICS = {
    "all_evidence_packed": ("annotated", lambda row: float(bool(row["all_packed"]))),
    "evidence_recall": ("annotated", lambda row: row["packed"] / row["annotated"]),
    "evidence_in_top_25": ("annotated", lambda row: row["top_25"] / row["annotated"]),
    "all_evidence_returned": ("annotated", lambda row: float(bool(row["all_returned"]))),
    "projected_accuracy": ("all", lambda row: row["projected"]),
}


def check_comparable(baseline: dict, candidate: dict) -> None:
    """Both reports must measure the same questions the same way, or their pairs mean different things."""
    for side, report in (("baseline", baseline), ("candidate", candidate)):
        if report.get("kind") != REPORT_KIND or report.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"The {side} is not a version {SCHEMA_VERSION} mid-tier report")
    checks = {
        "questions": lambda report: report["sample"]["plan_sha256"],
        "datasets": lambda report: report["settings"]["datasets"],
        "tokenizers": lambda report: report["tokenizer"],
        "projection constants": lambda report: report["projection"],
        "extraction models": lambda report: report["model"]["model"],
        "row fields": lambda report: report["fields"],
    }
    for label, value in checks.items():
        if value(baseline) != value(candidate):
            raise ValueError(f"The reports use different {label}")


def comparison_warnings(baseline: dict, candidate: dict) -> list[str]:
    warnings = []
    for side, report in (("baseline", baseline), ("candidate", candidate)):
        if report["total"]["calls"]:
            warnings.append(
                f"The {side} extracted live ({report['total']['calls']:,} calls, {report['total']['cache']}). "
                "Model output is close to repeatable but not exact, so part of any difference can be that noise, "
                "not the change. Compare against a baseline that replayed the same cache.")
        if report["code"]["dirty"]:
            warnings.append(f"The {side} ran on a working tree with uncommitted changes.")
    if baseline["model"]["server_versions"] != candidate["model"]["server_versions"]:
        warnings.append("The Ollama server versions differ: "
                        f"{baseline['model']['server_versions']} against {candidate['model']['server_versions']}.")
    if baseline["model"].get("digest") != candidate["model"].get("digest"):
        warnings.append("The extraction model identities differ.")
    return warnings


def _paired(pairs: list[tuple[float, float]], groups: list[str] | None, samples: int) -> dict:
    from benchmarks.compare_evidence import paired_statistics
    from benchmarks.diagnostics.compare_public_captures import cluster_statistics

    result = paired_statistics(pairs, samples=samples)
    questions_ok = result["queries"] >= MIN_GROUPS
    result["interval_95_questions"] = result.pop("interval_95") if questions_ok else None
    excludes = (result["interval_95_questions"] is not None
                and (result["interval_95_questions"][0] > 0 or result["interval_95_questions"][1] < 0))
    if groups is not None:
        clustered = cluster_statistics(pairs, groups, samples=samples)
        interval = clustered["interval_95"] if len(set(groups)) >= MIN_GROUPS else None
        result["interval_95_conversations"] = interval
        result["conversations"] = clustered["groups"]
        excludes = excludes and interval is not None and (interval[0] > 0 or interval[1] < 0)
    result["excludes_zero"] = bool(excludes)
    return result


def compare_reports(baseline: dict, candidate: dict, *, samples: int = SAMPLES) -> dict:
    """Pair two reports question by question: the change in each measure, overall and by category.

    A LoCoMo change counts as clear of zero only when the question-level and the conversation-level
    intervals both exclude it, as the repo's default-change rule reads them.
    """
    check_comparable(baseline, candidate)
    benchmarks = {}
    for name in BENCHMARKS:
        old, new = report_rows(baseline, name), report_rows(candidate, name)
        if old.keys() != new.keys():
            raise ValueError(f"The {name} reports hold different questions")
        for key, row in old.items():
            if (row["category"], row["annotated"], row["group"]) != (new[key]["category"], new[key]["annotated"],
                                                                      new[key]["group"]):
                raise ValueError(f"{key}: category or evidence annotation differs between the reports")
        clustered = name == "locomo"

        def pair(keys: list[str], metric: str) -> dict:
            value = METRICS[metric][1]
            return _paired([(value(old[key]), value(new[key])) for key in keys],
                           [old[key]["group"] for key in keys] if clustered else None, samples)

        every = list(old)
        annotated = [key for key in every if old[key]["annotated"] is not None]
        pools = {"annotated": annotated, "all": every}
        categories = sorted({row["category"] for row in old.values()})
        benchmarks[name] = {
            "questions": len(every), "annotated_questions": len(annotated),
            "metrics": {metric: pair(pools[pool], metric) for metric, (pool, _) in METRICS.items()},
            "categories": {category: pair([key for key in annotated if old[key]["category"] == category],
                                          "all_evidence_packed") for category in categories},
            "gained_all_evidence": [key for key in annotated if not old[key]["all_packed"] and new[key]["all_packed"]],
            "lost_all_evidence": [key for key in annotated if old[key]["all_packed"] and not new[key]["all_packed"]],
        }

    def identity(report: dict) -> dict:
        return {"label": report["label"], "commit": report["code"]["commit"], "src_tree": report["code"]["src_tree"],
                "dirty": report["code"]["dirty"], "overrides": report["settings"]["overrides"],
                "created_at": report["created_at"], "cache": report["total"]["cache"],
                "seconds": report["total"]["seconds"], "calls": report["total"]["calls"]}

    return {"kind": COMPARISON_KIND, "schema_version": SCHEMA_VERSION, "bootstrap_samples": samples,
            "bootstrap_seed": 42, "baseline": identity(baseline), "candidate": identity(candidate),
            "same_engine_source": baseline["code"]["src_tree"] == candidate["code"]["src_tree"],
            "warnings": comparison_warnings(baseline, candidate), "benchmarks": benchmarks,
            "stages": {"baseline": baseline["stages"], "candidate": candidate["stages"]}}


def comparison_markdown(result: dict) -> str:
    base, cand = result["baseline"], result["candidate"]
    lines = [
        "# Mid tier comparison", "",
        f"Baseline `{base['label']}` (commit `{base['commit'][:8]}`, {base['cache']}) against candidate "
        f"`{cand['label']}` (commit `{cand['commit'][:8]}`, {cand['cache']}). "
        f"Candidate settings: {json.dumps(cand['overrides']) if cand['overrides'] else 'the defaults'}. "
        + ("The engine source is identical." if result["same_engine_source"] else
           "The engine source differs."), ""]
    if result["warnings"]:
        lines += [*[f"- Warning: {warning}" for warning in result["warnings"]], ""]
    for name, title in (("locomo", "LoCoMo"), ("longmemeval", "LongMemEval-S")):
        bench = result["benchmarks"][name]
        clustered = name == "locomo"
        lines += [f"## {title}", "",
                  f"{bench['questions']:,} questions, {bench['annotated_questions']:,} with annotated evidence. "
                  "Intervals are 95% of the paired change"
                  + ("; a change is clear of zero only when the questions and the conversations intervals both "
                     "exclude zero." if clustered else "; with this few questions they are wide."), "",
                  "| Measure | Baseline | Candidate | Change (points) | 95% questions | "
                  + ("95% conversations | " if clustered else "") + "Wins / losses / ties | Clear of zero |",
                  "|---|---:|---:|---:|---:|" + ("---:|" if clustered else "") + "---:|---|"]
        for metric, stat in bench["metrics"].items():
            lines.append(_stat_line(metric.replace("_", " "), stat, clustered))
        lines += ["", "All evidence packed, by category:", "",
                  "| Category | Baseline | Candidate | Change (points) | 95% questions | "
                  + ("95% conversations | " if clustered else "") + "Wins / losses / ties | Clear of zero |",
                  "|---|---:|---:|---:|---:|" + ("---:|" if clustered else "") + "---:|---|"]
        for category, stat in bench["categories"].items():
            lines.append(_stat_line(category, stat, clustered))
        for key, title_ in (("gained_all_evidence", "Gained all evidence"), ("lost_all_evidence", "Lost all evidence")):
            ids = bench[key]
            shown = ", ".join(ids[:20]) + (f", and {len(ids) - 20} more" if len(ids) > 20 else "")
            lines += ["", f"{title_} ({len(ids)}): {shown or 'none'}"]
        lines.append("")
    lines += ["## Stage times", "", "| Stage | Baseline | Candidate |", "|---|---:|---:|"]
    for stage in result["stages"]["candidate"]:
        before = result["stages"]["baseline"].get(stage)
        after = result["stages"]["candidate"][stage]
        lines.append(f"| {stage} | {_clock(before['seconds']) if before else 'n/a'} | {_clock(after['seconds'])} |")
    lines += ["", f"Whole run: {_clock(base['seconds'])} against {_clock(cand['seconds'])}. "
                  "The mid tier is a screen: see the limits in either report.", ""]
    return "\n".join(lines)


def _stat_line(label: str, stat: dict, clustered: bool) -> str:
    cells = [label, _percent(stat["before"]), _percent(stat["after"]), _percent(stat["delta"], signed=True),
             _interval(stat["interval_95_questions"], signed=True)]
    if clustered:
        cells.append(_interval(stat["interval_95_conversations"], signed=True))
    cells += [f"{stat['wins']} / {stat['losses']} / {stat['ties']}", "yes" if stat["excludes_zero"] else "no"]
    return "| " + " | ".join(cells) + " |"


# Running ------------------------------------------------------------------------------------------------------


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_1m() -> float:
    return os.getloadavg()[0]


class Clock:
    """Wall time and the one-minute load average at the start and end of a stage."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.started_at = now()
        self.started = time.perf_counter()
        self.load_start = load_1m()
        print(f"[{self.started_at[11:19]}] {label}: start, load {self.load_start:.2f}", file=sys.stderr, flush=True)

    def finish(self, **extra) -> dict:
        seconds = time.perf_counter() - self.started
        load_end = load_1m()
        print(f"[{now()[11:19]}] {self.label}: done in {_clock(seconds)}, load {load_end:.2f}",
              file=sys.stderr, flush=True)
        return {"started_at": self.started_at, "seconds": seconds,
                "load_average_1m": {"start": self.load_start, "end": load_end}, **extra}


async def gate_shard(benchmark: str, packs: Path, slice_path: Path, output: Path, set_args: list[str],
                     log: Path) -> None:
    """One gate process over some of a build's questions; its output is the gate's JSON report."""
    command = [sys.executable, "-m", "benchmarks.diagnostics.product_packing", "gate", "--benchmark", benchmark,
               "--packs", str(packs), "--slice", str(slice_path), "--output", str(output)]
    for item in set_args:
        command += ["--set", item]
    await _run(command, log, f"The {benchmark} gate over {slice_path.name}")


def cache_entries(folder: Path) -> int:
    """Saved model responses in an extraction cache folder."""
    return sum(1 for _ in folder.glob("*/*.json")) if folder.is_dir() else 0


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def in_flight_shares(cap: int, packs: list[int]) -> list[int]:
    """Each process's share of the live-call cap: a call for every pack when they all fit, else a proportion.

    The shares are fixed, so a process whose packs finish early leaves its share idle. Keep the packs of a
    run within the cap (the default sample does: 22 packs against 24 calls) and this never matters.
    """
    total = sum(packs)
    if total <= cap:
        return list(packs)
    shares = [max(1, cap * count // total) for count in packs]
    # Hand out what rounding down left, to the processes with the most packs behind each call.
    for index in sorted(range(len(packs)), key=lambda i: -packs[i] / shares[i]):
        if sum(shares) >= cap:
            break
        shares[index] += 1
    return shares


async def build_shard(benchmark: str, label: str, root: Path, units: list[str], shard: tuple[int, int], *,
                      set_args: list[str], model: str, cache_dir: Path, in_flight: int, jobs: int,
                      log: Path) -> None:
    """One build process over its share of the units (``extracted_packs build --shard``); packs share the label."""
    command = [sys.executable, "-m", "benchmarks.diagnostics.extracted_packs", "build", "--benchmark", benchmark,
               "--label", label, "--root", str(root), "--model", model, "--cloud", "--jobs", str(jobs),
               "--shard", f"{shard[0]}/{shard[1]}", "--cache-dir", str(cache_dir), "--in-flight", str(in_flight)]
    for unit in units:
        command += ["--unit", unit]
    for item in set_args:
        command += ["--set", item]
    await _run(command, log, f"The {benchmark} build {shard[0]}/{shard[1]}")


async def _run(command: list[str], log: Path, what: str) -> None:
    with log.open("wb") as out:
        process = await asyncio.create_subprocess_exec(*command, cwd=REPO, stdout=out, stderr=asyncio.subprocess.STDOUT)
        try:
            code = await process.wait()
        finally:
            # A failed sibling or an interrupt cancels this; the child must not run on unattended.
            if process.returncode is None:
                process.terminate()
    if code:
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-15:])
        raise RuntimeError(f"{what} failed with exit code {code}:\n{tail}")


async def build_stage(benchmark: str, label: str, units: list[str], *, build_fn, root: Path, set_args: list[str],
                      model: str, cache_dir: Path, in_flight: int, shards: int, attempts: int, retry_wait: float,
                      out: Path) -> dict:
    """Build every unit's pack in ``shards`` processes at once; build again after a wait while some stay paused.

    A rate limit pauses a pack at a turn whose extraction keeps failing. A build then resumes it from there.
    """
    from benchmarks.diagnostics import extracted_packs

    clock = Clock(f"build-{benchmark}")
    shards = max(1, min(shards or math.ceil(len(units) / PACKS_PER_PROCESS), len(units)))
    tries = 0
    while True:
        tries += 1
        mine = [units[index::shards] for index in range(shards)]
        await asyncio.gather(*(
            build_fn(benchmark, label, root, units, (index, shards), set_args=set_args, model=model,
                     cache_dir=cache_dir, in_flight=share, jobs=len(group),
                     log=out / "logs" / f"build-{benchmark}-{index}.log")
            for index, (group, share) in enumerate(zip(mine, in_flight_shares(in_flight, [len(g) for g in mine]),
                                                       strict=True))))
        built = extracted_packs.built_units(root / label / benchmark)
        missing = [unit for unit in units if unit not in built]
        if not missing:
            break
        if tries >= attempts:
            raise RuntimeError(f"{len(missing)} of {len(units)} {benchmark} packs are not complete after {tries} "
                               f"builds ({', '.join(missing[:5])}). Run the same command again to resume them.")
        print(f"{benchmark}: {len(missing)} packs paused; building again in {retry_wait:.0f} s",
              file=sys.stderr, flush=True)
        await asyncio.sleep(retry_wait)
    summary = extracted_packs.build_summary(root / label, benchmark)
    return clock.finish(
        packs=summary["packs"], processes=shards, turns=summary["turns"], calls=summary["calls"],
        prompt_tokens=summary["prompt_tokens"], completion_tokens=summary["completion_tokens"],
        cache_hits=summary["cache_hits"], model_seconds_summed=summary["seconds"], builds=tries,
        claims=summary["claims"])


async def gate_stage(benchmark: str, packs: Path, slices: list[Path], out: Path, set_args: list[str], *,
                     gate_fn) -> tuple[dict, list[dict], dict]:
    """The gate over a build, one process for each slice file at the same time.

    Returns the stage, the benchmark's gate rows and the first shard's report, which names the run's inputs.
    """
    clock = Clock(f"gate-{benchmark}")
    outputs = [out / f"gate-{benchmark}-{index}.json" for index in range(len(slices))]
    await asyncio.gather(*(gate_fn(benchmark, packs, path, output, set_args,
                                   out / "logs" / f"gate-{benchmark}-{index}.log")
                           for index, (path, output) in enumerate(zip(slices, outputs, strict=True))))
    reports = [json.loads(output.read_text()) for output in outputs]
    rows = [row for report in reports for row in report["rows"] if row["benchmark"] == benchmark]
    return clock.finish(shards=len(slices), questions=len(rows)), rows, reports[0]


async def run_mid_tier(*, label: str, out: Path, overrides: dict, set_args: list[str], root: Path, cache_dir: Path,
                       lme_questions: int, conversations: list[str] | None, build_shards: int | None, gate_shards: int,
                       model: str, in_flight: int, attempts: int, retry_wait: float, command: list[str],
                       build_fn=build_shard, gate_fn=gate_shard, sample_path: Path = SAMPLE_PATH,
                       locomo_questions: dict[str, list[str]] | None = None) -> dict:
    """Build both benchmarks' packs, gate each as its build ends, and write the report into ``out``."""
    from benchmarks.diagnostics.product_packing import gate_config

    if (out / "report.json").exists():
        raise ValueError(f"{out / 'report.json'} exists: use another --out for a new run")
    sample = load_sample(sample_path)
    if locomo_questions is None:
        locomo_questions, _ = locomo_conversations()
    plan = plan_sample(sample, locomo_questions, lme_questions=lme_questions, conversations=conversations)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    (out / "slices").mkdir(parents=True, exist_ok=True)

    def write_slice(name: str, **benchmarks) -> Path:
        path = out / "slices" / f"{name}.json"
        path.write_text(json.dumps(slice_document(f"{plan['name']}-{name}", **benchmarks), indent=1) + "\n")
        return path

    benchmarks = plan["benchmarks"]
    sizes = {name: len(locomo_questions[name]) for name in benchmarks["locomo"]["units"]}
    locomo_slices = [
        write_slice(f"locomo-{index}", locomo={
            "units": group, "questions": [q for name in group for q in locomo_questions[name]]})
        for index, group in enumerate(shard_conversations(sizes, gate_shards))]
    lme_slice = write_slice("longmemeval", longmemeval=benchmarks["longmemeval"])
    config = gate_config(Path("{pack}"), overrides)
    from benchmarks.retrieval_eval import provenance

    code = {key: provenance(config)[key] for key in ("commit", "dirty", "worktree_sha256")}
    code["src_tree"] = _git("rev-parse", "HEAD:src")
    entries_at_start = cache_entries(cache_dir)
    whole = Clock("whole run")
    stages: dict[str, dict] = {}

    async def one(benchmark: str, units: list[str], slices: list[Path]):
        stages[f"build-{benchmark}"] = await build_stage(
            benchmark, f"{label}-{benchmark}", units, build_fn=build_fn, root=root, set_args=set_args,
            model=model, cache_dir=cache_dir, in_flight=in_flight, shards=build_shards, attempts=attempts,
            retry_wait=retry_wait, out=out)
        stage, rows, first = await gate_stage(benchmark, root / f"{label}-{benchmark}", slices, out, set_args,
                                              gate_fn=gate_fn)
        stages[f"gate-{benchmark}"] = stage
        return rows, first

    (locomo_rows, locomo_first), (lme_rows, lme_first) = await asyncio.gather(
        one("locomo", benchmarks["locomo"]["units"], locomo_slices),
        one("longmemeval", benchmarks["longmemeval"]["units"], [lme_slice]))
    finished = whole.finish()
    ordered = {name: stages[name] for name in ("build-locomo", "build-longmemeval", "gate-locomo", "gate-longmemeval")}
    records = [json.loads((root / f"{label}-{name}" / "build.json").read_text()) for name in BENCHMARKS]
    versions = sorted({version for record in records for version in record.get("server_versions", [])})
    identity = records[0]["model"]
    model_record = {"model": identity.get("model"), "provider": identity.get("provider"),
                    "digest": identity.get("model_digest_sha256") or identity.get("manifest_digest_sha256"),
                    "server_versions": versions}
    report = assemble_report(
        label=label, plan=plan, gate_rows={"locomo": locomo_rows, "longmemeval": lme_rows}, stages=ordered,
        total={"started_at": finished["started_at"], "finished_at": now(), "seconds": finished["seconds"],
               "load_average_1m": finished["load_average_1m"],
               "cache_entries": {"start": entries_at_start, "end": cache_entries(cache_dir)}},
        code=code, model=model_record,
        settings={"overrides": overrides, "in_flight": in_flight, "build_shards": build_shards,
                  "gate_shards": len(locomo_slices),
                  "datasets": {**locomo_first["provenance"]["datasets"], **lme_first["provenance"]["datasets"]},
                  "machine": {"cpus": os.cpu_count(), "platform": platform.platform()}},
        projection={"projection": locomo_first["projection"], "baseline": locomo_first["baseline"]},
        tokenizer=locomo_first["provenance"]["engine_config"]["packing"]["tokenizer"], command=command)
    (out / "report.json").write_text(dump_json(report) + "\n")
    (out / "report.md").write_text(report_markdown(report))
    return report


# Command line -------------------------------------------------------------------------------------------------


def public_command(argv: list[str]) -> list[str]:
    """The command line without the local paths it names, so a committed report leaks none."""
    hidden = {"--out", "--root", "--cache-dir"}
    shown = ["python", "-m", "benchmarks.mid_tier"]
    for index, item in enumerate(argv):
        shown.append("<path>" if index and argv[index - 1] in hidden else item)
    return shown


def _main_checkout() -> Path:
    from benchmarks import checkout

    return checkout.main_checkout()


def _resolve_report(path: Path) -> dict:
    target = path / "report.json" if path.is_dir() else path
    return json.loads(target.read_text())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.mid_tier", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Build the packs, run the offline evidence gate and write one report")
    run.add_argument("--label", required=True, help="Names the builds (<label>-locomo, <label>-longmemeval)")
    run.add_argument("--out", type=Path, required=True, help="Folder for the report, the gate reports and logs")
    run.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                     help="Build and gate setting: a dotted key and a JSON value, as for the gate")
    run.add_argument("--lme-questions", type=int, default=LME_QUESTIONS,
                     help="The first N questions of the sample's LongMemEval-S draw order (default %(default)s)")
    run.add_argument("--conversations", help="A trial: only these LoCoMo conversations, comma separated. "
                                             "The report is then not the mid tier and compares with nothing else")
    run.add_argument("--root", type=Path, help="Where builds live (default: the main checkout's data/mid-tier-v1/builds)")
    run.add_argument("--cache-dir", type=Path,
                     help="The extraction cache, shared by every label (default: data/mid-tier-v1/extraction-cache)")
    run.add_argument("--model", default=MODEL, help="An Ollama cloud model tag (default %(default)s)")
    run.add_argument("--in-flight", type=int, default=IN_FLIGHT, help="Live model calls at once (default %(default)s)")
    run.add_argument("--build-shards", type=int,
                     help=f"Build processes for each benchmark (default: one for every {PACKS_PER_PROCESS} packs)")
    run.add_argument("--gate-shards", type=int, default=GATE_SHARDS,
                     help="LoCoMo gate processes, split by conversation (default %(default)s)")
    run.add_argument("--attempts", type=int, default=ATTEMPTS,
                     help="Builds to try when packs pause on a rate limit (default %(default)s)")
    run.add_argument("--retry-wait", type=float, default=RETRY_WAIT, help="Seconds between those builds")
    compare = commands.add_parser("compare", help="Pair two reports question by question")
    compare.add_argument("baseline", type=Path, help="A report.json, or the folder that holds it")
    compare.add_argument("report", type=Path, help="The report to compare with the baseline")
    compare.add_argument("--output", type=Path, help="Write the comparison here (.json) and beside it (.md)")
    compare.add_argument("--samples", type=int, default=SAMPLES, help="Paired bootstrap samples")
    commands.add_parser("select", help="Derive the sample file from the datasets and print it")
    commands.add_parser("check", help="Confirm that the committed sample follows from the datasets")
    args = parser.parse_args(argv)

    if args.command == "select":
        print(json.dumps(select(), indent=1))
        return
    if args.command == "check":
        committed = json.loads(SAMPLE_PATH.read_text())
        if select() != committed:
            sys.exit(f"{SAMPLE_PATH} no longer follows from the datasets and the rules; select a new sample "
                     "under a new name")
        print(f"{SAMPLE_PATH} follows from the datasets")
        return
    if args.command == "compare":
        result = compare_reports(_resolve_report(args.baseline), _resolve_report(args.report), samples=args.samples)
        markdown = comparison_markdown(result)
        if args.output:
            if args.output.suffix != ".json":
                parser.error("the output must be a .json path; the Markdown goes beside it")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
            args.output.with_suffix(".md").write_text(markdown)
        print(markdown)
        return

    from benchmarks.diagnostics.product_packing import _quiet_offline_cli, parse_overrides

    try:
        overrides = parse_overrides(args.overrides)
    except ValueError as exc:
        parser.error(str(exc))
    _quiet_offline_cli()
    data = _main_checkout() / "data" / "mid-tier-v1"
    asyncio.run(run_mid_tier(
        label=args.label, out=args.out, overrides=overrides, set_args=args.overrides,
        root=args.root or data / "builds", cache_dir=args.cache_dir or data / "extraction-cache",
        lme_questions=args.lme_questions, conversations=args.conversations.split(",") if args.conversations else None,
        build_shards=args.build_shards, gate_shards=args.gate_shards, model=args.model, in_flight=args.in_flight,
        attempts=args.attempts, retry_wait=args.retry_wait,
        command=public_command(argv if argv is not None else sys.argv[1:])))
    print((args.out / "report.md").read_text())
    print(f"Wrote {args.out / 'report.json'} ({(args.out / 'report.json').stat().st_size:,} bytes)",
          file=sys.stderr)


if __name__ == "__main__":
    main()

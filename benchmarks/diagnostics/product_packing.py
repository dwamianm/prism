"""Replay development candidates through the product packer at fixed budgets.

This offline diagnostic changes only the multi-path tier's comparator. It never
runs retrieval concurrently with its temporary comparator substitution. Source
labels are used solely after packing, and pointer-only records earn no evidence
credit. No production defaults are changed.

The ``gate`` and ``gate-compare`` subcommands are the offline evidence gate. The
gate replays the public ``retrieve()`` over copies of the saved 2026-09-23
LoCoMo and LongMemEval-S memory packs and measures the context the product
renderer actually produced: records, memory text, annotated evidence and its
rank, and the packed records that session expansion brought in. Like the saved
run's diagnostics, it counts evidence packed in any
representation, and it also reports evidence packed with its text. It makes no
reader, judge or paid API calls. Its projected accuracy is a planning estimate,
not an answer score. ``gate --plain`` measures a plain RAG reference over the
same stored turns instead of ``retrieve()``, and
``benchmarks.integrations.gpt54_baselines`` prepares those contexts for the
GPT-5.4 baseline arms.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import itertools
import json
import logging
import math
import os
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

from pydantic import BaseModel
from pydantic_settings import BaseSettings

from benchmarks.compare_evidence import paired_statistics
from benchmarks.evidence import reciprocal_rank_fusion
from prme import MemoryEngine, NodeType, PRMEConfig
from prme.config import OrganizerConfig, with_product_retrieval_defaults
from prme.retrieval.config import RANK_FUSION_ONLY_SETTINGS, WEIGHTED_ONLY_SETTINGS, PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context, reader_text
from prme.retrieval.tokenization import count_tokens


def measure(bundle, gold: set[str], config: PackingConfig) -> dict:
    """Credit complete source text actually present in content-bearing JSON."""
    context = bundle.render()
    tokens = count_tokens(context, config.tokenizer)
    if tokens != bundle.tokens_used or tokens > max(0, config.token_budget - config.overhead_tokens):
        raise ValueError("Product context does not obey its measured budget")
    # JSONL uses literal LF boundaries. Unicode NEL/line/paragraph separators
    # are valid inside JSON strings and must not split a source record.
    entries = {entry["id"]: entry for line in context.split("\n")
               if line.startswith("{") for entry in [json.loads(line)]}
    content_ids, pointer_ids, blank_ids, representations = [], [], [], {}
    for group in bundle.sections.values():
        for candidate in group:
            entry = entries[str(candidate.node.id)]
            representation = entry["representation"]
            representations[representation] = representations.get(representation, 0) + 1
            source_id = candidate.node.metadata["source_turn"]
            if representation in {"full", "prose", "structured"}:
                content = candidate.node.content
                if content not in entry["text"]:
                    raise ValueError("Content-bearing representation lost source text")
                if content.strip():
                    content_ids.append(source_id)
                else:
                    blank_ids.append(source_id)
            else:
                pointer_ids.append(source_id)
    retained = set(content_ids)
    return {
        "tokens": tokens, "context_sha256": hashlib.sha256(context.encode()).hexdigest(),
        "content_source_ids": content_ids, "pointer_source_ids": pointer_ids,
        "blank_source_ids": blank_ids,
        "representations": representations,
        "evidence_recall": len(retained & gold) / len(gold) if gold else None,
        "all_evidence_retained": gold <= retained if gold else None,
    }


def _validate_confirmation(report: dict, confirmation: dict, samples: int) -> None:
    if confirmation.get("schema_version") != 1 or report["dataset"]["split"] != "test":
        raise ValueError("Confirmation requires a version 1 plan and the test split")
    if confirmation.get("algorithm") != "multipath_score_vs_density_v1" or report["budgets"] != [2048, 4096, 8192]:
        raise ValueError("Confirmation requires the frozen score/density comparison and budgets")
    for key in ("sha256", "variant", "split", "split_seed", "selected_question_ids"):
        if report["dataset"][key] != confirmation["dataset"][key]:
            raise ValueError(f"Confirmation dataset mismatch: {key}")
    if (report["provenance"]["commit"] != confirmation["runtime_commit"]
            or report["provenance"]["dirty"]
            or report["provenance"]["engine_config"]["packing"] != confirmation["packing_config"]
            or report["budgets"] != confirmation["budgets"]
            or report["query_clock"] != "question" or report["profile"] != "raw-turns-static"
            or samples != confirmation["bootstrap_samples"]):
        raise ValueError("Confirmation runtime, configuration or sampling mismatch")
    if (hashlib.sha256(Path(inspect.getfile(pack_context)).read_bytes()).hexdigest() != confirmation["packing_module_sha256"]
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != confirmation["diagnostic_sha256"]):
        raise ValueError("Confirmation implementation hash mismatch")
    if datetime.fromisoformat(confirmation["registered_at"]) >= datetime.fromisoformat(report["started_at"]):
        raise ValueError("Confirmation plan must predate the source run")


def compare(report: dict, snapshots: Path, *, samples: int = 2000, confirmation: dict | None = None) -> dict:
    if (not report.get("complete") or report.get("errors")
            or report.get("process_exit_code") != 0):
        raise ValueError("A complete, normally exited source run without errors is required")
    if confirmation is not None:
        _validate_confirmation(report, confirmation, samples)
    elif report["dataset"]["split"] != "dev":
        raise ValueError("This exploratory comparator is restricted to the development split")
    selected = report["dataset"]["selected_question_ids"]
    rows = report["details"]
    ids = [row["question_id"] for row in rows]
    if (not selected or len(set(selected)) != len(selected) or len(set(ids)) != len(ids)
            or set(ids) != set(selected) or any("error" in row for row in rows)):
        raise ValueError("Every selected question must have one successful result")
    budgets = report["budgets"]
    if not budgets or any(not isinstance(b, int) or b < 1 for b in budgets):
        raise ValueError("Positive token budgets are required")
    config = PackingConfig.model_validate(report["provenance"]["engine_config"]["packing"])
    prepared = []
    # Reproduce every control before beginning the experimental comparison.
    for row in rows:
        ref = row["candidate_snapshot"]
        filename = hashlib.sha256(row["question_id"].encode()).hexdigest() + ".json"
        if ref["filename"] != filename:
            raise ValueError("Snapshot filename does not match question identity")
        raw = (snapshots / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("Candidate snapshot hash mismatch")
        saved = json.loads(raw)
        if saved["question_id"] != row["question_id"] or saved["packing_config"] != config.model_dump(mode="json"):
            raise ValueError("Snapshot identity or packing configuration mismatch")
        candidates = [RetrievalCandidate.model_validate(c) for c in saved["candidates"]]
        original = [c.model_dump(mode="json") for c in candidates]
        control = pack_context(candidates, config)
        if control.render() != saved["control"]["context"] or control.tokens_used != saved["control"]["tokens"]:
            raise ValueError("Baseline context does not reproduce; use the original product packer")
        if original != [c.model_dump(mode="json") for c in candidates]:
            raise ValueError("Control packing mutated the candidates")
        prepared.append((row, candidates, original))
    details = []
    for row, candidates, original in prepared:
        gold = set(row["evidence_source_ids"])
        variants = {"density": {}, "score": {}}
        for budget in budgets:
            current = config.model_copy(update={"token_budget": budget})
            variants["density"][str(budget)] = measure(pack_context(candidates, current), gold, current)
            with patch("prme.retrieval.packing.compute_str", lambda c: c.composite_score):
                bundle = pack_context(candidates, current)
            variants["score"][str(budget)] = measure(bundle, gold, current)
        if original != [c.model_dump(mode="json") for c in candidates]:
            raise ValueError("Experimental packing mutated frozen candidates")
        details.append({
            "question_id": row["question_id"], "category": row["category"],
            "evidence_source_ids": sorted(gold), "candidate_count": len(candidates),
            "candidate_snapshot_sha256": row["candidate_snapshot"]["sha256"],
            "variants": variants,
        })

    def summarize(items):
        result = {}
        for budget in budgets:
            metrics = {}
            for metric in ("evidence_recall", "all_evidence_retained"):
                pairs = [(row["variants"]["density"][str(budget)][metric],
                          row["variants"]["score"][str(budget)][metric]) for row in items]
                metrics[metric] = paired_statistics(
                    [(float(a), float(b)) for a, b in pairs if a is not None and b is not None], samples=samples,
                )
            result[str(budget)] = metrics
        return result

    result = {
        "complete": True, "baseline_reproduction_passed": True,
        "kind": "development-product-packing-comparison", "dataset": report["dataset"],
        "source_provenance": report["provenance"], "packing_config": config.model_dump(mode="json"),
        "packing_module_sha256": hashlib.sha256(Path(inspect.getfile(pack_context)).read_bytes()).hexdigest(),
        "diagnostic_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "budgets": budgets, "bootstrap_samples": samples, "bootstrap_seed": 42,
        "comparator": "composite score within the multi-path tier; all other priorities and rendering unchanged",
        "limitations": [
            "Development source-support retention only; no answer accuracy or competitive quality claim.",
            "Both variants reuse identical public retrieval candidates, scores, identities and timestamps.",
            "All response candidates are packed; the shared evaluation ranking limit is not applied here.",
            "REFERENCE and KEY_VALUE pointers receive no supporting-evidence credit.",
            "Blank source text is retained in accounting but receives no positive supporting-evidence credit.",
            "Question bootstrap intervals are descriptive; shared histories can make questions dependent.",
            "Source snapshots contain benchmark text and are stored separately from this summary.",
        ],
        "summary": summarize(details),
        "categories": {category: summarize([row for row in details if row["category"] == category])
                       for category in sorted({row["category"] for row in details})},
        "details": details,
    }
    if confirmation is not None:
        primary = result["summary"]["4096"]["evidence_recall"]
        criteria = {
            "positive_primary_gain": primary["delta"] is not None and primary["delta"] > 0,
            "positive_primary_interval": primary["interval_95"] is not None and primary["interval_95"][0] > 0,
            "nonnegative_secondary_means": all(result["summary"][str(b)]["evidence_recall"]["delta"] is not None
                and result["summary"][str(b)]["evidence_recall"]["delta"] >= 0 for b in (2048, 8192)),
            "nonnegative_primary_category_means": all(v["4096"]["evidence_recall"]["delta"] >= 0
                for v in result["categories"].values() if v["4096"]["evidence_recall"]["queries"] >= 5),
        }
        result.update(kind="test-product-packing-confirmation", confirmation=confirmation,
                      confirmation_criteria=criteria, quality_gate_passed=all(criteria.values()))
    return result


# Offline evidence gate ------------------------------------------------------

GATE_KIND = "offline-evidence-gate"
GATE_SCHEMA_VERSION = 1
GATE_BENCHMARKS = ("locomo", "longmemeval")
# Correct answers / questions in the saved GPT-5.4 run, split by whether every
# annotated evidence turn was packed. Source: benchmarks/results/research/
# 2026-09-23/gpt54-posthoc-evidence-diagnostics.json; a test keeps them equal.
# LongMemEval-S uses its pooled rates, as issue #78 specifies: several of its
# categories have fewer than ten questions with evidence missing.
GATE_CONDITIONAL_ACCURACY = {
    "locomo": {
        "single-hop": {"all_packed": (600, 657), "missing": (30, 183)},
        "temporal": {"all_packed": (216, 252), "missing": (10, 68)},
        "multi-hop": {"all_packed": (37, 45), "missing": (42, 233)},
        "open-domain": {"all_packed": (22, 29), "missing": (19, 60)},
    },
    "longmemeval": {"*": {"all_packed": (385, 403), "missing": (21, 67)}},
}
# Questions that retrieval cannot move keep their category's measured rate:
# LoCoMo questions without an annotation or with one that names no stored turn,
# and LongMemEval-S abstention questions.
GATE_UNSCORED_ACCURACY = {
    "locomo": {"multi-hop": (1, 4), "open-domain": (7, 7), "single-hop": (0, 1), "temporal": (1, 1)},
    "longmemeval": {"knowledge-update": (4, 6), "multi-session": (9, 12), "single-session-user": (6, 6),
                    "temporal-reasoning": (5, 6)},
}
GATE_MEASURED_ACCURACY = {"locomo": (985, 1540), "longmemeval": (430, 500)}
GATE_BASELINE = {
    "locomo": {"records_per_context": 25.2, "memory_text_share": 0.29,
               "all_evidence_packed": {"category": "multi-hop", "questions": 45, "annotated": 282}},
    "longmemeval": {"records_per_context": 23.9, "memory_text_share": 0.32,
                    "all_evidence_packed": {"category": None, "questions": 403, "annotated": 470}},
}
GATE_PROJECTION = {
    "label": "Planning estimate, not an answer score.",
    "method": ("Each question takes the saved GPT-5.4 run's accuracy on questions whose annotated evidence was "
               "all packed, or partly missing: per category for LoCoMo, pooled for LongMemEval-S. Questions "
               "that retrieval cannot move (no resolvable annotation, or abstention) keep their category's "
               "measured rate."),
    "calibration": ("At the saved run's evidence states the projection reproduces 985/1,540 and 430/500 by "
                    "construction; LongMemEval-S category values are pooled estimates. The audit's re-pack "
                    "simulator, using the same LoCoMo rates, reproduced the real packed sets with mean Jaccard "
                    "0.83 and projected 63.3% against 64.0% measured "
                    "(memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md, section 1)."),
    "limits": [
        "It ignores distractor effects: added or reordered context can change answers without "
        "changing evidence coverage.",
        "It relies on the datasets' evidence annotations, which have gaps; equivalent evidence can "
        "exist elsewhere.",
        "Its conditional accuracies come from one reader and one strict judge (GPT-5.4).",
        "All 2,040 questions have already been examined, so this is a development gate. Publication "
        "claims need fresh or held-out data.",
    ],
}
# The gate replays saved packs with local query embedding only. Maintenance is
# off because it promotes records by wall-clock age between questions, which
# would make a replay depend on its date; the saved run's records were minutes
# old, so maintenance changed nothing there.
GATE_FIXED_SETTINGS = frozenset({
    "db_path", "vector_path", "lexical_path", "database_url", "namespace_id", "encryption_enabled",
    "encryption_key", "embedding", "extraction", "temporal_relation", "enable_query_reformulation",
    "organizer", "api", "mcp",
})
# The LoCoMo capture wrote this file after recording each pack's tree identity.
_PACK_IDENTITY_EXCLUDED = frozenset({"capture-manifest.json"})
# Candidate channels whose own ranking the gate records for each question, and
# the depths at which it reports their recall (issue #87). ``vector_k`` and
# ``lexical_k`` cut these rankings; "either" is the union of the two cuts.
GATE_CHANNELS = ("vector", "lexical")
CHANNEL_RECALL_DEPTHS = (25, 50, 100, 150, 500)
# Each engine searches this once before its first timed question, so that
# ``retrieval_seconds`` leaves out the one-time model load. Reports record it
# as their timing, and only reports timed the same way compare latency.
GATE_WARM_UP_QUERY = "offline evidence gate warm-up"
GATE_RETRIEVAL_TIMING = "after-warm-up"


@dataclass(frozen=True)
class GateCase:
    """One saved benchmark question and the pack its saved context came from."""

    benchmark: str
    question_id: str
    category: str
    question: str
    user_id: str
    reference_time: datetime
    pack: Path
    pack_sha256: str  # tree identity the saved run recorded for the pack
    saved_context_sha256: str
    evidence: frozenset[str] | None  # None when the question has no evidence annotation
    unresolved: tuple[str, ...] = ()  # annotations that name no stored turn


def _harness():
    """The GPT-5.4 harness also holds provider clients, so load it only when the gate runs."""
    from benchmarks.integrations import run_gpt54_comparison

    return run_gpt54_comparison


def default_archive() -> Path:
    # The datasets are read from the main checkout, so worktrees use its archive too.
    return _harness().ORIGINAL / "data" / "gpt54-comparison-v1"


def pack_identity(pack: Path) -> str:
    """Tree digest of a memory pack, as the saved run recorded it."""
    lme = _harness().lme
    files = [item for item in lme._tree_identity(pack)["files"] if item["path"] not in _PACK_IDENTITY_EXCLUDED]
    return lme._sha256(lme._canonical(files))


def _turn_key(session_id, position, index) -> str:
    return f"{session_id}#{position}#{index}"


def _source_key(benchmark: str, metadata: dict) -> str | None:
    """Evidence identity of a stored turn, in the form the dataset annotations use."""
    if benchmark == "locomo":
        return metadata.get("source_dialog_id")
    parts = (metadata.get("source_session_id"), metadata.get("source_session_position"),
             metadata.get("source_turn_index"))
    return None if None in parts else _turn_key(*parts)


def _prepared(folder: Path, questions: int) -> tuple[dict, dict[str, str]]:
    """The saved run's manifest, and the digest it recorded for each context file."""
    path = folder / "prepared.json"
    if not path.is_file():
        raise ValueError(f"No saved run at {folder}; pass --archive with the main checkout's "
                         "data/gpt54-comparison-v1")
    prepared = json.loads(path.read_text())
    if not prepared.get("complete") or len(prepared["contexts"]) != questions:
        raise ValueError(f"The saved run at {folder} is incomplete")
    return prepared, {row["question_id"]: row["sha256"] for row in prepared["contexts"]}


def _saved_context(folder: Path, question_id: str, manifest: dict[str, str]) -> dict:
    raw = (folder / "contexts" / f"{question_id}.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest[question_id]:
        raise ValueError(f"Saved context {question_id} differs from the archive manifest")
    return json.loads(raw)


def _pack_dir(path: Path) -> Path:
    if not (path / "memory.duckdb").is_file():
        raise ValueError(f"Saved memory pack not found: {path}")
    return path


def _locomo_cases(archive: Path) -> list[GateCase]:
    gpt54 = _harness()
    if gpt54.digest(gpt54.LOCOMO) != gpt54.LOCOMO_SHA:
        raise ValueError("The LoCoMo dataset differs from the saved run")
    folder = archive / "locomo"
    questions = gpt54.question_rows("locomo")
    prepared, manifest = _prepared(folder, len(questions))
    packs = {record["conversation_id"]: (_pack_dir(Path(record["config"]["db_path"]).parent),
                                         record["final_artifact"]["tree_sha256"])
             for record in prepared["packs"]}
    stored = {sample["sample_id"]: {turn["metadata"]["source_dialog_id"]
                                    for turn in gpt54.source_turns(sample["conversation"])}
              for sample in json.loads(gpt54.LOCOMO.read_text())}
    cases = []
    for question in questions:
        saved = _saved_context(folder, question["question_id"], manifest)
        pack, pack_sha256 = packs[question["conversation_id"]]
        wanted = frozenset(question.get("evidence", []))
        cases.append(GateCase(
            benchmark="locomo", question_id=question["question_id"], category=question["question_type"],
            question=question["question"], user_id=saved["receipt"]["user_id"],
            reference_time=datetime.fromisoformat(saved["receipt"]["reference_time"]),
            pack=pack, pack_sha256=pack_sha256,
            saved_context_sha256=hashlib.sha256(saved["context"].encode()).hexdigest(),
            evidence=wanted or None,
            unresolved=tuple(sorted(wanted - stored[question["conversation_id"]])),
        ))
    return cases


def _longmemeval_cases(archive: Path) -> list[GateCase]:
    gpt54 = _harness()
    if gpt54.digest(gpt54.LONGMEM) != gpt54.lme.DATASET_SHA256:
        raise ValueError("The LongMemEval-S dataset differs from the saved run")
    folder = archive / "longmemeval"
    questions = gpt54.question_rows("longmemeval")
    _, manifest = _prepared(folder, len(questions))
    cases = []
    for question in questions:
        saved = _saved_context(folder, question["question_id"], manifest)
        # The saved context points at its 2026-09-22 control capture, which holds
        # the receipt and sits next to the pack that produced the context.
        capture = Path(saved["source_capture"])
        raw = capture.read_bytes()
        if hashlib.sha256(raw).hexdigest() != saved["source_capture_sha256"]:
            raise ValueError(f"The control capture for {question['question_id']} differs from the archive")
        receipt = json.loads(raw)["retrievals"][0]["receipt"]
        turns = {_turn_key(session_id, position, index): turn
                 for position, (session_id, session) in enumerate(
                     zip(question["haystack_session_ids"], question["haystack_sessions"], strict=True))
                 for index, turn in enumerate(session)}
        # Abstention questions have no evidence to find. Blank answer turns were
        # never stored; the current dataset has none.
        abstention = question["question_id"].endswith("_abs")
        wanted = frozenset() if abstention else frozenset(
            key for key, turn in turns.items() if turn.get("has_answer") is True)
        cases.append(GateCase(
            benchmark="longmemeval", question_id=question["question_id"], category=question["question_type"],
            question=question["question"], user_id=receipt["user_id"],
            reference_time=datetime.fromisoformat(receipt["reference_time"]),
            pack=_pack_dir(capture.parent / "pack"), pack_sha256=saved["artifact_checksum"],
            saved_context_sha256=saved["context_sha256"], evidence=wanted or None,
            unresolved=tuple(sorted(key for key in wanted if not turns[key]["content"].strip())),
        ))
    return cases


def parse_overrides(items: list[str]) -> dict:
    """Turn ``packing.token_budget=8192`` style arguments into nested config values."""
    overrides: dict = {}
    for item in items:
        key, separator, raw = item.partition("=")
        if not separator or not key:
            raise ValueError(f"Expected KEY=VALUE, got {item!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        *parents, leaf = key.split(".")
        node = overrides
        for part in parents:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"{key} conflicts with another override")
        if leaf in node:
            raise ValueError(f"{key} is set twice")
        node[leaf] = value
    return overrides


def _check_override_keys(model: type[BaseModel], values: dict, prefix: str = "") -> None:
    # Nested models ignore unknown fields, so a misspelled key would otherwise do nothing.
    for key, value in values.items():
        field = model.model_fields.get(key)
        if field is None:
            raise ValueError(f"Unknown configuration key: {prefix}{key}")
        nested = field.annotation
        if isinstance(value, dict) and isinstance(nested, type) and issubclass(nested, BaseModel):
            _check_override_keys(nested, value, f"{prefix}{key}.")


def gate_config(pack: Path, overrides: dict | None = None) -> PRMEConfig:
    """Current defaults over a pack copy, isolated from environment variables and .env files."""
    overrides = overrides or {}
    fixed = sorted(GATE_FIXED_SETTINGS.intersection(overrides))
    if fixed:
        raise ValueError(f"The evidence gate cannot override {', '.join(fixed)}")
    _check_override_keys(PRMEConfig, overrides)
    # Scoring and packing values passed in code take their classes' historical
    # defaults; --set changes only the settings it names, like the environment.
    values = with_product_retrieval_defaults(overrides)
    with patch.dict(os.environ, {}, clear=True):
        # Nested settings read .env by themselves, so build each one without it.
        settings = {name: field.annotation(_env_file=None) for name, field in PRMEConfig.model_fields.items()
                    if isinstance(field.annotation, type) and issubclass(field.annotation, BaseSettings)}
        settings["organizer"] = OrganizerConfig(_env_file=None, opportunistic_enabled=False)
        config = PRMEConfig(_env_file=None, **values, **settings, db_path=str(pack / "memory.duckdb"),
                            vector_path=str(pack / "vectors.usearch"), lexical_path=str(pack / "lexical_index"))
    if config.enable_query_reformulation or config.temporal_relation.enabled:
        raise ValueError("The evidence gate does not run model-backed query features")
    for names, fusion in ((RANK_FUSION_ONLY_SETTINGS, "rrf"), (WEIGHTED_ONLY_SETTINGS, "weighted")):
        for name in names:
            if name in overrides.get("scoring", {}) and config.scoring.fusion != fusion:
                # The other fusion ignores the setting, so this run would change nothing.
                raise ValueError(f"scoring.{name} applies only with scoring.fusion=\"{fusion}\"")
    if (overrides.get("packing", {}).get("session_context_rank_fusion_score_decay") is not None
            and config.scoring.fusion != "rrf"):
        # Weighted scoring never applies it either. The default value is left
        # alone, so a weighted run needs only scoring.fusion="weighted".
        raise ValueError("packing.session_context_rank_fusion_score_decay applies only with "
                         "scoring.fusion=\"rrf\"")
    if config.packing.session_context_packing is not None and (
            config.packing.session_context_window <= 0 or config.packing.session_context_top_k == 0):
        # Without session expansion no neighbor exists, so this run would change nothing.
        raise ValueError("packing.session_context_packing applies only with session expansion "
                         "(a positive packing.session_context_window and a nonzero "
                         "packing.session_context_top_k)")
    return config


def projected_correct(benchmark: str, category: str, evidence: dict | None) -> float:
    """Planning estimate of this question's accuracy from its evidence state."""
    if evidence is None or evidence["unresolved"]:
        rates = GATE_UNSCORED_ACCURACY[benchmark].get(category)
        if rates is None:
            raise ValueError(f"The saved run has no measured rate for unscored {benchmark} {category} questions")
        correct, total = rates
    else:
        table = GATE_CONDITIONAL_ACCURACY[benchmark]
        rates = table[category] if category in table else table["*"]
        correct, total = rates["all_packed" if evidence["all_packed"] else "missing"]
    return correct / total


def _check_replay(case: GateCase, packing: PackingConfig, response, receipt, context: str,
                  context_sha256: str, packed: list) -> None:
    """The receipt must describe exactly the context the reader would see."""
    label = f"{case.benchmark} {case.question_id}"
    if response.metadata.backend_failures:
        raise ValueError(f"{label}: retrieval backends failed: {response.metadata.backend_failures}")
    if receipt is None or not response.metadata.receipt_persisted:
        raise ValueError(f"{label}: the retrieval receipt was not persisted")
    if receipt.context_sha256 != context_sha256:
        raise ValueError(f"{label}: receipt context {receipt.context_sha256} differs from rendered {context_sha256}")
    if receipt.replay_ranking() != tuple(result.node.id for result in response.results):
        raise ValueError(f"{label}: the receipt ranking differs from the response")
    tokens = count_tokens(context, packing.tokenizer)
    limit = max(0, packing.token_budget - packing.overhead_tokens)
    if tokens != response.bundle.tokens_used or tokens > limit:
        raise ValueError(f"{label}: the context has {tokens} tokens; the bundle reports "
                         f"{response.bundle.tokens_used} and the limit is {limit}")
    if ({candidate.node_id for candidate in receipt.candidates if candidate.in_context}
            != {candidate.node.id for candidate in packed}):
        raise ValueError(f"{label}: the receipt's in-context records differ from the packed bundle")


def _in_context(text: str, context: str) -> bool:
    """Whether the text appears in the context verbatim, as a JSON string body, or
    in the reader format's encoding."""
    return any(form in context for form in (
        text, json.dumps(text)[1:-1], json.dumps(text, ensure_ascii=False)[1:-1],
        reader_text(text)[1:-1]))


def _write_capture(capture_dir: Path, case: GateCase, context: str, record: dict) -> str:
    raw = json.dumps({"question_id": case.question_id, "context": context, **record}).encode()
    path = capture_dir / case.benchmark / f"{case.question_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _first_ranks(benchmark: str, ranked_metadata: Iterable[dict | None]) -> dict[str, int]:
    """The best rank at which each source turn appears."""
    first_rank: dict[str, int] = {}
    for rank, metadata in enumerate(ranked_metadata, start=1):
        source = _source_key(benchmark, metadata or {})
        if source is not None:
            first_rank.setdefault(source, rank)
    return first_rank


def _gate_row(case: GateCase, *, context: str, context_tokens: int, text_tokens: int, packed_sources: set[str],
              text_sources: set[str], first_rank: dict[str, int], records: int, records_without_text: int,
              representations: dict[str, int], candidates: int, seconds: float, stored: int,
              channel_ranks: dict[str, dict[str, int | None]] | None) -> dict:
    """One question's measurements; PRME and plain replays share it so their reports compare."""
    evidence = None
    if case.evidence is not None:
        evidence = {
            "annotated": len(case.evidence), "unresolved": list(case.unresolved),
            "packed": len(case.evidence & packed_sources),
            "packed_with_text": len(case.evidence & text_sources),
            "all_packed": case.evidence <= packed_sources,
            "all_packed_with_text": case.evidence <= text_sources,
            "ranks": {key: first_rank.get(key) for key in sorted(case.evidence.difference(case.unresolved))},
            "channel_ranks": channel_ranks,
        }
    context_sha256 = hashlib.sha256(context.encode()).hexdigest()
    return {
        "benchmark": case.benchmark, "question_id": case.question_id, "category": case.category,
        "context_sha256": context_sha256, "context_matches_saved": context_sha256 == case.saved_context_sha256,
        "context_tokens": context_tokens, "memory_text_tokens": text_tokens, "records": records,
        "records_without_text": records_without_text, "representations": representations,
        "candidates": candidates, "stored_records": stored, "retrieval_seconds": seconds, "evidence": evidence,
        "projected_correct": projected_correct(case.benchmark, case.category, evidence),
    }


async def _channel_ranks(engine, case: GateCase, turns: dict) -> dict[str, dict[str, int | None]] | None:
    """Where each resolved evidence turn ranks in each candidate channel's own ranking (issue #87).

    Each channel ranks every stored turn it matches by the question alone, the
    ranking that ``vector_k`` and ``lexical_k`` cut. The rest of retrieval
    (entity and aggregation lexical scans, session context, filters, fusion)
    does not change it, so it is the same for every configuration of one
    embedding model. None for a question without an evidence annotation.
    """
    if case.evidence is None:
        return None
    rankings = await _index_rankings(engine, case.question, case.user_id, turns, GATE_CHANNELS)
    wanted = sorted(case.evidence.difference(case.unresolved))
    ranks = {}
    for channel in GATE_CHANNELS:
        first_rank = _first_ranks(case.benchmark, (turns[node_id].metadata for node_id in rankings[channel]))
        ranks[channel] = {key: first_rank.get(key) for key in wanted}
    return ranks


def _aggregation_observations(metadata) -> dict | None:
    """Aggregation coverage of one retrieval: None unless the question was read as a count or list (issue #87).

    Aggregation questions widen every candidate limit by
    ``aggregation_k_multiplier`` up to ``aggregation_k_max``, and
    ``candidate_limit_paths`` names the channels that still filled theirs.
    """
    coverage = metadata.aggregation_coverage
    if coverage is None:
        return None
    return {"status": coverage.status, "candidate_limit_paths": list(coverage.candidate_limit_paths)}


def _query_scoring_observations(receipt) -> dict:
    """What a receipt shows about the question's temporal and current-state scoring (issue #85).

    ``temporal_affinity_varies``: the returned candidates do not all share one
    temporal affinity. Under rank fusion that is when temporal scoring can
    reorder them, and only candidates on a channel count, as only they set the
    pool-relative factor. ``current_state_path``: rank fusion's current-state
    recency boost applied, which it records on every candidate. None when the
    receipt cannot show it: weighted scoring, no recency boost, or no
    candidates.
    """
    provenance = list((receipt.score_provenance or {}).values())
    ranked = [item for item in provenance if item.rank_fusion is None
              or item.rank_fusion.semantic_rank is not None or item.rank_fusion.lexical_rank is not None]
    current_state_path = None
    if provenance and receipt.scoring.fusion == "rrf" and receipt.scoring.rrf_recency_boost is not None:
        current_state_path = any(item.rank_fusion is not None and item.rank_fusion.recency_boost_factor is not None
                                 for item in provenance)
    return {"temporal_affinity_varies": len({item.trace.temporal_affinity for item in ranked}) > 1,
            "current_state_path": current_state_path}


# Packed records that session expansion reached, found alone, or scored (issue #86).
SESSION_CONTEXT_COUNTS = ("reached", "added", "promoted")


def _session_context_observations(packed: Iterable) -> dict[str, int]:
    """How a question's packed records arrived through session expansion (issue #86).

    ``reached``: a trigger's session window held the record (its paths include
    SESSION_CONTEXT). ``added``: no other path found it. ``promoted``: its score
    is a trigger's score times the session decay.
    """
    counts = dict.fromkeys(SESSION_CONTEXT_COUNTS, 0)
    for candidate in packed:
        if "SESSION_CONTEXT" in candidate.paths:
            counts["reached"] += 1
            counts["added"] += candidate.paths == ["SESSION_CONTEXT"]
        provenance = candidate.score_provenance
        counts["promoted"] += provenance is not None and any(
            adjustment.kind == "session_decay" for adjustment in provenance.adjustments)
    return counts


async def _replay_case(engine, packing: PackingConfig, case: GateCase, capture_dir: Path | None) -> dict:
    started = time.perf_counter()
    response = await engine.retrieve(case.question, user_id=case.user_id, reference_time=case.reference_time)
    seconds = time.perf_counter() - started
    receipt = await engine.get_retrieval_receipt(str(response.metadata.request_id), user_id=case.user_id)
    context = response.bundle.render()
    context_sha256 = hashlib.sha256(context.encode()).hexdigest()
    packed = [candidate for group in response.bundle.sections.values() for candidate in group]
    _check_replay(case, packing, response, receipt, context, context_sha256, packed)
    # Receipt flags, not the rendered format, decide what counts as memory text,
    # and that text must actually appear in the context.
    has_text = {candidate.node_id: candidate.has_content for candidate in receipt.candidates}
    packed_sources, text_sources, text_tokens = set(), set(), 0
    for candidate in packed:
        source = _source_key(case.benchmark, candidate.node.metadata or {})
        if has_text[candidate.node.id]:
            text = candidate.rendered_text or ""
            if not _in_context(text, context):
                raise ValueError(f"{case.benchmark} {case.question_id}: packed memory text is missing "
                                 "from the rendered context")
            text_tokens += count_tokens(text, packing.tokenizer)
        if source is not None:
            packed_sources.add(source)
            if has_text[candidate.node.id]:
                text_sources.add(source)
    # Measured after the timed retrieval, and read-only, so neither its time
    # nor the next question's context depends on it.
    turns = await _stored_turns(engine, case.user_id)
    row = _gate_row(
        case, context=context, context_tokens=response.bundle.tokens_used, text_tokens=text_tokens,
        packed_sources=packed_sources, text_sources=text_sources,
        first_rank=_first_ranks(case.benchmark, (result.node.metadata for result in response.results)),
        records=len(packed), records_without_text=sum(not has_text[candidate.node.id] for candidate in packed),
        representations=dict(Counter(candidate.representation.value for candidate in packed)),
        candidates=len(response.results), seconds=seconds, stored=len(turns),
        channel_ranks=await _channel_ranks(engine, case, turns))
    row["query_scoring"] = _query_scoring_observations(receipt)
    row["session_context"] = _session_context_observations(packed)
    row["candidates_generated"] = dict(response.metadata.candidates_generated)
    row["aggregation"] = _aggregation_observations(response.metadata)
    if capture_dir is not None:
        row["capture_sha256"] = _write_capture(capture_dir, case, context,
                                               {"receipt": receipt.model_dump(mode="json")})
    return row



# Plain RAG reference --------------------------------------------------------

PLAIN_METHODS = ("vector", "bm25", "rrf")
PLAIN_RRF_K = 60
_PLAIN_SEPARATOR = "\n"
# A plain baseline reads only the context budget. The tokenizer stays fixed so
# its reports remain comparable with PRME's.
_PLAIN_OVERRIDES = frozenset({"token_budget", "overhead_tokens"})


def check_plain(method: str, overrides: dict | None) -> None:
    if method not in PLAIN_METHODS:
        raise ValueError(f"Choose a plain method from: {', '.join(PLAIN_METHODS)}")
    overrides = overrides or {}
    packing = overrides.get("packing", {})
    if set(overrides) - {"packing"} or not isinstance(packing, dict) or set(packing) - _PLAIN_OVERRIDES:
        raise ValueError("A plain baseline accepts only packing.token_budget and packing.overhead_tokens")


def context_limit(packing: PackingConfig) -> int:
    """Tokens the rendered context may use: the budget less the caller's reserved overhead."""
    return max(0, packing.token_budget - packing.overhead_tokens)


async def _stored_turns(engine, user_id: str) -> dict:
    """The user's stored records by node ID; in a gate pack each one is a turn, and anything else is an error."""
    stored = await engine.count_nodes(user_id=user_id)
    turns = {str(node.id): node for node in await engine.query_nodes(
        user_id=user_id, node_type=NodeType.FACT, limit=max(stored, 1))}
    if len(turns) != stored:
        raise ValueError(f"User {user_id} has {stored} stored records, of which {len(turns)} are turns")
    return turns


async def _index_rankings(engine, question: str, user_id: str, turns: dict,
                          channels: Iterable[str]) -> dict[str, list[str]]:
    """Each named index's own ranking of the user's stored turns, best first, as node IDs.

    The vector ranking must cover every stored turn. BM25 ranks only turns
    that share a term with the question. Anything else an index returns is an
    error, never silently dropped.
    """
    rankings: dict[str, list[str]] = {}
    for channel in channels:
        if not turns:
            rankings[channel] = []
        elif channel == "vector":
            hits = await engine._vector_index.search(question, user_id, k=len(turns))
            rankings[channel] = [hit["node_id"] for hit in hits]
            if sorted(rankings[channel]) != sorted(turns):
                raise ValueError(f"The vector index ranks {len(rankings[channel])} records for {len(turns)} "
                                 "stored turns")
        elif channel == "lexical":
            hits = await engine._lexical_index.search(question, user_id, limit=len(turns))
            rankings[channel] = list(dict.fromkeys(hit["node_id"] for hit in hits))
            if not set(rankings[channel]) <= turns.keys():
                raise ValueError("The BM25 index returned records that are not stored turns")
        else:
            raise ValueError(f"Unknown candidate channel: {channel}")
    return rankings


async def plain_ranking(engine, method: str, question: str, user_id: str) -> list:
    """The user's stored turns ranked by one index, or by RRF (k=60) of both; best first."""
    if method not in PLAIN_METHODS:
        raise ValueError(f"Choose a plain method from: {', '.join(PLAIN_METHODS)}")
    turns = await _stored_turns(engine, user_id)
    channels = {"vector": ("vector",), "bm25": ("lexical",), "rrf": ("lexical", "vector")}[method]
    rankings = await _index_rankings(engine, question, user_id, turns, channels)
    order = (reciprocal_rank_fusion([rankings["lexical"], rankings["vector"]], constant=PLAIN_RRF_K)
             if method == "rrf" else rankings[channels[0]])
    return [turns[node_id] for node_id in order]


def plain_record(benchmark: str, node) -> str:
    """One stored turn as ``(date) speaker: text``, with the speaker and date the dataset gives."""
    if benchmark == "locomo":
        # The registered LoCoMo source text already reads "(date) Speaker: text".
        return node.content
    role = (node.metadata or {}).get("source_role")
    if node.event_time is None or role not in {"user", "assistant"}:
        raise ValueError(f"LongMemEval-S turn {node.id} has no session date or role")
    # The dataset's own date format, which the reader prompt's "Current Date" also uses.
    return f"({node.event_time.astimezone(timezone.utc):%Y/%m/%d (%a) %H:%M}) {role}: {node.content}"


def pack_records(records: Sequence[str], limit: int, tokenizer: str) -> tuple[str, list[int], int]:
    """Fill the budget in rank order, skipping any record that does not fit.

    A record is tried when its own tokens plus a separator fit in the budget
    left. The whole context is then counted exactly, and the record is dropped
    again if that count exceeds ``limit``, so the result never does. Unlike
    ``benchmarks.evidence.pack_sources``, a record that cannot fit is rejected
    without counting the whole context again, which matters over hundreds of
    turns per question.
    """
    separator = count_tokens(_PLAIN_SEPARATOR, tokenizer)
    packed: list[int] = []
    used = 0
    for index, record in enumerate(records):
        if used + count_tokens(record, tokenizer) + (separator if packed else 0) > limit:
            continue
        packed.append(index)
        exact = count_tokens(_PLAIN_SEPARATOR.join(records[i] for i in packed), tokenizer)
        if exact > limit:
            packed.pop()
        else:
            used = exact
    return _PLAIN_SEPARATOR.join(records[i] for i in packed), packed, used


async def _replay_plain_case(engine, packing: PackingConfig, case: GateCase, method: str,
                             capture_dir: Path | None) -> dict:
    """The same measurements for a plain RAG baseline: one index's ranking of the stored turns, plain lines."""
    started = time.perf_counter()
    ranked = await plain_ranking(engine, method, case.question, case.user_id)
    records = [plain_record(case.benchmark, node) for node in ranked]
    context, indexes, tokens = pack_records(records, context_limit(packing), packing.tokenizer)
    seconds = time.perf_counter() - started
    packed = [ranked[index] for index in indexes]
    sources = {source for node in packed
               if (source := _source_key(case.benchmark, node.metadata or {})) is not None}
    turns = await _stored_turns(engine, case.user_id)
    row = _gate_row(
        case, context=context, context_tokens=tokens,
        text_tokens=sum(count_tokens(node.content, packing.tokenizer) for node in packed),
        packed_sources=sources, text_sources=sources,
        first_rank=_first_ranks(case.benchmark, (node.metadata for node in ranked)),
        records=len(packed), records_without_text=0, representations={"plain": len(packed)},
        candidates=len(ranked), seconds=seconds, stored=len(turns),
        channel_ranks=await _channel_ranks(engine, case, turns))
    if capture_dir is not None:
        row["capture_sha256"] = _write_capture(capture_dir, case, context, {"plain": {
            "method": method, "context_tokens": tokens, "packed_node_ids": [str(node.id) for node in packed]}})
    return row


def _verify_copy(copy: Path, expected_sha256: str) -> None:
    # A symlink in the copy would let retrieval write receipts into the saved archive.
    if copy.is_symlink() or any(path.is_symlink() for path in copy.rglob("*")):
        raise ValueError(f"The saved pack copied to {copy} contains a symlink")
    if pack_identity(copy) != expected_sha256:
        raise ValueError(f"The pack copied to {copy} differs from the identity the saved run recorded")


async def _warm_up(engine, user_id: str) -> None:
    """Load what an engine loads once, before the first timed question (issue #87).

    The query embedding model loads on the first search, and LongMemEval-S
    opens a new engine for every question, so without this each of its times
    would include the load. Both searches are read-only and use a fixed text
    that is never a question.
    """
    await engine._vector_index.search(GATE_WARM_UP_QUERY, user_id, k=1)
    await engine._lexical_index.search(GATE_WARM_UP_QUERY, user_id, limit=1)


async def replay_gate(cases: list[GateCase], *, scratch: Path, overrides: dict | None = None,
                      capture_dir: Path | None = None, plain: str | None = None,
                      progress: Callable[[int], None] | None = None) -> list[dict]:
    """Replay each question on a verified scratch copy of its pack; saved packs are never opened.

    With ``plain`` set to a plain RAG method, the stored turns are ranked by
    that method and packed as plain lines instead of through ``retrieve()``.
    """
    from benchmarks.diagnostics.longmemeval_s_compact import _clone_pack

    if plain is not None:
        check_plain(plain, overrides)

    # Retrieval persists receipts, so questions sharing a pack replay together in saved order.
    groups = [(key, list(group))
              for key, group in itertools.groupby(cases, key=lambda case: (case.benchmark, case.pack))]
    if len({key for key, _ in groups}) != len(groups):
        raise ValueError("Questions that share a pack must be contiguous so they replay in saved order")
    rows: list[dict] = []
    for number, ((_, pack), group) in enumerate(groups, start=1):
        copy = scratch / f"pack-{number:05d}"
        if copy.exists():
            raise ValueError(f"Scratch path already exists: {copy}")
        try:
            _clone_pack(pack, copy)
            _verify_copy(copy, group[0].pack_sha256)
            config = gate_config(copy, overrides)
            async with MemoryEngine.open(config) as engine:
                await _warm_up(engine, group[0].user_id)
                for case in group:
                    if plain is None:
                        rows.append(await _replay_case(engine, config.packing, case, capture_dir))
                    else:
                        rows.append(await _replay_plain_case(engine, config.packing, case, plain, capture_dir))
        finally:
            shutil.rmtree(copy, ignore_errors=True)
        if progress is not None:
            progress(len(rows))
    return rows


def _share(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _all_within(evidence: dict, limit: float) -> bool:
    """Every annotated turn resolves and ranks inside ``limit``."""
    return not evidence["unresolved"] and all(rank is not None and rank <= limit
                                              for rank in evidence["ranks"].values())


def _session_context_summary(rows: list[dict]) -> dict | None:
    """Totals of the session expansion counts, and each as a share of packed records.

    None when a row has no counts: a plain baseline, or a report written
    before they were recorded.
    """
    observed = [row.get("session_context") for row in rows]
    if any(item is None for item in observed):
        return None
    records = sum(row["records"] for row in rows)
    totals = {key: sum(item[key] for item in observed) for key in SESSION_CONTEXT_COUNTS}
    return {**totals, **{f"{key}_share": _share(totals[key], records) for key in SESSION_CONTEXT_COUNTS}}


def _latency(seconds: list[float]) -> dict:
    """Median and 95th percentile (linear interpolation) of per-question retrieval seconds."""
    return {"p50": statistics.median(seconds),
            "p95": statistics.quantiles(seconds, n=20, method="inclusive")[-1] if len(seconds) > 1 else seconds[0]}


def _channel_rank(evidence: dict, channel: str, key: str) -> int | None:
    """A turn's rank in one channel, or its better rank in the two for "either"."""
    ranks = [evidence["channel_ranks"][name][key] for name in (GATE_CHANNELS if channel == "either" else (channel,))]
    return min((rank for rank in ranks if rank is not None), default=None)


def _channel_recall(annotated: list[dict]) -> dict | None:
    """Recall at each depth of each candidate channel's own ranking (issue #87).

    For each channel and depth: the share of resolved annotated evidence turns
    ranked inside it, and the share of annotated questions with all their
    evidence inside it. "all" is the channel's whole ranking: every stored turn
    for vector, only turns that share a term with the question for lexical.
    None when a report predates these ranks.
    """
    if not annotated or any(evidence.get("channel_ranks") is None for evidence in annotated):
        return None
    depths = [*map(str, CHANNEL_RECALL_DEPTHS), "all"]
    recall = {}
    for channel in (*GATE_CHANNELS, "either"):
        by_question = [[_channel_rank(evidence, channel, key) for key in evidence["ranks"]] for evidence in annotated]
        turns = [rank for ranks in by_question for rank in ranks]
        recall[channel] = {}
        for depth in depths:
            limit = math.inf if depth == "all" else int(depth)

            def inside(rank: int | None) -> bool:
                return rank is not None and rank <= limit

            recall[channel][depth] = {
                "evidence_turns": _share(sum(map(inside, turns)), len(turns)),
                "all_evidence": _share(sum(not evidence["unresolved"] and all(map(inside, ranks))
                                           for evidence, ranks in zip(annotated, by_question)), len(annotated)),
            }
    return recall


def _aggregation_summary(rows: list[dict]) -> dict | None:
    """Aggregation questions, and those whose widened limit a channel still filled; None for earlier reports."""
    if any("aggregation" not in row for row in rows):
        return None
    aggregation = [row["aggregation"] for row in rows if row["aggregation"] is not None]
    return {"questions": len(aggregation),
            "candidate_limited": sum(bool(item["candidate_limit_paths"]) for item in aggregation),
            "limited_paths": dict(sorted(Counter(path for item in aggregation
                                                 for path in item["candidate_limit_paths"]).items()))}


def summarize_gate(rows: list[dict]) -> dict:
    """Per-context and evidence metrics for one benchmark or category."""
    if not rows:
        raise ValueError("Cannot summarize an empty set of questions")
    annotated = [row["evidence"] for row in rows if row["evidence"] is not None]
    ranks = [rank for evidence in annotated for rank in evidence["ranks"].values()]
    returned = [rank for rank in ranks if rank is not None]
    context_tokens = sum(row["context_tokens"] for row in rows)
    all_packed = sum(evidence["all_packed"] for evidence in annotated)
    with_text = sum(evidence["all_packed_with_text"] for evidence in annotated)
    all_returned = sum(_all_within(evidence, math.inf) for evidence in annotated)
    projected = sum(row["projected_correct"] for row in rows)
    representations = Counter()
    for row in rows:
        representations.update(row["representations"])
    stored = [row.get("stored_records") for row in rows]
    return {
        "questions": len(rows),
        "contexts_matching_saved": sum(row["context_matches_saved"] for row in rows),
        "candidates_mean": statistics.fmean(row["candidates"] for row in rows),
        "candidates_max": max(row["candidates"] for row in rows),
        # Candidates per stored turn; session context can add turns no channel returned.
        "candidate_share_mean": None if None in stored or 0 in stored else statistics.fmean(
            row["candidates"] / row["stored_records"] for row in rows),
        "retrieval_seconds": _latency([row["retrieval_seconds"] for row in rows]),
        "records_per_context": statistics.fmean(row["records"] for row in rows),
        "context_tokens_mean": context_tokens / len(rows),
        "memory_text_share": _share(sum(row["memory_text_tokens"] for row in rows), context_tokens),
        "records_without_text": sum(row["records_without_text"] for row in rows),
        "representations": dict(sorted(representations.items())),
        "annotated_questions": len(annotated),
        "all_evidence_packed": all_packed,
        "all_evidence_packed_share": _share(all_packed, len(annotated)),
        "all_evidence_packed_with_text": with_text,
        "all_evidence_packed_with_text_share": _share(with_text, len(annotated)),
        # Candidate recall, separate from what the context kept.
        "all_evidence_returned": all_returned,
        "all_evidence_returned_share": _share(all_returned, len(annotated)),
        "channel_recall": _channel_recall(annotated),
        "aggregation": _aggregation_summary(rows),
        "evidence_ranks": {
            "annotated_turns": len(ranks), "not_returned": len(ranks) - len(returned),
            "median": statistics.median(returned) if returned else None,
            "top_25_share": _share(sum(rank <= 25 for rank in returned), len(returned)),
            "beyond_150_share": _share(sum(rank > 150 for rank in returned), len(returned)),
            "all_within_top_25_share": _share(sum(_all_within(e, 25) for e in annotated), len(annotated)),
            "all_within_top_75_share": _share(sum(_all_within(e, 75) for e in annotated), len(annotated)),
        },
        "projected_correct": projected,
        "projected_accuracy": projected / len(rows),
        "session_context_records": _session_context_summary(rows),
    }


def gate_report(rows: list[dict], *, provenance: dict) -> dict:
    if not rows:
        raise ValueError("An evidence gate report needs at least one question")
    benchmarks = {}
    for name in dict.fromkeys(row["benchmark"] for row in rows):
        items = [row for row in rows if row["benchmark"] == name]
        benchmarks[name] = {
            "summary": summarize_gate(items),
            "categories": {category: summarize_gate([row for row in items if row["category"] == category])
                           for category in sorted({row["category"] for row in items})},
            "context_mismatches": [row["question_id"] for row in items if not row["context_matches_saved"]],
        }
    return {
        "kind": GATE_KIND, "schema_version": GATE_SCHEMA_VERSION, "complete": True, "provenance": provenance,
        "projection": {**GATE_PROJECTION, "conditional_accuracy": GATE_CONDITIONAL_ACCURACY,
                       "unscored_accuracy": GATE_UNSCORED_ACCURACY,
                       "measured_accuracy": GATE_MEASURED_ACCURACY},
        "baseline": GATE_BASELINE, "benchmarks": benchmarks, "rows": rows,
    }


async def run_gate(benchmarks: Iterable[str] = GATE_BENCHMARKS, *, archive: Path | None = None,
                   overrides: dict | None = None, capture_dir: Path | None = None, plain: str | None = None,
                   progress: Callable[[int], None] | None = None) -> dict:
    from benchmarks.retrieval_eval import provenance

    requested = {benchmarks} if isinstance(benchmarks, str) else set(benchmarks)
    if not requested or not requested <= set(GATE_BENCHMARKS):
        raise ValueError(f"Choose benchmarks from: {', '.join(GATE_BENCHMARKS)}")
    if plain is not None:
        check_plain(plain, overrides)
    selected = [name for name in GATE_BENCHMARKS if name in requested]
    gpt54 = _harness()
    archive = archive or default_archive()
    # Record the code that runs before replaying; this also rejects bad overrides.
    run_provenance = provenance(gate_config(Path("{pack}"), overrides))
    if run_provenance["commit"] is None:
        raise ValueError("Run the gate from a git checkout so the report names its commit")
    loaders = {"locomo": _locomo_cases, "longmemeval": _longmemeval_cases}
    started_at, started = gpt54.utc(), time.perf_counter()
    cases = [case for name in selected for case in loaders[name](archive)]
    if capture_dir is not None:
        capture_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="prme-evidence-gate-") as scratch:
        rows = await replay_gate(cases, scratch=Path(scratch), overrides=overrides,
                                 capture_dir=capture_dir, plain=plain, progress=progress)
    datasets = {"locomo": gpt54.LOCOMO_SHA, "longmemeval": gpt54.lme.DATASET_SHA256}
    report = gate_report(rows, provenance={
        **run_provenance, "overrides": overrides or {}, "retrieval_timing": GATE_RETRIEVAL_TIMING,
        **({"plain": plain, "plain_rrf_k": PLAIN_RRF_K} if plain is not None else {}),
        "archive": {"path": str(archive),
                    "prepared_sha256": {name: gpt54.digest(archive / name / "prepared.json") for name in selected}},
        "datasets": {name: datasets[name] for name in selected},
    })
    report.update(started_at=started_at, seconds=time.perf_counter() - started)
    return report


def _gate_rows(report: dict, side: str) -> dict:
    if (report.get("kind") != GATE_KIND or report.get("schema_version") != GATE_SCHEMA_VERSION
            or not report.get("complete")):
        raise ValueError(f"The {side} report is not a complete evidence gate report")
    rows = {(row["benchmark"], row["question_id"]): row for row in report["rows"]}
    if len(rows) != len(report["rows"]):
        raise ValueError(f"The {side} report repeats a question")
    return rows


def _labels(row: dict) -> tuple:
    evidence = row["evidence"]
    return row["category"], None if evidence is None else (
        evidence["annotated"], tuple(evidence["unresolved"]), tuple(evidence["ranks"]))


def _all_packed(row: dict) -> bool:
    return row["evidence"]["all_packed"]


def _all_packed_with_text(row: dict) -> bool:
    return row["evidence"]["all_packed_with_text"]


def _evidence_recall(row: dict) -> float:
    return row["evidence"]["packed"] / row["evidence"]["annotated"]


def _all_returned(row: dict) -> bool:
    return _all_within(row["evidence"], math.inf)


def _projected(row: dict) -> float:
    return row["projected_correct"]


# Both reports must share these inputs, or their question pairs mean different things.
_COMPARED_INPUTS = {
    "datasets": lambda report: report["provenance"]["datasets"],
    "archives": lambda report: report["provenance"]["archive"]["prepared_sha256"],
    "tokenizers": lambda report: report["provenance"]["engine_config"]["packing"]["tokenizer"],
    "projection constants": lambda report: report["projection"],
    "baselines": lambda report: report["baseline"],
}


def _query_scoring(old: dict, new: dict, keys: list) -> dict | None:
    """Temporal affinity and current-state changes between two PRME replays (issue #85).

    None when either side has a question without observations: a plain
    baseline, or a report written before they were recorded. A question whose
    current-state path either side cannot show is counted as unknown and is in
    neither list.
    """
    if any(rows[key].get("query_scoring") is None for rows in (old, new) for key in keys):
        return None
    categories = sorted({old[key]["category"] for key in keys})

    def path(rows: dict, key) -> bool | None:
        return rows[key]["query_scoring"]["current_state_path"]

    def moved(was: bool, now: bool) -> list[str]:
        return [key[1] for key in keys if path(old, key) is was and path(new, key) is now]

    return {
        "temporal_affinity_varies_by_category": {
            "questions": {category: sum(old[key]["category"] == category for key in keys) for category in categories},
            **{side: {category: sum(rows[key]["query_scoring"]["temporal_affinity_varies"]
                                    for key in keys if rows[key]["category"] == category)
                      for category in categories}
               for side, rows in (("before", old), ("after", new))}},
        "entered_current_state_path": moved(False, True),
        "left_current_state_path": moved(True, False),
        "current_state_path_unknown": sum(path(old, key) is None or path(new, key) is None for key in keys),
    }


def _session_context_comparison(before: dict, after: dict, name: str) -> dict | None:
    """Packed records through session expansion before and after, overall and by category (issue #86).

    None when either report lacks the counts.
    """
    sides = {}
    for side, report in (("before", before), ("after", after)):
        bench = report["benchmarks"][name]
        counts = {"all": bench["summary"].get("session_context_records"),
                  **{category: summary.get("session_context_records")
                     for category, summary in bench["categories"].items()}}
        if any(value is None for value in counts.values()):
            return None
        sides[side] = counts
    return sides


def _compared_latency(before: dict, after: dict, old: dict, new: dict, keys: list) -> dict | None:
    """Retrieval p50 and p95 on each side, or None unless both reports timed retrieval after a warm-up.

    Latency depends on the machine and its load, so compare only reports run
    on one machine, one at a time.
    """
    if any(report["provenance"].get("retrieval_timing") != GATE_RETRIEVAL_TIMING for report in (before, after)):
        return None
    return {side: _latency([rows[key]["retrieval_seconds"] for key in keys])
            for side, rows in (("before", old), ("after", new))}


def compare_gates(before: dict, after: dict, *, samples: int = 2000) -> dict:
    """Pair two gate reports question by question; their questions and inputs must be identical."""
    old, new = _gate_rows(before, "before"), _gate_rows(after, "after")
    if old.keys() != new.keys():
        only_before, only_after = sorted(old.keys() - new.keys()), sorted(new.keys() - old.keys())
        raise ValueError(f"Question sets differ: {len(only_before)} only before {only_before[:5]}, "
                         f"{len(only_after)} only after {only_after[:5]}")
    for label, value in _COMPARED_INPUTS.items():
        if value(before) != value(after):
            raise ValueError(f"The reports use different {label}")
    for key, row in old.items():
        if _labels(row) != _labels(new[key]):
            raise ValueError(f"{key[1]}: category or evidence annotation differs between reports")

    def paired(keys, metric):
        return paired_statistics([(float(metric(old[key])), float(metric(new[key]))) for key in keys],
                                 samples=samples)

    benchmarks = {}
    for name in dict.fromkeys(benchmark for benchmark, _ in old):
        keys = [key for key in old if key[0] == name]
        annotated = [key for key in keys if old[key]["evidence"] is not None]
        benchmarks[name] = {
            "all_evidence_packed": paired(annotated, _all_packed),
            "all_evidence_packed_with_text": paired(annotated, _all_packed_with_text),
            "evidence_recall": paired(annotated, _evidence_recall),
            "all_evidence_returned": paired(annotated, _all_returned),
            "projected_accuracy": paired(keys, _projected),
            "gained_all_evidence": [key[1] for key in annotated
                                    if not _all_packed(old[key]) and _all_packed(new[key])],
            "lost_all_evidence": [key[1] for key in annotated if _all_packed(old[key]) and not _all_packed(new[key])],
            "all_evidence_packed_by_category": {
                category: paired([key for key in annotated if old[key]["category"] == category], _all_packed)
                for category in sorted({old[key]["category"] for key in keys})},
            "context": {side: {field: report["benchmarks"][name]["summary"][field]
                               for field in ("questions", "contexts_matching_saved", "records_per_context",
                                             "memory_text_share", "records_without_text", "candidates_mean")}
                        for side, report in (("before", before), ("after", after))},
            "retrieval_seconds": _compared_latency(before, after, old, new, keys),
            "aggregation": {side: _aggregation_summary([rows[key] for key in keys])
                            for side, rows in (("before", old), ("after", new))},
            "query_scoring": _query_scoring(old, new, keys),
            "session_context_records": _session_context_comparison(before, after, name),
        }
    return {
        "kind": f"{GATE_KIND}-comparison", "complete": True, "bootstrap_samples": samples, "bootstrap_seed": 42,
        "interval_note": ("Intervals resample questions. LoCoMo's 1,540 questions come from 10 conversations, "
                          "so they are narrower than conversation-level intervals."),
        "before": {"provenance": before["provenance"], "started_at": before.get("started_at")},
        "after": {"provenance": after["provenance"], "started_at": after.get("started_at")},
        "projection": before["projection"], "benchmarks": benchmarks,
    }


def _pct(value: float | None, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.1f} pp" if signed else f"{value:.1%}"


def _rank(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}"


def _run_label(provenance: dict) -> str:
    commit = f"commit `{provenance.get('commit')}`"
    if provenance.get("dirty"):
        commit += " with uncommitted changes"
    overrides = provenance.get("overrides")
    setting = f"overrides `{json.dumps(overrides, sort_keys=True)}`" if overrides else "current defaults"
    if provenance.get("plain"):
        return f"{commit}, plain {provenance['plain']} baseline (no PRME retrieval), " + (
            setting if overrides else "default budget")
    return f"{commit}, {setting}"


def _projection_note(projection: dict) -> list[str]:
    return ["", f"**Projected accuracy is a planning estimate, not an answer score.** {projection['method']} "
            f"{projection['calibration']}", "", *[f"- {limit}" for limit in projection["limits"]], ""]


def _baseline_line(baseline: dict, bench: dict) -> str:
    target = baseline["all_evidence_packed"]
    now = bench["categories"].get(target["category"]) if target["category"] else bench["summary"]
    packed_now = f"{now['all_evidence_packed']}/{now['annotated_questions']}" if now else "n/a"
    return (f"2026-09-23 baseline: {baseline['records_per_context']} records per context, "
            f"{baseline['memory_text_share']:.0%} memory text, all evidence packed for "
            f"{target['questions']}/{target['annotated']} {target['category'] or 'annotated'} questions. "
            f"This run: {bench['summary']['records_per_context']:.1f}, "
            f"{_pct(bench['summary']['memory_text_share'])}, {packed_now}.")


def gate_markdown(report: dict) -> str:
    lines = ["# Offline evidence gate", "", f"Run: {_run_label(report['provenance'])}."]
    plain = report["provenance"].get("plain")
    changed = sum(bench["summary"]["questions"] - bench["summary"]["contexts_matching_saved"]
                  for bench in report["benchmarks"].values())
    if plain:
        ranking = {"vector": "vector similarity", "bm25": "BM25",
                   "rrf": f"reciprocal rank fusion (k={report['provenance'].get('plain_rrf_k')}) of both"}[plain]
        lines += ["", f"**Plain RAG reference, not PRME.** Stored turns are ranked by {ranking} and packed in "
                  "rank order as plain lines, so no context is expected to match the saved PRME run."]
    elif report["provenance"].get("overrides") and not changed:
        lines += ["", "**Every context matches the saved run, so these overrides changed nothing the reader "
                  "sees.** Check the setting names. Changes that act when memories are stored need new packs."]
    lines += ["", "| Benchmark | Questions | Saved contexts reproduced | Candidates per question "
              "| Retrieval p50 / p95 | Records per context | Memory text share | Records without text "
              "| All evidence packed | Median evidence rank | Projected accuracy |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, bench in report["benchmarks"].items():
        summary = bench["summary"]
        lines.append(
            f"| {name} | {summary['questions']} | {summary['contexts_matching_saved']}/{summary['questions']} "
            f"| {_candidates(summary)} | {_seconds(summary['retrieval_seconds'])} "
            f"| {summary['records_per_context']:.1f} | {_pct(summary['memory_text_share'])} "
            f"| {summary['records_without_text']} | {summary['all_evidence_packed']}/"
            f"{summary['annotated_questions']} ({_pct(summary['all_evidence_packed_share'])}) "
            f"| {_rank(summary['evidence_ranks']['median'])} | {_pct(summary['projected_accuracy'])} |")
    for name, bench in report["benchmarks"].items():
        lines += ["", f"## {name}", "", _baseline_line(report["baseline"][name], bench), "",
                  "| Category | Questions | All evidence among candidates | All evidence packed "
                  "| Packed with memory text | Median rank | Evidence in top 25 | Projected accuracy |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for category, summary in bench["categories"].items():
            lines.append(
                f"| {category} | {summary['questions']} | {_pct(summary['all_evidence_returned_share'])} "
                f"| {summary['all_evidence_packed']}/"
                f"{summary['annotated_questions']} ({_pct(summary['all_evidence_packed_share'])}) "
                f"| {_pct(summary['all_evidence_packed_with_text_share'])} "
                f"| {_rank(summary['evidence_ranks']['median'])} | {_pct(summary['evidence_ranks']['top_25_share'])} "
                f"| {_pct(summary['projected_accuracy'])} |")
        session = bench["summary"].get("session_context_records")
        if session is not None:
            lines += ["", f"Packed records through session expansion: {session['reached']} reached "
                      f"({_pct(session['reached_share'])} of packed records), {session['added']} found by no "
                      f"other path, {session['promoted']} scored by a session decay."]
        lines += _candidate_markdown(bench["summary"])
        mismatches = bench["context_mismatches"]
        if mismatches and not plain:
            shown = _listed(mismatches)
            lines += ["", f"Contexts that differ from the saved run: {len(mismatches)} ({shown}). "
                      "The JSON report lists every one."]
    return "\n".join(lines + _projection_note(report["projection"]))


def _seconds(latency: dict | None) -> str:
    return "n/a" if latency is None else f"{latency['p50']:.3f} / {latency['p95']:.3f} s"


def _candidates(summary: dict) -> str:
    share = summary.get("candidate_share_mean")
    return f"{summary['candidates_mean']:.1f}" + ("" if share is None else f" ({share:.0%} of stored)")


def _aggregation_line(aggregation: dict) -> str:
    paths = ", ".join(f"{path} {count}" for path, count in aggregation["limited_paths"].items())
    return (f"{aggregation['questions']} questions read as counts or lists, which widen the candidate limits; "
            f"{aggregation['candidate_limited']} still filled a limit" + (f" ({paths})." if paths else "."))


def _candidate_markdown(summary: dict) -> list[str]:
    """Candidate recall per channel and the aggregation widening for one benchmark (issue #87)."""
    lines = []
    recall = summary.get("channel_recall")
    if recall is not None:
        depths = list(recall["vector"])
        lines += ["", "Candidate recall per channel: the share of annotated evidence turns inside each channel's "
                  "own top k, and in parentheses the share of questions with all their evidence inside it. "
                  "`vector_k` and `lexical_k` cut these rankings; either is the union of the two cuts. Entity "
                  "and aggregation lexical scans and session context are not counted. Lexical ranks only turns "
                  "that share a term with the question.", "",
                  "| Channel | " + " | ".join("All" if depth == "all" else f"Top {depth}" for depth in depths) + " |",
                  "|---|" + "---:|" * len(depths)]
        for channel, by_depth in recall.items():
            lines.append(f"| {channel} | " + " | ".join(
                f"{_pct(value['evidence_turns'])} ({_pct(value['all_evidence'])})" for value in by_depth.values()) + " |")
    aggregation = summary.get("aggregation")
    if aggregation is not None:
        lines += ["", f"Aggregation: {_aggregation_line(aggregation)}"]
    return lines


def _listed(ids: list[str]) -> str:
    """Up to 20 question IDs, for a Markdown summary whose JSON report lists every one."""
    return ", ".join(ids[:20]) + (", ..." if len(ids) > 20 else "")


def _query_scoring_markdown(benchmarks: dict) -> list[str]:
    scored = {name: bench.get("query_scoring") for name, bench in benchmarks.items()}
    missing = [name for name, scoring in scored.items() if scoring is None]
    lines = []
    if missing:
        lines += ["", f"Temporal affinity and the current-state path are not shown for {', '.join(missing)}: "
                  "a report is a plain baseline or was written before they were recorded."]
    if len(missing) == len(scored):
        return lines
    lines += ["", "Questions whose candidates do not all share one temporal affinity, so that temporal scoring "
              "can reorder them under rank fusion:", "",
              "| Benchmark | Category | Questions | Before | After |", "|---|---|---:|---:|---:|"]
    for name, scoring in scored.items():
        if scoring is not None:
            varies = scoring["temporal_affinity_varies_by_category"]
            lines += [f"| {name} | {category} | {count} | {varies['before'][category]} | {varies['after'][category]} |"
                      for category, count in varies["questions"].items()]
    lines.append("")
    for name, scoring in scored.items():
        if scoring is None:
            continue
        for label, moved in (("entered", scoring["entered_current_state_path"]),
                             ("left", scoring["left_current_state_path"])):
            lines.append(f"- {name} questions that {label} the current-state path: {len(moved)}"
                         + (f" ({_listed(moved)})." if moved else "."))
        if scoring["current_state_path_unknown"]:
            lines.append(f"- {name} questions whose current-state path these receipts cannot show (weighted "
                         f"scoring or no recency boost): {scoring['current_state_path_unknown']}. They are in "
                         "neither list.")
    if any(scoring and (scoring["entered_current_state_path"] or scoring["left_current_state_path"])
           for scoring in scored.values()):
        lines.append("The JSON report lists every question.")
    return lines


def comparison_markdown(result: dict) -> str:
    lines = ["# Offline evidence gate comparison", "",
             f"Before: {_run_label(result['before']['provenance'])}.",
             f"After: {_run_label(result['after']['provenance'])}.", "",
             "| Benchmark | Metric | Before | After | Change | 95% interval | Wins | Losses | Ties |",
             "|---|---|---:|---:|---:|---|---:|---:|---:|"]
    for name, bench in result["benchmarks"].items():
        metrics = [("All evidence packed", bench["all_evidence_packed"]),
                   ("All evidence packed with memory text", bench["all_evidence_packed_with_text"]),
                   ("Evidence recall", bench["evidence_recall"]),
                   ("All evidence among the candidates", bench["all_evidence_returned"]),
                   ("Projected accuracy", bench["projected_accuracy"]),
                   *[(f"All evidence packed, {category}", stats)
                     for category, stats in bench["all_evidence_packed_by_category"].items()]]
        for label, stats in metrics:
            interval = stats["interval_95"]
            shown = f"{_pct(interval[0], True)} to {_pct(interval[1], True)}" if interval else "n/a"
            lines.append(f"| {name} | {label} | {_pct(stats['before'])} | {_pct(stats['after'])} "
                         f"| {_pct(stats['delta'], True)} | {shown} | {stats.get('wins', 0)} "
                         f"| {stats.get('losses', 0)} | {stats.get('ties', 0)} |")
    lines += ["", "| Benchmark | Saved contexts reproduced | Candidates per question | Retrieval p50 / p95 "
              "| Records per context | Memory text share | Records without text |", "|---|---|---|---|---|---|---|"]
    for name, bench in result["benchmarks"].items():
        old, new = bench["context"]["before"], bench["context"]["after"]
        latency = bench["retrieval_seconds"] or {"before": None, "after": None}
        lines.append(f"| {name} | {old['contexts_matching_saved']}/{old['questions']} to "
                     f"{new['contexts_matching_saved']}/{new['questions']} "
                     f"| {old['candidates_mean']:.1f} to {new['candidates_mean']:.1f} "
                     f"| {_seconds(latency['before'])} to {_seconds(latency['after'])} "
                     f"| {old['records_per_context']:.1f} to {new['records_per_context']:.1f} "
                     f"| {_pct(old['memory_text_share'])} to {_pct(new['memory_text_share'])} "
                     f"| {old['records_without_text']} to {new['records_without_text']} |")
    if any(bench["retrieval_seconds"] is None for bench in result["benchmarks"].values()):
        lines += ["", "Retrieval latency is shown only when both reports timed each question after a warm-up "
                  "search; earlier reports included the embedding model's load."]
    else:
        lines += ["", "Retrieval latency depends on the machine and its load: compare it only between reports "
                  "run on one machine, one at a time."]
    for name, bench in result["benchmarks"].items():
        aggregation = bench["aggregation"]
        if aggregation["before"] is not None and aggregation["after"] is not None:
            lines += [f"- {name} aggregation, before: {_aggregation_line(aggregation['before'])}",
                      f"- {name} aggregation, after: {_aggregation_line(aggregation['after'])}"]
    lines += _query_scoring_markdown(result["benchmarks"])
    lines += _session_context_markdown(result["benchmarks"])
    lines += ["", result["interval_note"]]
    return "\n".join(lines + _projection_note(result["projection"]))


def _session_context_markdown(benchmarks: dict) -> list[str]:
    compared = {name: bench.get("session_context_records") for name, bench in benchmarks.items()}
    missing = [name for name, sides in compared.items() if sides is None]
    lines = []
    if missing:
        lines += ["", f"No session expansion counts for {', '.join(missing)}: a report is a plain baseline "
                  "or was written before they were recorded."]
    if len(missing) == len(compared):
        return lines

    def shown(counts: dict) -> str:
        return (f"{counts['reached']} ({_pct(counts['reached_share'])}) / {counts['added']} "
                f"/ {counts['promoted']}")

    lines += ["", "Packed records through session expansion: reached (share of packed records) / found by no "
              "other path / scored by a session decay.", "",
              "| Benchmark | Category | Before | After |", "|---|---|---|---|"]
    for name, sides in compared.items():
        if sides is not None:
            lines += [f"| {name} | {category} | {shown(counts)} | {shown(sides['after'][category])} |"
                      for category, counts in sides["before"].items()]
    return lines


def _write_report(path: Path, data: dict, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    path.with_suffix(".md").write_text(markdown)


def _json_output(value: str) -> Path:
    path = Path(value)
    if path.suffix != ".json":
        raise argparse.ArgumentTypeError("the output must be a .json path; the Markdown summary goes beside it")
    return path


def _quiet_offline_cli() -> None:
    # Local model assets only: a missing model fails instead of downloading.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import structlog

    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
                        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def gate_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks.diagnostics.product_packing gate",
        description="Replay retrieve() over the saved LoCoMo and LongMemEval-S packs and measure the reader's context.")
    parser.add_argument("--output", type=_json_output, required=True,
                        help="JSON report; a Markdown summary is written beside it")
    parser.add_argument("--benchmark", choices=GATE_BENCHMARKS, action="append", help="Repeatable; both by default")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="Configuration override: a dotted key and a JSON value, e.g. packing.token_budget=8192")
    parser.add_argument("--archive", type=Path,
                        help="Saved 2026-09-23 run archive (default: the main checkout's data/gpt54-comparison-v1)")
    parser.add_argument("--capture-dir", type=Path,
                        help="New directory for every rendered context and receipt (contains benchmark text)")
    parser.add_argument("--plain", choices=PLAIN_METHODS,
                        help="Measure a plain RAG baseline instead of retrieve(): stored turns ranked by this "
                             "method alone and packed as plain lines")
    args = parser.parse_args(argv)
    try:
        overrides = parse_overrides(args.overrides)
        gate_config(Path("{pack}"), overrides)
        if args.plain:
            check_plain(args.plain, overrides)
    except ValueError as exc:
        parser.error(str(exc))
    _quiet_offline_cli()
    last_done = 0

    def progress(done: int) -> None:
        nonlocal last_done
        if done // 100 > last_done // 100:
            print(f"{done} questions replayed", file=sys.stderr, flush=True)
        last_done = done

    report = asyncio.run(run_gate(args.benchmark or GATE_BENCHMARKS, archive=args.archive, overrides=overrides,
                                  capture_dir=args.capture_dir, plain=args.plain, progress=progress))
    markdown = gate_markdown(report)
    _write_report(args.output, report, markdown)
    print(markdown)


def gate_compare_main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.diagnostics.product_packing gate-compare",
                                     description="Compare two evidence gate reports question by question.")
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=_json_output, required=True,
                        help="JSON comparison; a Markdown summary is written beside it")
    parser.add_argument("--samples", type=int, default=2000, help="Paired bootstrap samples")
    args = parser.parse_args(argv)
    if args.output.resolve() in {args.before.resolve(), args.after.resolve()}:
        parser.error("the output would overwrite an input report")
    before, after = args.before.read_bytes(), args.after.read_bytes()
    result = compare_gates(json.loads(before), json.loads(after), samples=args.samples)
    result["inputs"] = {"before_sha256": hashlib.sha256(before).hexdigest(),
                        "after_sha256": hashlib.sha256(after).hexdigest()}
    markdown = comparison_markdown(result)
    _write_report(args.output, result, markdown)
    print(markdown)


def main(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["gate"]:
        return gate_main(argv[1:])
    if argv[:1] == ["gate-compare"]:
        return gate_compare_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path, help="Prospectively registered frozen test-split plan")
    args = parser.parse_args(argv)
    raw = args.input.read_bytes()
    confirmation = json.loads(args.confirmation.read_bytes()) if args.confirmation else None
    result = compare(json.loads(raw), args.snapshots, confirmation=confirmation)
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"complete": result["complete"], "questions": len(result["details"]),
                      "summary": result["summary"]}))


if __name__ == "__main__":
    main()

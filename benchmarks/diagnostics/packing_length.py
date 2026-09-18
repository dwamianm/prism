"""Registered offline length-penalty ablation; never changes product defaults."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import version
import inspect
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from uuid import uuid4

from benchmarks.compare_evidence import paired_statistics
from benchmarks.diagnostics.packing_reader import canonical, digest, write
from benchmarks.diagnostics.product_packing import measure
from benchmarks.retrieval_eval import supervise
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context

ALPHAS = (0, 0.25, 0.5, 0.75, 1)
POLICIES = ("parser", "literal_stopwords")
BUDGETS = (2048, 4096, 8192)


def runtime():
    import prme.retrieval.models as models
    import prme.retrieval.tokenization as tokenization
    import prme.retrieval.config as config
    modules = [sys.modules[__name__], models, tokenization, config,
               sys.modules[pack_context.__module__], sys.modules[measure.__module__],
               sys.modules[paired_statistics.__module__]]
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
        "modules": {m.__name__: digest(Path(inspect.getfile(m)).read_bytes()) for m in modules},
        "python": sys.version,
        "dependencies": {n: version(n) for n in ("pydantic", "tiktoken", "numpy")},
    }


def pack_length(candidates, config, alpha):
    """Only multi-path ranking changes; calls must be serial within a process."""
    if alpha not in ALPHAS:
        raise ValueError("Unregistered length penalty")
    config = config.model_copy(update={"multipath_ordering": "density"})
    with patch("prme.retrieval.packing.compute_str",
               lambda c: c.composite_score / max(c.token_cost, 1) ** alpha):
        return pack_context(candidates, config)


def capture(saved):
    """Receives candidates and control contexts, never relevance labels."""
    result = {}
    for policy in POLICIES:
        values = saved["candidates"][policy]
        candidates = [RetrievalCandidate.model_validate(v) for v in values]
        before = canonical([c.model_dump(mode="json") for c in candidates])
        for budget in BUDGETS:
            for alpha in ALPHAS:
                control_order = "score" if alpha == 0 else "density"
                control = saved["arms"][f"{policy}:{control_order}:{budget}"]
                config = PackingConfig.model_validate(control["packing"])
                bundle = pack_length(candidates, config, alpha)
                measured = measure(bundle, set(), config)
                if alpha in (0, 1):
                    if (bundle.render() != control["context"]
                            or measured != control["measurement"]):
                        raise ValueError("Endpoint control did not reproduce exactly")
                result[f"{policy}:{alpha}:{budget}"] = measured
        if canonical([c.model_dump(mode="json") for c in candidates]) != before:
            raise ValueError("Packing changed immutable candidate inputs")
    return result


def score_capture(captured, gold):
    for measurement in captured.values():
        retained = set(measurement["content_source_ids"])
        measurement["evidence_recall"] = len(retained & gold) / len(gold) if gold else None
        measurement["all_evidence_retained"] = gold <= retained if gold else None
    return captured


def summarize(rows):
    result = {}
    for policy in POLICIES:
        for budget in BUDGETS:
            for alpha in ALPHAS:
                key = f"{policy}:{alpha}:{budget}"
                metrics = {}
                for metric in ("evidence_recall", "all_evidence_retained"):
                    pairs = [(r["arms"][f"{policy}:1:{budget}"][metric],
                              r["arms"][key][metric]) for r in rows]
                    metrics[metric] = paired_statistics(
                        [(float(a), float(b)) for a, b in pairs if a is not None and b is not None],
                        samples=2000, seed=42)
                result[key] = metrics
    return result


def validated_source(args):
    source_raw, verification_raw = args.source.read_bytes(), args.verification.read_bytes()
    source, verification = json.loads(source_raw), json.loads(verification_raw)
    source_plan_raw = args.source_plan.read_bytes()
    source_plan = json.loads(source_plan_raw)
    if (not source["complete"] or source["process_exit_code"] != 0 or source["errors"]
            or verification["source_output_sha256"] != digest(source_raw)
            or not verification["all_contexts_and_metrics_reproduced"]
            or source["plan_sha256"] != digest(source_plan_raw)
            or verification["plan_sha256"] != digest(source_plan_raw)
            or source_plan["dataset"]["split"] != "dev"):
        raise ValueError("A completed, independently verified development source is required")
    rows = source["details"]
    ids = [r["question_id"] for r in rows]
    if (ids != source_plan["dataset"]["selected_question_ids"] or len(set(ids)) != len(ids)
            or len(ids) != 119 or verification["questions"] != len(ids)):
        raise ValueError("Complete fixed development cohort required")
    refs = [r["snapshot"] for r in rows]
    if digest(canonical(refs)) != verification["verified_snapshots_sha256"]:
        raise ValueError("Verified snapshot inventory mismatch")
    for row in rows:
        ref = row["snapshot"]
        if ref["filename"] != digest(row["question_id"].encode()) + ".json":
            raise ValueError("Invalid snapshot identity")
        if digest((args.snapshots / ref["filename"]).read_bytes()) != ref["sha256"]:
            raise ValueError("Snapshot changed after source verification")
    return source, {
        "output_sha256": digest(source_raw), "verification_sha256": digest(verification_raw),
        "snapshots_sha256": digest(canonical(refs)), "dataset": source_plan["dataset"],
        "source_plan_sha256": digest(source_plan_raw),
    }


def run(args):
    source, source_identity = validated_source(args)
    plan = json.loads(args.plan.read_bytes())
    started = datetime.now(timezone.utc).isoformat()
    if (plan["runtime"] != runtime() or plan["source"] != source_identity
            or plan["registered_at"] >= started
            or plan["alphas"] != list(ALPHAS) or plan["policies"] != list(POLICIES)
            or plan["budgets"] != list(BUDGETS) or plan["workers"] != 2):
        raise ValueError("Registered runtime, source or experiment mismatch")
    report = {"run_id": args.run_id, "complete": False, "errors": 0,
              "plan_sha256": digest(args.plan.read_bytes()), "started_at": started,
              "source": source_identity, "runtime": plan["runtime"], "details": []}
    try:
        # Worker payloads omit source labels. Spawn gives every worker its own
        # temporary comparator; no shared-thread monkeypatch is used.
        def payloads():
            for row in source["details"]:
                saved = json.loads((args.snapshots / row["snapshot"]["filename"]).read_bytes())
                yield {"candidates": saved["candidates"], "arms": saved["arms"]}
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
            for row, captured in zip(source["details"], pool.map(capture, payloads()), strict=True):
                report["details"].append({
                    "question_id": row["question_id"], "category": row["category"],
                    "evidence_source_ids": row["evidence_source_ids"], "snapshot": row["snapshot"],
                    "arms": score_capture(captured, set(row["evidence_source_ids"])),
                })
                print(f"Completed {len(report['details'])}/119 questions", flush=True)
        if runtime() != plan["runtime"] or validated_source(args)[1] != source_identity:
            raise ValueError("Inputs or runtime changed during study")
        report["summary"] = summarize(report["details"])
        report["categories"] = {
            category: summarize([r for r in report["details"] if r["category"] == category])
            for category in sorted({r["category"] for r in report["details"]})}
        report["complete"] = True
        report["endpoint_controls_reproduced"] = 119 * 12
        report["contexts_evaluated"] = 119 * 30
    except BaseException as exc:
        report["errors"] = 1
        report["error_type"] = type(exc).__name__
        raise
    finally:
        write(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["register", "run"])
    for name in ("source", "source-plan", "verification", "snapshots", "plan"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.mode == "register":
        if args.plan.exists():
            raise ValueError("Refusing to replace registration")
        _, identity = validated_source(args)
        observed = runtime()
        if observed["dirty"]:
            raise ValueError("Commit implementation before registration")
        write(args.plan, {
            "registered_at": datetime.now(timezone.utc).isoformat(), "runtime": observed,
            "source": identity, "alphas": ALPHAS, "policies": POLICIES, "budgets": BUDGETS,
            "workers": 2, "bootstrap_samples": 2000, "bootstrap_seed": 42,
            "comparator": "multi-path score / max(full rendered entry tokens, 1) ** alpha",
            "controls": "alpha 0 reproduces score and alpha 1 density, all 1428 contexts exactly",
            "limits": ["Previously examined development cohort; exploratory, no promotion gate.",
                       "Whole-source evidence retention, not answer accuracy or competitive quality.",
                       "All 30 arms reported after full native exit; no selective retries.",
                       "Question intervals ignore shared histories and multiple comparisons.",
                       "Candidate snapshots fixed; retrieval, embedding and extraction are not re-executed.",
                       "The failed 381-question confirmation gate remains unchanged."],
        })
        return
    if args.output is None:
        raise ValueError("Run requires output path")
    if args.worker:
        run(args)
        return
    if args.output.exists():
        raise ValueError("Refusing to overwrite previous results")
    run_id = str(uuid4())
    result = supervise(args.output, [sys.executable, "-m", __spec__.name, *sys.argv[1:],
                                     "--worker", "--run-id", run_id], run_id)
    raise SystemExit(0 if result["complete"] else 1)


if __name__ == "__main__":
    main()

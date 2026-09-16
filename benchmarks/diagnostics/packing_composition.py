"""Fixed development composition of one reserved head and a quarter length penalty.

Uses the verified hybrid study's native-parser candidates. The concurrently
running native-context answer trial is separate and is never modified here.
"""

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

from benchmarks.diagnostics import packing_length as length
from benchmarks.diagnostics.compare_public_captures import (
    cluster_statistics,
    groups_for,
)
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from benchmarks.diagnostics.product_packing import measure
from prme.retrieval import packing
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
import prme.models.nodes as nodes
import prme.retrieval.tokenization as tokenization
from prme.types import NodeType

BUDGETS = (2048, 4096, 8192)
ARMS = {
    "density": [False, 1],
    "quarter": [False, 0.25],
    "head1": [True, 1],
    "head1_quarter": [True, 0.25],
}
COMPARISONS = {
    "quarter_vs_density": ["density", "quarter"],
    "head1_vs_density": ["density", "head1"],
    "composition_vs_density": ["density", "head1_quarter"],
    "composition_vs_quarter": ["quarter", "head1_quarter"],
    "composition_vs_head1": ["head1", "head1_quarter"],
}


def pack(candidates, config, *, reserve_head, alpha):
    if alpha not in (1, 0.25):
        raise ValueError("Unregistered length penalty")
    eligible = [
        c
        for c in candidates
        if c.path_count >= 2
        and c.node.node_type != NodeType.INSTRUCTION
        and not packing._is_pinned_or_active_task(c)
    ]
    head = (
        min(eligible, key=lambda c: (-c.composite_score, str(c.node.id))).node.id
        if reserve_head and eligible
        else None
    )
    with patch.object(
        packing,
        "compute_str",
        lambda c: (
            float("inf")
            if c.node.id == head
            else c.composite_score / max(c.token_cost, 1) ** alpha
        ),
    ):
        return packing.pack_context(candidates, config)


def runtime():
    modules = [
        sys.modules[__name__],
        length,
        sys.modules[measure.__module__],
        sys.modules[cluster_statistics.__module__],
        packing,
        sys.modules[RetrievalCandidate.__module__],
        sys.modules[PackingConfig.__module__],
        nodes,
        tokenization,
    ]
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()
        ),
        "python": sys.version,
        "dependencies": {
            key: version(key) for key in ("pydantic", "tiktoken", "numpy")
        },
        "modules": {
            module.__name__: digest(Path(inspect.getfile(module)).read_bytes())
            for module in modules
        },
    }


def sources(args):
    source, identity = length.validated_source(args)
    prior = json.loads(args.length.read_bytes())
    completion = json.loads(args.length_completion.read_bytes())
    if (
        not prior["complete"]
        or prior["errors"]
        or prior["process_exit_code"] != 0
        or prior["source"] != identity
        or not completion["all_credit_and_summaries_recomputed"]
        or completion["process_exit_code"] != 0
        or completion["output_sha256"] != digest(args.length.read_bytes())
        or prior["plan_sha256"] != digest(args.length_plan.read_bytes())
        or completion["plan_sha256"] != prior["plan_sha256"]
    ):
        raise ValueError("Complete verified length study required")
    ids = [row["question_id"] for row in source["details"]]
    if ids != [row["question_id"] for row in prior["details"]]:
        raise ValueError("Length study cohort differs")
    cases = json.loads(args.inputs.read_bytes())["cases"]
    refs = json.loads(args.references.read_bytes())["references"]
    by_case = {row["case_id"]: row for row in refs}
    if (
        len(cases) != 119
        or len(refs) != 119
        or len(by_case) != 119
        or set(by_case) != {row["case_id"] for row in cases}
        or {row["question_id"] for row in refs} != set(ids)
    ):
        raise ValueError("Complete grouping cohort required")
    grouped = groups_for(cases, by_case)
    groups = {row["question_id"]: grouped[row["case_id"]] for row in refs}
    identity = {
        "hybrid": identity,
        "files": {
            name: digest(getattr(args, name).read_bytes())
            for name in (
                "length",
                "length_completion",
                "length_plan",
                "inputs",
                "references",
            )
        },
    }
    return (
        source,
        {row["question_id"]: row for row in prior["details"]},
        groups,
        identity,
    )


def capture(saved):
    # Receives no reference labels, question categories, or old outcome scores.
    candidates = [RetrievalCandidate.model_validate(row) for row in saved["candidates"]]
    before = canonical([candidate.model_dump(mode="json") for candidate in candidates])
    arms = {}
    for budget in BUDGETS:
        control = saved["controls"][str(budget)]
        if (
            control["measurement"]["evidence_recall"] is not None
            or control["measurement"]["all_evidence_retained"] is not None
        ):
            raise ValueError("Outcome labels cannot enter context production")
        config = PackingConfig.model_validate(control["packing"])
        if config.token_budget != budget or config.multipath_ordering != "density":
            raise ValueError("Original density configuration required")
        for name, (reserve_head, alpha) in ARMS.items():
            bundle = pack(candidates, config, reserve_head=reserve_head, alpha=alpha)
            measured = measure(bundle, set(), config)
            if name == "density" and (
                measured != control["measurement"]
                or bundle.render() != control["context"]
            ):
                raise ValueError("Density control did not reproduce")
            arms[f"{name}:{budget}"] = {**measured, "context": bundle.render()}
    if (
        canonical([candidate.model_dump(mode="json") for candidate in candidates])
        != before
    ):
        raise ValueError("Candidate inputs changed")
    return arms


def summarize(rows):
    result = {}
    for name, (before, after) in COMPARISONS.items():
        result[name] = {}
        for budget in BUDGETS:
            valid = [
                row
                for row in rows
                if row["arms"][f"{before}:{budget}"]["evidence_recall"] is not None
            ]
            result[name][str(budget)] = cluster_statistics(
                [
                    (
                        row["arms"][f"{before}:{budget}"]["evidence_recall"],
                        row["arms"][f"{after}:{budget}"]["evidence_recall"],
                    )
                    for row in valid
                ],
                [row["group"] for row in valid],
            )
    return result


def run(args):
    source, prior, groups, identity = sources(args)
    plan = json.loads(args.plan.read_bytes())
    observed = runtime()
    if (
        plan["source"] != identity
        or plan["runtime"] != observed
        or observed["dirty"]
        or plan["arms"] != ARMS
        or plan["budgets"] != list(BUDGETS)
        or plan["comparisons"] != COMPARISONS
        or plan["registered_at"] >= datetime.now(timezone.utc).isoformat()
    ):
        raise ValueError("Registered inputs, runtime or policies changed")
    report = {
        "complete": False,
        "errors": 0,
        "plan_sha256": digest(args.plan.read_bytes()),
        "source": identity,
        "runtime": observed,
        "details": [],
        "limits": plan["limits"],
    }
    try:

        def payloads():
            for row in source["details"]:
                saved = json.loads(
                    (args.snapshots / row["snapshot"]["filename"]).read_bytes()
                )
                yield {
                    "candidates": saved["candidates"]["parser"],
                    "controls": {
                        str(b): saved["arms"][f"parser:density:{b}"] for b in BUDGETS
                    },
                }

        with ProcessPoolExecutor(
            max_workers=2, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            for row, arms in zip(
                source["details"], pool.map(capture, payloads()), strict=True
            ):
                qid = row["question_id"]
                for budget in BUDGETS:
                    previous = prior[qid]["arms"][f"parser:0.25:{budget}"]
                    if any(
                        arms[f"quarter:{budget}"][key] != previous[key]
                        for key in (
                            "tokens",
                            "context_sha256",
                            "content_source_ids",
                            "pointer_source_ids",
                            "blank_source_ids",
                            "representations",
                        )
                    ):
                        raise ValueError("Quarter-length control did not reproduce")
                gold = set(row["evidence_source_ids"])
                for measured in arms.values():
                    retained = set(measured["content_source_ids"])
                    measured["evidence_recall"] = (
                        len(retained & gold) / len(gold) if gold else None
                    )
                    measured["all_evidence_retained"] = (
                        gold <= retained if gold else None
                    )
                report["details"].append(
                    {
                        "question_id": qid,
                        "category": row["category"],
                        "group": groups[qid],
                        "arms": arms,
                        "snapshot": row["snapshot"],
                    }
                )
                print(f"Completed {len(report['details'])}/119", flush=True)
        if runtime() != observed or sources(args)[3] != identity:
            raise ValueError("Study changed during execution")
        report.update(
            complete=True,
            contexts_evaluated=119 * 12,
            controls_reproduced=119 * 6,
            overall=summarize(report["details"]),
            categories={
                category: summarize(
                    [row for row in report["details"] if row["category"] == category]
                )
                for category in sorted({row["category"] for row in report["details"]})
            },
        )
    except BaseException as exc:
        report.update(errors=1, error_type=type(exc).__name__)
        raise
    finally:
        write(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["register", "run"])
    for name in (
        "source",
        "source-plan",
        "verification",
        "snapshots",
        "length",
        "length-completion",
        "length-plan",
        "inputs",
        "references",
        "plan",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "register":
        if args.plan.exists() or runtime()["dirty"]:
            raise ValueError("Fresh registration and committed code required")
        write(
            args.plan,
            {
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "source": sources(args)[3],
                "runtime": runtime(),
                "arms": ARMS,
                "budgets": BUDGETS,
                "comparisons": COMPARISONS,
                "primary_comparison": "head1_quarter versus quarter at 4096 tokens",
                "limits": [
                    "Previously examined development cohort; not independent confirmation.",
                    "Uses hybrid native-parser snapshots, separate from the ongoing native-context answer trial.",
                    "All 119 questions, four arms and three budgets retained; no outcome retries.",
                    "Fixed candidate pools; whole-source retention is not answer accuracy.",
                    "No production promotion; held-out answer evidence and regression checks remain necessary.",
                ],
            },
        )
    else:
        if args.output is None or args.output.exists():
            raise ValueError("Fresh output required")
        run(args)


if __name__ == "__main__":
    main()

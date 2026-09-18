"""Independently account for rendered composition contexts after native exit.

Does not invoke the experimental packer or its measurement/summary functions.
Source checks and declared history grouping reuse the existing input validators.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import statistics

import tiktoken

from benchmarks.diagnostics import packing_composition as study
from benchmarks.diagnostics.hindsight_capture import digest, write


def account(context, candidates, config, gold):
    nodes = {c["node"]["id"]: c["node"] for c in candidates}
    if len(nodes) != len(candidates):
        raise ValueError("Duplicate candidate identity")
    tokens = len(tiktoken.get_encoding(config["tokenizer"]).encode(
        context, disallowed_special=(),
    ))
    if tokens > max(0, config["token_budget"] - config["overhead_tokens"]):
        raise ValueError("Rendered context exceeds effective budget")
    content, pointers, blank, representations = [], [], [], Counter()
    seen = set()
    for line in context.split("\n"):
        if not line.startswith("{"):
            continue
        entry = json.loads(line)
        key = entry["id"]
        if key not in nodes or key in seen:
            raise ValueError("Unknown or duplicate rendered candidate")
        seen.add(key)
        node = nodes[key]
        source = node["metadata"]["source_turn"]
        representation = entry["representation"]
        if representation not in {"full", "prose", "structured", "key_value", "reference"}:
            raise ValueError("Unknown representation")
        representations[representation] += 1
        if representation in {"full", "prose", "structured"}:
            if node["content"] not in entry["text"]:
                raise ValueError("Content-bearing record lost original source text")
            (content if node["content"].strip() else blank).append(source)
        else:
            pointers.append(source)
    retained = set(content)
    return {
        "tokens": tokens,
        "context_sha256": digest(context.encode()),
        "content_source_ids": content,
        "pointer_source_ids": pointers,
        "blank_source_ids": blank,
        "representations": dict(representations),
        "evidence_recall": len(retained & gold) / len(gold) if gold else None,
        "all_evidence_retained": gold <= retained if gold else None,
    }


def statistics_for(rows, before, after):
    observed = [
        (r["group"], r["arms"][before]["evidence_recall"],
         r["arms"][after]["evidence_recall"])
        for r in rows if r["arms"][before]["evidence_recall"] is not None
    ]
    if not observed:
        return dict(queries=0, groups=0, before=None, after=None,
                    delta=None, interval_95=None)
    group_names = sorted({g for g, _, _ in observed})
    deltas = [b - a for _, a, b in observed]
    result = {
        "queries": len(observed), "groups": len(group_names),
        "before": statistics.mean(a for _, a, _ in observed),
        "after": statistics.mean(b for _, _, b in observed),
        "delta": statistics.mean(deltas),
        "wins": sum(d > 1e-12 for d in deltas),
        "losses": sum(d < -1e-12 for d in deltas),
        "ties": sum(abs(d) <= 1e-12 for d in deltas),
        "interval_95": None,
    }
    if len(group_names) > 1:
        rng = random.Random(42)
        clustered = {g: [b - a for group, a, b in observed if group == g]
                     for g in group_names}
        samples = sorted(statistics.mean(
            delta for group in rng.choices(group_names, k=len(group_names))
            for delta in clustered[group]
        ) for _ in range(2000))
        result["interval_95"] = [samples[int(.025 * 1999)], samples[int(.975 * 1999)]]
    return result


def summaries(rows, plan):
    return {
        comparison: {
            str(b): statistics_for(rows, f"{before}:{b}", f"{after}:{b}")
            for b in plan["budgets"]
        }
        for comparison, (before, after) in plan["comparisons"].items()
    }


def verify(args):
    raw = args.report.read_bytes()
    report = json.loads(raw)
    completion = json.loads(args.completion.read_bytes())
    plan_raw = args.plan.read_bytes()
    plan = json.loads(plan_raw)
    if (completion["native_exit_code"] != 0
            or completion["output_sha256"] != digest(raw)
            or completion["plan_sha256"] != digest(plan_raw)
            or not report["complete"] or report["errors"]):
        raise ValueError("Complete native-exited artifact required")
    source, previous, groups, identity = study.sources(args)
    if (report["source"] != identity or plan["source"] != identity
            or report["plan_sha256"] != digest(plan_raw)
            or report["runtime"] != plan["runtime"]
            or report["contexts_evaluated"] != 1428
            or report["controls_reproduced"] != 714
            or plan["arms"] != study.ARMS or plan["budgets"] != list(study.BUDGETS)
            or plan["comparisons"] != study.COMPARISONS):
        raise ValueError("Registered source, runtime or coverage differs")
    expected = [r["question_id"] for r in source["details"]]
    if [r["question_id"] for r in report["details"]] != expected:
        raise ValueError("Complete ordered cohort required")
    verified = []
    for row, original in zip(report["details"], source["details"], strict=True):
        qid = row["question_id"]
        snapshot = json.loads((args.snapshots / original["snapshot"]["filename"]).read_bytes())
        if (row["snapshot"] != original["snapshot"] or row["group"] != groups[qid]
                or row["category"] != original["category"]):
            raise ValueError("Question provenance differs")
        keys = {f"{name}:{budget}" for name in study.ARMS for budget in study.BUDGETS}
        if set(row["arms"]) != keys:
            raise ValueError("Every registered arm required")
        measured = {}
        for key, saved in row["arms"].items():
            name, budget = key.split(":")
            control = snapshot["arms"][f"parser:density:{budget}"]
            current = account(saved["context"], snapshot["candidates"]["parser"],
                              control["packing"], set(original["evidence_source_ids"]))
            if {k: v for k, v in saved.items() if k != "context"} != current:
                raise ValueError("Rendered source credit or token accounting differs")
            if name == "density" and saved["context"] != control["context"]:
                raise ValueError("Original density context differs")
            if name == "quarter" and current != previous[qid]["arms"][f"parser:0.25:{budget}"]:
                raise ValueError("Original quarter-length control differs")
            measured[key] = current
        verified.append({"question_id": qid, "category": row["category"],
                         "group": groups[qid], "arms": measured})
    overall = summaries(verified, plan)
    categories = {c: summaries([r for r in verified if r["category"] == c], plan)
                  for c in sorted({r["category"] for r in verified})}
    if overall != report["overall"] or categories != report["categories"]:
        raise ValueError("Independently recomputed statistics differ")
    return {
        "complete": True, "questions": len(verified), "contexts_verified": 1428,
        "controls_verified": 714, "all_credit_and_summaries_recomputed": True,
        "output_sha256": digest(raw), "plan_sha256": digest(plan_raw),
        "completion_sha256": digest(args.completion.read_bytes()),
        "verifier_sha256": digest(Path(__file__).read_bytes()),
        "overall": overall, "categories": categories, "limits": report["limits"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "source-plan", "verification", "snapshots", "length",
                 "length-completion", "length-plan", "inputs", "references", "plan",
                 "report", "completion", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Fresh verification destination required")
    write(args.output, verify(args))


if __name__ == "__main__":
    main()

"""Reproduce every public policy against completed, verified saved contexts."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
from pathlib import Path

from benchmarks.diagnostics import packing_composition as research
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from benchmarks.diagnostics.product_packing import measure
from prme.retrieval.config import PackingConfig
from prme.retrieval.models import RetrievalCandidate
from prme.retrieval.packing import pack_context

BUDGETS = (2048, 4096, 8192)
POLICIES = ("density", "score", "balanced")


def completed(root, kind):
    evidence = root / "benchmarks/results/research/2026-09-12"
    if kind == "dev":
        names = ("packing-composition-dev-273fd96.json", "packing-composition-dev-completion-273fd96.json",
                 "packing-composition-dev-verification.json", "packing-composition-dev-verifier-completion.json")
        count, contexts, verification_hash_key = 119, 1428, "report_sha256"
    else:
        names = ("packing-regression.json", "packing-regression-completion.json",
                 "packing-regression-verification.json", "packing-regression-verifier-completion.json")
        count, contexts, verification_hash_key = 381, 4572, "verification_sha256"
    paths = [root / "data/benchmarks" / names[0], *[evidence / name for name in names[1:]]]
    raw = [p.read_bytes() for p in paths]
    report, completion, verified, final = [json.loads(r) for r in raw]
    if (not report["complete"] or report["errors"] or len(report["details"]) != count
            or completion["native_exit_code"] != 0 or completion["output_sha256"] != digest(raw[0])
            or not verified["complete"] or verified["contexts_verified"] != contexts
            or verified["output_sha256"] != digest(raw[0]) or final["native_exit_code"] != 0
            or final[verification_hash_key] != digest(raw[2])):
        raise ValueError("Complete native-exited verified source required")
    return report, {p.name: digest(r) for p, r in zip(paths, raw, strict=True)}


def checked(path, ref, qid):
    if ref["filename"] != digest(qid.encode()) + ".json":
        raise ValueError("Question-bound artifact name required")
    raw = (path / ref["filename"]).read_bytes()
    if digest(raw) != ref["sha256"]:
        raise ValueError("Source artifact changed")
    return json.loads(raw)


def payloads(root, reports):
    data = root / "data/benchmarks"
    for kind, report in reports.items():
        for row in report["details"]:
            qid = row["question_id"]
            if kind == "dev":
                saved = checked(data / "hybrid-lexical-dev-a744fe0-snapshots", row["snapshot"], qid)
                candidates = saved["candidates"]["parser"]
                configs = {str(b): saved["arms"][f"parser:density:{b}"]["packing"] for b in BUDGETS}
                controls = {}
                for b in BUDGETS:
                    score = saved["arms"][f"parser:score:{b}"]
                    controls[f"score:{b}"] = {**score["measurement"], "context": score["context"]}
                    for public, old in (("density", "density"), ("balanced", "head1_quarter")):
                        controls[f"{public}:{b}"] = dict(row["arms"][f"{old}:{b}"])
            else:
                saved = checked(data / "packing-confirmation-1f5375a-candidates", row["candidate_snapshot"], qid)
                contexts = checked(data / "packing-regression-contexts", row["context_file"], qid)
                candidates = saved["candidates"]
                configs = {str(b): {**saved["packing_config"], "token_budget": b} for b in BUDGETS}
                controls = {}
                for public, old in (("density", "density"), ("score", "score"), ("balanced", "head1_quarter")):
                    for b in BUDGETS:
                        controls[f"{public}:{b}"] = {**row["arms"][f"{old}:{b}"],
                                                    "context": contexts["contexts"][f"{old}:{b}"]}
            # Outcome values are irrelevant to implementation parity and never
            # enter the packing process. Reference-based scoring is not rerun.
            for control in controls.values():
                control["evidence_recall"] = control["all_evidence_retained"] = None
            yield {"question_id": qid, "candidates": candidates, "configs": configs, "controls": controls}


def compare(item):
    candidates = [RetrievalCandidate.model_validate(c) for c in item["candidates"]]
    before = canonical([c.model_dump(mode="json") for c in candidates])
    verified = {}
    for budget in BUDGETS:
        for policy in POLICIES:
            key = f"{policy}:{budget}"
            config = PackingConfig.model_validate({**item["configs"][str(budget)], "multipath_ordering": policy})
            expected = item["controls"][key]
            if expected["evidence_recall"] is not None or expected["all_evidence_retained"] is not None:
                raise ValueError("Parity worker must receive no outcome labels")
            bundle = pack_context(candidates, config)
            actual = {**measure(bundle, set(), config), "context": bundle.render()}
            if actual != expected:
                raise ValueError(f"Public context differs for {item['question_id']} {key}")
            verified[key] = actual["context_sha256"]
    if canonical([c.model_dump(mode="json") for c in candidates]) != before:
        raise ValueError("Public packing mutated input candidates")
    return {"question_id": item["question_id"], "contexts": verified}


def run(root, output):
    if output.exists():
        raise ValueError("Fresh parity output required")
    reports, identities = {}, {}
    for kind in ("dev", "regression"):
        reports[kind], identities[kind] = completed(root, kind)
    runtime = research.runtime()
    if runtime["dirty"]:
        raise ValueError("Commit the implementation before full parity verification")
    result = {"complete": False, "runtime": runtime, "inputs": identities,
              "worker_sha256": digest(Path(__file__).read_bytes()), "details": []}
    try:
        iterator = iter(payloads(root, reports))
        with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
            while True:
                batch = []
                for _ in range(8):
                    try:
                        batch.append(next(iterator))
                    except StopIteration:
                        break
                if not batch:
                    break
                result["details"].extend(pool.map(compare, batch))
                print(f"Verified {len(result['details'])}/500 questions", flush=True)
        if research.runtime() != runtime or any(completed(root, kind)[1] != identities[kind] for kind in reports):
            raise ValueError("Inputs or runtime changed during parity verification")
        if len(result["details"]) != 500:
            raise ValueError("All 500 captured questions required")
        result.update(complete=True, contexts_verified=4500,
                      limits="Implementation parity against existing studies, not new quality evidence or answer validation.")
    except BaseException as exc:
        result["error_type"] = type(exc).__name__
        raise
    finally:
        write(output, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.output)


if __name__ == "__main__":
    main()

"""Score whole claims over a bounded lattice of ranked evidence subsets.

This post hoc development diagnostic reuses only typed-reference v2 cases that
were already observed. It scores each complete claim against individual ranked
evidence segments, the ordered group, and its reverse. It accepts no test split,
performs no provider calls, and emits no source text.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import platform
from typing import Any

from benchmarks.diagnostics import llm_aggrefact_partition_scoring as scoring
from benchmarks.diagnostics import llm_aggrefact_typed_tool_recovery as recovery
from benchmarks.integrations import run_llm_aggrefact_evidence_cascade as cascade
from benchmarks.integrations import run_llm_aggrefact_factcg as factcg
from benchmarks.integrations import run_llm_aggrefact_typed_references as typed


CONTROL_SCORE_TOLERANCE = 2e-5


def _load_control(path: Path, expected_ids: tuple[str, ...]) -> dict[str, Any]:
    result = json.loads(path.read_text())
    payload = dict(result)
    claimed = payload.pop("result_sha256", None)
    source = result.get("source_trial", {})
    samples = result.get("scoring", {}).get("samples")
    if (
        result.get("kind") != "llm-aggrefact-atomic-partition-scoring-diagnostic"
        or result.get("development_only") is not True
        or result.get("test_accessed") is not False
        or claimed != factcg._canonical_sha256(payload)
        or source.get("cohort") != "observed"
        or source.get("cohort_cases") != len(expected_ids)
        or source.get("cohort_identity_sha256")
        != factcg._canonical_sha256(list(expected_ids))
        or not isinstance(samples, list)
        or [sample.get("id") for sample in samples] != list(expected_ids)
    ):
        raise ValueError(
            "control scoring result is not intact observed-cohort evidence"
        )
    return result


def _build_lattice_pairs(
    prepared: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    pairs: list[dict[str, Any]] = []
    keys: list[tuple[str, str]] = []
    for item in prepared:
        evidence = [text for _identifier, text in item["evidence_segments"]]
        if not evidence:
            raise ValueError("evidence lattice requires at least one ranked segment")
        variants = [(f"single_{index}", text) for index, text in enumerate(evidence)]
        variants.append(("group_ordered", "\n".join(evidence)))
        if len(evidence) > 1:
            variants.append(("group_reversed", "\n".join(reversed(evidence))))
        for variant, document in variants:
            pairs.append(
                {
                    "dataset": item["dataset"],
                    "doc": document,
                    "claim": item["claim"],
                    "label": item["label"],
                    "contamination_identifier": f"{item['id']}:{variant}",
                }
            )
            keys.append((item["id"], variant))
    return pairs, keys


def _collect_samples(
    prepared: list[dict[str, Any]],
    keys: list[tuple[str, str]],
    scored: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_case: dict[str, dict[str, float]] = {}
    for (identifier, variant), sample in zip(keys, scored, strict=True):
        by_case.setdefault(identifier, {})[variant] = float(
            sample["support_probability"]
        )
    samples: list[dict[str, Any]] = []
    for item in prepared:
        variants = by_case[item["id"]]
        singles = [
            variants[f"single_{index}"]
            for index in range(len(item["evidence_segments"]))
        ]
        ordered = variants["group_ordered"]
        reversed_score = variants.get("group_reversed", ordered)
        max_single = max(singles)
        min_group = min(ordered, reversed_score)
        max_group = max(ordered, reversed_score)
        samples.append(
            {
                "id": item["id"],
                "dataset": item["dataset"],
                "label": item["label"],
                "evidence_count": len(singles),
                "single_support_probabilities": singles,
                "group_ordered_support_probability": ordered,
                "group_reversed_support_probability": reversed_score,
                "projections": {
                    "ordered_group": ordered,
                    "order_robust_group": min_group,
                    "max_single": max_single,
                    "max_subset": max(max_single, max_group),
                    "order_robust_max_subset": max(max_single, min_group),
                    "single_group_consensus": min(max_single, min_group),
                },
            }
        )
    return samples


def _policy_diagnostics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    policies = tuple(samples[0]["projections"])
    result: dict[str, Any] = {}
    for policy in policies:
        projected = [
            {
                "label": sample["label"],
                "support_probability": sample["projections"][policy],
            }
            for sample in samples
        ]
        diagnostics = scoring._diagnostics(projected)
        point = diagnostics["best_precision_at_recall_min"]
        diagnostics["passes_90_precision_60_recall"] = bool(
            point
            and point["supported_precision"] >= 0.90
            and point["supported_recall"] >= 0.60
        )
        result[policy] = diagnostics
    return result


def _validate_control_scores(
    samples: list[dict[str, Any]], control: dict[str, Any]
) -> float:
    expected = {sample["id"]: sample for sample in control["scoring"]["samples"]}
    differences: list[float] = []
    for sample in samples:
        prior = expected[sample["id"]]
        if sample["label"] != prior["label"]:
            raise ValueError("control label differs")
        differences.append(
            abs(
                sample["projections"]["ordered_group"]
                - prior["unpartitioned_support_probability"]
            )
        )
    maximum = max(differences, default=0.0)
    if maximum > CONTROL_SCORE_TOLERANCE:
        raise ValueError(
            "ordered evidence scores do not reproduce the paired control: "
            f"max absolute difference {maximum}"
        )
    return maximum


def run(
    *,
    registration_path: Path,
    base_registration_path: Path,
    base_result_path: Path,
    prior_registration_path: Path,
    prior_result_path: Path,
    registered_result_path: Path,
    prior_state_path: Path,
    control_scoring_path: Path,
    dev_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    _failures, registered_result, _prior_state = recovery._failure_ids(
        registration_path=registration_path,
        result_path=registered_result_path,
        prior_state_path=prior_state_path,
    )
    observed = tuple(registered_result["development"]["observed_ids"])
    control = _load_control(control_scoring_path, observed)
    registration = json.loads(registration_path.read_text())
    selected, base_registration = typed._validate_registration(
        registration,
        project_root=project_root,
        base_registration_path=base_registration_path,
        base_result_path=base_result_path,
        prior_registration_path=prior_registration_path,
        prior_result_path=prior_result_path,
        dev_path=dev_path,
    )
    rows_by_id = {row["contamination_identifier"]: row for row in selected}
    if len(rows_by_id) != len(selected) or not set(observed) <= set(rows_by_id):
        raise ValueError("observed cases do not belong to the registered cohort")
    prepared, ranker_runtime = cascade._prepare_evidence(
        [rows_by_id[identifier] for identifier in observed],
        model_spec=base_registration["model"],
        top_k=registration["protocol"]["ranker_top_k"],
        batch_size=registration["protocol"]["ranker_batch_size"],
    )
    pairs, keys = _build_lattice_pairs(prepared)
    scored, scoring_runtime = factcg._score_rows(
        pairs,
        model_spec=base_registration["model"],
        batch_size=registration["protocol"]["atomic_factcg_batch_size"],
    )
    samples = _collect_samples(prepared, keys, scored)
    max_control_difference = _validate_control_scores(samples, control)
    diagnostics = _policy_diagnostics(samples)
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-evidence-lattice-diagnostic",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "test_accessed": False,
        "source_trial": {
            "registration_sha256": factcg._sha256_file(registration_path),
            "result_sha256": factcg._sha256_file(registered_result_path),
            "result_canonical_sha256": registered_result["result_sha256"],
            "cohort": "observed",
            "cohort_cases": len(observed),
            "cohort_identity_sha256": factcg._canonical_sha256(list(observed)),
        },
        "control": {
            "file_sha256": factcg._sha256_file(control_scoring_path),
            "canonical_result_sha256": control["result_sha256"],
            "ordered_score_max_absolute_difference": max_control_difference,
            "ordered_score_absolute_tolerance": CONTROL_SCORE_TOLERANCE,
        },
        "scoring": {
            "model": base_registration["model"],
            "evidence_lattice": [
                "each_ranked_segment",
                "ordered_group",
                "reversed_group",
            ],
            "label_counts": dict(
                sorted(Counter(str(item["label"]) for item in prepared).items())
            ),
            "policy_diagnostics": diagnostics,
            "samples": samples,
        },
        "protocol": {
            "runner_sha256": factcg._sha256_file(Path(inspect.getfile(run)).resolve()),
            "factcg_runner_sha256": factcg._sha256_file(
                Path(factcg.__file__).resolve()
            ),
            "source_text_emitted": False,
            "provider_calls": False,
            "reference": {
                "title": "Minimal Evidence Group Identification for Claim Verification",
                "doi": "10.18653/v1/2025.trustnlp-main.8",
            },
        },
        "runtime": {
            "ranker": ranker_runtime,
            "factcg": scoring_runtime,
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "limitations": [
            "Post hoc analysis of cases already observed during typed-reference v2.",
            "The lattice is bounded to the two registered ranked evidence segments.",
            "Static score projections are diagnostics, not cross-fitted policies.",
            "No external test split was accepted or accessed.",
        ],
    }
    result["result_sha256"] = factcg._canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--base-registration", type=Path, required=True)
    parser.add_argument("--base-result", type=Path, required=True)
    parser.add_argument("--prior-registration", type=Path, required=True)
    parser.add_argument("--prior-result", type=Path, required=True)
    parser.add_argument("--registered-result", type=Path, required=True)
    parser.add_argument("--prior-state", type=Path, required=True)
    parser.add_argument("--control-scoring", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = run(
        registration_path=args.registration.resolve(),
        base_registration_path=args.base_registration.resolve(),
        base_result_path=args.base_result.resolve(),
        prior_registration_path=args.prior_registration.resolve(),
        prior_result_path=args.prior_result.resolve(),
        registered_result_path=args.registered_result.resolve(),
        prior_state_path=args.prior_state.resolve(),
        control_scoring_path=args.control_scoring.resolve(),
        dev_path=args.dev.resolve(),
        output_path=args.output.resolve(),
        project_root=args.project_root.resolve(),
    )
    print(
        json.dumps(
            {
                "cases": result["source_trial"]["cohort_cases"],
                "policies": {
                    name: value["best_balanced_accuracy"]
                    for name, value in result["scoring"]["policy_diagnostics"].items()
                },
                "result_sha256": result["result_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

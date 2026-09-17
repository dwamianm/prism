"""Score validated atomic partitions with the pinned FactCG task model.

This consumes a completed private partition state over a previously observed
development cohort. It reconstructs every atom from authoritative source-token
IDs, scores every atom against the ranked evidence, and emits no source text. It
accepts no test split and cannot unlock evaluation.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import platform
from typing import Any

from benchmarks.diagnostics import llm_aggrefact_atomic_partition as partitioning
from benchmarks.diagnostics import llm_aggrefact_typed_tool_recovery as recovery
from benchmarks.integrations import run_llm_aggrefact_evidence_cascade as cascade
from benchmarks.integrations import run_llm_aggrefact_factcg as factcg
from benchmarks.integrations import run_llm_aggrefact_typed_references as typed


def _canonical_without_result_sha(value: dict[str, Any]) -> str:
    payload = dict(value)
    claimed = payload.pop("result_sha256", None)
    observed = factcg._canonical_sha256(payload)
    if claimed != observed:
        raise ValueError("result canonical checksum differs")
    return observed


def _load_partition_artifacts(
    *,
    result_path: Path,
    state_path: Path,
    expected_ids: tuple[str, ...],
    cohort: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = json.loads(result_path.read_text())
    canonical = _canonical_without_result_sha(result)
    development = result.get("development", {})
    source_trial = result.get("source_trial", {})
    legacy_failure_cohort = (
        cohort == "failures"
        and "cohort" not in source_trial
        and source_trial.get("observed_failures") == len(expected_ids)
        and source_trial.get("failure_identity_sha256")
        == factcg._canonical_sha256(list(expected_ids))
    )
    current_cohort = (
        source_trial.get("cohort") == cohort
        and source_trial.get("cohort_cases") == len(expected_ids)
        and source_trial.get("cohort_identity_sha256")
        == factcg._canonical_sha256(list(expected_ids))
    )
    if (
        result.get("kind") != "llm-aggrefact-atomic-partition-diagnostic"
        or result.get("development_only") is not True
        or result.get("test_accessed") is not False
        or result.get("result_sha256") != canonical
        or development.get("cases") != len(expected_ids)
        or development.get("complete_cases") != len(expected_ids)
        or development.get("safe_abstention_cases") != 0
        or development.get("unsafe_fallback_cases") != 0
        or not (legacy_failure_cohort or current_cohort)
    ):
        raise ValueError("partition result is not a complete sealed diagnostic")
    state = json.loads(state_path.read_text())
    jobs = state.get("jobs")
    if (
        state.get("schema_version") != 1
        or state.get("complete") is not True
        or not isinstance(jobs, dict)
        or set(jobs) != set(expected_ids)
        or any(job.get("status") != "complete" for job in jobs.values())
        or state.get("identity", {}).get("run", {}).get("runner_sha256")
        != result.get("protocol", {}).get("runner_sha256")
        or state.get("identity", {}).get("run", {}).get("verifier_manifest_digest")
        != result.get("transport", {}).get("manifest_digest")
    ):
        raise ValueError("private partition state does not match the public result")
    for job in jobs.values():
        for attempt in job.get("semantic_attempts", []):
            response = attempt.get("response")
            if response is not None and attempt.get(
                "response_sha256"
            ) != factcg._canonical_sha256(response):
                raise ValueError("private provider response checksum differs")
    return result, state


def _final_partition(
    item: dict[str, Any], job: dict[str, Any]
) -> partitioning._AtomicPartition:
    successful = [
        attempt
        for attempt in job["semantic_attempts"]
        if attempt.get("validation_errors") == []
    ]
    if len(successful) != 1 or not isinstance(successful[0].get("arguments"), dict):
        raise ValueError("completed partition job has no unique valid output")
    arguments = successful[0]["arguments"]
    valid, errors = partitioning._strict_validate(item, arguments)
    if not valid or errors:
        raise ValueError("saved atomic partition no longer validates")
    return partitioning._AtomicPartition.model_validate(arguments)


def _build_atomic_pairs(
    prepared: list[dict[str, Any]], state: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str], dict[str, dict[str, Any]]]:
    pairs: list[dict[str, Any]] = []
    owners: list[str] = []
    partition_metadata: dict[str, dict[str, Any]] = {}
    for item in prepared:
        value = _final_partition(item, state["jobs"][item["id"]])
        evidence = "\n".join(text for _identifier, text in item["evidence_segments"])
        atom_hashes: list[str] = []
        for atom_index in range(1, len(value.atoms) + 1):
            atom_text, token_ids = partitioning._atom_text(
                item["claim"], value, atom_index
            )
            atom_hashes.append(
                factcg._canonical_sha256(
                    {
                        "token_ids": list(token_ids),
                        "text_sha256": hashlib.sha256(atom_text.encode()).hexdigest(),
                    }
                )
            )
            pairs.append(
                {
                    "dataset": item["dataset"],
                    "doc": evidence,
                    "claim": atom_text,
                    "label": item["label"],
                    "contamination_identifier": f"{item['id']}:atom:{atom_index}",
                }
            )
            owners.append(item["id"])
        partition_metadata[item["id"]] = {
            "atom_count": len(value.atoms),
            "partition_sha256": factcg._canonical_sha256(value.model_dump(mode="json")),
            "atom_source_sha256": factcg._canonical_sha256(atom_hashes),
        }
    return pairs, owners, partition_metadata


def _build_unpartitioned_pairs(
    prepared: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "dataset": item["dataset"],
            "doc": "\n".join(text for _identifier, text in item["evidence_segments"]),
            "claim": item["claim"],
            "label": item["label"],
            "contamination_identifier": f"{item['id']}:unpartitioned",
        }
        for item in prepared
    ]


def _diagnostics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    thresholds = sorted(
        {0.0, 1.0, *(float(sample["support_probability"]) for sample in samples)}
    )
    points = [factcg._metrics(samples, threshold) for threshold in thresholds]
    recall_floor = [point for point in points if point["supported_recall"] >= 0.60]
    precision_floor = [
        point for point in points if point["supported_precision"] >= 0.90
    ]
    return {
        "threshold_zero": points[0],
        "best_balanced_accuracy": max(
            points,
            key=lambda point: (
                point["balanced_accuracy"],
                point["supported_precision"],
                point["supported_recall"],
            ),
        ),
        "best_precision_at_recall_min": (
            max(
                recall_floor,
                key=lambda point: (
                    point["supported_precision"],
                    point["supported_recall"],
                    -point["threshold"],
                ),
            )
            if recall_floor
            else None
        ),
        "best_recall_at_precision_min": (
            max(
                precision_floor,
                key=lambda point: (
                    point["supported_recall"],
                    point["supported_precision"],
                    -point["threshold"],
                ),
            )
            if precision_floor
            else None
        ),
    }


def run(
    *,
    registration_path: Path,
    base_registration_path: Path,
    base_result_path: Path,
    prior_registration_path: Path,
    prior_result_path: Path,
    registered_result_path: Path,
    prior_state_path: Path,
    partition_result_path: Path,
    partition_state_path: Path,
    dev_path: Path,
    output_path: Path,
    project_root: Path,
    cohort: str = "failures",
) -> dict[str, Any]:
    failures, registered_result, _prior_state = recovery._failure_ids(
        registration_path=registration_path,
        result_path=registered_result_path,
        prior_state_path=prior_state_path,
    )
    observed = tuple(registered_result["development"]["observed_ids"])
    if cohort == "failures":
        cohort_ids = failures
    elif cohort == "observed":
        cohort_ids = observed
    else:
        raise ValueError(f"unsupported cohort: {cohort}")
    partition_result, partition_state = _load_partition_artifacts(
        result_path=partition_result_path,
        state_path=partition_state_path,
        expected_ids=cohort_ids,
        cohort=cohort,
    )
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
    if not set(cohort_ids) <= set(rows_by_id):
        raise ValueError(
            "partition cohort is not part of the registered development cohort"
        )
    prepared, ranker_runtime = cascade._prepare_evidence(
        [rows_by_id[identifier] for identifier in cohort_ids],
        model_spec=base_registration["model"],
        top_k=registration["protocol"]["ranker_top_k"],
        batch_size=registration["protocol"]["ranker_batch_size"],
    )
    pairs, owners, metadata = _build_atomic_pairs(prepared, partition_state)
    unpartitioned_pairs = _build_unpartitioned_pairs(prepared)
    scored, scoring_runtime = factcg._score_rows(
        pairs + unpartitioned_pairs,
        model_spec=base_registration["model"],
        batch_size=registration["protocol"]["atomic_factcg_batch_size"],
    )
    by_owner: dict[str, list[float]] = defaultdict(list)
    for owner, sample in zip(owners, scored[: len(pairs)], strict=True):
        by_owner[owner].append(float(sample["support_probability"]))
    unpartitioned_scores = {
        item["id"]: float(sample["support_probability"])
        for item, sample in zip(prepared, scored[len(pairs) :], strict=True)
    }
    samples: list[dict[str, Any]] = []
    for item in prepared:
        atomic_scores = by_owner[item["id"]]
        value = {
            "id": item["id"],
            "dataset": item["dataset"],
            "label": item["label"],
            **metadata[item["id"]],
            "atomic_support_probabilities": atomic_scores,
            "support_probability": min(atomic_scores),
            "unpartitioned_support_probability": unpartitioned_scores[item["id"]],
        }
        samples.append(value)
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-atomic-partition-scoring-diagnostic",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "test_accessed": False,
        "source_trial": {
            "registration_sha256": factcg._sha256_file(registration_path),
            "result_sha256": factcg._sha256_file(registered_result_path),
            "result_canonical_sha256": registered_result["result_sha256"],
            "observed_cases": len(observed),
            "observed_failures": len(failures),
            "cohort": cohort,
            "cohort_cases": len(cohort_ids),
            "cohort_identity_sha256": factcg._canonical_sha256(list(cohort_ids)),
        },
        "partition": {
            "result_file_sha256": factcg._sha256_file(partition_result_path),
            "result_canonical_sha256": partition_result["result_sha256"],
            "private_state_sha256": factcg._sha256_file(partition_state_path),
            "provider": partition_result["transport"],
            "representation": partition_result["representation"],
        },
        "scoring": {
            "model": base_registration["model"],
            "parent_rule": "minimum_atomic_support_probability",
            "evidence": "all_ranked_top_two_source_segments",
            "diagnostics": _diagnostics(samples),
            "unpartitioned_diagnostics": _diagnostics(
                [
                    {
                        **sample,
                        "support_probability": sample[
                            "unpartitioned_support_probability"
                        ],
                    }
                    for sample in samples
                ]
            ),
            "label_counts": dict(
                sorted(Counter(str(item["label"]) for item in prepared).items())
            ),
            "samples": samples,
        },
        "protocol": {
            "runner_sha256": factcg._sha256_file(Path(inspect.getfile(run)).resolve()),
            "partition_runner_sha256": partition_result["protocol"]["runner_sha256"],
            "factcg_runner_sha256": factcg._sha256_file(
                Path(factcg.__file__).resolve()
            ),
            "source_text_emitted": False,
        },
        "runtime": {
            "ranker": ranker_runtime,
            "atomic_factcg": scoring_runtime,
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "limitations": [
            "Post hoc scoring of cases already observed during typed-reference v2.",
            "The observed cohort is not an independent quality estimate.",
            "Provider disagreement requires full observed-cohort comparison before registration.",
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
    parser.add_argument("--partition-result", type=Path, required=True)
    parser.add_argument("--partition-state", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--cohort",
        choices=partitioning.COHORTS,
        default="failures",
    )
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
        partition_result_path=args.partition_result.resolve(),
        partition_state_path=args.partition_state.resolve(),
        dev_path=args.dev.resolve(),
        output_path=args.output.resolve(),
        project_root=args.project_root.resolve(),
        cohort=args.cohort,
    )
    print(
        json.dumps(
            {
                "cases": len(result["scoring"]["samples"]),
                "best_balanced_accuracy": result["scoring"]["diagnostics"][
                    "best_balanced_accuracy"
                ],
                "result_sha256": result["result_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

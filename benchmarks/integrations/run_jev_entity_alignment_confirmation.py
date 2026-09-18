"""Register and run the frozen Jev entity-alignment confirmation."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
from typing import Any

import httpx

from benchmarks.integrations import run_jev_claim_verification as common
from benchmarks.integrations import run_jev_entity_alignment as development


SPLITS = ("valid", "test")
SELECTED_ARM = "score_route"


def _pairs(root: Path) -> list[dict[str, Any]]:
    return [
        pair
        for split in SPLITS
        for pair in development._load_pairs(root, split=split)
    ]


def _upstream(
    registration_path: Path, result_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    registration = json.loads(registration_path.read_text())
    result = json.loads(result_path.read_text())
    if (
        registration.get("kind")
        != "jev-entity-alignment-development-registration"
        or result.get("kind") != "jev-entity-alignment-development-result"
        or not result.get("passed")
        or result.get("selected_arm") != SELECTED_ARM
        or result.get("registration_sha256")
        != common._sha256_file(registration_path)
        or result.get("protocol") != registration.get("protocol")
    ):
        raise ValueError("development evidence does not authorize confirmation")
    return registration, result


def _evaluation(cases: int) -> dict[str, Any]:
    return {
        "gates": {
            "pairs_evaluated_min": cases,
            "response_validity_min": cases,
            "merge_precision_min": 0.98,
            "merge_recall_min": 0.50,
            "false_merges_max": 1,
            "recall_gain_over_current_min": 0.25,
        },
        "decision_rule": (
            "Only a complete pass permits implementation of an optional advisor. "
            "The confirmation never authorizes automatic graph mutation."
        ),
    }


def create_registration(
    *,
    dataset_root: Path,
    development_registration_path: Path,
    development_result_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    source_registration, source_result = _upstream(
        development_registration_path, development_result_path
    )
    pairs = _pairs(dataset_root)
    labels = Counter(str(pair["label"]) for pair in pairs)
    dataset = development._dataset(dataset_root)
    dataset["split"] = list(SPLITS)
    dataset["test_access"] = "confirmation_only_after_development_pass"
    registration = {
        "schema_version": 1,
        "kind": "jev-entity-alignment-confirmation-registration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing confirms the frozen Jev score route on the untouched "
            "BeerAdvocate-RateBeer validation and test pairs. It supports an "
            "optional alignment advisor, not autonomous graph mutation or "
            "cross-domain superiority."
        ),
        "source": {
            "prme_revision": common._git_revision(project_root),
            "runner_sha256": common._sha256_file(Path(__file__).resolve()),
            "development_registration_sha256": common._sha256_file(
                development_registration_path
            ),
            "development_result_sha256": common._sha256_file(development_result_path),
            "development_result_canonical_sha256": source_result["result_sha256"],
        },
        "dataset": dataset,
        "cohort": {
            "role": "confirmation",
            "splits": list(SPLITS),
            "pairs": len(pairs),
            "counts_by_label": dict(sorted(labels.items())),
            "identity_sha256": common._canonical_sha256(
                [pair["id"] for pair in pairs]
            ),
        },
        "provider": source_registration["provider"],
        "protocol": {
            **source_registration["protocol"],
            "selected_arm": SELECTED_ARM,
            "decision_arms": {
                SELECTED_ARM: source_registration["protocol"]["decision_arms"][
                    SELECTED_ARM
                ]
            },
        },
        "evaluation": _evaluation(len(pairs)),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any],
    *,
    dataset_root: Path,
    development_registration_path: Path,
    development_result_path: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "jev-entity-alignment-confirmation-registration"
    ):
        raise ValueError("invalid Jev entity-alignment confirmation registration")
    revision = registration.get("source", {}).get("prme_revision")
    if not isinstance(revision, str) or not common._git_is_ancestor(revision, project_root):
        raise ValueError("registered PRME revision is not an ancestor")
    if registration.get("source", {}).get("runner_sha256") != common._sha256_file(
        Path(__file__).resolve()
    ):
        raise ValueError("registered confirmation runner differs")
    source_registration, source_result = _upstream(
        development_registration_path, development_result_path
    )
    expected_source = {
        "prme_revision": revision,
        "runner_sha256": common._sha256_file(Path(__file__).resolve()),
        "development_registration_sha256": common._sha256_file(
            development_registration_path
        ),
        "development_result_sha256": common._sha256_file(development_result_path),
        "development_result_canonical_sha256": source_result["result_sha256"],
    }
    if registration.get("source") != expected_source:
        raise ValueError("registered development evidence differs")
    pairs = _pairs(dataset_root)
    labels = Counter(str(pair["label"]) for pair in pairs)
    dataset = development._dataset(dataset_root)
    dataset["split"] = list(SPLITS)
    dataset["test_access"] = "confirmation_only_after_development_pass"
    expected_protocol = {
        **source_registration["protocol"],
        "selected_arm": SELECTED_ARM,
        "decision_arms": {
            SELECTED_ARM: source_registration["protocol"]["decision_arms"][
                SELECTED_ARM
            ]
        },
    }
    if (
        registration.get("dataset") != dataset
        or registration.get("cohort")
        != {
            "role": "confirmation",
            "splits": list(SPLITS),
            "pairs": len(pairs),
            "counts_by_label": dict(sorted(labels.items())),
            "identity_sha256": common._canonical_sha256(
                [pair["id"] for pair in pairs]
            ),
        }
        or registration.get("provider") != source_registration["provider"]
        or registration.get("protocol") != expected_protocol
        or registration.get("evaluation") != _evaluation(len(pairs))
    ):
        raise ValueError("registered confirmation inputs differ")
    return pairs


async def _execute(
    pairs: list[dict[str, Any]],
    *,
    registration_path: Path,
    registration: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    identity = {
        "kind": "jev_entity_alignment_confirmation_v1",
        "registration_sha256": common._sha256_file(registration_path),
        "cohort_identity_sha256": registration["cohort"]["identity_sha256"],
        "model": development.MODEL,
        "questions_sha256": common._canonical_sha256(development.QUESTIONS),
        "selected_arm": SELECTED_ARM,
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("identity") != identity:
            raise ValueError("durable Jev confirmation state belongs to another run")
    else:
        state = {"identity": identity, "results": {}}
        common._atomic_write(state_path, state)
    key = common._api_key()
    semaphore = asyncio.Semaphore(development.CONCURRENCY)
    lock = asyncio.Lock()
    completed = 0
    async with httpx.AsyncClient(timeout=development.TIMEOUT_SECONDS) as client:

        async def execute(pair: dict[str, Any]) -> None:
            nonlocal completed
            identifier = pair["id"]
            if identifier in state["results"]:
                completed += 1
                return
            async with semaphore:
                value = await development._request_one(client, key, pair)
            async with lock:
                state["results"][identifier] = value
                common._atomic_write(state_path, state)
                completed += 1
                if completed % 20 == 0 or completed == len(pairs):
                    print(f"[jev-confirm] completed {completed}/{len(pairs)}", flush=True)

        await asyncio.gather(*(execute(pair) for pair in pairs))
    return state


def _result(
    pairs: list[dict[str, Any]],
    state: dict[str, Any],
    registration: dict[str, Any],
    registration_path: Path,
) -> dict[str, Any]:
    labels = [pair["label"] == 1 for pair in pairs]
    baseline = development._metrics(
        labels, [development._baseline_decision(pair) for pair in pairs]
    )
    predictions: list[bool] = []
    samples: list[dict[str, Any]] = []
    input_tokens = output_tokens = attempts = 0
    elapsed: list[float] = []
    for pair in pairs:
        item = state["results"].get(pair["id"])
        if not isinstance(item, dict):
            raise ValueError(f"missing durable response for {pair['id']}")
        response = development._validate_response(item["response"])
        decision = development._decisions(response)[SELECTED_ARM]
        predictions.append(decision)
        link = response["answers"]["link_state"]
        usage = response["usage"]
        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]
        attempts += item["attempts"]
        elapsed.append(item["elapsed_seconds"])
        samples.append(
            {
                "id": pair["id"],
                "label": pair["label"],
                "link_score": link["score"],
                "link_confidence": link["confidence"],
                "link_probabilities": link["probabilities"],
                "predicted_merge": decision,
                "attempts": item["attempts"],
                "elapsed_seconds": item["elapsed_seconds"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            }
        )
    metrics = development._metrics(labels, predictions)
    gates = registration["evaluation"]["gates"]
    gate_results = {
        "pairs_evaluated_min": sum(
            metrics[key] for key in ("tp", "fp", "tn", "fn")
        )
        >= gates["pairs_evaluated_min"],
        "response_validity_min": len(samples) >= gates["response_validity_min"],
        "merge_precision_min": metrics["merge_precision"] >= gates["merge_precision_min"],
        "merge_recall_min": metrics["merge_recall"] >= gates["merge_recall_min"],
        "false_merges_max": metrics["fp"] <= gates["false_merges_max"],
        "recall_gain_over_current_min": metrics["merge_recall"]
        - baseline["merge_recall"]
        >= gates["recall_gain_over_current_min"],
    }
    passed = all(gate_results.values())
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "jev-entity-alignment-confirmation-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": common._sha256_file(registration_path),
        "cohort": registration["cohort"],
        "provider": registration["provider"],
        "protocol": registration["protocol"],
        "confirmation": {
            "current_prme_baseline": baseline,
            "selected_arm": SELECTED_ARM,
            "metrics": metrics,
            "gate_results": gate_results,
            "samples": samples,
        },
        "runtime": {
            "requests": len(samples),
            "attempts": attempts,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "median_request_seconds": sorted(elapsed)[len(elapsed) // 2],
            "max_request_seconds": max(elapsed),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "passed": passed,
        "decision": (
            "eligible_for_optional_advisor_implementation"
            if passed
            else "jev_entity_alignment_not_confirmed"
        ),
        "limitations": [
            "Held-out splits from the same public product-matching dataset.",
            "Candidate pairs are supplied; this does not evaluate candidate generation.",
            "The cookbook publicly described this dataset for an earlier Jev version.",
            "No graph mutation occurred and no cross-domain superiority is claimed.",
        ],
    }
    result["result_sha256"] = common._canonical_sha256(result)
    return result


async def run(
    *,
    registration_path: Path,
    dataset_root: Path,
    development_registration_path: Path,
    development_result_path: Path,
    state_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    pairs = _validate_registration(
        registration,
        dataset_root=dataset_root,
        development_registration_path=development_registration_path,
        development_result_path=development_result_path,
        project_root=project_root,
    )
    state = await _execute(
        pairs,
        registration_path=registration_path,
        registration=registration,
        state_path=state_path,
    )
    result = _result(pairs, state, registration, registration_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--development-registration", type=Path, required=True)
    parser.add_argument("--development-result", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    _common_args(register)
    register.add_argument("--output", type=Path, required=True)
    execute = subparsers.add_parser("run")
    _common_args(execute)
    execute.add_argument("--registration", type=Path, required=True)
    execute.add_argument("--state", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    common_args = {
        "dataset_root": args.dataset_root.resolve(),
        "development_registration_path": args.development_registration.resolve(),
        "development_result_path": args.development_result.resolve(),
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        result = create_registration(output_path=args.output.resolve(), **common_args)
        print(json.dumps({"cohort": result["cohort"], "protocol": result["protocol"]}, indent=2))
        return
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            **common_args,
        )
    )
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "baseline": result["confirmation"]["current_prme_baseline"],
                "metrics": result["confirmation"]["metrics"],
                "gates": result["confirmation"]["gate_results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

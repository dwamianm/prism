"""Register and run a pinned Jev entity-alignment development trial."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import time
from typing import Any

import httpx

from benchmarks.integrations import run_jev_claim_verification as common


MODEL = "jev-1.13.0"
API_URL = common.API_URL
SPLIT = "train"
CONCURRENCY = 6
TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 3
EXPECTED_FILES = ("tableA.csv", "tableB.csv", "train.csv", "valid.csv", "test.csv")
LEVELS = [
    "They describe two different products.",
    (
        "They describe closely related products that may or may not be the same "
        "one: a variant, a special edition, or a name that could plausibly refer "
        "to either."
    ),
    "They describe one and the same product.",
]
QUESTIONS: dict[str, Any] = {
    "link_state": {
        "type": "score",
        "instructions": "How do the two entity descriptions relate as products?",
        "criteria": LEVELS,
    },
    "same_name": {
        "type": "noul",
        "instructions": "Do the two entities state the same beer name?",
    },
    "same_brewery": {
        "type": "noul",
        "instructions": "Are the two entities from the same brewery?",
    },
    "same_style": {
        "type": "noul",
        "instructions": "Do the two entities describe the same beer style?",
    },
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    import csv

    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _entity(row: dict[str, str]) -> dict[str, str]:
    return {
        "name": row["Beer_Name"],
        "brewery": row["Brew_Factory_Name"],
        "style": row["Style"],
        "abv": row["ABV"],
    }


def _load_pairs(root: Path, split: str = SPLIT) -> list[dict[str, Any]]:
    left = {row["id"]: row for row in _read_csv(root / "tableA.csv")}
    right = {row["id"]: row for row in _read_csv(root / "tableB.csv")}
    pairs: list[dict[str, Any]] = []
    for index, row in enumerate(_read_csv(root / f"{split}.csv")):
        left_id = row["ltable_id"]
        right_id = row["rtable_id"]
        if left_id not in left or right_id not in right or row["label"] not in {"0", "1"}:
            raise ValueError("entity-alignment dataset row is invalid")
        pairs.append(
            {
                "id": f"{split}-{index:04d}-{left_id}-{right_id}",
                "label": int(row["label"]),
                "entity_a": _entity(left[left_id]),
                "entity_b": _entity(right[right_id]),
            }
        )
    return pairs


def _dataset(root: Path) -> dict[str, Any]:
    files = {
        name: {"sha256": common._sha256_file(root / name), "bytes": (root / name).stat().st_size}
        for name in EXPECTED_FILES
    }
    return {
        "name": "DeepMatcher Structured BeerAdvo-RateBeer",
        "source": (
            "https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/"
            "Structured/Beer/exp_data/"
        ),
        "split": SPLIT,
        "files": files,
        "test_access": "forbidden_until_development_passes",
    }


def _protocol() -> dict[str, Any]:
    return {
        "task": "candidate_pair_entity_alignment",
        "state_fields": ["entity_a", "entity_b"],
        "questions": QUESTIONS,
        "questions_sha256": common._canonical_sha256(QUESTIONS),
        "model": MODEL,
        "api_url": API_URL,
        "decision_arms": {
            "score_route": "link_state.score >= 1.5",
            "confident_score": "link_state.score >= 1.5 and confidence >= 0.8",
            "field_guarded": (
                "confident_score and same_name.noul > 0.5 and "
                "same_brewery.noul > 0.5"
            ),
        },
        "review_rule": "link_state.score >= 0.5",
        "current_prme_baseline": (
            "stripped case-insensitive exact entity name or built-in known alias"
        ),
        "concurrency": CONCURRENCY,
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
        "retryable_statuses": sorted(common.RETRYABLE_STATUSES),
        "emit_entity_text": False,
    }


def _evaluation(cases: int) -> dict[str, Any]:
    return {
        "gates_per_arm": {
            "pairs_evaluated_min": cases,
            "response_validity_min": cases,
            "merge_precision_min": 0.98,
            "merge_recall_min": 0.50,
            "false_merges_max": 1,
            "recall_gain_over_current_min": 0.25,
        },
        "selection": (
            "Among passing arms, choose greatest merge recall, then precision, "
            "then lexicographically smallest name."
        ),
        "decision_rule": (
            "Do not implement or access held-out entity-alignment pairs unless at "
            "least one frozen development arm passes every gate."
        ),
    }


def create_registration(
    *, dataset_root: Path, output_path: Path, project_root: Path
) -> dict[str, Any]:
    pairs = _load_pairs(dataset_root)
    labels = Counter(str(pair["label"]) for pair in pairs)
    registration = {
        "schema_version": 1,
        "kind": "jev-entity-alignment-development-registration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing establishes development evidence that pinned Jev improves "
            "candidate-pair entity alignment over PRME's conservative automatic "
            "name matching. It does not authorize graph mutation or establish "
            "cross-domain confirmation."
        ),
        "source": {
            "prme_revision": common._git_revision(project_root),
            "runner_sha256": common._sha256_file(Path(__file__).resolve()),
        },
        "dataset": _dataset(dataset_root),
        "cohort": {
            "role": "development",
            "pairs": len(pairs),
            "counts_by_label": dict(sorted(labels.items())),
            "identity_sha256": common._canonical_sha256(
                [pair["id"] for pair in pairs]
            ),
        },
        "provider": {
            "name": "TypeSafe AI",
            "model": MODEL,
            "endpoint": API_URL,
            "credential_environment": ["JEV_API_KEY", "TYPESAFE_API_KEY"],
        },
        "protocol": _protocol(),
        "evaluation": _evaluation(len(pairs)),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any], *, dataset_root: Path, project_root: Path
) -> list[dict[str, Any]]:
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "jev-entity-alignment-development-registration"
    ):
        raise ValueError("invalid Jev entity-alignment registration")
    revision = registration.get("source", {}).get("prme_revision")
    if not isinstance(revision, str) or not common._git_is_ancestor(revision, project_root):
        raise ValueError("registered PRME revision is not an ancestor")
    if registration.get("source", {}).get("runner_sha256") != common._sha256_file(
        Path(__file__).resolve()
    ):
        raise ValueError("registered entity-alignment runner differs")
    if registration.get("dataset") != _dataset(dataset_root):
        raise ValueError("registered entity-alignment dataset differs")
    pairs = _load_pairs(dataset_root)
    labels = Counter(str(pair["label"]) for pair in pairs)
    if registration.get("cohort") != {
        "role": "development",
        "pairs": len(pairs),
        "counts_by_label": dict(sorted(labels.items())),
        "identity_sha256": common._canonical_sha256([pair["id"] for pair in pairs]),
    }:
        raise ValueError("registered entity-alignment cohort differs")
    if registration.get("protocol") != _protocol() or registration.get(
        "evaluation"
    ) != _evaluation(len(pairs)):
        raise ValueError("registered entity-alignment protocol differs")
    return pairs


def _validate_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("Jev response model differs from pinned registration")
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
        raise ValueError("Jev entity-alignment answer set is invalid")
    link = answers["link_state"]
    if not isinstance(link, dict) or link.get("type") != "score":
        raise ValueError("Jev link-state answer is invalid")
    score = link.get("score")
    confidence = link.get("confidence")
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 2:
        raise ValueError("Jev link-state score is invalid")
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        raise ValueError("Jev link-state confidence is invalid")
    probabilities = link.get("probabilities")
    legend = link.get("legend")
    if (
        not isinstance(probabilities, dict)
        or set(probabilities) != {"0", "1", "2"}
        or not isinstance(legend, dict)
        or [legend.get(str(index)) for index in range(3)] != LEVELS
    ):
        raise ValueError("Jev link-state distribution is invalid")
    if any(
        type(item) not in (int, float)
        or not math.isfinite(item)
        or not 0 <= item <= 1
        for item in probabilities.values()
    ) or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.02):
        raise ValueError("Jev link-state probabilities are invalid")
    for name in ("same_name", "same_brewery", "same_style"):
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise ValueError(f"Jev {name} answer is invalid")
        item = answer.get("noul")
        if type(item) not in (int, float) or not math.isfinite(item) or not 0 <= item <= 1:
            raise ValueError(f"Jev {name} probability is invalid")
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Jev entity-alignment usage is invalid")
    return value


async def _request_one(
    client: httpx.AsyncClient, key: str, pair: dict[str, Any]
) -> dict[str, Any]:
    payload = {
        "model": MODEL,
        "state": {"entity_a": pair["entity_a"], "entity_b": pair["entity_b"]},
        "questions": QUESTIONS,
    }
    started = time.perf_counter()
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None
        try:
            response = await client.post(
                API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {key}"},
            )
            if response.status_code in common.RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS:
                errors.append(f"http_{response.status_code}")
                await asyncio.sleep(common._retry_delay(response, attempt))
                continue
            response.raise_for_status()
            return {
                "response": _validate_response(response.json()),
                "attempts": attempt,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "transient_errors": errors,
            }
        except (httpx.TransportError, httpx.TimeoutException):
            errors.append("transport_error")
            if attempt >= MAX_ATTEMPTS:
                raise
            await asyncio.sleep(common._retry_delay(response, attempt))
    raise AssertionError("unreachable request loop")


async def _execute(
    pairs: list[dict[str, Any]],
    *,
    registration_path: Path,
    registration: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    identity = {
        "kind": "jev_entity_alignment_development_v1",
        "registration_sha256": common._sha256_file(registration_path),
        "cohort_identity_sha256": registration["cohort"]["identity_sha256"],
        "model": MODEL,
        "questions_sha256": common._canonical_sha256(QUESTIONS),
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("identity") != identity:
            raise ValueError("durable Jev entity state belongs to another run")
    else:
        state = {"identity": identity, "results": {}}
        common._atomic_write(state_path, state)
    key = common._api_key()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    completed = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:

        async def execute(pair: dict[str, Any]) -> None:
            nonlocal completed
            identifier = pair["id"]
            if identifier in state["results"]:
                completed += 1
                return
            async with semaphore:
                value = await _request_one(client, key, pair)
            async with lock:
                state["results"][identifier] = value
                common._atomic_write(state_path, state)
                completed += 1
                if completed % 20 == 0 or completed == len(pairs):
                    print(f"[jev-entity] completed {completed}/{len(pairs)}", flush=True)

        await asyncio.gather(*(execute(pair) for pair in pairs))
    return state


def _normalized_name(value: str) -> str:
    return value.strip().casefold()


def _baseline_decision(pair: dict[str, Any]) -> bool:
    return _normalized_name(pair["entity_a"]["name"]) == _normalized_name(
        pair["entity_b"]["name"]
    )


def _decisions(response: dict[str, Any]) -> dict[str, bool]:
    answers = response["answers"]
    link = answers["link_state"]
    score_route = link["score"] >= 1.5
    confident = score_route and link["confidence"] >= 0.8
    return {
        "score_route": score_route,
        "confident_score": confident,
        "field_guarded": (
            confident
            and answers["same_name"]["noul"] > 0.5
            and answers["same_brewery"]["noul"] > 0.5
        ),
    }


def _metrics(labels: list[bool], predictions: list[bool]) -> dict[str, Any]:
    tp = sum(prediction and label for prediction, label in zip(predictions, labels))
    fp = sum(prediction and not label for prediction, label in zip(predictions, labels))
    tn = sum(not prediction and not label for prediction, label in zip(predictions, labels))
    fn = sum(not prediction and label for prediction, label in zip(predictions, labels))
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": (tp + tn) / len(labels),
        "merge_precision": tp / (tp + fp) if tp + fp else 1.0,
        "merge_recall": tp / (tp + fn) if tp + fn else 0.0,
        "false_merge_rate": fp / (fp + tn) if fp + tn else 0.0,
    }


def _gate(
    metrics: dict[str, Any], *, valid: int, baseline_recall: float, registration: dict[str, Any]
) -> dict[str, bool]:
    gates = registration["evaluation"]["gates_per_arm"]
    return {
        "pairs_evaluated_min": sum(metrics[key] for key in ("tp", "fp", "tn", "fn"))
        >= gates["pairs_evaluated_min"],
        "response_validity_min": valid >= gates["response_validity_min"],
        "merge_precision_min": metrics["merge_precision"] >= gates["merge_precision_min"],
        "merge_recall_min": metrics["merge_recall"] >= gates["merge_recall_min"],
        "false_merges_max": metrics["fp"] <= gates["false_merges_max"],
        "recall_gain_over_current_min": metrics["merge_recall"] - baseline_recall
        >= gates["recall_gain_over_current_min"],
    }


def _result(
    pairs: list[dict[str, Any]],
    state: dict[str, Any],
    registration: dict[str, Any],
    registration_path: Path,
) -> dict[str, Any]:
    labels = [pair["label"] == 1 for pair in pairs]
    baseline = _metrics(labels, [_baseline_decision(pair) for pair in pairs])
    predictions = {name: [] for name in registration["protocol"]["decision_arms"]}
    samples: list[dict[str, Any]] = []
    input_tokens = output_tokens = attempts = 0
    elapsed: list[float] = []
    for pair in pairs:
        item = state["results"].get(pair["id"])
        if not isinstance(item, dict):
            raise ValueError(f"missing durable response for {pair['id']}")
        response = _validate_response(item["response"])
        decisions = _decisions(response)
        for name, decision in decisions.items():
            predictions[name].append(decision)
        answers = response["answers"]
        usage = response["usage"]
        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]
        attempts += item["attempts"]
        elapsed.append(item["elapsed_seconds"])
        samples.append(
            {
                "id": pair["id"],
                "label": pair["label"],
                "link_score": answers["link_state"]["score"],
                "link_confidence": answers["link_state"]["confidence"],
                "link_probabilities": answers["link_state"]["probabilities"],
                "same_name_noul": answers["same_name"]["noul"],
                "same_brewery_noul": answers["same_brewery"]["noul"],
                "same_style_noul": answers["same_style"]["noul"],
                "decisions": decisions,
                "review_or_merge": answers["link_state"]["score"] >= 0.5,
                "attempts": item["attempts"],
                "elapsed_seconds": item["elapsed_seconds"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            }
        )
    arms: dict[str, Any] = {}
    for name, values in predictions.items():
        metrics = _metrics(labels, values)
        gates = _gate(
            metrics,
            valid=len(samples),
            baseline_recall=baseline["merge_recall"],
            registration=registration,
        )
        arms[name] = {"metrics": metrics, "gate_results": gates, "passed": all(gates.values())}
    passing = [name for name, value in arms.items() if value["passed"]]
    selected = (
        sorted(
            passing,
            key=lambda name: (
                -arms[name]["metrics"]["merge_recall"],
                -arms[name]["metrics"]["merge_precision"],
                name,
            ),
        )[0]
        if passing
        else None
    )
    review_predictions = [sample["review_or_merge"] for sample in samples]
    review_metrics = _metrics(labels, review_predictions)
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "jev-entity-alignment-development-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "heldout_accessed": False,
        "registration_sha256": common._sha256_file(registration_path),
        "cohort": registration["cohort"],
        "provider": registration["provider"],
        "protocol": registration["protocol"],
        "development": {
            "current_prme_baseline": baseline,
            "review_or_merge": review_metrics,
            "arms": arms,
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
        "passed": selected is not None,
        "selected_arm": selected,
        "decision": (
            "eligible_for_cross_domain_confirmation"
            if selected is not None
            else "jev_entity_alignment_rejected"
        ),
        "limitations": [
            "Development split from one public product-matching dataset.",
            "Candidate pairs are supplied; this does not evaluate candidate generation.",
            "A semantic decision cannot bypass PRME owner, scope, type or provenance checks.",
            "No graph mutation or held-out split was performed.",
        ],
    }
    result["result_sha256"] = common._canonical_sha256(result)
    return result


async def run(
    *,
    registration_path: Path,
    dataset_root: Path,
    state_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    pairs = _validate_registration(
        registration, dataset_root=dataset_root, project_root=project_root
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--dataset-root", type=Path, required=True)
    register.add_argument("--output", type=Path, required=True)
    register.add_argument("--project-root", type=Path, default=Path.cwd())
    execute = subparsers.add_parser("run")
    execute.add_argument("--registration", type=Path, required=True)
    execute.add_argument("--dataset-root", type=Path, required=True)
    execute.add_argument("--state", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "register":
        result = create_registration(
            dataset_root=args.dataset_root.resolve(),
            output_path=args.output.resolve(),
            project_root=args.project_root.resolve(),
        )
        print(json.dumps({"cohort": result["cohort"], "dataset": result["dataset"]}, indent=2))
        return
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            dataset_root=args.dataset_root.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            project_root=args.project_root.resolve(),
        )
    )
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "selected_arm": result["selected_arm"],
                "baseline": result["development"]["current_prme_baseline"],
                "arms": result["development"]["arms"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

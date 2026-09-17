"""Register and run a pinned generic-product Jev alignment trial."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import time
from typing import Any, Literal

import httpx

from benchmarks.integrations import run_jev_claim_verification as common


MODEL = "jev-1.13.0"
API_URL = common.API_URL
CONCURRENCY = 6
TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 3
CASES_PER_LABEL = 200
EXPECTED_FILES = ("tableA.csv", "tableB.csv", "train.csv", "valid.csv", "test.csv")
ROLE_SPECS = {
    "development": {
        "splits": ("train",),
        "seed": "prme-jev-product-alignment-development-v1",
    },
    "confirmation": {
        "splits": ("valid", "test"),
        "seed": "prme-jev-product-alignment-confirmation-v1",
    },
}
LEVELS = [
    "They are different catalog products.",
    (
        "They are related but not safely identical: a different version, edition, "
        "bundle, platform, capacity, pack size, or an ambiguous listing."
    ),
    (
        "They are the same catalog product despite formatting differences, "
        "abbreviations, price variation, or a missing optional field."
    ),
]
QUESTIONS: dict[str, Any] = {
    "link_state": {
        "type": "score",
        "instructions": "How do the two entity descriptions relate as products?",
        "criteria": LEVELS,
    },
    "same_name": {
        "type": "noul",
        "instructions": "Do the two listings name the same product?",
    },
    "same_manufacturer": {
        "type": "noul",
        "instructions": "Are the two listings from the same manufacturer?",
    },
    "compatible_price": {
        "type": "noul",
        "instructions": (
            "Could the stated prices plausibly describe the same product listing, "
            "allowing a missing price or ordinary price variation?"
        ),
    },
}


Role = Literal["development", "confirmation"]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _entity(row: dict[str, str]) -> dict[str, str]:
    return {
        "name": row["title"],
        "manufacturer": row["manufacturer"],
        "price": row["price"],
    }


def _all_pairs(root: Path, splits: tuple[str, ...]) -> list[dict[str, Any]]:
    left = {row["id"]: row for row in _read_csv(root / "tableA.csv")}
    right = {row["id"]: row for row in _read_csv(root / "tableB.csv")}
    pairs: list[dict[str, Any]] = []
    for split in splits:
        for index, row in enumerate(_read_csv(root / f"{split}.csv")):
            left_id = row["ltable_id"]
            right_id = row["rtable_id"]
            if (
                left_id not in left
                or right_id not in right
                or row["label"] not in {"0", "1"}
            ):
                raise ValueError("product-alignment dataset row is invalid")
            pairs.append(
                {
                    "id": f"{split}-{index:05d}-{left_id}-{right_id}",
                    "label": int(row["label"]),
                    "entity_a": _entity(left[left_id]),
                    "entity_b": _entity(right[right_id]),
                }
            )
    return pairs


def _select_pairs(root: Path, role: Role) -> list[dict[str, Any]]:
    spec = ROLE_SPECS[role]
    pairs = _all_pairs(root, spec["splits"])
    selected: list[dict[str, Any]] = []
    for label in (0, 1):
        candidates = [pair for pair in pairs if pair["label"] == label]
        candidates.sort(
            key=lambda pair: (
                hashlib.sha256(
                    f"{spec['seed']}\0{pair['id']}".encode()
                ).digest(),
                pair["id"],
            )
        )
        if len(candidates) < CASES_PER_LABEL:
            raise ValueError(f"{role} product cohort has too few label {label} pairs")
        selected.extend(candidates[:CASES_PER_LABEL])
    selected.sort(key=lambda pair: pair["id"])
    return selected


def _dataset(root: Path, role: Role) -> dict[str, Any]:
    return {
        "name": "DeepMatcher Structured Amazon-Google",
        "source": (
            "https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/"
            "Structured/Amazon-Google/exp_data/"
        ),
        "splits": list(ROLE_SPECS[role]["splits"]),
        "files": {
            name: {
                "sha256": common._sha256_file(root / name),
                "bytes": (root / name).stat().st_size,
            }
            for name in EXPECTED_FILES
        },
        "source_text_policy": (
            "Public source tables remain outside the repository; committed artifacts "
            "contain pair identities, labels, typed scores and hashes."
        ),
    }


def _protocol() -> dict[str, Any]:
    return {
        "task": "candidate_pair_generic_product_alignment",
        "state_fields": {
            "entity_a": ["name", "manufacturer", "price"],
            "entity_b": ["name", "manufacturer", "price"],
        },
        "questions": QUESTIONS,
        "questions_sha256": common._canonical_sha256(QUESTIONS),
        "model": MODEL,
        "api_url": API_URL,
        "proposal_rule": "link_state.score >= 1.5",
        "automatic_merge_authorized": False,
        "concurrency": CONCURRENCY,
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
        "retryable_statuses": sorted(common.RETRYABLE_STATUSES),
        "emit_entity_text": False,
    }


def _evaluation(cases: int) -> dict[str, Any]:
    return {
        "gates": {
            "pairs_evaluated_min": cases,
            "response_validity_min": cases,
            "proposal_precision_min": 0.90,
            "proposal_recall_min": 0.50,
            "false_positive_rate_max": 0.10,
            "recall_gain_over_exact_name_min": 0.20,
            "accuracy_gain_over_exact_name_min": 0.05,
            "p95_request_seconds_max": 1.0,
        },
        "decision_rule": (
            "Development must pass every gate before confirmation is registered. "
            "Confirmation must pass every gate before the exact protocol may ship "
            "as an optional unverified-proposal advisor. Automatic merges remain "
            "forbidden regardless of score."
        ),
    }


def _validated_development(
    registration_path: Path | None, result_path: Path | None
) -> dict[str, str] | None:
    if registration_path is None and result_path is None:
        return None
    if registration_path is None or result_path is None:
        raise ValueError("both development artifacts are required")
    registration = json.loads(registration_path.read_text())
    result = json.loads(result_path.read_text())
    if (
        registration.get("kind") != "jev-product-alignment-registration"
        or registration.get("cohort", {}).get("role") != "development"
        or result.get("kind") != "jev-product-alignment-result"
        or result.get("cohort", {}).get("role") != "development"
        or not result.get("passed")
        or result.get("registration_sha256")
        != common._sha256_file(registration_path)
        or result.get("protocol") != registration.get("protocol")
    ):
        raise ValueError("development evidence does not authorize confirmation")
    return {
        "registration_sha256": common._sha256_file(registration_path),
        "result_file_sha256": common._sha256_file(result_path),
        "result_sha256": result["result_sha256"],
    }


def create_registration(
    *,
    dataset_root: Path,
    role: Role,
    output_path: Path,
    project_root: Path,
    development_registration_path: Path | None = None,
    development_result_path: Path | None = None,
) -> dict[str, Any]:
    if role == "development" and (
        development_registration_path is not None or development_result_path is not None
    ):
        raise ValueError("development registration cannot bind development results")
    upstream = _validated_development(
        development_registration_path, development_result_path
    )
    if role == "confirmation" and upstream is None:
        raise ValueError("confirmation requires passing development evidence")
    pairs = _select_pairs(dataset_root, role)
    labels = Counter(str(pair["label"]) for pair in pairs)
    registration = {
        "schema_version": 1,
        "kind": "jev-product-alignment-registration",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing confirms the exact pinned protocol as an optional product-pair "
            "advisor that may recommend durable unverified alias proposals. It does "
            "not authorize automatic identity merges or establish other domains."
        ),
        "source": {
            "prme_revision": common._git_revision(project_root),
            "runner_sha256": common._sha256_file(Path(__file__).resolve()),
            "development": upstream,
        },
        "dataset": _dataset(dataset_root, role),
        "cohort": {
            "role": role,
            "selection_seed": ROLE_SPECS[role]["seed"],
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
    registration: dict[str, Any],
    *,
    dataset_root: Path,
    project_root: Path,
    development_registration_path: Path | None,
    development_result_path: Path | None,
) -> list[dict[str, Any]]:
    role = registration.get("cohort", {}).get("role")
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "jev-product-alignment-registration"
        or role not in ROLE_SPECS
    ):
        raise ValueError("invalid Jev product-alignment registration")
    revision = registration.get("source", {}).get("prme_revision")
    if not isinstance(revision, str) or not common._git_is_ancestor(
        revision, project_root
    ):
        raise ValueError("registered PRME revision is not an ancestor")
    if registration.get("source", {}).get("runner_sha256") != common._sha256_file(
        Path(__file__).resolve()
    ):
        raise ValueError("registered product-alignment runner differs")
    upstream = _validated_development(
        development_registration_path, development_result_path
    )
    if registration.get("source", {}).get("development") != upstream:
        raise ValueError("registered development evidence differs")
    pairs = _select_pairs(dataset_root, role)
    labels = Counter(str(pair["label"]) for pair in pairs)
    expected_cohort = {
        "role": role,
        "selection_seed": ROLE_SPECS[role]["seed"],
        "pairs": len(pairs),
        "counts_by_label": dict(sorted(labels.items())),
        "identity_sha256": common._canonical_sha256(
            [pair["id"] for pair in pairs]
        ),
    }
    if (
        registration.get("dataset") != _dataset(dataset_root, role)
        or registration.get("cohort") != expected_cohort
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _evaluation(len(pairs))
    ):
        raise ValueError("registered product-alignment inputs differ")
    return pairs


def _validate_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("Jev response model differs from pinned registration")
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
        raise ValueError("Jev product-alignment answer set is invalid")
    link = answers["link_state"]
    if not isinstance(link, dict) or link.get("type") != "score":
        raise ValueError("Jev link-state answer is invalid")
    score = link.get("score")
    confidence = link.get("confidence")
    probabilities = link.get("probabilities")
    legend = link.get("legend")
    if (
        type(score) not in (int, float)
        or not math.isfinite(score)
        or not 0 <= score <= 2
        or type(confidence) not in (int, float)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
        or not isinstance(probabilities, dict)
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
    for name in ("same_name", "same_manufacturer", "compatible_price"):
        answer = answers[name]
        probability = answer.get("noul") if isinstance(answer, dict) else None
        if (
            not isinstance(answer, dict)
            or answer.get("type") != "noul"
            or type(probability) not in (int, float)
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
        ):
            raise ValueError(f"Jev {name} answer is invalid")
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Jev product-alignment usage is invalid")
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
            if (
                response.status_code in common.RETRYABLE_STATUSES
                and attempt < MAX_ATTEMPTS
            ):
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
    role = registration["cohort"]["role"]
    identity = {
        "kind": f"jev_product_alignment_{role}_v1",
        "registration_sha256": common._sha256_file(registration_path),
        "cohort_identity_sha256": registration["cohort"]["identity_sha256"],
        "model": MODEL,
        "questions_sha256": common._canonical_sha256(QUESTIONS),
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("identity") != identity:
            raise ValueError("durable Jev product state belongs to another run")
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
                    print(
                        f"[jev-product-{role}] completed {completed}/{len(pairs)}",
                        flush=True,
                    )

        await asyncio.gather(*(execute(pair) for pair in pairs))
    return state


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _baseline_decision(pair: dict[str, Any]) -> bool:
    return _normalize(pair["entity_a"]["name"]) == _normalize(
        pair["entity_b"]["name"]
    )


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
        "proposal_precision": tp / (tp + fp) if tp + fp else 1.0,
        "proposal_recall": tp / (tp + fn) if tp + fn else 0.0,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
    }


def _gate(
    *,
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    valid: int,
    p95: float,
    registration: dict[str, Any],
) -> dict[str, bool]:
    gates = registration["evaluation"]["gates"]
    return {
        "pairs_evaluated_min": sum(
            candidate[key] for key in ("tp", "fp", "tn", "fn")
        )
        >= gates["pairs_evaluated_min"],
        "response_validity_min": valid >= gates["response_validity_min"],
        "proposal_precision_min": candidate["proposal_precision"]
        >= gates["proposal_precision_min"],
        "proposal_recall_min": candidate["proposal_recall"]
        >= gates["proposal_recall_min"],
        "false_positive_rate_max": candidate["false_positive_rate"]
        <= gates["false_positive_rate_max"],
        "recall_gain_over_exact_name_min": candidate["proposal_recall"]
        - baseline["proposal_recall"]
        >= gates["recall_gain_over_exact_name_min"],
        "accuracy_gain_over_exact_name_min": candidate["accuracy"]
        - baseline["accuracy"]
        >= gates["accuracy_gain_over_exact_name_min"],
        "p95_request_seconds_max": p95 <= gates["p95_request_seconds_max"],
    }


def _result(
    pairs: list[dict[str, Any]],
    state: dict[str, Any],
    registration: dict[str, Any],
    registration_path: Path,
) -> dict[str, Any]:
    labels = [pair["label"] == 1 for pair in pairs]
    baseline = _metrics(labels, [_baseline_decision(pair) for pair in pairs])
    predictions: list[bool] = []
    samples: list[dict[str, Any]] = []
    input_tokens = output_tokens = attempts = 0
    elapsed: list[float] = []
    for pair in pairs:
        item = state["results"].get(pair["id"])
        if not isinstance(item, dict):
            raise ValueError(f"missing durable response for {pair['id']}")
        response = _validate_response(item["response"])
        answers = response["answers"]
        decision = answers["link_state"]["score"] >= 1.5
        predictions.append(decision)
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
                "same_manufacturer_noul": answers["same_manufacturer"]["noul"],
                "compatible_price_noul": answers["compatible_price"]["noul"],
                "proposal_recommended": decision,
                "attempts": item["attempts"],
                "elapsed_seconds": item["elapsed_seconds"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            }
        )
    candidate = _metrics(labels, predictions)
    p95 = sorted(elapsed)[math.ceil(len(elapsed) * 0.95) - 1]
    gate_results = _gate(
        candidate=candidate,
        baseline=baseline,
        valid=len(samples),
        p95=p95,
        registration=registration,
    )
    passed = all(gate_results.values())
    role = registration["cohort"]["role"]
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "jev-product-alignment-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": common._sha256_file(registration_path),
        "cohort": registration["cohort"],
        "provider": registration["provider"],
        "protocol": registration["protocol"],
        "evaluation": {
            "exact_name_baseline": baseline,
            "jev_proposal": candidate,
            "gate_results": gate_results,
            "samples": samples,
        },
        "runtime": {
            "requests": len(samples),
            "attempts": attempts,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "median_request_seconds": sorted(elapsed)[len(elapsed) // 2],
            "p95_request_seconds": p95,
            "max_request_seconds": max(elapsed),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "passed": passed,
        "decision": (
            "eligible_for_confirmation"
            if passed and role == "development"
            else "optional_product_advisor_confirmed"
            if passed
            else f"jev_product_alignment_{role}_rejected"
        ),
        "automatic_merge_authorized": False,
        "limitations": [
            "Candidate pairs are supplied; candidate generation is not evaluated.",
            "One public software-product dataset does not establish other domains.",
            "The advisor cannot authorize automatic identity merges.",
            "The pinned service is external and sends the supplied entity fields.",
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
    development_registration_path: Path | None = None,
    development_result_path: Path | None = None,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    pairs = _validate_registration(
        registration,
        dataset_root=dataset_root,
        project_root=project_root,
        development_registration_path=development_registration_path,
        development_result_path=development_result_path,
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


def _shared(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--development-registration", type=Path)
    parser.add_argument("--development-result", type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    _shared(register)
    register.add_argument("--role", choices=tuple(ROLE_SPECS), required=True)
    register.add_argument("--output", type=Path, required=True)
    execute = subparsers.add_parser("run")
    _shared(execute)
    execute.add_argument("--registration", type=Path, required=True)
    execute.add_argument("--state", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    shared = {
        "dataset_root": args.dataset_root.resolve(),
        "project_root": args.project_root.resolve(),
        "development_registration_path": (
            args.development_registration.resolve()
            if args.development_registration
            else None
        ),
        "development_result_path": (
            args.development_result.resolve() if args.development_result else None
        ),
    }
    if args.command == "register":
        result = create_registration(
            role=args.role,
            output_path=args.output.resolve(),
            **shared,
        )
        print(json.dumps({"cohort": result["cohort"]}, indent=2))
        return
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            **shared,
        )
    )
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "decision": result["decision"],
                "baseline": result["evaluation"]["exact_name_baseline"],
                "candidate": result["evaluation"]["jev_proposal"],
                "gates": result["evaluation"]["gate_results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

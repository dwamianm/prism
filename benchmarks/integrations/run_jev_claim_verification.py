"""Register and run a pinned Jev claim-verification development trial.

The trial uses fresh LLM-AggreFact development rows, excluding every row ID
found in prior committed PRME LLM-AggreFact results.  Jev receives the complete
source document and claim in one state and answers independent typed questions.
No benchmark source text or API credential is written to committed artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import time
from typing import Any

import httpx

from benchmarks.integrations import run_llm_aggrefact_factcg as factcg


MODEL = "jev-1.13.0"
API_URL = "https://api.typesafe.ai/v1/systemone"
SELECTION_SEED = "prme-jev-claim-verification-development-v1"
PER_LABEL_PER_DATASET = 25
CONCURRENCY = 6
TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 3
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
ELIGIBLE_DATASETS = (
    "AggreFact-XSum",
    "ClaimVerify",
    "ExpertQA",
    "FactCheck-GPT",
    "Lfqa",
    "Reveal",
    "TofuEval-MediaS",
    "TofuEval-MeetB",
)

QUESTIONS: dict[str, Any] = {
    "fully_supported": {
        "type": "noul",
        "instructions": (
            "Does the source document fully establish the entire claim exactly as "
            "written, using only the source document?"
        ),
        "criteria": {
            "true": (
                "Every independently checkable assertion and every meaningful "
                "qualifier in the claim follows from the source document."
            ),
            "false": (
                "Any assertion or qualifier is missing, only partially supported, "
                "ambiguous, hypothetical, conditional, attributed differently, "
                "or contradicted."
            ),
        },
    },
    "has_unsupported_content": {
        "type": "noul",
        "instructions": (
            "Does any meaningful part of the claim fail to follow from the source "
            "document?"
        ),
        "criteria": {
            "true": (
                "At least one independently checkable assertion or qualifier is "
                "missing, partial, ambiguous, hypothetical, conditional, "
                "misattributed, or contradicted."
            ),
            "false": (
                "The source document establishes every assertion and qualifier in "
                "the claim."
            ),
        },
    },
    "relationship": {
        "type": "choice",
        "instructions": (
            "Which option best describes how the complete source document relates "
            "to the complete claim?"
        ),
        "criteria": {
            "fully_supported": (
                "Every assertion and qualifier in the claim is established."
            ),
            "partially_supported": (
                "Some claim content is established, but at least one assertion or "
                "qualifier is missing, weaker, or ambiguous."
            ),
            "contradicted": (
                "The source establishes information incompatible with at least one "
                "material part of the claim."
            ),
            "not_addressed": (
                "The source does not provide evidence for the material claim."
            ),
        },
    },
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def _git_revision(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_is_ancestor(revision: str, root: Path) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", revision, "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _walk_matching_ids(value: Any, known_ids: frozenset[str], found: set[str]) -> None:
    if isinstance(value, str):
        if value in known_ids:
            found.add(value)
        return
    if isinstance(value, list):
        for item in value:
            _walk_matching_ids(item, known_ids, found)
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk_matching_ids(item, known_ids, found)


def _prior_observations(
    rows: list[dict[str, Any]], project_root: Path
) -> tuple[frozenset[str], list[dict[str, Any]]]:
    known_ids = frozenset(row["contamination_identifier"] for row in rows)
    observed: set[str] = set()
    artifacts: list[dict[str, Any]] = []
    results_root = project_root / "benchmarks/results/research"
    for path in sorted(results_root.rglob("llm-aggrefact*.json")):
        if path.name.startswith("jev-"):
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        matched: set[str] = set()
        _walk_matching_ids(value, known_ids, matched)
        if not matched:
            continue
        observed.update(matched)
        artifacts.append(
            {
                "path": str(path.relative_to(project_root)),
                "sha256": _sha256_file(path),
                "matched_ids": len(matched),
            }
        )
    if not observed:
        raise ValueError("no prior LLM-AggreFact observations were discovered")
    return frozenset(observed), artifacts


def _select_cohort(
    rows: list[dict[str, Any]], excluded_ids: frozenset[str]
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["contamination_identifier"] in excluded_ids:
            continue
        groups[(str(row["dataset"]), int(row["label"]))].append(row)
    selected: list[dict[str, Any]] = []
    for key in sorted((dataset, label) for dataset in ELIGIBLE_DATASETS for label in (0, 1)):
        candidates = sorted(
            groups[key],
            key=lambda row: (
                hashlib.sha256(
                    f"{SELECTION_SEED}\0{row['contamination_identifier']}".encode()
                ).digest(),
                row["contamination_identifier"],
            ),
        )
        if len(candidates) < PER_LABEL_PER_DATASET:
            raise ValueError(f"fresh Jev cohort group {key!r} is too small")
        selected.extend(candidates[:PER_LABEL_PER_DATASET])
    selected.sort(
        key=lambda row: (
            hashlib.sha256(
                f"{SELECTION_SEED}\0order\0{row['contamination_identifier']}".encode()
            ).digest(),
            row["contamination_identifier"],
        )
    )
    return selected


def _counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts = Counter((str(row["dataset"]), str(row["label"])) for row in rows)
    return {
        dataset: {label: counts[(dataset, label)] for label in ("0", "1")}
        for dataset in ELIGIBLE_DATASETS
    }


def _registration_inputs(
    dev_path: Path, project_root: Path
) -> tuple[list[dict[str, Any]], frozenset[str], list[dict[str, Any]]]:
    rows = factcg._load_rows(dev_path)
    excluded, artifacts = _prior_observations(rows, project_root)
    selected = _select_cohort(rows, excluded)
    return selected, excluded, artifacts


def _protocol() -> dict[str, Any]:
    return {
        "task": "complete_document_claim_support",
        "state_fields": ["claim", "source_document"],
        "questions": QUESTIONS,
        "questions_sha256": _canonical_sha256(QUESTIONS),
        "model": MODEL,
        "api_url": API_URL,
        "decision_arms": {
            "choice_top": "relationship.choice == fully_supported",
            "direct_noul": "fully_supported.noul > 0.5",
            "conservative_agreement": (
                "choice_top and fully_supported.noul > 0.5 and "
                "has_unsupported_content.noul < 0.5"
            ),
        },
        "threshold_operator": "strict",
        "concurrency": CONCURRENCY,
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
        "retryable_statuses": sorted(RETRYABLE_STATUSES),
        "emit_source_text": False,
        "test_access": "forbidden_no_test_path",
    }


def _evaluation(cases: int) -> dict[str, Any]:
    return {
        "gates_per_arm": {
            "claims_evaluated_min": cases,
            "response_validity_min": math.ceil(cases * 0.99),
            "supported_precision_min": 0.90,
            "supported_recall_min": 0.60,
            "balanced_accuracy_min": 0.75,
            "false_support_rate_max": 0.10,
        },
        "selection": (
            "Among arms passing every gate, choose greatest supported recall, then "
            "precision, then balanced accuracy, then lexicographically smallest name."
        ),
        "decision_rule": (
            "Do not integrate Jev or access an external test split unless at least "
            "one frozen development arm passes every gate."
        ),
    }


def create_registration(
    *, dev_path: Path, output_path: Path, project_root: Path
) -> dict[str, Any]:
    selected, excluded, artifacts = _registration_inputs(dev_path, project_root)
    labels = Counter(str(row["label"]) for row in selected)
    registration = {
        "schema_version": 1,
        "kind": "jev-claim-verification-development-registration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing establishes development evidence for pinned Jev on fresh "
            "complete-document claim verification. It does not establish an "
            "external confirmation, product integration, or universal quality."
        ),
        "source": {
            "prme_revision": _git_revision(project_root),
            "runner_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "dataset": {
            "name": "LLM-AggreFact",
            "repository": "https://huggingface.co/datasets/lytang/LLM-AggreFact",
            "revision": "981dfd0bd8e58e7238a9ab92b2e6ea44bce918e4",
            "license": "cc-by-nd-4.0",
            "split": "development",
            "sha256": _sha256_file(dev_path),
            "test_access": "forbidden_no_test_path",
        },
        "exclusions": {
            "observed_cases": len(excluded),
            "observed_identity_sha256": hashlib.sha256(
                "\n".join(sorted(excluded)).encode()
            ).hexdigest(),
            "artifacts": artifacts,
        },
        "cohort": {
            "role": "development",
            "selection_seed": SELECTION_SEED,
            "eligible_datasets": list(ELIGIBLE_DATASETS),
            "per_label_per_dataset": PER_LABEL_PER_DATASET,
            "cases": len(selected),
            "counts_by_label": dict(sorted(labels.items())),
            "counts_by_dataset_label": _counts(selected),
            "selected_identity_sha256": factcg._identity_sha256(selected),
            "document_chars_sum": sum(len(row["doc"]) for row in selected),
            "document_chars_max": max(len(row["doc"]) for row in selected),
            "claim_chars_max": max(len(row["claim"]) for row in selected),
        },
        "provider": {
            "name": "TypeSafe AI",
            "model": MODEL,
            "endpoint": API_URL,
            "credential_environment": ["JEV_API_KEY", "TYPESAFE_API_KEY"],
        },
        "protocol": _protocol(),
        "evaluation": _evaluation(len(selected)),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any], *, dev_path: Path, project_root: Path
) -> list[dict[str, Any]]:
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "jev-claim-verification-development-registration"
    ):
        raise ValueError("invalid Jev development registration")
    revision = registration.get("source", {}).get("prme_revision")
    if not isinstance(revision, str) or not _git_is_ancestor(revision, project_root):
        raise ValueError("registered PRME revision is not an ancestor")
    if registration.get("source", {}).get("runner_sha256") != _sha256_file(
        Path(__file__).resolve()
    ):
        raise ValueError("registered runner differs")
    selected, excluded, artifacts = _registration_inputs(dev_path, project_root)
    expected = create_registration(
        dev_path=dev_path,
        output_path=Path(os.devnull),
        project_root=project_root,
    )
    expected["created_at"] = registration.get("created_at")
    expected["source"]["prme_revision"] = revision
    if expected != registration:
        raise ValueError("registered Jev protocol or cohort differs")
    if len(excluded) != registration["exclusions"]["observed_cases"]:
        raise AssertionError("exclusion accounting differs")
    if artifacts != registration["exclusions"]["artifacts"]:
        raise AssertionError("exclusion artifacts differ")
    return selected


def _api_key() -> str:
    for name in ("JEV_API_KEY", "TYPESAFE_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError("set JEV_API_KEY or TYPESAFE_API_KEY")


def _validate_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("Jev response model differs from pinned registration")
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
        raise ValueError("Jev response answer set is invalid")
    for name in ("fully_supported", "has_unsupported_content"):
        answer = answers[name]
        if not isinstance(answer, dict):
            raise ValueError(f"Jev {name} answer is invalid")
        score = answer.get("noul")
        if answer.get("type") != "noul" or type(score) not in (int, float):
            raise ValueError(f"Jev {name} answer is invalid")
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError(f"Jev {name} probability is invalid")
    relation = answers["relationship"]
    expected_options = set(QUESTIONS["relationship"]["criteria"])
    if not isinstance(relation, dict) or relation.get("type") != "choice":
        raise ValueError("Jev relationship answer is invalid")
    if relation.get("choice") not in expected_options:
        raise ValueError("Jev relationship choice is invalid")
    probabilities = relation.get("probabilities")
    confidence = relation.get("confidence")
    if not isinstance(probabilities, dict) or set(probabilities) != expected_options:
        raise ValueError("Jev relationship probabilities are invalid")
    if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
        raise ValueError("Jev relationship confidence is invalid")
    if any(
        type(score) not in (int, float)
        or not math.isfinite(score)
        or not 0 <= score <= 1
        for score in probabilities.values()
    ):
        raise ValueError("Jev relationship probability is invalid")
    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.02):
        raise ValueError("Jev relationship probabilities do not sum to one")
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Jev usage is invalid")
    return value


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        for header, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
            raw = response.headers.get(header)
            if raw:
                try:
                    return min(max(float(raw) * scale, 0.0), 30.0)
                except ValueError:
                    pass
    return min(0.5 * (2 ** (attempt - 1)), 4.0) + random.random() * 0.1


async def _request_one(
    client: httpx.AsyncClient, key: str, row: dict[str, Any]
) -> dict[str, Any]:
    payload = {
        "model": MODEL,
        "state": {"claim": row["claim"], "source_document": row["doc"]},
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
            if response.status_code in RETRYABLE_STATUSES and attempt < MAX_ATTEMPTS:
                errors.append(f"http_{response.status_code}")
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            value = _validate_response(response.json())
            return {
                "response": value,
                "attempts": attempt,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "transient_errors": errors,
            }
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            errors.append(type(exc).__name__)
            if attempt >= MAX_ATTEMPTS:
                raise
            await asyncio.sleep(_retry_delay(response, attempt))
    raise AssertionError("unreachable request loop")


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _state_identity(registration_path: Path, registration: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "jev_claim_verification_development_v1",
        "registration_sha256": _sha256_file(registration_path),
        "cohort_identity_sha256": registration["cohort"][
            "selected_identity_sha256"
        ],
        "model": MODEL,
        "questions_sha256": _canonical_sha256(QUESTIONS),
    }


async def _execute(
    rows: list[dict[str, Any]],
    *,
    registration_path: Path,
    registration: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    identity = _state_identity(registration_path, registration)
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("identity") != identity or not isinstance(
            state.get("results"), dict
        ):
            raise ValueError("durable Jev state belongs to another run")
    else:
        state = {"identity": identity, "results": {}}
        _atomic_write(state_path, state)
    key = _api_key()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    write_lock = asyncio.Lock()
    completed = 0

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:

        async def execute(row: dict[str, Any]) -> None:
            nonlocal completed
            identifier = row["contamination_identifier"]
            if identifier in state["results"]:
                completed += 1
                return
            async with semaphore:
                value = await _request_one(client, key, row)
            async with write_lock:
                state["results"][identifier] = value
                _atomic_write(state_path, state)
                completed += 1
                if completed % 20 == 0 or completed == len(rows):
                    print(f"[jev] completed {completed}/{len(rows)}", flush=True)

        await asyncio.gather(*(execute(row) for row in rows))
    return state


def _arm_decisions(response: dict[str, Any]) -> dict[str, bool]:
    answers = response["answers"]
    choice_top = answers["relationship"]["choice"] == "fully_supported"
    direct = answers["fully_supported"]["noul"] > 0.5
    inverse = answers["has_unsupported_content"]["noul"] < 0.5
    return {
        "choice_top": choice_top,
        "direct_noul": direct,
        "conservative_agreement": choice_top and direct and inverse,
    }


def _gate_arm(metrics: dict[str, Any], valid: int, registration: dict[str, Any]) -> dict[str, bool]:
    gates = registration["evaluation"]["gates_per_arm"]
    return {
        "claims_evaluated_min": metrics["tp"]
        + metrics["fp"]
        + metrics["tn"]
        + metrics["fn"]
        >= gates["claims_evaluated_min"],
        "response_validity_min": valid >= gates["response_validity_min"],
        "supported_precision_min": metrics["supported_precision"]
        >= gates["supported_precision_min"],
        "supported_recall_min": metrics["supported_recall"]
        >= gates["supported_recall_min"],
        "balanced_accuracy_min": metrics["balanced_accuracy"]
        >= gates["balanced_accuracy_min"],
        "false_support_rate_max": metrics["false_support_rate"]
        <= gates["false_support_rate_max"],
    }


def _result(
    rows: list[dict[str, Any]],
    state: dict[str, Any],
    registration: dict[str, Any],
    registration_path: Path,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    arm_samples: dict[str, list[dict[str, Any]]] = {
        name: [] for name in registration["protocol"]["decision_arms"]
    }
    input_tokens = output_tokens = attempts = 0
    elapsed: list[float] = []
    for row in rows:
        identifier = row["contamination_identifier"]
        item = state["results"].get(identifier)
        if not isinstance(item, dict):
            raise ValueError(f"missing durable response for {identifier}")
        response = _validate_response(item["response"])
        decisions = _arm_decisions(response)
        answers = response["answers"]
        usage = response["usage"]
        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]
        attempts += item["attempts"]
        elapsed.append(float(item["elapsed_seconds"]))
        samples.append(
            {
                "id": identifier,
                "dataset": row["dataset"],
                "label": int(row["label"]),
                "fully_supported_noul": answers["fully_supported"]["noul"],
                "unsupported_content_noul": answers["has_unsupported_content"][
                    "noul"
                ],
                "relationship": answers["relationship"]["choice"],
                "relationship_probabilities": answers["relationship"][
                    "probabilities"
                ],
                "relationship_confidence": answers["relationship"]["confidence"],
                "decisions": decisions,
                "attempts": item["attempts"],
                "elapsed_seconds": item["elapsed_seconds"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            }
        )
        for name, decision in decisions.items():
            arm_samples[name].append(
                {
                    "id": identifier,
                    "dataset": row["dataset"],
                    "label": int(row["label"]),
                    "support_probability": 1.0 if decision else 0.0,
                }
            )
    arms: dict[str, Any] = {}
    for name, values in arm_samples.items():
        metrics = factcg._metrics(values, 0.5)
        gates = _gate_arm(metrics, len(samples), registration)
        arms[name] = {
            "metrics": metrics,
            "metrics_by_dataset": {
                dataset: factcg._metrics(
                    [value for value in values if value["dataset"] == dataset], 0.5
                )
                for dataset in ELIGIBLE_DATASETS
            },
            "gate_results": gates,
            "passed": all(gates.values()),
        }
    passing = [name for name, value in arms.items() if value["passed"]]
    selected_arm = (
        sorted(
            passing,
            key=lambda name: (
                -arms[name]["metrics"]["supported_recall"],
                -arms[name]["metrics"]["supported_precision"],
                -arms[name]["metrics"]["balanced_accuracy"],
                name,
            ),
        )[0]
        if passing
        else None
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "jev-claim-verification-development-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "test_accessed": False,
        "registration_sha256": _sha256_file(registration_path),
        "cohort": registration["cohort"],
        "provider": registration["provider"],
        "protocol": registration["protocol"],
        "development": {"arms": arms, "samples": samples},
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
        "passed": selected_arm is not None,
        "selected_arm": selected_arm,
        "decision": (
            "eligible_for_fresh_confirmation"
            if selected_arm is not None
            else "jev_claim_verification_rejected"
        ),
        "limitations": [
            "Development-only trial on fresh rows from a public benchmark split.",
            "The benchmark label is binary and does not expose every epistemic state.",
            "A pinned service model is reproducible by name but remains externally hosted.",
            "No external test split was accessed.",
        ],
    }
    result["result_sha256"] = _canonical_sha256(result)
    return result


async def run(
    *,
    registration_path: Path,
    dev_path: Path,
    state_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    rows = _validate_registration(
        registration, dev_path=dev_path, project_root=project_root
    )
    state = await _execute(
        rows,
        registration_path=registration_path,
        registration=registration,
        state_path=state_path,
    )
    result = _result(rows, state, registration, registration_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    register = subparsers.add_parser("register")
    register.add_argument("--dev", type=Path, required=True)
    register.add_argument("--output", type=Path, required=True)
    register.add_argument("--project-root", type=Path, default=Path.cwd())
    execute = subparsers.add_parser("run")
    execute.add_argument("--registration", type=Path, required=True)
    execute.add_argument("--dev", type=Path, required=True)
    execute.add_argument("--state", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "register":
        result = create_registration(
            dev_path=args.dev.resolve(),
            output_path=args.output.resolve(),
            project_root=args.project_root.resolve(),
        )
        print(json.dumps({"cohort": result["cohort"], "provider": result["provider"]}, indent=2))
        return
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            dev_path=args.dev.resolve(),
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
                "arms": {
                    name: value["metrics"]
                    for name, value in result["development"]["arms"].items()
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

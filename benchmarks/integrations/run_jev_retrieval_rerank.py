"""Register and run a pinned Jev reranking development trial."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
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
SELECTION_SEED = "prme-jev-agentmembench-rerank-development-v1"
DEVELOPMENT_CASES = 200
CANDIDATES = 5
CONCURRENCY = 6
TIMEOUT_SECONDS = 120.0
MAX_ATTEMPTS = 3


def _question(index: int) -> dict[str, Any]:
    return {
        "type": "noul",
        "instructions": (
            f"Does candidates[{index}] directly provide the specific information "
            "needed to answer the query?"
        ),
        "criteria": {
            "true": (
                "The candidate directly states the answer or supplies the specific "
                "facts needed to answer the query."
            ),
            "false": (
                "The candidate is unrelated, merely on a similar topic, or omits "
                "a material detail needed by the query."
            ),
        },
    }


QUESTIONS = {f"candidate_{index}": _question(index) for index in range(CANDIDATES)}


def _load_details(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "unified-benchmark-v2"
        or value.get("system") != "prme"
    ):
        raise ValueError("AgentMemBench raw result is invalid")
    retrieval = value.get("phases", {}).get("retrieval")
    if not isinstance(retrieval, dict) or not isinstance(retrieval.get("details"), list):
        raise ValueError("AgentMemBench retrieval details are missing")
    details = retrieval["details"]
    for item in details:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("source_id"), str)
            or not isinstance(item.get("memory"), str)
            or not isinstance(item.get("query"), str)
            or not isinstance(item.get("retrieved"), list)
            or len(item["retrieved"]) != CANDIDATES
            or any(not isinstance(candidate, str) for candidate in item["retrieved"])
        ):
            raise ValueError("AgentMemBench retrieval case is invalid")
    return value, details


def _selected(raw_path: Path, excluded_raw_path: Path) -> tuple[list[dict[str, Any]], int]:
    _raw, details = _load_details(raw_path)
    _excluded_raw, excluded = _load_details(excluded_raw_path)
    excluded_ids = {item["source_id"] for item in excluded}
    available = [item for item in details if item["source_id"] not in excluded_ids]
    available.sort(
        key=lambda item: (
            hashlib.sha256(
                f"{SELECTION_SEED}\0{item['source_id']}".encode()
            ).digest(),
            item["source_id"],
        )
    )
    if len(available) < DEVELOPMENT_CASES:
        raise ValueError("fresh AgentMemBench reranking cohort is too small")
    return available[:DEVELOPMENT_CASES], len(excluded_ids)


def _source_rank(item: dict[str, Any], candidates: list[str] | None = None) -> int | None:
    values = item["retrieved"] if candidates is None else candidates
    try:
        return values.index(item["memory"]) + 1
    except ValueError:
        return None


def _order(scores: list[float]) -> list[int]:
    if len(scores) != CANDIDATES:
        raise ValueError("Jev reranking score count is invalid")
    return sorted(range(CANDIDATES), key=lambda index: (-scores[index], index))


def _protocol() -> dict[str, Any]:
    return {
        "task": "query_candidate_relevance_reranking",
        "state_fields": ["query", "candidates"],
        "candidate_count": CANDIDATES,
        "questions": QUESTIONS,
        "questions_sha256": common._canonical_sha256(QUESTIONS),
        "model": MODEL,
        "api_url": API_URL,
        "ordering": "descending_noul_then_original_rank",
        "relevance_unit": "exact_source_memory",
        "candidate_set_mutation": False,
        "concurrency": CONCURRENCY,
        "timeout_seconds": TIMEOUT_SECONDS,
        "max_attempts": MAX_ATTEMPTS,
        "retryable_statuses": sorted(common.RETRYABLE_STATUSES),
        "emit_query_or_candidate_text": False,
    }


def _evaluation() -> dict[str, Any]:
    return {
        "gates": {
            "cases_evaluated_min": DEVELOPMENT_CASES,
            "response_validity_min": DEVELOPMENT_CASES,
            "candidate_set_preservation": True,
            "hit_at_1_gain_min": 0.05,
            "mrr_gain_min": 0.03,
            "recall_at_5_noninferiority_min": 0.0,
            "top1_net_gains_min": 10,
            "p95_request_seconds_max": 1.0,
        },
        "decision_rule": (
            "Do not implement the reranker or score the remaining cohort unless "
            "every development gate passes."
        ),
    }


def _dataset(raw_path: Path, excluded_raw_path: Path, verification_path: Path) -> dict[str, Any]:
    verification = json.loads(verification_path.read_text())
    expected_raw_sha = verification.get("source", {}).get("result_sha256")
    raw_sha = common._sha256_file(raw_path)
    if expected_raw_sha != raw_sha or verification.get("status") != "verified_complete":
        raise ValueError("raw AgentMemBench result does not match verification")
    return {
        "name": "AgentMemBench retrieval",
        "upstream_revision": verification["source"]["upstream_revision"],
        "raw_result_sha256": raw_sha,
        "excluded_development_raw_sha256": common._sha256_file(excluded_raw_path),
        "verification_sha256": common._sha256_file(verification_path),
        "source_text_policy": (
            "Raw public benchmark memories remain outside the repository; public "
            "Jev artifacts contain identities, ranks, probabilities and hashes."
        ),
    }


def create_registration(
    *,
    raw_path: Path,
    excluded_raw_path: Path,
    verification_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    selected, excluded_count = _selected(raw_path, excluded_raw_path)
    baseline_ranks = [_source_rank(item) for item in selected]
    registration = {
        "schema_version": 1,
        "kind": "jev-retrieval-rerank-development-registration",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Passing establishes development evidence that pinned Jev improves "
            "the ordering of PRME's fixed top-five AgentMemBench candidates. It "
            "does not improve candidate recall or establish answer quality."
        ),
        "source": {
            "prme_revision": common._git_revision(project_root),
            "runner_sha256": common._sha256_file(Path(__file__).resolve()),
        },
        "dataset": _dataset(raw_path, excluded_raw_path, verification_path),
        "cohort": {
            "role": "development",
            "selection_seed": SELECTION_SEED,
            "excluded_prior_cases": excluded_count,
            "cases": len(selected),
            "identity_sha256": common._canonical_sha256(
                [item["source_id"] for item in selected]
            ),
            "baseline_exact_source_present": sum(rank is not None for rank in baseline_ranks),
            "baseline_exact_source_at_1": sum(rank == 1 for rank in baseline_ranks),
        },
        "provider": {
            "name": "TypeSafe AI",
            "model": MODEL,
            "endpoint": API_URL,
            "credential_environment": ["JEV_API_KEY", "TYPESAFE_API_KEY"],
        },
        "protocol": _protocol(),
        "evaluation": _evaluation(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(registration, indent=2) + "\n")
    return registration


def _validate_registration(
    registration: dict[str, Any],
    *,
    raw_path: Path,
    excluded_raw_path: Path,
    verification_path: Path,
    project_root: Path,
) -> list[dict[str, Any]]:
    if (
        registration.get("schema_version") != 1
        or registration.get("kind") != "jev-retrieval-rerank-development-registration"
    ):
        raise ValueError("invalid Jev reranking registration")
    revision = registration.get("source", {}).get("prme_revision")
    if not isinstance(revision, str) or not common._git_is_ancestor(revision, project_root):
        raise ValueError("registered PRME revision is not an ancestor")
    if registration.get("source", {}).get("runner_sha256") != common._sha256_file(
        Path(__file__).resolve()
    ):
        raise ValueError("registered Jev reranking runner differs")
    selected, excluded_count = _selected(raw_path, excluded_raw_path)
    ranks = [_source_rank(item) for item in selected]
    expected_cohort = {
        "role": "development",
        "selection_seed": SELECTION_SEED,
        "excluded_prior_cases": excluded_count,
        "cases": len(selected),
        "identity_sha256": common._canonical_sha256(
            [item["source_id"] for item in selected]
        ),
        "baseline_exact_source_present": sum(rank is not None for rank in ranks),
        "baseline_exact_source_at_1": sum(rank == 1 for rank in ranks),
    }
    if (
        registration.get("dataset")
        != _dataset(raw_path, excluded_raw_path, verification_path)
        or registration.get("cohort") != expected_cohort
        or registration.get("protocol") != _protocol()
        or registration.get("evaluation") != _evaluation()
    ):
        raise ValueError("registered Jev reranking inputs differ")
    return selected


def _validate_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("Jev response model differs from pinned registration")
    answers = value.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
        raise ValueError("Jev reranking answer set is invalid")
    for name in QUESTIONS:
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            raise ValueError(f"Jev {name} answer is invalid")
        score = answer.get("noul")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError(f"Jev {name} probability is invalid")
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ValueError("Jev reranking usage is invalid")
    return value


async def _request_one(
    client: httpx.AsyncClient, key: str, item: dict[str, Any]
) -> dict[str, Any]:
    payload = {
        "model": MODEL,
        "state": {"query": item["query"], "candidates": item["retrieved"]},
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
    selected: list[dict[str, Any]],
    *,
    registration_path: Path,
    registration: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    identity = {
        "kind": "jev_retrieval_rerank_development_v1",
        "registration_sha256": common._sha256_file(registration_path),
        "cohort_identity_sha256": registration["cohort"]["identity_sha256"],
        "model": MODEL,
        "questions_sha256": common._canonical_sha256(QUESTIONS),
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state.get("identity") != identity:
            raise ValueError("durable Jev reranking state belongs to another run")
    else:
        state = {"identity": identity, "results": {}}
        common._atomic_write(state_path, state)
    key = common._api_key()
    semaphore = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    completed = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:

        async def execute(item: dict[str, Any]) -> None:
            nonlocal completed
            identifier = item["source_id"]
            if identifier in state["results"]:
                completed += 1
                return
            async with semaphore:
                value = await _request_one(client, key, item)
            async with lock:
                state["results"][identifier] = value
                common._atomic_write(state_path, state)
                completed += 1
                if completed % 20 == 0 or completed == len(selected):
                    print(f"[jev-rerank] completed {completed}/{len(selected)}", flush=True)

        await asyncio.gather(*(execute(item) for item in selected))
    return state


def _metrics(ranks: list[int | None]) -> dict[str, Any]:
    return {
        "cases": len(ranks),
        "exact_source_present": sum(rank is not None for rank in ranks),
        "hit_at_1": sum(rank == 1 for rank in ranks) / len(ranks),
        "recall_at_5": sum(rank is not None for rank in ranks) / len(ranks),
        "mrr": sum(1 / rank for rank in ranks if rank is not None) / len(ranks),
        "rank_counts": {
            str(rank): sum(value == rank for value in ranks) for rank in range(1, 6)
        }
        | {"missing": sum(rank is None for rank in ranks)},
    }


def _result(
    selected: list[dict[str, Any]],
    state: dict[str, Any],
    registration: dict[str, Any],
    registration_path: Path,
) -> dict[str, Any]:
    baseline_ranks: list[int | None] = []
    candidate_ranks: list[int | None] = []
    samples: list[dict[str, Any]] = []
    attempts = input_tokens = output_tokens = 0
    elapsed: list[float] = []
    preserved = True
    for item in selected:
        saved = state["results"].get(item["source_id"])
        if not isinstance(saved, dict):
            raise ValueError(f"missing durable response for {item['source_id']}")
        response = _validate_response(saved["response"])
        scores = [
            response["answers"][f"candidate_{index}"]["noul"]
            for index in range(CANDIDATES)
        ]
        order = _order(scores)
        reranked = [item["retrieved"][index] for index in order]
        preserved = preserved and sorted(reranked) == sorted(item["retrieved"])
        baseline_rank = _source_rank(item)
        candidate_rank = _source_rank(item, reranked)
        baseline_ranks.append(baseline_rank)
        candidate_ranks.append(candidate_rank)
        usage = response["usage"]
        attempts += saved["attempts"]
        input_tokens += usage["input_tokens"]
        output_tokens += usage["output_tokens"]
        elapsed.append(saved["elapsed_seconds"])
        samples.append(
            {
                "id": item["source_id"],
                "event_type": item["event_type"],
                "baseline_rank": baseline_rank,
                "candidate_rank": candidate_rank,
                "scores_by_original_rank": scores,
                "order": [index + 1 for index in order],
                "attempts": saved["attempts"],
                "elapsed_seconds": saved["elapsed_seconds"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
            }
        )
    baseline = _metrics(baseline_ranks)
    candidate = _metrics(candidate_ranks)
    top1_gains = sum(
        before != 1 and after == 1
        for before, after in zip(baseline_ranks, candidate_ranks)
    )
    top1_losses = sum(
        before == 1 and after != 1
        for before, after in zip(baseline_ranks, candidate_ranks)
    )
    p95 = sorted(elapsed)[math.ceil(len(elapsed) * 0.95) - 1]
    gates = registration["evaluation"]["gates"]
    gate_results = {
        "cases_evaluated_min": len(samples) >= gates["cases_evaluated_min"],
        "response_validity_min": len(samples) >= gates["response_validity_min"],
        "candidate_set_preservation": preserved is gates["candidate_set_preservation"],
        "hit_at_1_gain_min": candidate["hit_at_1"] - baseline["hit_at_1"]
        >= gates["hit_at_1_gain_min"],
        "mrr_gain_min": candidate["mrr"] - baseline["mrr"] >= gates["mrr_gain_min"],
        "recall_at_5_noninferiority_min": candidate["recall_at_5"]
        - baseline["recall_at_5"]
        >= gates["recall_at_5_noninferiority_min"],
        "top1_net_gains_min": top1_gains - top1_losses >= gates["top1_net_gains_min"],
        "p95_request_seconds_max": p95 <= gates["p95_request_seconds_max"],
    }
    passed = all(gate_results.values())
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "jev-retrieval-rerank-development-result",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "confirmation_accessed": False,
        "registration_sha256": common._sha256_file(registration_path),
        "cohort": registration["cohort"],
        "provider": registration["provider"],
        "protocol": registration["protocol"],
        "development": {
            "baseline": baseline,
            "candidate": candidate,
            "paired": {
                "top1_gains": top1_gains,
                "top1_losses": top1_losses,
                "top1_net_gains": top1_gains - top1_losses,
            },
            "candidate_set_preserved": preserved,
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
            "eligible_for_fresh_confirmation"
            if passed
            else "jev_retrieval_reranker_rejected"
        ),
        "limitations": [
            "Development subset of one public retrieval benchmark.",
            "Exact-source ranking is stricter than semantic answer relevance in some cases.",
            "The reranker cannot restore a source absent from the fixed candidate set.",
            "No generated-answer quality was measured.",
        ],
    }
    result["result_sha256"] = common._canonical_sha256(result)
    return result


async def run(
    *,
    registration_path: Path,
    raw_path: Path,
    excluded_raw_path: Path,
    verification_path: Path,
    state_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    registration = json.loads(registration_path.read_text())
    selected = _validate_registration(
        registration,
        raw_path=raw_path,
        excluded_raw_path=excluded_raw_path,
        verification_path=verification_path,
        project_root=project_root,
    )
    state = await _execute(
        selected,
        registration_path=registration_path,
        registration=registration,
        state_path=state_path,
    )
    result = _result(selected, state, registration, registration_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--excluded-raw", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
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
    shared = {
        "raw_path": args.raw.resolve(),
        "excluded_raw_path": args.excluded_raw.resolve(),
        "verification_path": args.verification.resolve(),
        "project_root": args.project_root.resolve(),
    }
    if args.command == "register":
        result = create_registration(output_path=args.output.resolve(), **shared)
        print(json.dumps({"cohort": result["cohort"], "protocol": result["protocol"]}, indent=2))
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
                "baseline": result["development"]["baseline"],
                "candidate": result["development"]["candidate"],
                "paired": result["development"]["paired"],
                "gates": result["development"]["gate_results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

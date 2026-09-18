"""Calibrate a typed Jev gate over validated temporal relation operands."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time
from typing import Any, cast

from dotenv import dotenv_values
import httpx

from benchmarks.diagnostics import packing_reader as runtime
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    _load_dataset,
)


MODEL = "jev-1.13.0"
API_URL = "https://api.typesafe.ai/v1/systemone"
TIMEOUT_SECONDS = 60.0
MAX_ATTEMPTS = 3
CONCURRENCY = 4
THRESHOLDS = (0.5, 0.75, 0.8, 0.85, 0.9, 0.95)
QUESTION = {
    "type": "noul",
    "instructions": (
        "Does the evidence_quote for this operand explicitly establish that the "
        "named event happened at the proposed_time_expression?"
    ),
    "criteria": {
        "true": (
            "The proposed expression directly dates the same event, preserving "
            "distinctions such as ordering versus receiving, planning versus doing, "
            "and starting versus finishing."
        ),
        "false": (
            "The expression dates another event or milestone, or the link is ambiguous."
        ),
    },
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _api_key(env_file: Path | None) -> str:
    value = os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    if value:
        return value
    if env_file is not None:
        local = dotenv_values(env_file)
        value = local.get("JEV_API_KEY") or local.get("TYPESAFE_API_KEY")
        if isinstance(value, str) and value:
            return value
    raise RuntimeError("set JEV_API_KEY or TYPESAFE_API_KEY")


def _questions(count: int) -> dict[str, Any]:
    return {f"operand_{index}": dict(QUESTION) for index in range(count)}


def _request(item: dict[str, Any]) -> dict[str, Any]:
    operands = [
        {
            "name": operand["name"],
            "evidence_quote": operand["quote"],
            "proposed_time_expression": (
                "today (the cited record event_time)"
                if operand["time_basis"] == "event_time"
                else operand["time_expression"]
            ),
        }
        for operand in item["relation"]["operands"]
    ]
    return {
        "model": MODEL,
        "state": {
            "question": item["question"],
            "operation": item["relation"]["operation"],
            "operands": operands,
        },
        "questions": _questions(len(operands)),
    }


def _validate_response(value: Any, operand_count: int) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("model") != MODEL:
        raise ValueError("Jev response model differs")
    answers = value.get("answers")
    expected = set(_questions(operand_count))
    if not isinstance(answers, dict) or set(answers) != expected:
        raise ValueError("Jev operand answer coverage differs")
    for name in expected:
        answer = answers[name]
        score = answer.get("noul") if isinstance(answer, dict) else None
        if (
            not isinstance(answer, dict)
            or answer.get("type") != "noul"
            or type(score) not in (int, float)
        ):
            raise ValueError(f"Jev {name} answer is invalid")
        numeric_score = float(cast(int | float, score))
        if not math.isfinite(numeric_score) or not 0 <= numeric_score <= 1:
            raise ValueError(f"Jev {name} answer is invalid")
    usage = value.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(name)) is not int or usage[name] < 0
        for name in ("input_tokens", "output_tokens")
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
    client: httpx.AsyncClient,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
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
                response.status_code in {429, 500, 502, 503, 504, 529}
                and attempt < MAX_ATTEMPTS
            ):
                errors.append(f"http_{response.status_code}")
                await asyncio.sleep(_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            return {
                "response": _validate_response(
                    response.json(), len(payload["state"]["operands"])
                ),
                "attempts": attempt,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "transient_errors": errors,
            }
        except (httpx.TransportError, httpx.TimeoutException):
            errors.append("transport_error")
            if attempt >= MAX_ATTEMPTS:
                raise
            await asyncio.sleep(_retry_delay(response, attempt))
    raise AssertionError("unreachable request loop")


def threshold_metrics(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    """Measure relation-level acceptance at one minimum-operand threshold."""
    accepted = [row for row in rows if row["minimum_probability"] >= threshold]
    correct = [row for row in accepted if row["relation_correct"]]
    total_correct = sum(row["relation_correct"] for row in rows)
    return {
        "threshold": threshold,
        "accepted": len(accepted),
        "correct_accepted": len(correct),
        "false_accepts": len(accepted) - len(correct),
        "precision": len(correct) / len(accepted) if accepted else None,
        "recall_of_correct_relations": len(correct) / total_correct
        if total_correct
        else None,
    }


async def run(
    *,
    probe_path: Path,
    judgments_path: Path,
    dataset_path: Path,
    state_path: Path,
    output_path: Path,
    env_file: Path | None,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh output required")
    if _sha256_file(dataset_path) != DATASET_SHA256:
        raise ValueError("LongMemEval-S dataset checksum differs")
    probe = json.loads(probe_path.read_text())
    judgments = json.loads(judgments_path.read_text())
    if (
        probe.get("kind") != "longmemeval-s-temporal-relation-development-probe"
        or not probe.get("complete")
        or probe.get("failed_attempts")
        or not judgments.get("complete")
        or judgments.get("prior_failed_attempts")
    ):
        raise ValueError("source probe or judgments are incomplete")
    cases = {case["question_id"]: case for case in _load_dataset(dataset_path)}
    verdicts = {
        judgment["id"].removesuffix(":relation"): judgment["correct"]
        for judgment in judgments["judgments"]
    }
    items = []
    for row in probe["rows"]:
        if row["relation"] is None:
            continue
        question_id = row["question_id"]
        if question_id not in verdicts:
            raise ValueError("relation judgment coverage differs")
        items.append(
            {
                "question_id": question_id,
                "question": cases[question_id]["question"],
                "relation": row["relation"],
                "relation_correct": verdicts[question_id],
            }
        )
    identity = {
        "kind": "longmemeval-s-temporal-relation-jev-state",
        "probe_sha256": _sha256_file(probe_path),
        "judgments_sha256": _sha256_file(judgments_path),
        "dataset_sha256": DATASET_SHA256,
        "model": MODEL,
        "api_url": API_URL,
        "question_sha256": runtime.digest(runtime.canonical(QUESTION)),
    }
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {"identity": identity, "results": {}, "failures": []}
    )
    if state.get("identity") != identity:
        raise ValueError("Jev resume identity differs")
    wanted = {item["question_id"] for item in items}
    if not set(state["results"]) <= wanted:
        raise ValueError("Jev state contains unrelated results")
    for item in items:
        saved = state["results"].get(item["question_id"])
        if saved is not None:
            _validate_response(saved["response"], len(item["relation"]["operands"]))
    runtime.write(state_path, state)

    key = _api_key(env_file)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    completed = len(state["results"])
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:

        async def execute(item: dict[str, Any]) -> None:
            nonlocal completed
            question_id = item["question_id"]
            if question_id in state["results"]:
                return
            payload = _request(item)
            try:
                async with semaphore:
                    value = await _request_one(client, key, payload)
            except Exception as exc:
                async with lock:
                    state["failures"].append(
                        {"question_id": question_id, "error_type": type(exc).__name__}
                    )
                    runtime.write(state_path, state)
                raise
            async with lock:
                state["results"][question_id] = value
                runtime.write(state_path, state)
                completed += 1
                print(f"Jev gated {completed}/{len(items)}", flush=True)

        await asyncio.gather(*(execute(item) for item in items))

    rows = []
    for item in items:
        response = state["results"][item["question_id"]]["response"]
        probabilities = [
            float(response["answers"][f"operand_{index}"]["noul"])
            for index in range(len(item["relation"]["operands"]))
        ]
        rows.append(
            {
                "question_id": item["question_id"],
                "relation_correct": item["relation_correct"],
                "operand_probabilities": probabilities,
                "minimum_probability": min(probabilities),
            }
        )
    value = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-jev-development-result",
        "claim_boundary": (
            "Observed development calibration of a typed semantic gate; not "
            "confirmation or product-promotion evidence."
        ),
        "identity": identity,
        "complete": len(rows) == len(items) and not state["failures"],
        "failures": state["failures"],
        "relations": len(rows),
        "correct_relations": sum(row["relation_correct"] for row in rows),
        "thresholds": [threshold_metrics(rows, threshold) for threshold in THRESHOLDS],
        "usage": {
            name: sum(
                saved["response"]["usage"][name] for saved in state["results"].values()
            )
            for name in ("input_tokens", "output_tokens")
        },
        "rows": rows,
    }
    value["result_sha256"] = runtime.digest(runtime.canonical(value))
    runtime.write(output_path, value)
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    value = asyncio.run(
        run(
            probe_path=args.probe.resolve(),
            judgments_path=args.judgments.resolve(),
            dataset_path=args.dataset.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            env_file=args.env_file.resolve() if args.env_file else None,
        )
    )
    print(
        json.dumps(
            {
                "relations": value["relations"],
                "correct_relations": value["correct_relations"],
                "thresholds": value["thresholds"],
                "result_sha256": value["result_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

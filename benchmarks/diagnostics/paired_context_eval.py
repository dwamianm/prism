"""Reusable fail-closed execution for frozen multi-arm context trials."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from benchmarks.diagnostics import packing_reader as reader_runtime
from benchmarks.diagnostics import reader_judge as judge_runtime


def digest(raw: bytes) -> str:
    return reader_runtime.digest(raw)


def canonical(value: Any) -> bytes:
    return reader_runtime.canonical(value)


def write(path: Path, value: Any) -> None:
    reader_runtime.write(path, value)


def model_identity(
    model: str, base_url: str, options: dict[str, Any], system_prompt: str
) -> dict[str, Any]:
    resolved = (
        model.removesuffix(":cloud")
        if model.endswith(":cloud")
        else model.removesuffix("-cloud")
        if model.endswith("-cloud")
        else model
    )
    return {
        "provider": "ollama",
        "model": model,
        "model_digest": reader_runtime.model_digest(base_url, model),
        "ollama_version": reader_runtime.request(base_url, "/api/version")["version"],
        "accepted_response_models": sorted({model, resolved}),
        "options": options,
        "system_prompt_sha256": digest(system_prompt.encode()),
    }


def reader_payload(
    row: dict[str, Any],
    arm: str,
    *,
    model: str,
    options: dict[str, Any],
    system_prompt: str,
) -> dict[str, Any]:
    context = row["contexts"][arm]
    if context["sha256"] != digest(context["context"].encode()):
        raise ValueError("prepared context checksum differs")
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "QUESTION DATE:\n"
                + row["question_date"]
                + "\n\nMEMORY:\n"
                + context["context"]
                + "\n\nQUESTION:\n"
                + row["question"]
            ),
        },
    ]
    if sum(len(message["content"].encode()) for message in messages) + 4096 + int(
        options["num_predict"]
    ) > int(options["num_ctx"]):
        raise ValueError("reader prompt exceeds conservative context headroom")
    return {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": options,
    }


def reader_jobs(
    prepared: dict[str, Any],
    arms: tuple[str, ...],
    *,
    model: str,
    options: dict[str, Any],
    system_prompt: str,
) -> list[tuple[dict[str, Any], str, dict[str, Any], str]]:
    jobs = []
    for row in prepared["rows"]:
        if len(arms) < 2 or len(set(arms)) != len(arms):
            raise ValueError("reader arms must contain distinct alternatives")
        if len(arms) == 2:
            order = (
                arms
                if int(digest(row["question_id"].encode()), 16) % 2
                else tuple(reversed(arms))
            )
        else:
            offset = int(digest(row["question_id"].encode()), 16) % len(arms)
            rotated = arms[offset:] + arms[:offset]
            order = (
                rotated
                if int(digest((row["question_id"] + ":direction").encode()), 16) % 2
                else tuple(reversed(rotated))
            )
        for arm in order:
            body = reader_payload(
                row,
                arm,
                model=model,
                options=options,
                system_prompt=system_prompt,
            )
            jobs.append((row, arm, body, digest(canonical(body))))
    return jobs


def run_reader(
    prepared: dict[str, Any],
    arms: tuple[str, ...],
    state_path: Path,
    *,
    registration_sha256: str,
    prepared_sha256: str,
    model: str,
    model_digest: str,
    options: dict[str, Any],
    system_prompt: str,
    base_url: str,
) -> dict[str, Any]:
    identity = {
        "registration_sha256": registration_sha256,
        "prepared_sha256": prepared_sha256,
        "model": model,
        "model_digest": model_digest,
        "options": options,
        "system_prompt_sha256": digest(system_prompt.encode()),
        "arms": list(arms),
    }
    jobs = reader_jobs(
        prepared,
        arms,
        model=model,
        options=options,
        system_prompt=system_prompt,
    )
    with reader_runtime.exclusive_state(state_path):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "identity": identity,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "generations": {},
                "failed_attempts": [],
                "complete": False,
            }
        )
        if state["identity"] != identity:
            raise ValueError("reader resume identity differs")
        wanted = {key for _row, _arm, _body, key in jobs}
        if not set(state["generations"]) <= wanted:
            raise ValueError("reader state contains unrelated generations")
        for saved in state["generations"].values():
            if saved["response_sha256"] != digest(canonical(saved["response"])):
                raise ValueError("saved reader response checksum differs")
            reader_runtime.validate_response(saved["response"])
        write(state_path, state)
        for index, (_row, _arm, body, key) in enumerate(jobs):
            if key not in state["generations"]:
                response = None
                try:
                    if reader_runtime.model_digest(base_url, model) != model_digest:
                        raise ValueError("reader model changed")
                    response = reader_runtime.request(base_url, "/api/chat", body)
                    answer = reader_runtime.validate_response(response)
                    if (
                        not answer
                        or not judge_runtime.matches_response_model(
                            model, response.get("model")
                        )
                        or reader_runtime.model_digest(base_url, model) != model_digest
                    ):
                        raise ValueError("reader response identity changed")
                    state["generations"][key] = {
                        "response": response,
                        "response_sha256": digest(canonical(response)),
                    }
                except Exception as exc:
                    state["failed_attempts"].append(
                        {
                            "prompt_sha256": key,
                            "error_type": type(exc).__name__,
                            "at": datetime.now(timezone.utc).isoformat(),
                            "response": response,
                            "response_sha256": (
                                digest(canonical(response))
                                if response is not None
                                else None
                            ),
                        }
                    )
                    write(state_path, state)
                    raise
                write(state_path, state)
            print(f"Reader {index + 1}/{len(jobs)}", flush=True)
        state["complete"] = True
        write(state_path, state)
        rows = [
            {
                "question_id": row["question_id"],
                "arm": arm,
                "context_sha256": row["contexts"][arm]["sha256"],
                "prompt_sha256": key,
                "hypothesis": reader_runtime.validate_response(
                    state["generations"][key]["response"]
                ),
            }
            for row, arm, _body, key in jobs
        ]
        return {
            "complete": True,
            "identity": identity,
            "started_at": state["started_at"],
            "questions": len(prepared["rows"]),
            "logical_predictions": len(jobs),
            "unique_generations": len(state["generations"]),
            "failed_attempts": state["failed_attempts"],
            "rows": rows,
        }


def judge_cases(
    predictions: dict[str, Any],
    references: list[dict[str, Any]],
    arms: tuple[str, ...],
) -> list[dict[str, Any]]:
    by_id = {row["question_id"]: row for row in references}
    if len(by_id) != len(references):
        raise ValueError("reference identities are duplicated")
    cases = []
    for row in predictions["rows"]:
        reference = by_id[row["question_id"]]
        cases.append(
            {
                "id": row["question_id"] + ":" + row["arm"],
                "question_id": row["question_id"],
                "arm": row["arm"],
                "category": (
                    "abstention"
                    if row["question_id"].endswith("_abs")
                    else reference["question_type"]
                ),
                "question": reference["question"],
                "reference": str(reference["answer"]),
                "hypothesis": row["hypothesis"],
            }
        )
    expected = {(question_id, arm) for question_id in by_id for arm in arms}
    if {(row["question_id"], row["arm"]) for row in cases} != expected:
        raise ValueError("judge case coverage differs")
    return cases


def paired_metrics(
    cases: list[dict[str, Any]],
    judgments: dict[str, Any],
    *,
    control_arm: str,
    candidate_arm: str,
) -> dict[str, Any]:
    arms = (control_arm, candidate_arm)
    verdicts = {row["id"]: row["correct"] for row in judgments["judgments"]}
    if set(verdicts) != {row["id"] for row in cases}:
        raise ValueError("judge verdict coverage differs")
    selected_cases = [case for case in cases if case["arm"] in arms]
    grouped: dict[str, dict[str, Any]] = {}
    for case in selected_cases:
        row = grouped.setdefault(case["question_id"], {"category": case["category"]})
        row[case["arm"]] = verdicts[case["id"]]
    if any(set(row) != {"category", *arms} for row in grouped.values()):
        raise ValueError("paired judgment coverage differs")

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        control = sum(row[control_arm] for row in rows)
        candidate = sum(row[candidate_arm] for row in rows)
        wins = sum(row[candidate_arm] and not row[control_arm] for row in rows)
        losses = sum(row[control_arm] and not row[candidate_arm] for row in rows)
        return {
            "questions": len(rows),
            "control_correct": control,
            "candidate_correct": candidate,
            "paired_wins": wins,
            "paired_losses": losses,
            "paired_ties": len(rows) - wins - losses,
            "accuracy_delta": (candidate - control) / len(rows),
        }

    values = list(grouped.values())
    return {
        "arms": {"control": control_arm, "candidate": candidate_arm},
        "overall": summarize(values),
        "categories": {
            category: summarize([row for row in values if row["category"] == category])
            for category in sorted({row["category"] for row in values})
        },
        "complete_reader_execution": True,
        "complete_judge_execution": judgments.get("complete") is True,
        "reader_failed_attempts": 0,
        "judge_failed_attempts": len(judgments.get("prior_failed_attempts", [])),
    }

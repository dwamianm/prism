"""Verify and compare a registered LongMemEval-V2 episode-routing trial."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from benchmarks.integrations import compare_longmemeval_v2 as paired
from benchmarks.integrations import compare_longmemeval_v2_curve as curve


REGISTRATION_KIND = "longmemeval-v2-episode-policy-registration"
RESULT_KIND = "longmemeval-v2-episode-policy-comparison"
_ARMS = ("flat", "episode")
_EPISODE_FIELDS = {
    "episode_context_top_k",
    "episode_context_local_k",
    "episode_context_score_decay",
}
_SYSTEM_FIELDS = curve._SYSTEM_FIELDS | _EPISODE_FIELDS


def _protocol_specification() -> dict[str, Any]:
    return {
        "analysis": "complete_registered_arms_with_question_paired_statistics",
        "claim_boundary": (
            "Development episode-routing trial on a previously scored cohort; it "
            "can guide evidence routing but is not a new holdout or competitor result."
        ),
        "configuration_rule": (
            "Arms differ only in the registered episode-context policy; source "
            "pack, token budget, context format, image policy, reader, and question "
            "order match."
        ),
        "emit_source_text": False,
        "stopping_rule": (
            "Run every registered question in both arms; exact checkpoints may "
            "resume infrastructure failures without replacing completed generations."
        ),
    }


def _episode_policy(system: dict[str, Any]) -> dict[str, int | float]:
    top_k = system.get("episode_context_top_k")
    local_k = system.get("episode_context_local_k")
    decay = system.get("episode_context_score_decay")
    if (
        type(top_k) is not int
        or top_k < 0
        or type(local_k) is not int
        or local_k < 1
        or type(decay) not in {int, float}
        or not math.isfinite(decay)
        or not 0 < decay <= 1
    ):
        raise ValueError("registered episode-context policy is invalid")
    return {
        "episode_context_top_k": top_k,
        "episode_context_local_k": local_k,
        "episode_context_score_decay": float(decay),
    }


def _validate_system(
    label: str,
    run: dict[str, Any],
    system: dict[str, Any],
) -> dict[str, Any]:
    if set(system) != _SYSTEM_FIELDS:
        raise ValueError(f"registered system fields are invalid for {label}")
    episode_policy = _episode_policy(system)
    base_system = {
        field: value for field, value in system.items() if field not in _EPISODE_FIELDS
    }
    binding = curve._validate_system(label, run, base_system)
    binding.pop("non_budget_policy_sha256")
    config = paired._load_json(Path(system["memory_config"]))
    params = config.get("memory_params")
    if not isinstance(params, dict) or any(
        params.get(field, default) != episode_policy[field]
        for field, default in (
            ("episode_context_top_k", 0),
            ("episode_context_local_k", 8),
            ("episode_context_score_decay", 0.95),
        )
    ):
        raise ValueError(f"saved episode-context policy does not match system {label}")
    normalized = dict(params)
    for field in _EPISODE_FIELDS:
        normalized[field] = "<registered-episode-policy>"
    return {
        **binding,
        **episode_policy,
        "non_episode_policy_sha256": hashlib.sha256(
            json.dumps(
                normalized,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest(),
    }


def _validate_prompt_metadata(
    run: dict[str, Any],
    policy: dict[str, int | float],
    *,
    label: str,
) -> None:
    path = run["directory"] / "prompt_rows.jsonl"
    observed: set[str] = set()
    for row in paired._iter_jsonl(path):
        question_id = row.get("question_id")
        metadata = row.get("memory_post_query_metadata")
        if (
            not isinstance(question_id, str)
            or not question_id
            or question_id in observed
            or not isinstance(metadata, dict)
            or any(metadata.get(field) != value for field, value in policy.items())
        ):
            raise ValueError(f"prompt row episode policy is invalid for {label}")
        observed.add(question_id)
    expected = {row["question_id"] for row in run["records"]}
    if observed != expected:
        raise ValueError(f"prompt episode metadata is incomplete for {label}")


def _arm_summary(run: dict[str, Any]) -> dict[str, Any]:
    records = run["records"]
    correct = sum(row["score_bool"] for row in records)
    return {
        "questions": len(records),
        "correct": correct,
        "accuracy": correct / len(records),
        "unknown": sum(row["is_unknown"] for row in records),
        "empty_responses": sum(row["response_empty"] for row in records),
        **paired._efficiency(run),
    }


def compare_episode_policies(
    directories: dict[str, Path],
    *,
    registration_path: Path,
    samples: int = 10_000,
) -> dict[str, Any]:
    """Verify the registered policies and return aggregate paired evidence."""
    if samples < 1:
        raise ValueError("bootstrap samples must be positive")
    if set(directories) != set(_ARMS):
        raise ValueError("episode-policy comparison requires flat and episode arms")
    registration = paired._load_json(registration_path)
    if (
        registration.get("schema_version") != 2
        or registration.get("kind") != REGISTRATION_KIND
        or registration.get("protocol") != _protocol_specification()
    ):
        raise ValueError("episode-policy registration identity is invalid")
    systems = registration.get("systems")
    if not isinstance(systems, dict) or set(systems) != set(_ARMS):
        raise ValueError("run labels do not match registered episode-policy systems")

    runs, question_ids, input_hashes = curve._matched_runs(directories)
    reference = runs["flat"]
    curve._validate_selection_and_reader(
        registration, reference, question_ids, input_hashes
    )
    registration_sha256 = paired._digest(registration_path)
    execution_source = curve._validate_execution_source(
        runs, registration, registration_sha256
    )
    reader_runtime = curve._validate_reader_runtime(runs, registration)
    bindings = {
        label: _validate_system(label, runs[label], systems[label])
        for label in _ARMS
    }
    for field in (
        "budget",
        "context_format",
        "memory_payload_artifact_sha256",
        "non_episode_policy_sha256",
    ):
        if len({binding[field] for binding in bindings.values()}) != 1:
            raise ValueError(f"episode-policy arms differ outside episode policy: {field}")
    if (
        bindings["flat"]["episode_context_top_k"] != 0
        or bindings["episode"]["episode_context_top_k"] <= 0
    ):
        raise ValueError("episode-policy arms do not identify control and treatment")
    for label in _ARMS:
        _validate_prompt_metadata(
            runs[label],
            {field: bindings[label][field] for field in _EPISODE_FIELDS},
            label=label,
        )

    left = runs["episode"]["by_id"]
    right = runs["flat"]["by_id"]
    categories = sorted({str(row["category"]) for row in reference["records"]})
    return {
        "schema_version": 1,
        "kind": RESULT_KIND,
        "registration_sha256": registration_sha256,
        "labels": {"left": "episode", "right": "flat"},
        "input_sha256": input_hashes,
        "reader_settings": {
            field: reference["args"].get(field) for field in paired._READER_FIELDS
        },
        "reader_runtime": reader_runtime,
        "bootstrap": {"samples": samples, "seed": 42, "unit": "question"},
        "bindings": bindings,
        "arms": {label: _arm_summary(runs[label]) for label in _ARMS},
        "overall": paired._outcomes(question_ids, left, right, samples=samples),
        "categories": {
            category: paired._outcomes(
                [
                    question_id
                    for question_id in question_ids
                    if reference["by_id"][question_id]["category"] == category
                ],
                left,
                right,
                samples=samples,
            )
            for category in categories
        },
        "artifacts": {label: runs[label]["artifacts"] for label in _ARMS},
        "execution_source": execution_source,
        "limitations": [
            "This is a development episode-routing trial on a previously scored cohort, not a new holdout.",
            "Question bootstrap intervals condition on the selected cohort; shared haystacks can make questions dependent.",
            "The result covers one registered reader and source artifact family and does not establish universal superiority.",
        ],
    }


def _arm(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("arms must use LABEL=/absolute/run/path")
    return label, Path(raw_path).expanduser().resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", type=_arm, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    arms = dict(args.arm)
    if len(arms) != len(args.arm):
        raise SystemExit("arm labels must be unique")
    result = compare_episode_policies(
        arms,
        registration_path=args.registration.resolve(),
        samples=args.bootstrap_samples,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

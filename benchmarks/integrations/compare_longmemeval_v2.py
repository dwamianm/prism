"""Compare two complete, matched LongMemEval-V2 harness runs."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from benchmarks.compare_evidence import paired_statistics


_READER_FIELDS = (
    "domain",
    "model",
    "base_url",
    "max_completion_tokens",
    "memory_context_max_tokens",
    "reader_max_concurrent_requests",
    "prompt_build_max_workers",
    "temperature",
    "top_p",
    "top_k",
    "presence_penalty",
    "repetition_penalty",
    "reasoning_effort",
    "reader_enable_thinking",
    "shuffle_questions_seed",
)
_INPUT_FIELDS = ("questions_path", "haystack_path", "trajectories_path")
_QUESTION_FIELDS = (
    "question_id",
    "question_type",
    "category",
    "is_abstention_problem",
    "eval_function",
    "question_text",
    "answer_gold",
)
_ARTIFACT_NAMES = (
    "run_args.json",
    "prompt_build_summary.json",
    "prompt_rows.jsonl",
    "reader_outputs.checkpoint.jsonl",
    "per_question.jsonl",
    "aggregated_metrics.json",
)
_EXECUTION_MANIFEST_FILENAME = "execution_manifest.json"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _iter_jsonl(path: Path):
    """Parse one JSONL row at a time so prompt artifacts stay bounded in memory."""
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row {line_number} is not an object: {path}")
            yield value


def _load_result_rows(path: Path) -> list[dict[str, Any]]:
    """Retain comparison fields without holding repeated prompts and contexts."""
    fields = (*_QUESTION_FIELDS, "score_bool", "is_unknown", "usage",
              "memory_context_token_count", "memory_query_duration_seconds")
    records = []
    for value in _iter_jsonl(path):
        record = {field: value.get(field) for field in fields}
        record["response_empty"] = not str(value.get("response_raw") or "").strip()
        records.append(record)
    return records


def _require_exact_question_ids(path: Path, expected: set[str]) -> None:
    ids = [row.get("question_id") for row in _iter_jsonl(path)]
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError(f"artifact has invalid question identities: {path}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"artifact has duplicate question identities: {path}")
    if set(ids) != expected:
        raise ValueError(f"artifact question identities are incomplete: {path}")


def _load_run(directory: Path) -> dict[str, Any]:
    required = [directory / name for name in _ARTIFACT_NAMES]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"incomplete LongMemEval-V2 run {directory}: missing {missing}")

    records = _load_result_rows(directory / "per_question.jsonl")
    ids = [row.get("question_id") for row in records]
    if not records or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError(f"run has invalid question identities: {directory}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"run has duplicate question identities: {directory}")
    for row in records:
        if type(row.get("score_bool")) is not bool or type(row.get("is_unknown")) is not bool:
            raise ValueError(f"run has invalid binary outcomes: {directory}")
        usage = row.get("usage")
        if not isinstance(usage, dict) or any(
            type(usage.get(field)) is not int or usage[field] < 0
            for field in ("prompt_tokens", "completion_tokens", "total_tokens")
        ):
            raise ValueError(f"run has invalid token usage: {directory}")

    aggregated = _load_json(directory / "aggregated_metrics.json")
    observed_count = aggregated.get("overall", {}).get("count_all_questions")
    if observed_count != len(records):
        raise ValueError(f"aggregate question count does not match rows: {directory}")
    expected_score = sum(row["score_bool"] for row in records) / len(records)
    if not math.isclose(
        float(aggregated.get("overall", {}).get("overall_full_set", math.nan)),
        expected_score,
        abs_tol=1e-12,
    ):
        raise ValueError(f"aggregate score does not match rows: {directory}")
    expected_ids = set(ids)
    summary = _load_json(directory / "prompt_build_summary.json")
    if (summary.get("prompt_row_count") != len(records)
            or summary.get("question_ids") != ids):
        raise ValueError(f"prompt build summary does not match final rows: {directory}")
    for name in ("prompt_rows.jsonl", "reader_outputs.checkpoint.jsonl"):
        _require_exact_question_ids(directory / name, expected_ids)
    artifacts = {name: _digest(directory / name) for name in _ARTIFACT_NAMES}
    execution_path = directory / _EXECUTION_MANIFEST_FILENAME
    execution = None
    if execution_path.exists():
        if not execution_path.is_file():
            raise ValueError(f"execution manifest is not a file: {execution_path}")
        execution = _load_json(execution_path)
        artifacts[_EXECUTION_MANIFEST_FILENAME] = _digest(execution_path)
    return {
        "directory": directory,
        "args": _load_json(directory / "run_args.json"),
        "aggregated": aggregated,
        "records": records,
        "by_id": {row["question_id"]: row for row in records},
        "artifacts": artifacts,
        "execution": execution,
    }


def _exact_mcnemar(wins: int, losses: int) -> float:
    """Two-sided exact McNemar p-value over discordant binary pairs."""
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(wins, losses) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _outcomes(
    ids: list[str],
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
    *,
    samples: int,
) -> dict[str, Any]:
    pairs = [
        (float(right[question_id]["score_bool"]), float(left[question_id]["score_bool"]))
        for question_id in ids
    ]
    paired = paired_statistics(pairs, samples=samples)
    wins = int(paired["wins"])
    losses = int(paired["losses"])
    paired["exact_mcnemar_p_two_sided"] = _exact_mcnemar(wins, losses)
    return {
        "questions": len(ids),
        "left_correct": sum(left[question_id]["score_bool"] for question_id in ids),
        "right_correct": sum(right[question_id]["score_bool"] for question_id in ids),
        "left_unknown": sum(left[question_id]["is_unknown"] for question_id in ids),
        "right_unknown": sum(right[question_id]["is_unknown"] for question_id in ids),
        "left_empty_responses": sum(left[question_id]["response_empty"] for question_id in ids),
        "right_empty_responses": sum(right[question_id]["response_empty"] for question_id in ids),
        "paired_left_minus_right": paired,
    }


def _efficiency(run: dict[str, Any]) -> dict[str, Any]:
    rows = run["records"]
    started = datetime.fromisoformat(run["args"]["started_at_utc"])
    completed = datetime.fromisoformat(run["aggregated"]["completed_at_utc"])
    if started.utcoffset() is None or completed.utcoffset() is None or completed < started:
        raise ValueError(f"invalid run clock: {run['directory']}")
    return {
        "wall_seconds": (completed - started).total_seconds(),
        "prompt_tokens": sum(row["usage"]["prompt_tokens"] for row in rows),
        "completion_tokens": sum(row["usage"]["completion_tokens"] for row in rows),
        "total_tokens": sum(row["usage"]["total_tokens"] for row in rows),
        "memory_context_tokens_mean": sum(
            int(row["memory_context_token_count"]) for row in rows
        ) / len(rows),
        "memory_query_seconds_mean": sum(
            float(row["memory_query_duration_seconds"]) for row in rows
        ) / len(rows),
        "memory_query_seconds_max": max(
            float(row["memory_query_duration_seconds"]) for row in rows
        ),
    }


def _validate_registered_systems(
    left: dict[str, Any],
    right: dict[str, Any],
    registration: dict[str, Any],
) -> dict[str, Any]:
    """Bind each comparison arm to the system registered before generation."""
    source = registration.get("source")
    systems = registration.get("systems")
    if not isinstance(source, dict) or not isinstance(systems, dict):
        raise ValueError("registration is missing source or system settings")
    prme = systems.get("prme")
    baseline = systems.get("baseline")
    if not isinstance(prme, dict) or not isinstance(baseline, dict):
        raise ValueError("registration is missing PRME or baseline settings")

    left_args = left["args"]
    right_args = right["args"]
    if left_args.get("memory_config_path") != prme.get("memory_config"):
        raise ValueError("left arm does not use the registered PRME memory configuration")
    if right_args.get("memory_config_path") != baseline.get("memory_config"):
        raise ValueError("right arm does not use the registered baseline memory configuration")
    upstream_budget = prme.get("upstream_context_budget_tokens")
    if (
        type(upstream_budget) is not int
        or upstream_budget < 1
        or left_args.get("memory_context_max_tokens") != upstream_budget
        or right_args.get("memory_context_max_tokens") != upstream_budget
    ):
        raise ValueError("run context budget does not match the registered systems")
    if any(
        args.get("save_memory") is not False
        or args.get("skip_evaluation") is not False
        for args in (left_args, right_args)
    ):
        raise ValueError("registered comparison arms must load memory and complete evaluation")

    load_memory = left_args.get("load_memory_dir")
    if not isinstance(load_memory, str) or not load_memory:
        raise ValueError("registered PRME arm must load its frozen memory artifact")
    if right_args.get("load_memory_dir") is not None:
        raise ValueError("registered no-memory baseline cannot load a memory artifact")
    load_root = Path(load_memory)
    if not load_root.is_absolute():
        raise ValueError("registered PRME memory artifact path must be absolute")
    saved_config_path = load_root / "memory_config.json"
    pack_manifest_path = load_root / "prme_pack" / "longmemeval_v2_manifest.json"
    if not saved_config_path.is_file() or not pack_manifest_path.is_file():
        raise ValueError("registered PRME memory artifact is incomplete")

    saved_config_sha256 = _digest(saved_config_path)
    pack_manifest_sha256 = _digest(pack_manifest_path)
    if saved_config_sha256 != source.get("prme_memory_config_sha256"):
        raise ValueError("loaded PRME memory configuration does not match registration")
    if pack_manifest_sha256 != source.get("prme_pack_manifest_sha256"):
        raise ValueError("loaded PRME pack manifest does not match registration")
    saved_config = _load_json(saved_config_path)
    memory_params = saved_config.get("memory_params")
    effective_context_format = (
        memory_params.get("context_format", "auditable")
        if isinstance(memory_params, dict)
        else None
    )
    if (
        saved_config.get("memory_type") != "prme"
        or not isinstance(memory_params, dict)
        or memory_params.get("token_budget")
        != prme.get("internal_context_budget_cl100k_tokens")
        or memory_params.get("image_limit") != prme.get("max_source_screenshots")
        or effective_context_format not in {"auditable", "compact"}
        or (
            prme.get("context_format") is not None
            and effective_context_format != prme.get("context_format")
        )
    ):
        raise ValueError("loaded PRME adapter settings do not match registration")
    pack_manifest = _load_json(pack_manifest_path)
    if (
        pack_manifest.get("schema_version")
        != source.get("prme_adapter_schema_version")
        or pack_manifest.get("upstream_revision") != source.get("upstream_revision")
    ):
        raise ValueError("loaded PRME pack identity does not match registration")
    if any(row["memory_context_token_count"] != 0 for row in right["records"]):
        raise ValueError("registered no-memory baseline returned memory context")
    return {
        "left_memory_config": left_args["memory_config_path"],
        "right_memory_config": right_args["memory_config_path"],
        "prme_memory_config_sha256": saved_config_sha256,
        "prme_pack_manifest_sha256": pack_manifest_sha256,
        "prme_adapter_schema_version": pack_manifest["schema_version"],
        "prme_context_format": effective_context_format,
        "upstream_revision": pack_manifest["upstream_revision"],
        "baseline_memory_context_tokens_zero": True,
    }


def _validate_registered_execution(
    left: dict[str, Any],
    right: dict[str, Any],
    registration: dict[str, Any],
    registration_sha256: str,
) -> dict[str, Any]:
    """Verify launch-time source proof required by registration schema 2."""
    left_manifest = left.get("execution")
    right_manifest = right.get("execution")
    if not isinstance(left_manifest, dict) or not isinstance(right_manifest, dict):
        raise ValueError("schema-2 registered runs require execution manifests")
    for manifest in (left_manifest, right_manifest):
        if (
            manifest.get("schema_version") != 2
            or manifest.get("kind") != "longmemeval-v2-execution"
            or manifest.get("registration_sha256") != registration_sha256
        ):
            raise ValueError("execution manifest does not match the registration")
    left_source = left_manifest.get("source")
    right_source = right_manifest.get("source")
    if not isinstance(left_source, dict) or left_source != right_source:
        raise ValueError("comparison arms do not share one execution source")

    registered_source = registration.get("source")
    if not isinstance(registered_source, dict):
        raise ValueError("registration is missing source identity")
    if (
        left_source.get("prme_revision") != registered_source.get("prme_revision")
        or left_source.get("upstream_revision")
        != registered_source.get("upstream_revision")
    ):
        raise ValueError("execution source revisions do not match registration")
    if left_source.get("prme_worktree_changes") != []:
        raise ValueError("registered execution used a modified PRME worktree")
    if left_source.get("upstream_worktree_changes") not in (
        [],
        [
            "evaluation/memory_configs/prme.json",
            "evaluation/memory_configs/prme_compact.json",
            "memory_modules/__init__.py",
            "memory_modules/prme.py",
        ],
    ):
        raise ValueError("registered execution used unexpected upstream changes")

    digest_fields = (
        "launcher_sha256",
        "installer_sha256",
        "adapter_source_sha256",
        "adapter_installed_sha256",
        "config_source_sha256",
        "config_installed_sha256",
        "compact_config_source_sha256",
        "compact_config_installed_sha256",
        "upstream_harness_sha256",
    )
    for field in digest_fields:
        value = left_source.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"execution source has an invalid {field}")
    if (
        left_source["adapter_source_sha256"]
        != left_source["adapter_installed_sha256"]
        or left_source["config_source_sha256"]
        != left_source["config_installed_sha256"]
        or left_source["compact_config_source_sha256"]
        != left_source["compact_config_installed_sha256"]
    ):
        raise ValueError("installed benchmark system differs from its registered source")

    systems = registration.get("systems")
    if not isinstance(systems, dict):
        raise ValueError("registration is missing system settings")
    invocations: dict[str, dict[str, Any]] = {}
    for label, run, manifest, system_name in (
        ("left", left, left_manifest, "prme"),
        ("right", right, right_manifest, "baseline"),
    ):
        invocation = manifest.get("invocation")
        system = systems.get(system_name)
        if not isinstance(invocation, dict) or not isinstance(system, dict):
            raise ValueError("execution manifest is missing invocation identity")
        config_path = invocation.get("memory_config_path")
        config_sha256 = invocation.get("memory_config_sha256")
        if (
            config_path != run["args"].get("memory_config_path")
            or config_path != system.get("memory_config")
        ):
            raise ValueError("execution memory configuration path does not match the run")
        if (
            not isinstance(config_sha256, str)
            or len(config_sha256) != 64
            or any(character not in "0123456789abcdef" for character in config_sha256)
            or config_sha256 != system.get("memory_config_sha256")
        ):
            raise ValueError("execution memory configuration hash does not match registration")
        load_memory_dir = invocation.get("load_memory_dir")
        memory_artifact = invocation.get("memory_artifact")
        if load_memory_dir != run["args"].get("load_memory_dir"):
            raise ValueError("execution saved-memory path does not match the run")
        if system_name == "prme":
            if not isinstance(memory_artifact, dict):
                raise ValueError("PRME execution is missing saved-memory identity")
            if (
                memory_artifact.get("sha256")
                != system.get("memory_artifact_sha256")
                or memory_artifact.get("file_count")
                != system.get("memory_artifact_file_count")
                or memory_artifact.get("bytes")
                != system.get("memory_artifact_bytes")
            ):
                raise ValueError("execution saved-memory identity does not match registration")
        elif load_memory_dir is not None or memory_artifact is not None:
            raise ValueError("baseline execution unexpectedly loaded saved memory")
        invocations[label] = {
            "memory_config_path": config_path,
            "memory_config_sha256": config_sha256,
            "load_memory_dir": load_memory_dir,
            "memory_artifact_sha256": (
                memory_artifact["sha256"] if memory_artifact is not None else None
            ),
        }
    return {**left_source, "invocations": invocations}


def compare(
    left_directory: Path,
    right_directory: Path,
    *,
    left_label: str,
    right_label: str,
    samples: int = 10_000,
    registration: Path | None = None,
) -> dict[str, Any]:
    if not left_label.strip() or not right_label.strip() or left_label == right_label:
        raise ValueError("comparison labels must be distinct and non-empty")
    if samples < 1:
        raise ValueError("bootstrap samples must be positive")
    left = _load_run(left_directory)
    right = _load_run(right_directory)

    left_settings = {field: left["args"].get(field) for field in _READER_FIELDS}
    right_settings = {field: right["args"].get(field) for field in _READER_FIELDS}
    if left_settings != right_settings:
        raise ValueError("reader settings differ between runs")

    left_ids = [row["question_id"] for row in left["records"]]
    if set(left_ids) != set(right["by_id"]):
        raise ValueError("question identity sets differ between runs")
    for question_id in left_ids:
        left_row = left["by_id"][question_id]
        right_row = right["by_id"][question_id]
        if any(left_row.get(field) != right_row.get(field) for field in _QUESTION_FIELDS):
            raise ValueError(f"question inputs differ between runs: {question_id}")

    input_hashes: dict[str, str] = {}
    for field in _INPUT_FIELDS:
        left_path = Path(str(left["args"].get(field) or ""))
        right_path = Path(str(right["args"].get(field) or ""))
        if not left_path.is_file() or not right_path.is_file():
            raise ValueError(f"run input is unavailable for hashing: {field}")
        left_hash = _digest(left_path)
        if left_hash != _digest(right_path):
            raise ValueError(f"run input differs: {field}")
        input_hashes[field] = left_hash

    categories = sorted({str(row["category"]) for row in left["records"]})
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "longmemeval-v2-paired-comparison",
        "labels": {"left": left_label, "right": right_label},
        "reader_settings": left_settings,
        "input_sha256": input_hashes,
        "bootstrap": {"samples": samples, "seed": 42, "unit": "question"},
        "overall": _outcomes(
            left_ids, left["by_id"], right["by_id"], samples=samples
        ),
        "categories": {
            category: _outcomes(
                [
                    question_id
                    for question_id in left_ids
                    if left["by_id"][question_id]["category"] == category
                ],
                left["by_id"],
                right["by_id"],
                samples=samples,
            )
            for category in categories
        },
        "efficiency": {
            "left": _efficiency(left),
            "right": _efficiency(right),
        },
        "artifacts": {
            "left": left["artifacts"],
            "right": right["artifacts"],
        },
        "limitations": [
            "Question bootstrap intervals condition on this selected cohort; shared haystacks can make questions dependent.",
            "Results compare the recorded reader and execution settings only and do not establish universal system leadership.",
        ],
    }
    if registration is not None:
        if not registration.is_file():
            raise ValueError("registration file does not exist")
        registered = _load_json(registration)
        registration_schema = registered.get("schema_version", 1)
        if type(registration_schema) is not int or registration_schema not in (1, 2):
            raise ValueError("unsupported registration schema version")
        selection = registered.get("selection")
        reader = registered.get("reader")
        if not isinstance(selection, dict) or not isinstance(reader, dict):
            raise ValueError("registration is missing selection or reader settings")
        ordered_hash = hashlib.sha256("\n".join(left_ids).encode()).hexdigest()
        counts_by_type: dict[str, int] = {}
        for row in left["records"]:
            question_type = str(row["question_type"])
            counts_by_type[question_type] = counts_by_type.get(question_type, 0) + 1
        if (selection.get("question_count") != len(left_ids)
                or selection.get("ordered_question_ids_sha256") != ordered_hash
                or selection.get("counts_by_type") != counts_by_type
                or selection.get("questions_file_sha256") != input_hashes["questions_path"]
                or selection.get("haystack_file_sha256") != input_hashes["haystack_path"]):
            raise ValueError("run cohort does not match registration")
        registered_reader = {
            "model": reader.get("model"),
            "reasoning_effort": reader.get("reasoning_effort"),
            "reader_enable_thinking": reader.get("reader_enable_thinking"),
            "temperature": reader.get("temperature"),
            "top_p": reader.get("top_p"),
            "top_k": reader.get("top_k"),
            "max_completion_tokens": reader.get("max_completion_tokens"),
            "reader_max_concurrent_requests": reader.get("max_concurrent_requests"),
        }
        if registered_reader != {
            field: left_settings[field] for field in registered_reader
        }:
            raise ValueError("run reader settings do not match registration")
        registration_sha256 = _digest(registration)
        report["system_binding"] = _validate_registered_systems(
            left, right, registered
        )
        if registration_schema == 2:
            report["execution_source"] = _validate_registered_execution(
                left,
                right,
                registered,
                registration_sha256,
            )
        report["registration_schema_version"] = registration_schema
        report["registration_sha256"] = registration_sha256
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left_directory", type=Path)
    parser.add_argument("right_directory", type=Path)
    parser.add_argument("--left-label", required=True)
    parser.add_argument("--right-label", required=True)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(
        args.left_directory,
        args.right_directory,
        left_label=args.left_label,
        right_label=args.right_label,
        samples=args.bootstrap_samples,
        registration=args.registration,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

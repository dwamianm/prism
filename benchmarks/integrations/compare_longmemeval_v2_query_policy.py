"""Verify and compare a registered LongMemEval-V2 retrieval-query policy trial."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.integrations import compare_longmemeval_v2 as paired
from benchmarks.integrations import compare_longmemeval_v2_curve as curve
from benchmarks.integrations import install_longmemeval_v2 as installer
from benchmarks.integrations import run_longmemeval_v2 as launcher


REGISTRATION_KIND = "longmemeval-v2-query-policy-registration"
RESULT_KIND = "longmemeval-v2-query-policy-comparison"
_POLICIES = ("verbatim", "question_stem_v1")
_SYSTEM_FIELDS = curve._SYSTEM_FIELDS | {"retrieval_query_policy"}


def _protocol_specification() -> dict[str, Any]:
    return {
        "analysis": "complete_registered_arms_with_question_paired_statistics",
        "claim_boundary": (
            "Development query-policy trial on a previously scored cohort; it can "
            "guide retrieval-query handling but is not a new holdout or competitor "
            "result."
        ),
        "configuration_rule": (
            "Arms differ only in the registered retrieval_query_policy; source pack, "
            "token budget, context format, image policy, reader, and question order "
            "match."
        ),
        "emit_source_text": False,
        "stopping_rule": (
            "Run every registered question in both arms; exact checkpoints may resume "
            "infrastructure failures without replacing completed generations."
        ),
    }


def _expected_source_files() -> dict[str, str]:
    return {
        "adapter_sha256": paired._digest(Path(installer._ADAPTER_SOURCE).resolve()),
        "comparator_sha256": paired._digest(Path(paired.__file__).resolve()),
        "compact_config_sha256": paired._digest(
            Path(installer._COMPACT_CONFIG_SOURCE).resolve()
        ),
        "config_sha256": paired._digest(Path(installer._CONFIG_SOURCE).resolve()),
        "installer_sha256": paired._digest(Path(installer.__file__).resolve()),
        "launcher_sha256": paired._digest(Path(launcher.__file__).resolve()),
    }


def _validate_execution_source(
    runs: dict[str, dict[str, Any]],
    registration: dict[str, Any],
    registration_sha256: str,
) -> dict[str, Any]:
    source = registration.get("source")
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "dataset_revision",
            "files",
            "prme_revision",
            "upstream_revision",
        }
        or source.get("dataset_revision") != curve.DATASET_REVISION
        or source.get("upstream_revision") != installer.UPSTREAM_REVISION
        or not curve._git_revision(source.get("prme_revision"))
        or source.get("files") != _expected_source_files()
    ):
        raise ValueError("registration source files do not match this comparator")

    observed: dict[str, Any] | None = None
    for label, run in runs.items():
        manifest = run.get("execution")
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 3
            or manifest.get("kind") != "longmemeval-v2-execution"
            or manifest.get("registration_sha256") != registration_sha256
        ):
            raise ValueError(f"arm {label} has no matching execution manifest")
        execution_source = manifest.get("source")
        if not isinstance(execution_source, dict):
            raise ValueError(f"arm {label} has no execution source")
        if observed is None:
            observed = execution_source
        elif execution_source != observed:
            raise ValueError("query-policy arms do not share one execution source")
    assert observed is not None
    if (
        observed.get("prme_revision") != source.get("prme_revision")
        or observed.get("upstream_revision") != source.get("upstream_revision")
        or observed.get("prme_worktree_changes") != []
        or observed.get("upstream_worktree_changes")
        not in (
            [],
            [
                "evaluation/memory_configs/prme.json",
                "evaluation/memory_configs/prme_compact.json",
                "memory_modules/__init__.py",
                "memory_modules/prme.py",
            ],
        )
    ):
        raise ValueError("query-policy execution source does not match registration")
    for field in curve._EXECUTION_DIGEST_FIELDS:
        if not curve._hex_digest(observed.get(field)):
            raise ValueError(f"execution source has an invalid {field}")
    files = source["files"]
    if (
        observed["launcher_sha256"] != files["launcher_sha256"]
        or observed["installer_sha256"] != files["installer_sha256"]
        or observed["adapter_source_sha256"] != files["adapter_sha256"]
        or observed["config_source_sha256"] != files["config_sha256"]
        or observed["compact_config_source_sha256"]
        != files["compact_config_sha256"]
        or observed["adapter_source_sha256"]
        != observed["adapter_installed_sha256"]
        or observed["config_source_sha256"]
        != observed["config_installed_sha256"]
        or observed["compact_config_source_sha256"]
        != observed["compact_config_installed_sha256"]
    ):
        raise ValueError("installed query-policy source differs from registration")
    return observed


def _validate_system(
    label: str,
    run: dict[str, Any],
    system: dict[str, Any],
) -> dict[str, Any]:
    if set(system) != _SYSTEM_FIELDS:
        raise ValueError(f"registered system fields are invalid for {label}")
    if label not in _POLICIES or system["retrieval_query_policy"] != label:
        raise ValueError(f"registered retrieval-query policy is invalid for {label}")

    base_system = {
        field: value
        for field, value in system.items()
        if field != "retrieval_query_policy"
    }
    binding = curve._validate_system(label, run, base_system)
    binding.pop("non_budget_policy_sha256")
    config = paired._load_json(Path(system["memory_config"]))
    params = config.get("memory_params")
    if (
        not isinstance(params, dict)
        or params.get("retrieval_query_policy", "verbatim") != label
    ):
        raise ValueError(f"saved retrieval-query policy does not match system {label}")
    normalized = dict(params)
    normalized["retrieval_query_policy"] = "<registered-query-policy>"
    return {
        **binding,
        "retrieval_query_policy": label,
        "non_query_policy_sha256": hashlib.sha256(
            json.dumps(
                normalized,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest(),
    }


def _load_query_metadata(
    run: dict[str, Any],
    *,
    policy: str,
) -> dict[str, bool]:
    path = run["directory"] / "prompt_rows.jsonl"
    observed: dict[str, bool] = {}
    for row in paired._iter_jsonl(path):
        question_id = row.get("question_id")
        question_text = row.get("question_text")
        metadata = row.get("memory_post_query_metadata")
        if (
            not isinstance(question_id, str)
            or not isinstance(question_text, str)
            or not isinstance(metadata, dict)
            or metadata.get("retrieval_query_policy") != policy
        ):
            raise ValueError(f"prompt row query policy is invalid for {policy}")
        original = hashlib.sha256(question_text.encode()).hexdigest()
        retrieval = metadata.get("retrieval_query_sha256")
        if (
            metadata.get("original_query_sha256") != original
            or not curve._hex_digest(retrieval)
            or question_id in observed
        ):
            raise ValueError(f"prompt row query identity is invalid for {policy}")
        if policy == "verbatim" and retrieval != original:
            raise ValueError("verbatim arm changed a retrieval query")
        observed[question_id] = retrieval != original
    expected = {row["question_id"] for row in run["records"]}
    if set(observed) != expected:
        raise ValueError(f"prompt query metadata is incomplete for {policy}")
    return observed


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


def compare_query_policies(
    directories: dict[str, Path],
    *,
    registration_path: Path,
    samples: int = 10_000,
) -> dict[str, Any]:
    """Verify the registered policies and return aggregate paired evidence."""
    if samples < 1:
        raise ValueError("bootstrap samples must be positive")
    if set(directories) != set(_POLICIES):
        raise ValueError("query-policy comparison requires verbatim and question_stem_v1")
    registration = paired._load_json(registration_path)
    if (
        registration.get("schema_version") != 2
        or registration.get("kind") != REGISTRATION_KIND
        or registration.get("protocol") != _protocol_specification()
    ):
        raise ValueError("query-policy registration identity is invalid")
    systems = registration.get("systems")
    if not isinstance(systems, dict) or set(systems) != set(_POLICIES):
        raise ValueError("run labels do not match registered query-policy systems")

    runs, question_ids, input_hashes = curve._matched_runs(directories)
    reference = runs["verbatim"]
    curve._validate_selection_and_reader(
        registration, reference, question_ids, input_hashes
    )
    registration_sha256 = paired._digest(registration_path)
    execution_source = _validate_execution_source(
        runs, registration, registration_sha256
    )
    reader_runtime = curve._validate_reader_runtime(runs, registration)
    bindings = {
        label: _validate_system(label, runs[label], systems[label])
        for label in _POLICIES
    }
    for field in (
        "budget",
        "context_format",
        "memory_payload_artifact_sha256",
        "non_query_policy_sha256",
    ):
        if len({binding[field] for binding in bindings.values()}) != 1:
            raise ValueError(f"query-policy arms differ outside query policy: {field}")

    query_metadata = {
        label: _load_query_metadata(runs[label], policy=label)
        for label in _POLICIES
    }
    changed_ids = [
        question_id
        for question_id in question_ids
        if query_metadata["question_stem_v1"][question_id]
    ]
    unchanged_ids = [
        question_id
        for question_id in question_ids
        if not query_metadata["question_stem_v1"][question_id]
    ]
    mc_ids = [
        question_id
        for question_id in question_ids
        if str(reference["by_id"][question_id]["eval_function"]).startswith(
            "mc_choice_"
        )
    ]
    left = runs["question_stem_v1"]["by_id"]
    right = runs["verbatim"]["by_id"]
    categories = sorted({str(row["category"]) for row in reference["records"]})

    return {
        "schema_version": 1,
        "kind": RESULT_KIND,
        "registration_sha256": registration_sha256,
        "labels": {"left": "question_stem_v1", "right": "verbatim"},
        "input_sha256": input_hashes,
        "reader_settings": {
            field: reference["args"].get(field) for field in paired._READER_FIELDS
        },
        "reader_runtime": reader_runtime,
        "bootstrap": {"samples": samples, "seed": 42, "unit": "question"},
        "bindings": bindings,
        "arms": {label: _arm_summary(runs[label]) for label in _POLICIES},
        "overall": paired._outcomes(
            question_ids, left, right, samples=samples
        ),
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
        "subsets": {
            "retrieval_query_changed": paired._outcomes(
                changed_ids, left, right, samples=samples
            ),
            "retrieval_query_unchanged": paired._outcomes(
                unchanged_ids, left, right, samples=samples
            ),
            "multiple_choice": paired._outcomes(
                mc_ids, left, right, samples=samples
            ),
        },
        "artifacts": {label: runs[label]["artifacts"] for label in _POLICIES},
        "execution_source": execution_source,
        "limitations": [
            "This is a development query-policy trial on a previously scored cohort, not a new holdout.",
            "Question bootstrap intervals condition on the selected cohort; shared haystacks can make questions dependent.",
            "Unchanged-query outcomes expose reader run-to-run variation and are not effects of the query policy.",
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
    result = compare_query_policies(
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

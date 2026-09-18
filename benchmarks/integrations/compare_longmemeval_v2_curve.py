"""Verify and compare a registered LongMemEval-V2 context-budget curve."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.integrations import compare_longmemeval_v2 as paired
from benchmarks.integrations import install_longmemeval_v2 as installer
from benchmarks.integrations import run_longmemeval_v2 as launcher


REGISTRATION_KIND = "longmemeval-v2-budget-curve-registration"
RESULT_KIND = "longmemeval-v2-budget-curve-comparison"
DATASET_REVISION = "f152293e235517d504809563c833d7190b8c713b"
_SYSTEM_FIELDS = {
    "context_format",
    "internal_context_budget_cl100k_tokens",
    "max_source_screenshots",
    "memory_artifact_bytes",
    "memory_artifact_file_count",
    "memory_artifact_sha256",
    "memory_config",
    "memory_config_sha256",
    "memory_payload_artifact_bytes",
    "memory_payload_artifact_file_count",
    "memory_payload_artifact_sha256",
    "pack_manifest_sha256",
    "saved_memory_config_sha256",
    "upstream_context_budget_tokens",
}
_EXECUTION_DIGEST_FIELDS = (
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


def _hex_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _git_revision(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _expected_source_files() -> dict[str, str]:
    return {
        "adapter_sha256": paired._digest(Path(installer._ADAPTER_SOURCE).resolve()),
        "comparator_sha256": paired._digest(Path(__file__).resolve()),
        "compact_config_sha256": paired._digest(
            Path(installer._COMPACT_CONFIG_SOURCE).resolve()
        ),
        "config_sha256": paired._digest(Path(installer._CONFIG_SOURCE).resolve()),
        "installer_sha256": paired._digest(Path(installer.__file__).resolve()),
        "launcher_sha256": paired._digest(Path(launcher.__file__).resolve()),
    }


def _protocol_specification() -> dict[str, Any]:
    return {
        "analysis": "complete_registered_arms_with_question_paired_statistics",
        "claim_boundary": (
            "Development budget curve on a previously scored cohort; it can guide "
            "context efficiency work but is not a new holdout or competitor result."
        ),
        "configuration_rule": (
            "Arms differ only in the registered internal token budget; source pack, "
            "context format, result limit, image policy, reader, and question order match."
        ),
        "emit_source_text": False,
        "stopping_rule": (
            "Run every registered question in every arm; exact checkpoints may resume "
            "infrastructure failures without replacing completed generations."
        ),
    }


def _holdout_protocol_specification() -> dict[str, Any]:
    """Return the fixed protocol for a fresh cross-domain confirmation."""
    return {
        "analysis": "complete_registered_arms_with_question_paired_statistics",
        "claim_boundary": (
            "Confirmation budget curve on an unscored domain cohort; it can test "
            "cross-domain generalization and context-efficiency policy but is not "
            "a competitor result."
        ),
        "configuration_rule": (
            "Arms differ only in the registered internal token budget; source pack, "
            "context format, result limit, image policy, reader, and question order match."
        ),
        "emit_source_text": False,
        "stopping_rule": (
            "Run every registered question in every arm; exact checkpoints may resume "
            "infrastructure failures without replacing completed generations."
        ),
    }


def _protocol_limitations(protocol: dict[str, Any]) -> list[str]:
    if protocol == _holdout_protocol_specification():
        return [
            "This is a registered cross-domain confirmation cohort, not a competitor comparison.",
            "Question bootstrap intervals condition on the selected cohort; shared haystacks can make questions dependent.",
            "The curve isolates registered context budgets under one reader and source artifact family; it does not establish universal system leadership.",
        ]
    return [
        "This is a development budget curve on a previously scored cohort, not a new holdout.",
        "Question bootstrap intervals condition on the selected cohort; shared haystacks can make questions dependent.",
        "The curve isolates registered context budgets under one reader and source artifact family; it does not establish universal system leadership.",
    ]


def _matched_runs(
    directories: dict[str, Path],
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, str]]:
    if len(directories) < 2:
        raise ValueError("budget curve requires at least two arms")
    if any(not label.strip() for label in directories):
        raise ValueError("budget curve labels must be non-empty")
    runs = {label: paired._load_run(path) for label, path in directories.items()}
    first_label = next(iter(runs))
    first = runs[first_label]
    settings = {field: first["args"].get(field) for field in paired._READER_FIELDS}
    question_ids = [row["question_id"] for row in first["records"]]
    for label, run in runs.items():
        observed_settings = {
            field: run["args"].get(field) for field in paired._READER_FIELDS
        }
        if observed_settings != settings:
            raise ValueError(f"reader settings differ in arm {label}")
        if [row["question_id"] for row in run["records"]] != question_ids:
            raise ValueError(f"question order differs in arm {label}")
        for question_id in question_ids:
            if any(
                first["by_id"][question_id].get(field)
                != run["by_id"][question_id].get(field)
                for field in paired._QUESTION_FIELDS
            ):
                raise ValueError(
                    f"question inputs differ in arm {label}: {question_id}"
                )

    input_hashes: dict[str, str] = {}
    for field in paired._INPUT_FIELDS:
        expected: str | None = None
        for label, run in runs.items():
            path = Path(str(run["args"].get(field) or ""))
            if not path.is_file():
                raise ValueError(f"run input is unavailable for {label}: {field}")
            observed = paired._digest(path)
            if expected is None:
                expected = observed
            elif observed != expected:
                raise ValueError(f"run input differs in arm {label}: {field}")
        assert expected is not None
        input_hashes[field] = expected
    return runs, question_ids, input_hashes


def _validate_selection_and_reader(
    registration: dict[str, Any],
    reference: dict[str, Any],
    question_ids: list[str],
    input_hashes: dict[str, str],
) -> None:
    selection = registration.get("selection")
    reader = registration.get("reader")
    if not isinstance(selection, dict) or not isinstance(reader, dict):
        raise ValueError("registration is missing selection or reader settings")
    counts_by_type: dict[str, int] = {}
    for row in reference["records"]:
        value = str(row["question_type"])
        counts_by_type[value] = counts_by_type.get(value, 0) + 1
    if (
        selection.get("question_count") != len(question_ids)
        or selection.get("ordered_question_ids_sha256")
        != hashlib.sha256("\n".join(question_ids).encode()).hexdigest()
        or selection.get("counts_by_type") != counts_by_type
        or selection.get("questions_file_sha256") != input_hashes["questions_path"]
        or selection.get("haystack_file_sha256") != input_hashes["haystack_path"]
        or selection.get("trajectories_file_sha256")
        != input_hashes["trajectories_path"]
    ):
        raise ValueError("budget-curve cohort does not match registration")
    observed = reference["args"]
    registered_reader = {
        "model": reader.get("model"),
        "base_url": reader.get("base_url"),
        "reasoning_effort": reader.get("reasoning_effort"),
        "reader_enable_thinking": reader.get("reader_enable_thinking"),
        "temperature": reader.get("temperature"),
        "top_p": reader.get("top_p"),
        "top_k": reader.get("top_k"),
        "max_completion_tokens": reader.get("max_completion_tokens"),
        "reader_max_concurrent_requests": reader.get("max_concurrent_requests"),
        "prompt_build_max_workers": reader.get("prompt_build_max_workers"),
    }
    if registered_reader != {field: observed.get(field) for field in registered_reader}:
        raise ValueError("budget-curve reader settings do not match registration")


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
        or source.get("dataset_revision") != DATASET_REVISION
        or source.get("upstream_revision") != installer.UPSTREAM_REVISION
        or not _git_revision(source.get("prme_revision"))
        or source.get("files") != _expected_source_files()
    ):
        raise ValueError("registration source files do not match this comparator")
    expected_source: dict[str, Any] | None = None
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
        if expected_source is None:
            expected_source = execution_source
        elif execution_source != expected_source:
            raise ValueError("budget-curve arms do not share one execution source")
    assert expected_source is not None
    if (
        expected_source.get("prme_revision") != source.get("prme_revision")
        or expected_source.get("upstream_revision") != source.get("upstream_revision")
        or expected_source.get("prme_worktree_changes") != []
        or expected_source.get("upstream_worktree_changes")
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
        raise ValueError("budget-curve execution source does not match registration")
    for field in _EXECUTION_DIGEST_FIELDS:
        if not _hex_digest(expected_source.get(field)):
            raise ValueError(f"execution source has an invalid {field}")
    if (
        expected_source["launcher_sha256"] != source["files"]["launcher_sha256"]
        or expected_source["installer_sha256"] != source["files"]["installer_sha256"]
        or expected_source["adapter_source_sha256"] != source["files"]["adapter_sha256"]
        or expected_source["config_source_sha256"] != source["files"]["config_sha256"]
        or expected_source["compact_config_source_sha256"]
        != source["files"]["compact_config_sha256"]
        or expected_source["adapter_source_sha256"]
        != expected_source["adapter_installed_sha256"]
        or expected_source["config_source_sha256"]
        != expected_source["config_installed_sha256"]
        or expected_source["compact_config_source_sha256"]
        != expected_source["compact_config_installed_sha256"]
    ):
        raise ValueError("installed budget-curve source differs from registration")
    return expected_source


def _validate_reader_runtime(
    runs: dict[str, dict[str, Any]],
    registration: dict[str, Any],
) -> dict[str, Any]:
    reader = registration.get("reader")
    registered = reader.get("runtime_identity") if isinstance(reader, dict) else None
    if not isinstance(registered, dict):
        raise ValueError("registration is missing reader runtime identity")
    observed: dict[str, Any] | None = None
    for label, run in runs.items():
        manifest = run.get("execution")
        identity = manifest.get("reader_runtime") if isinstance(manifest, dict) else None
        if not isinstance(identity, dict):
            raise ValueError(f"arm {label} has no reader runtime identity")
        if observed is None:
            observed = identity
        elif identity != observed:
            raise ValueError("budget-curve arms do not share one reader runtime")
    if observed != registered:
        raise ValueError("budget-curve reader runtime does not match registration")
    return registered


def _validate_system(
    label: str,
    run: dict[str, Any],
    system: dict[str, Any],
) -> dict[str, Any]:
    if set(system) != _SYSTEM_FIELDS:
        raise ValueError(f"registered system fields are invalid for {label}")
    budget = system["internal_context_budget_cl100k_tokens"]
    upstream_budget = system["upstream_context_budget_tokens"]
    if (
        isinstance(budget, bool)
        or not isinstance(budget, int)
        or budget < 1
        or isinstance(upstream_budget, bool)
        or not isinstance(upstream_budget, int)
        or upstream_budget < budget
        or system["context_format"] not in {"auditable", "compact"}
        or isinstance(system["max_source_screenshots"], bool)
        or not isinstance(system["max_source_screenshots"], int)
        or system["max_source_screenshots"] < 0
    ):
        raise ValueError(f"registered system policy is invalid for {label}")
    for field in (
        "memory_artifact_sha256",
        "memory_config_sha256",
        "memory_payload_artifact_sha256",
        "pack_manifest_sha256",
        "saved_memory_config_sha256",
    ):
        if not _hex_digest(system[field]):
            raise ValueError(f"registered {field} is invalid for {label}")
    if system["memory_config_sha256"] != system["saved_memory_config_sha256"]:
        raise ValueError(f"selected and saved configuration differ for {label}")

    args = run["args"]
    if (
        args.get("memory_config_path") != system["memory_config"]
        or args.get("memory_context_max_tokens") != upstream_budget
        or args.get("save_memory") is not False
        or args.get("skip_evaluation") is not False
    ):
        raise ValueError(f"run invocation does not match registered system {label}")
    load_value = args.get("load_memory_dir")
    if not isinstance(load_value, str) or not Path(load_value).is_absolute():
        raise ValueError(f"registered system {label} has no absolute saved-memory path")
    load_root = Path(load_value)
    selected_config = Path(system["memory_config"])
    saved_config = load_root / "memory_config.json"
    pack_manifest = load_root / "prme_pack" / "longmemeval_v2_manifest.json"
    if (
        not selected_config.is_file()
        or not saved_config.is_file()
        or not pack_manifest.is_file()
    ):
        raise ValueError(f"registered system files are unavailable for {label}")
    if (
        paired._digest(selected_config) != system["memory_config_sha256"]
        or paired._digest(saved_config) != system["saved_memory_config_sha256"]
        or paired._digest(pack_manifest) != system["pack_manifest_sha256"]
    ):
        raise ValueError(f"registered system files changed for {label}")
    config = paired._load_json(saved_config)
    params = config.get("memory_params")
    if (
        config.get("memory_type") != "prme"
        or not isinstance(params, dict)
        or params.get("token_budget") != budget
        or params.get("context_format", "auditable") != system["context_format"]
        or params.get("image_limit") != system["max_source_screenshots"]
    ):
        raise ValueError(f"saved memory policy does not match system {label}")

    manifest = run["execution"]
    invocation = manifest.get("invocation")
    if not isinstance(invocation, dict):
        raise ValueError(f"execution invocation is missing for {label}")
    artifact = invocation.get("memory_artifact")
    payload_artifact = invocation.get("memory_payload_artifact")
    if (
        invocation.get("memory_config_path") != system["memory_config"]
        or invocation.get("memory_config_sha256") != system["memory_config_sha256"]
        or invocation.get("load_memory_dir") != load_value
        or not isinstance(artifact, dict)
        or artifact.get("sha256") != system["memory_artifact_sha256"]
        or artifact.get("file_count") != system["memory_artifact_file_count"]
        or artifact.get("bytes") != system["memory_artifact_bytes"]
        or not isinstance(payload_artifact, dict)
        or payload_artifact.get("sha256") != system["memory_payload_artifact_sha256"]
        or payload_artifact.get("file_count")
        != system["memory_payload_artifact_file_count"]
        or payload_artifact.get("bytes") != system["memory_payload_artifact_bytes"]
    ):
        raise ValueError(f"execution artifact does not match system {label}")
    normalized_policy = dict(params)
    normalized_policy["token_budget"] = "<registered-arm-budget>"
    return {
        "budget": budget,
        "context_format": system["context_format"],
        "memory_artifact_sha256": system["memory_artifact_sha256"],
        "memory_config_sha256": system["memory_config_sha256"],
        "memory_payload_artifact_sha256": system["memory_payload_artifact_sha256"],
        "non_budget_policy_sha256": hashlib.sha256(
            json.dumps(
                normalized_policy,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest(),
    }


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


def compare_curve(
    directories: dict[str, Path],
    *,
    registration_path: Path,
    samples: int = 10_000,
) -> dict[str, Any]:
    """Verify complete registered arms and return an aggregate-only curve."""
    if samples < 1:
        raise ValueError("bootstrap samples must be positive")
    registration = paired._load_json(registration_path)
    protocol = registration.get("protocol")
    if (
        registration.get("schema_version") != 2
        or registration.get("kind") != REGISTRATION_KIND
        or protocol
        not in (_protocol_specification(), _holdout_protocol_specification())
    ):
        raise ValueError("budget-curve registration identity is invalid")
    assert isinstance(protocol, dict)
    systems = registration.get("systems")
    if not isinstance(systems, dict) or set(systems) != set(directories):
        raise ValueError("run labels do not match registered budget-curve systems")

    runs, question_ids, input_hashes = _matched_runs(directories)
    reference = runs[next(iter(runs))]
    _validate_selection_and_reader(registration, reference, question_ids, input_hashes)
    registration_sha256 = paired._digest(registration_path)
    execution_source = _validate_execution_source(
        runs, registration, registration_sha256
    )
    reader_runtime = _validate_reader_runtime(runs, registration)
    bindings = {
        label: _validate_system(label, runs[label], system)
        for label, system in systems.items()
    }
    budgets = [binding["budget"] for binding in bindings.values()]
    if len(budgets) != len(set(budgets)):
        raise ValueError("registered budget-curve systems have duplicate budgets")
    for field in (
        "context_format",
        "memory_payload_artifact_sha256",
        "non_budget_policy_sha256",
    ):
        if len({binding[field] for binding in bindings.values()}) != 1:
            raise ValueError(f"budget-curve arms differ outside token budget: {field}")
    ordered_labels = sorted(bindings, key=lambda label: bindings[label]["budget"])

    categories = sorted({str(row["category"]) for row in reference["records"]})
    pairwise: dict[str, Any] = {}
    for right_index, right_label in enumerate(ordered_labels):
        for left_label in ordered_labels[right_index + 1 :]:
            name = f"{left_label}_minus_{right_label}"
            pairwise[name] = {
                "labels": {"left": left_label, "right": right_label},
                "overall": paired._outcomes(
                    question_ids,
                    runs[left_label]["by_id"],
                    runs[right_label]["by_id"],
                    samples=samples,
                ),
                "categories": {
                    category: paired._outcomes(
                        [
                            question_id
                            for question_id in question_ids
                            if reference["by_id"][question_id]["category"] == category
                        ],
                        runs[left_label]["by_id"],
                        runs[right_label]["by_id"],
                        samples=samples,
                    )
                    for category in categories
                },
            }

    return {
        "schema_version": 1,
        "kind": RESULT_KIND,
        "registration_sha256": registration_sha256,
        "ordered_labels": ordered_labels,
        "input_sha256": input_hashes,
        "reader_settings": {
            field: reference["args"].get(field) for field in paired._READER_FIELDS
        },
        "reader_runtime": reader_runtime,
        "bootstrap": {"samples": samples, "seed": 42, "unit": "question"},
        "bindings": bindings,
        "arms": {label: _arm_summary(runs[label]) for label in ordered_labels},
        "pairwise": pairwise,
        "artifacts": {label: runs[label]["artifacts"] for label in ordered_labels},
        "execution_source": execution_source,
        "limitations": _protocol_limitations(protocol),
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
    result = compare_curve(
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

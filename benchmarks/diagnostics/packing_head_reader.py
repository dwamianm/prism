"""Frozen one-head packing answer trial using the existing neutral reader/judge.

Runs two readers, then a separately calibrated judge, then verifies every raw
response before scoring. Fresh controls, complete coverage, no outcome retries.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys

from benchmarks.diagnostics import public_context_reader as reader
from benchmarks.diagnostics import public_context_judge as judge
from benchmarks.diagnostics import reader_judge
from benchmarks.diagnostics.compare_public_captures import groups_for
from benchmarks.diagnostics.hindsight_capture import digest, write
from benchmarks.diagnostics.public_context_finish import run_stage
from benchmarks.diagnostics.public_context_judging import (
    prepare_judgments,
    verify_predictions,
)
from benchmarks.diagnostics.public_context_scores import summarize

MODELS = ("qwen3.5:4b", "gemma4:26b")
ARMS = {"memory_a": "density", "memory_b": "head1", "empty": "no_memory"}
COMPARISONS = {
    "head1_vs_density": ("memory_a", "memory_b"),
    "head1_vs_empty": ("empty", "memory_b"),
    "density_vs_empty": ("empty", "memory_a"),
}


def code_identity():
    names = (
        "packing_head_reader",
        "public_context_reader",
        "packing_reader",
        "public_context_judge",
        "reader_judge",
        "public_context_judging",
        "public_context_finish",
        "public_context_scores",
        "compare_public_captures",
        "hindsight_capture",
    )
    return {
        name: digest(
            Path(
                importlib.import_module("benchmarks.diagnostics." + name).__file__
            ).read_bytes()
        )
        for name in names
    }


def prepare(
    inputs_path, capture_path, completion_path, verification_path, capture_plan_path
):
    """Extract only neutral questions and exact, completely verified 4K contexts."""
    capture_raw = capture_path.read_bytes()
    capture = json.loads(capture_raw)
    completion = json.loads(completion_path.read_bytes())
    verification = json.loads(verification_path.read_bytes())
    plan = json.loads(capture_plan_path.read_bytes())
    cases = json.loads(inputs_path.read_bytes())["cases"]
    expected = [c["case_id"] for c in cases]
    if (
        not capture["complete"]
        or completion["native_exit_code"] != 0
        or completion["output_sha256"] != digest(capture_raw)
        or verification["output_sha256"] != digest(capture_raw)
        or not verification["complete"]
        or verification["contexts_verified"] != len(cases) * 6
        or capture["controls_reproduced"] != len(cases) * 3
        or capture["plan_sha256"] != digest(capture_plan_path.read_bytes())
        or plan["identity"]["files"]["inputs"] != digest(inputs_path.read_bytes())
        or expected != [r["case_id"] for r in capture["details"]]
        or len(set(expected)) != len(expected)
    ):
        raise ValueError("Complete native-exited verified capture required")
    rows = []
    for case, saved in zip(cases, capture["details"], strict=True):
        contexts = {
            arm: {
                key: saved["arms"][policy + ":4096"][key]
                for key in ("context", "tokens", "sha256")
            }
            for arm, policy in ARMS.items()
            if arm != "empty"
        }
        contexts["empty"] = {"context": "", "tokens": 0, "sha256": digest(b"")}
        rows.append(
            {
                **{key: case[key] for key in ("case_id", "question", "question_date")},
                "contexts": contexts,
            }
        )
    prepared = {
        "kind": "matched-public-context-reader-inputs",
        "schema_version": 1,
        "budget": 4096,
        "inputs_sha256": digest(inputs_path.read_bytes()),
        "analysis_sha256": digest(capture_raw),
        "analysis_completion_sha256": digest(completion_path.read_bytes()),
        "rows": rows,
    }
    reader.validate_prepared(prepared)
    return prepared


def source_paths(root):
    evidence = root / "benchmarks/results/research/2026-09-12"
    data = root / "data/benchmarks"
    return dict(
        inputs_path=data / "hindsight-prme-dev-normalized-inputs.json",
        capture_path=data / "packing-head1-dev-fc8e1f3.json",
        completion_path=evidence / "packing-head1-dev-results.json",
        verification_path=evidence / "packing-head1-dev-verification.json",
        capture_plan_path=evidence / "packing-head1-dev-plan.json",
    )


def register(root, directory, registration_path, base_url):
    if directory.exists() or registration_path.exists():
        raise ValueError("Fresh registration and study directory required")
    sources = source_paths(root)
    prepared = prepare(**sources)
    if len(prepared["rows"]) != 119:
        raise ValueError("The complete fixed development cohort is required")
    evidence = root / "benchmarks/results/research/2026-09-12"
    refs = root / "data/benchmarks/hindsight-prme-dev-references.json"
    controls = root / "benchmarks/fixtures/reader_judge_controls.json"
    calibration = root / "data/benchmarks/public-context-judge-calibration-f511aaa.json"
    prior = json.loads(
        (evidence / "hindsight-prme-reader-registration.json").read_bytes()
    )
    declared_judge = prior["judge_declaration"]
    if (
        reader_judge.declaration(
            declared_judge["model"], base_url, declared_judge["controls_sha256"]
        )
        != declared_judge
    ):
        raise ValueError("Previously calibrated judge identity changed")
    reader_judge.validate_calibration(
        json.loads(controls.read_bytes()),
        json.loads(calibration.read_bytes()),
        declared_judge,
    )
    declared_readers = {model: reader.declaration(model, base_url) for model in MODELS}
    if any(
        d["model_digest"] == declared_judge["model_digest"]
        for d in declared_readers.values()
    ):
        raise ValueError("Reader and judge must be separate models")
    directory.mkdir(parents=True)
    prepared_path = directory / "prepared.json"
    write(prepared_path, prepared)
    registration = {
        "kind": "one-head-packing-reader-development",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "arm_mapping": ARMS,
        "comparisons": COMPARISONS,
        "model_order": MODELS,
        "reader_declarations": declared_readers,
        "judge_declaration": declared_judge,
        "expected_cases": 119,
        "budget": 4096,
        "logical_predictions_per_reader": 357,
        "neutral_inputs_sha256": digest(sources["inputs_path"].read_bytes()),
        "references_sha256": digest(refs.read_bytes()),
        "prepared_sha256": digest(prepared_path.read_bytes()),
        "controls_file_sha256": digest(controls.read_bytes()),
        "calibration_sha256": digest(calibration.read_bytes()),
        "code_identity": code_identity(),
        "sources": {name: digest(path.read_bytes()) for name, path in sources.items()},
        "limits": [
            "Previously examined development cohort, not independent confirmation.",
            "Custom calibrated local judge; Gemma reader/judge share a family.",
            "Fresh reader/judge states; identical full requests may share a response within a study.",
            "All cases and categories retained, no retries after a failed model response.",
            "No production default promotion or competitive leadership claim.",
        ],
    }
    write(registration_path, registration)
    for index, model in enumerate(MODELS):
        write(
            directory / f"reader-{index}-plan.json",
            {
                "reader": declared_readers[model],
                "prepared_sha256": digest(prepared_path.read_bytes()),
                "case_ids": [r["case_id"] for r in prepared["rows"]],
            },
        )


def verify_registration(root, directory, registration_path):
    registration = json.loads(registration_path.read_bytes())
    sources = source_paths(root)
    if (
        registration["code_identity"] != code_identity()
        or registration["sources"]
        != {k: digest(v.read_bytes()) for k, v in sources.items()}
        or registration["arm_mapping"] != ARMS
        or registration["comparisons"] != {k: list(v) for k, v in COMPARISONS.items()}
        or registration["model_order"] != list(MODELS)
        or registration["prepared_sha256"]
        != digest((directory / "prepared.json").read_bytes())
        or json.loads((directory / "prepared.json").read_bytes()) != prepare(**sources)
    ):
        raise ValueError("Frozen registration, code or contexts changed")
    for key, path in (
        (
            "references_sha256",
            root / "data/benchmarks/hindsight-prme-dev-references.json",
        ),
        (
            "controls_file_sha256",
            root / "benchmarks/fixtures/reader_judge_controls.json",
        ),
        (
            "calibration_sha256",
            root / "data/benchmarks/public-context-judge-calibration-f511aaa.json",
        ),
    ):
        if digest(path.read_bytes()) != registration[key]:
            raise ValueError("Registered references or calibration changed")
    ids = [
        r["case_id"]
        for r in json.loads((directory / "prepared.json").read_bytes())["rows"]
    ]
    for index, model in enumerate(MODELS):
        if json.loads((directory / f"reader-{index}-plan.json").read_bytes()) != {
            "reader": registration["reader_declarations"][model],
            "prepared_sha256": registration["prepared_sha256"],
            "case_ids": ids,
        }:
            raise ValueError("Reader plan differs from registration")
    return registration


def reader_paths(directory, index):
    return dict(
        prepared_path=directory / "prepared.json",
        plan_path=directory / f"reader-{index}-plan.json",
        state_path=directory / f"reader-{index}-state.json",
        report_path=directory / f"reader-{index}.json",
        completion_path=directory / f"reader-{index}-completion.json",
    )


def command(module, paths):
    return [
        sys.executable,
        "-m",
        "benchmarks.diagnostics." + module,
        *[part for name, path in paths.items() for part in ("--" + name, str(path))],
    ]


def score(root, directory, registration_path):
    registration = verify_registration(root, directory, registration_path)
    refs_path = root / "data/benchmarks/hindsight-prme-dev-references.json"
    controls = root / "benchmarks/fixtures/reader_judge_controls.json"
    calibration = root / "data/benchmarks/public-context-judge-calibration-f511aaa.json"
    if digest(refs_path.read_bytes()) != registration["references_sha256"]:
        raise ValueError("Registered references changed")
    readers = {model: reader_paths(directory, i) for i, model in enumerate(MODELS)}
    expected_inputs, expected_mapping = prepare_judgments(
        readers, refs_path, registration_path
    )
    if (
        json.loads((directory / "judge-inputs.json").read_bytes()) != expected_inputs
        or json.loads((directory / "judge-mapping.json").read_bytes())
        != expected_mapping
    ):
        raise ValueError("Judge export differs from verified predictions")
    plan = json.loads((directory / "judge-plan.json").read_bytes())
    if (
        plan["registration_sha256"] != digest(registration_path.read_bytes())
        or plan["mapping_sha256"]
        != digest((directory / "judge-mapping.json").read_bytes())
        or plan["judge"] != registration["judge_declaration"]
        or plan["controls_file_sha256"] != registration["controls_file_sha256"]
        or plan["calibration_sha256"] != registration["calibration_sha256"]
    ):
        raise ValueError("Judge plan differs from registration")
    _, result = judge.verify_result(
        directory / "judge-inputs.json",
        directory / "judge-plan.json",
        controls,
        calibration,
        directory / "judge-state.json",
        directory / "judge.json",
        directory / "judge-completion.json",
    )
    verdicts = {r["id"]: r["correct"] for r in result["judgments"]}
    answers = {}
    for mapped in expected_mapping["mapping"]:
        answers.setdefault(mapped["case_id"], {}).setdefault(mapped["reader"], {})[
            mapped["arm"]
        ] = verdicts[mapped["id"]]
    cases = json.loads(source_paths(root)["inputs_path"].read_bytes())["cases"]
    refs = {r["case_id"]: r for r in json.loads(refs_path.read_bytes())["references"]}
    groups = groups_for(cases, refs)
    details = [
        {
            "case_id": c["case_id"],
            "category": "abstention"
            if refs[c["case_id"]]["question_id"].endswith("_abs")
            else refs[c["case_id"]]["category"],
            "answers": answers[c["case_id"]],
        }
        for c in cases
    ]
    return {
        "complete": True,
        "verified": True,
        "questions": len(details),
        "registration_sha256": digest(registration_path.read_bytes()),
        "details": details,
        "readers": summarize(details, groups, MODELS, comparisons=COMPARISONS),
        "limits": registration["limits"],
    }


def run(root, directory, registration_path, base_url):
    registration = verify_registration(root, directory, registration_path)
    # Reject prior outcomes before sending a model request.
    for pattern in (
        "reader-*-state.json",
        "reader-*-completion.json",
        "reader-*.log",
        "judge*",
        "scores*",
    ):
        if list(directory.glob(pattern)):
            raise ValueError("Fresh study outcomes required")
    if any(
        reader_paths(directory, i)["report_path"].exists() for i in range(len(MODELS))
    ):
        raise ValueError("Fresh reader reports required")
    for index, model in enumerate(MODELS):
        paths = reader_paths(directory, index)
        run_stage(
            command(
                "public_context_reader",
                {
                    "prepared": paths["prepared_path"],
                    "plan": paths["plan_path"],
                    "state": paths["state_path"],
                    "output": paths["report_path"],
                    "base-url": base_url,
                },
            ),
            report=paths["report_path"],
            completion=paths["completion_path"],
            log=directory / f"reader-{index}.log",
        )
        verify_predictions(**paths)
        print(f"Complete verified reader: {model}", flush=True)
    verify_registration(root, directory, registration_path)
    refs = root / "data/benchmarks/hindsight-prme-dev-references.json"
    inputs, mapping = prepare_judgments(
        {m: reader_paths(directory, i) for i, m in enumerate(MODELS)},
        refs,
        registration_path,
    )
    write(directory / "judge-inputs.json", inputs)
    write(directory / "judge-mapping.json", mapping)
    write(
        directory / "judge-plan.json",
        {
            "inputs_sha256": digest((directory / "judge-inputs.json").read_bytes()),
            "case_ids": [r["id"] for r in inputs["cases"]],
            "judge": registration["judge_declaration"],
            "controls_file_sha256": registration["controls_file_sha256"],
            "calibration_sha256": registration["calibration_sha256"],
            "worker_sha256": digest(Path(judge.__file__).read_bytes()),
            "registration_sha256": digest(registration_path.read_bytes()),
            "mapping_sha256": digest((directory / "judge-mapping.json").read_bytes()),
        },
    )
    run_stage(
        command(
            "public_context_judge",
            {
                "inputs": directory / "judge-inputs.json",
                "plan": directory / "judge-plan.json",
                "controls": root / "benchmarks/fixtures/reader_judge_controls.json",
                "calibration": root
                / "data/benchmarks/public-context-judge-calibration-f511aaa.json",
                "state": directory / "judge-state.json",
                "output": directory / "judge.json",
                "base-url": base_url,
            },
        ),
        report=directory / "judge.json",
        completion=directory / "judge-completion.json",
        log=directory / "judge.log",
    )
    # The scorer independently reconstructs all reader and judge artifacts.
    run_stage(
        [
            sys.executable,
            "-m",
            "benchmarks.diagnostics.packing_head_reader",
            "score",
            "--root",
            str(root),
            "--directory",
            str(directory),
            "--registration",
            str(registration_path),
        ],
        report=directory / "scores.json",
        completion=directory / "scores-completion.json",
        log=directory / "scores.log",
    )
    print("All reader, judge and independent scoring stages exited zero", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["register", "run", "score"])
    for name in ("root", "directory", "registration"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    root, directory, registration = (
        args.root.resolve(),
        args.directory.resolve(),
        args.registration.resolve(),
    )
    if args.mode == "register":
        register(root, directory, registration, args.base_url)
    elif args.mode == "run":
        run(root, directory, registration, args.base_url)
    else:
        if (directory / "scores.json").exists():
            raise ValueError("Refusing to replace scored outcomes")
        write(directory / "scores.json", score(root, directory, registration))


if __name__ == "__main__":
    main()

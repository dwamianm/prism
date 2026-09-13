"""Verify native reader artifacts and export separately labelled judge inputs."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.diagnostics import public_context_reader as reader
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write


def verify_predictions(
    prepared_path, plan_path, state_path, report_path, completion_path
):
    prepared = json.loads(prepared_path.read_bytes())
    reader.validate_prepared(prepared)
    plan = json.loads(plan_path.read_bytes())
    state = json.loads(state_path.read_bytes())
    report_raw = report_path.read_bytes()
    report = json.loads(report_raw)
    completion = json.loads(completion_path.read_bytes())
    declared = plan["reader"]
    if (
        declared["runner_sha256"] != digest(Path(reader.__file__).read_bytes())
        or declared["runtime_helper_sha256"]
        != digest(Path(reader.runtime.__file__).read_bytes())
        or declared["options"] != reader.OPTIONS
        or declared.get("request_timeout_seconds") != reader.GENERATION_TIMEOUT_SECONDS
        or declared["system_prompt"] != reader.GENERATION_SYSTEM_PROMPT
    ):
        raise ValueError("Reader reproduction code or configuration differs")
    if completion["native_exit_code"] != 0 or completion["report_sha256"] != digest(
        report_raw
    ):
        raise ValueError("Native-exited matching predictions required")
    identity = {
        "prepared_sha256": digest(prepared_path.read_bytes()),
        "plan_sha256": digest(plan_path.read_bytes()),
    }
    if (
        not report["complete"]
        or not state["complete"]
        or state["failed_attempts"]
        or report["identity"] != identity
        or state["identity"] != identity
        or report["reader"] != plan["reader"]
        or plan["prepared_sha256"] != identity["prepared_sha256"]
        or plan["case_ids"] != [r["case_id"] for r in prepared["rows"]]
        or report["state_sha256"] != digest(state_path.read_bytes())
    ):
        raise ValueError("Reader completion, state or registration differs")
    rows, wanted = [], set()
    for case in prepared["rows"]:
        for arm in sorted(
            reader.ARMS, key=lambda a: digest(canonical([case["case_id"], a]))
        ):
            body = reader.payload(case, arm, plan["reader"])
            key = digest(canonical(body))
            wanted.add(key)
            saved = state["generations"][key]
            if digest(canonical(saved["response"])) != saved["response_sha256"]:
                raise ValueError("Raw reader response changed")
            hypothesis = reader.validate_response(
                saved["response"], plan["reader"]["model"]
            )
            rows.append(
                {
                    "case_id": case["case_id"],
                    "arm": arm,
                    "context_sha256": case["contexts"][arm]["sha256"],
                    "prompt_sha256": key,
                    "hypothesis": hypothesis,
                }
            )
    if (
        report["rows"] != rows
        or set(state["generations"]) != wanted
        or report["logical_predictions"] != len(rows)
        or report["unique_generations"] != len(wanted)
        or report["questions"] != len(prepared["rows"])
    ):
        raise ValueError("Predictions do not reproduce complete registered requests")
    return prepared, report


def prepare_judgments(readers, references_path, registration_path):
    """Require every registered reader before producing opaque judge cases.

    `readers` maps model names to path dictionaries accepted by verify_predictions.
    It does not select products or cases using their generated answers.
    """
    registration = json.loads(registration_path.read_bytes())
    if set(readers) != set(registration["reader_declarations"]):
        raise ValueError("Every registered reader is required")
    refs_raw = references_path.read_bytes()
    refs_list = json.loads(refs_raw)["references"]
    refs = {r["case_id"]: r for r in refs_list}
    if len(refs) != len(refs_list) or not refs:
        raise ValueError("Ambiguous references")
    if digest(refs_raw) != registration["references_sha256"]:
        raise ValueError("References differ from registration")
    cases, mapping, artifacts = [], [], {}
    expected = None
    shared_prepared = None
    for model, paths in sorted(readers.items()):
        prepared, report = verify_predictions(**paths)
        ids = [r["case_id"] for r in prepared["rows"]]
        if expected is None:
            expected = ids
            shared_prepared = prepared
        if (
            ids != expected
            or prepared != shared_prepared
            or set(ids) != set(refs)
            or len(ids) != registration["expected_cases"]
            or prepared["budget"] != registration["budget"]
            or prepared["inputs_sha256"] != registration["neutral_inputs_sha256"]
            or report["reader"] != registration["reader_declarations"][model]
            or report["logical_predictions"]
            != registration["logical_predictions_per_reader"]
        ):
            raise ValueError("Reader cohort or runtime differs from study registration")
        questions = {r["case_id"]: r for r in prepared["rows"]}
        for row in report["rows"]:
            ref = refs[row["case_id"]]
            opaque = digest(canonical([row["case_id"], model, row["arm"]]))
            cases.append(
                {
                    "id": opaque,
                    "question": questions[row["case_id"]]["question"],
                    "category": "abstention"
                    if ref["question_id"].endswith("_abs")
                    else ref["category"],
                    "reference": str(ref["answer"]),
                    "hypothesis": row["hypothesis"],
                }
            )
            mapping.append(
                {
                    "id": opaque,
                    "case_id": row["case_id"],
                    "reader": model,
                    "arm": row["arm"],
                }
            )
        artifacts[model] = {
            name: digest(path.read_bytes()) for name, path in paths.items()
        }
    cases.sort(key=lambda row: row["id"])
    mapping.sort(key=lambda row: row["id"])
    return {
        "kind": "normalized-public-context-judge-inputs",
        "cases": cases,
        "registration_sha256": digest(registration_path.read_bytes()),
        "references_sha256": digest(refs_raw),
        "reader_artifacts": artifacts,
    }, {"mapping": mapping}


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--readers", required=True, type=Path, help="Model to artifact-path dictionary"
    )
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--registration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mapping", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.mapping.exists():
        raise ValueError("Refusing to overwrite judge inputs")
    readers = {
        model: {name: Path(path) for name, path in paths.items()}
        for model, paths in json.loads(args.readers.read_bytes()).items()
    }
    cases, mapping = prepare_judgments(readers, args.references, args.registration)
    write(args.output, cases)
    write(args.mapping, mapping)


if __name__ == "__main__":
    main()

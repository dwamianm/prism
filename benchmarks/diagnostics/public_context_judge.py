"""Judge a complete frozen reader export using the separately calibrated model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.diagnostics import reader_judge as runtime
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write


def validate_inputs(prepared):
    ids = []
    for case in prepared["cases"]:
        if set(case) != {"id", "question", "category", "reference", "hypothesis"}:
            raise ValueError("Judge cases must omit product and reader identities")
        if case["category"] not in runtime.RULES:
            raise ValueError("Unknown judge category")
        if any(
            not isinstance(case[field], str) or not case[field].strip()
            for field in ("id", "question", "reference", "hypothesis")
        ):
            raise ValueError("Nonempty judge fields required")
        ids.append(case["id"])
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Unique nonempty judge case coverage required")
    return ids


def verify_inputs(inputs_path, plan_path, controls_path, calibration_path):
    raw = inputs_path.read_bytes()
    prepared = json.loads(raw)
    plan = json.loads(plan_path.read_bytes())
    ids = validate_inputs(prepared)
    for name, path in (
        ("inputs_sha256", inputs_path),
        ("controls_file_sha256", controls_path),
        ("calibration_sha256", calibration_path),
    ):
        if plan[name] != digest(path.read_bytes()):
            raise ValueError("Judge artifact differs from registration")
    if plan["case_ids"] != ids or plan["worker_sha256"] != digest(
        Path(__file__).read_bytes()
    ):
        raise ValueError("Judge worker or case coverage differs")
    declared = plan["judge"]
    controls = json.loads(controls_path.read_bytes())
    calibration = json.loads(calibration_path.read_bytes())
    runtime.validate_calibration(controls, calibration, declared)
    if calibration.get("prior_failed_attempts"):
        raise ValueError("Calibration contains failed attempts")
    return prepared, plan


def run(inputs_path, plan_path, controls_path, calibration_path, state_path, base_url):
    prepared, plan = verify_inputs(
        inputs_path, plan_path, controls_path, calibration_path
    )
    if state_path.exists():
        state = json.loads(state_path.read_bytes())
        if state["failed_attempts"]:
            raise ValueError(
                "A failed judge study cannot replace responses by retrying"
            )
        wanted = {
            digest(canonical(runtime.payload(case, plan["judge"])))
            for case in prepared["cases"]
        }
        if state["complete"] and set(state["generations"]) != wanted:
            raise ValueError("Completed judge state is missing registered responses")
    result = runtime.run_cases(prepared["cases"], plan["judge"], state_path, base_url)
    if result["prior_failed_attempts"]:
        raise ValueError("Judge study contains failed attempts")
    result.update(
        plan_sha256=digest(plan_path.read_bytes()),
        inputs_sha256=digest(inputs_path.read_bytes()),
        state_sha256=digest(state_path.read_bytes()),
    )
    return result


def verify_result(
    inputs_path,
    plan_path,
    controls_path,
    calibration_path,
    state_path,
    report_path,
    completion_path,
):
    """Reproduce every verdict from raw native responses, without provider calls."""
    prepared, plan = verify_inputs(
        inputs_path, plan_path, controls_path, calibration_path
    )
    raw = report_path.read_bytes()
    completion = json.loads(completion_path.read_bytes())
    if completion["native_exit_code"] != 0 or completion["report_sha256"] != digest(
        raw
    ):
        raise ValueError("Native-exited matching judge result required")
    result = json.loads(raw)
    state = json.loads(state_path.read_bytes())
    expected_identity = {
        "declaration": plan["judge"],
        "cases_sha256": digest(canonical(prepared["cases"])),
    }
    if (
        not result["passed"]
        or not result["complete"]
        or not state["complete"]
        or result["prior_failed_attempts"]
        or state["failed_attempts"]
        or result["plan_sha256"] != digest(plan_path.read_bytes())
        or result["inputs_sha256"] != digest(inputs_path.read_bytes())
        or result["state_sha256"] != digest(state_path.read_bytes())
        or state["identity"] != expected_identity
        or result["identity"] != expected_identity
        or state["generations"] != result["generations"]
    ):
        raise ValueError("Judge result or raw state differs from registration")
    judgments, wanted = [], set()
    for case in prepared["cases"]:
        key = digest(canonical(runtime.payload(case, plan["judge"])))
        wanted.add(key)
        saved = state["generations"][key]
        if digest(canonical(saved["response"])) != saved["response_sha256"]:
            raise ValueError("Raw judge response changed")
        verdict = runtime.verdict(saved["response"], plan["judge"]["model"])
        judgments.append({"id": case["id"], "prompt_sha256": key, **verdict})
    if (
        result["judgments"] != judgments
        or set(state["generations"]) != wanted
        or result["unique_calls"] != len(wanted)
    ):
        raise ValueError("Judge verdicts do not reproduce complete raw responses")
    return prepared, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inputs", "plan", "controls", "calibration", "state", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite a prior judge report")
    result = {"complete": False, "passed": False}
    try:
        result = run(
            args.inputs,
            args.plan,
            args.controls,
            args.calibration,
            args.state,
            args.base_url,
        )
    except BaseException as exc:
        result["fatal_error_type"] = type(exc).__name__
        raise
    finally:
        write(args.output, result)


if __name__ == "__main__":
    main()

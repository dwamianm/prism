"""Frozen common-reader trials of actual public contexts, with no gold in generation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from benchmarks.diagnostics import packing_reader as runtime
from benchmarks.diagnostics.hindsight_capture import canonical, digest, write
from benchmarks.llm_judge import GENERATION_SYSTEM_PROMPT
import tiktoken

ARMS = ("memory_a", "memory_b", "empty")
PRODUCTS = {"prme": "memory_a", "hindsight": "memory_b"}
OPTIONS = dict(runtime.OPTIONS)


def declaration(model, base_url):
    return {
        "model": model,
        "model_digest": runtime.model_digest(base_url, model),
        "ollama_version": runtime.request(base_url, "/api/version")["version"],
        "options": OPTIONS,
        "system_prompt": GENERATION_SYSTEM_PROMPT,
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "runtime_helper_sha256": digest(Path(runtime.__file__).read_bytes()),
        "python": sys.version.split()[0],
    }


def prepare(inputs, analysis, completion, captures, *, budget=4096):
    """Export only questions and exact verified contexts; references stay outside."""
    raw = inputs.read_bytes()
    analyzed_raw = analysis.read_bytes()
    verified = json.loads(analyzed_raw)
    native = json.loads(completion.read_bytes())
    if native["native_exit_code"] != 0 or native["report_sha256"] != digest(
        analyzed_raw
    ):
        raise ValueError("Complete native-exited analysis required")
    cases = json.loads(raw)["cases"]
    expected = [case["case_id"] for case in cases]
    if (
        not verified["complete"]
        or not verified["verification_passed"]
        or verified["inputs_sha256"] != digest(raw)
        or verified["questions"] != len(cases)
        or [row["case_id"] for row in verified["details"]] != expected
        or len(set(expected)) != len(expected)
        or not expected
    ):
        raise ValueError("Analysis does not cover the exact input cohort")
    rows = []
    for case, analyzed in zip(cases, verified["details"]):
        contexts = {}
        for product, arm in PRODUCTS.items():
            snapshot = json.loads(
                (captures[product] / (case["case_id"] + ".json")).read_bytes()
            )
            if snapshot["case_id"] != case["case_id"]:
                raise ValueError("Capture identity differs")
            saved = snapshot["contexts"][str(budget)]
            expected_hash = analyzed["products"][product][str(budget)]["actual"][
                "context_sha256"
            ]
            if (
                saved["sha256"] != expected_hash
                or digest(saved["context"].encode()) != expected_hash
            ):
                raise ValueError("Context differs from verified analysis")
            contexts[arm] = {key: saved[key] for key in ("context", "sha256", "tokens")}
        contexts["empty"] = {"context": "", "sha256": digest(b""), "tokens": 0}
        rows.append(
            {
                "case_id": case["case_id"],
                "question": case["question"],
                "question_date": case["question_date"],
                "contexts": contexts,
            }
        )
    prepared = {
        "kind": "matched-public-context-reader-inputs",
        "schema_version": 1,
        "budget": budget,
        "inputs_sha256": digest(raw),
        "analysis_sha256": digest(analyzed_raw),
        "analysis_completion_sha256": digest(completion.read_bytes()),
        "rows": rows,
    }
    validate_prepared(prepared)
    return prepared


def validate_prepared(prepared):
    if (
        set(prepared)
        != {
            "kind",
            "schema_version",
            "budget",
            "inputs_sha256",
            "analysis_sha256",
            "analysis_completion_sha256",
            "rows",
        }
        or prepared["kind"] != "matched-public-context-reader-inputs"
        or prepared["schema_version"] != 1
    ):
        raise ValueError("Only declared neutral reader inputs are accepted")
    if type(prepared["budget"]) is not int or prepared["budget"] <= 0:
        raise ValueError("Positive context budget required")
    encoding = tiktoken.get_encoding("cl100k_base")
    seen = set()
    for row in prepared["rows"]:
        if set(row) != {"case_id", "question", "question_date", "contexts"}:
            raise ValueError("Only neutral question/context fields are accepted")
        if (
            not isinstance(row["case_id"], str)
            or not row["case_id"]
            or row["case_id"] in seen
        ):
            raise ValueError("Unique case identities required")
        seen.add(row["case_id"])
        if (
            not isinstance(row["question"], str)
            or datetime.fromisoformat(row["question_date"]).tzinfo is None
        ):
            raise ValueError("Question text and timezone-aware date required")
        if set(row["contexts"]) != set(ARMS):
            raise ValueError("Every question requires exactly the registered arms")
        for arm, context in row["contexts"].items():
            if set(context) != {"context", "sha256", "tokens"}:
                raise ValueError("Only rendered context fields are accepted")
            text = context["context"]
            count = len(encoding.encode(text, disallowed_special=()))
            if (
                digest(text.encode()) != context["sha256"]
                or type(context["tokens"]) is not int
                or count != context["tokens"]
                or count > prepared["budget"]
            ):
                raise ValueError("Context identity or serialized token budget differs")
            if arm == "empty" and text:
                raise ValueError("No-memory control must be empty")
    if not seen:
        raise ValueError("Reader cohort must be nonempty")


def payload(row, arm, declared):
    # Reuse the established common prompt and conservative byte headroom check.
    return runtime.payload(
        row,
        arm,
        {"generation_system_prompt": declared["system_prompt"]},
        declared["model"],
    )


def validate_response(response, model):
    if response.get("model") != model:
        raise ValueError("Reader returned another model")
    for field in ("prompt_eval_count", "eval_count"):
        if type(response.get(field)) is not int or response[field] < 0:
            raise ValueError("Invalid reader token observation")
    return runtime.validate_response(response)


def run(prepared_path, plan_path, state_path, base_url):
    raw = prepared_path.read_bytes()
    prepared = json.loads(raw)
    validate_prepared(prepared)
    plan = json.loads(plan_path.read_bytes())
    declared = plan["reader"]
    if plan["prepared_sha256"] != digest(raw) or plan["case_ids"] != [
        r["case_id"] for r in prepared["rows"]
    ]:
        raise ValueError("Prepared reader input differs from registration")
    if declaration(declared["model"], base_url) != declared:
        raise ValueError("Reader runtime differs from registration")
    jobs = []
    for row in prepared["rows"]:
        for arm in sorted(ARMS, key=lambda a: digest(canonical([row["case_id"], a]))):
            body = payload(row, arm, declared)
            jobs.append((row, arm, body, digest(canonical(body))))
    identity = {
        "prepared_sha256": digest(raw),
        "plan_sha256": digest(plan_path.read_bytes()),
    }
    with runtime.exclusive_state(state_path):
        state = (
            json.loads(state_path.read_bytes())
            if state_path.exists()
            else {
                "identity": identity,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "generations": {},
                "failed_attempts": [],
                "complete": False,
            }
        )
        if state["identity"] != identity or state["failed_attempts"]:
            raise ValueError(
                "Resume requires the identical study without failed attempts"
            )
        wanted = {key for _, _, _, key in jobs}
        if not set(state["generations"]) <= wanted:
            raise ValueError("State contains unrelated responses")
        if state["complete"] and set(state["generations"]) != wanted:
            raise ValueError("Completed state is missing registered responses")
        for saved in state["generations"].values():
            if digest(canonical(saved["response"])) != saved["response_sha256"]:
                raise ValueError("Saved response changed")
            validate_response(saved["response"], declared["model"])
        write(state_path, state)
        for index, (_, _, body, key) in enumerate(jobs):
            if key not in state["generations"]:
                response = None
                try:
                    if (
                        runtime.model_digest(base_url, declared["model"])
                        != declared["model_digest"]
                    ):
                        raise ValueError("Model changed before generation")
                    response = runtime.request(base_url, "/api/chat", body)
                    validate_response(response, declared["model"])
                    if (
                        runtime.model_digest(base_url, declared["model"])
                        != declared["model_digest"]
                    ):
                        raise ValueError("Model changed during generation")
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
                            "response_sha256": digest(canonical(response))
                            if response is not None
                            else None,
                        }
                    )
                    write(state_path, state)
                    raise
                write(state_path, state)
            print(
                f"Reader {index + 1}/{len(jobs)}; generations={len(state['generations'])}",
                flush=True,
            )
        state["complete"] = True
        write(state_path, state)
        return {
            "complete": True,
            "identity": identity,
            "reader": declared,
            "questions": len(prepared["rows"]),
            "logical_predictions": len(jobs),
            "unique_generations": len(state["generations"]),
            "state_sha256": digest(state_path.read_bytes()),
            "rows": [
                {
                    "case_id": row["case_id"],
                    "arm": arm,
                    "context_sha256": row["contexts"][arm]["sha256"],
                    "prompt_sha256": key,
                    "hypothesis": validate_response(
                        state["generations"][key]["response"], declared["model"]
                    ),
                }
                for row, arm, _, key in jobs
            ],
            "limits": "Predictions only; all registered cases and native exit zero required before independent judging.",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to replace a prior prediction report")
    report = {"complete": False}
    try:
        report = run(args.prepared, args.plan, args.state, args.base_url)
    except BaseException as exc:
        report["fatal_error_type"] = type(exc).__name__
        raise
    finally:
        write(args.output, report)


if __name__ == "__main__":
    main()

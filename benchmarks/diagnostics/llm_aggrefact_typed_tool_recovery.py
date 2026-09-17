"""Replay observed typed-reference failures through durable Ollama tool calls.

This is a development diagnostic over cases already exposed by the registered
typed-reference v2 run. It accepts no test split and cannot unlock evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import platform
from typing import Any

import httpx
from pydantic import ValidationError

from benchmarks.diagnostics import durable_tool_calls
from benchmarks.integrations import run_llm_aggrefact_evidence_cascade as cascade
from benchmarks.integrations import run_llm_aggrefact_factcg as factcg
from benchmarks.integrations import run_llm_aggrefact_typed_references as typed


TOOL_NAME = "submit_typed_verdict"
TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Submit one complete source-bound typed verdict.",
        "parameters": typed._TypedVerdict.model_json_schema(),
    },
}
SYSTEM_PROMPT = (
    typed.ALIGNMENT_PROMPT
    + "\nReturn no prose. Call submit_typed_verdict exactly once with the complete "
    "verdict.\n"
)
OPTIONS = {"temperature": 0, "seed": 17, "num_predict": 2048}
TIMEOUT_SECONDS = 180.0
MAX_TRANSPORT_ATTEMPTS = 2
MAX_SEMANTIC_ATTEMPTS = 3
CONCURRENCY = 4


def _failure_ids(
    *,
    registration_path: Path,
    result_path: Path,
    prior_state_path: Path,
) -> tuple[tuple[str, ...], dict[str, Any], dict[str, Any]]:
    registration_sha256 = factcg._sha256_file(registration_path)
    result = json.loads(result_path.read_text())
    payload = dict(result)
    claimed_sha256 = payload.pop("result_sha256", None)
    development = result.get("development", {})
    observed_ids = development.get("observed_ids")
    if (
        result.get("kind") != "llm-aggrefact-typed-reference-invalid-result"
        or result.get("status") != "aborted_gate_mathematically_impossible"
        or result.get("registration_sha256") != registration_sha256
        or claimed_sha256 != factcg._canonical_sha256(payload)
        or result.get("test") != {"accessed": False, "samples": []}
        or not isinstance(observed_ids, list)
        or not all(isinstance(value, str) for value in observed_ids)
        or len(observed_ids) != len(set(observed_ids))
        or development.get("observed_cases") != len(observed_ids)
        or development.get("observed_identity_sha256")
        != factcg._canonical_sha256(observed_ids)
    ):
        raise ValueError("typed-reference result is not an intact sealed failure")
    state = json.loads(prior_state_path.read_text())
    samples = state.get("samples")
    if (
        state.get("registration_sha256") != registration_sha256
        or not isinstance(samples, dict)
        or set(samples) != set(observed_ids)
        or any(
            not isinstance(value, dict)
            or type(value.get("reference_integrity")) is not bool
            for value in samples.values()
        )
    ):
        raise ValueError("private typed-reference state does not match the result")
    failures = tuple(
        sorted(
            identifier
            for identifier, sample in samples.items()
            if not sample["reference_integrity"]
        )
    )
    if len(failures) != development.get("reference_integrity_failures"):
        raise ValueError("private failure count does not match the public result")
    return failures, result, state


def _strict_validate(
    item: dict[str, Any], arguments: dict[str, Any]
) -> tuple[bool, tuple[str, ...]]:
    try:
        verdict = typed._TypedVerdict.model_validate(arguments)
    except ValidationError:
        return False, ("tool_arguments_schema_invalid",)
    if verdict.model_dump(mode="json") != arguments:
        return False, ("tool_arguments_contain_unrecognized_fields",)
    return typed._validate_typed_verdict(item, verdict)


def _repair_message(errors: list[str] | tuple[str, ...]) -> str:
    return (
        "The source-reference validator rejected that tool call with these machine "
        "error codes: "
        + ", ".join(errors)
        + ". Call submit_typed_verdict exactly once with a complete corrected "
        "verdict using only the displayed identifiers."
    )


def _summarize(state: dict[str, Any]) -> dict[str, Any]:
    jobs = state["jobs"]
    statuses = Counter(value["status"] for value in jobs.values())
    transport_statuses: Counter[str] = Counter()
    transport_errors: Counter[str] = Counter()
    semantic_attempts: Counter[str] = Counter()
    for value in jobs.values():
        semantic_attempts[str(len(value["semantic_attempts"]))] += 1
        for semantic in value["semantic_attempts"]:
            for transport in semantic["transport_attempts"]:
                transport_statuses[transport["status"]] += 1
                if transport.get("error_type"):
                    transport_errors[transport["error_type"]] += 1
    return {
        "cases": len(jobs),
        "complete_cases": statuses.get("complete", 0),
        "completion_rate": statuses.get("complete", 0) / len(jobs),
        "job_statuses": dict(sorted(statuses.items())),
        "semantic_attempts_per_case": dict(sorted(semantic_attempts.items())),
        "transport_statuses": dict(sorted(transport_statuses.items())),
        "transport_error_types": dict(sorted(transport_errors.items())),
    }


async def run(
    *,
    registration_path: Path,
    base_registration_path: Path,
    base_result_path: Path,
    prior_registration_path: Path,
    prior_result_path: Path,
    registered_result_path: Path,
    prior_state_path: Path,
    dev_path: Path,
    state_path: Path,
    output_path: Path,
    project_root: Path,
) -> dict[str, Any]:
    failures, registered_result, _prior_state = _failure_ids(
        registration_path=registration_path,
        result_path=registered_result_path,
        prior_state_path=prior_state_path,
    )
    registration = json.loads(registration_path.read_text())
    selected, base_registration = typed._validate_registration(
        registration,
        project_root=project_root,
        base_registration_path=base_registration_path,
        base_result_path=base_result_path,
        prior_registration_path=prior_registration_path,
        prior_result_path=prior_result_path,
        dev_path=dev_path,
    )
    rows_by_id = {row["contamination_identifier"]: row for row in selected}
    if len(rows_by_id) != len(selected) or not set(failures) <= set(rows_by_id):
        raise ValueError("observed failures do not belong to the registered cohort")
    failure_rows = [rows_by_id[identifier] for identifier in failures]
    prepared, ranker_runtime = cascade._prepare_evidence(
        failure_rows,
        model_spec=base_registration["model"],
        top_k=registration["protocol"]["ranker_top_k"],
        batch_size=registration["protocol"]["ranker_batch_size"],
    )
    prepared_by_id = {item["id"]: item for item in prepared}
    verifier = registration["verifier"]
    installed_before = cascade._ollama_model(verifier["base_url"], verifier["name"])
    if installed_before.get("digest") != verifier["manifest_digest"]:
        raise ValueError("Ollama verifier changed before the recovery diagnostic")
    jobs = [
        {
            "id": item["id"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": typed._render_request(item)},
            ],
        }
        for item in prepared
    ]
    runner_path = Path(inspect.getfile(run)).resolve()
    run_identity = {
        "kind": "llm_aggrefact_typed_tool_recovery",
        "registered_result_sha256": factcg._sha256_file(registered_result_path),
        "registered_result_canonical_sha256": registered_result["result_sha256"],
        "failure_identity_sha256": factcg._canonical_sha256(list(failures)),
        "runner_sha256": factcg._sha256_file(runner_path),
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "verifier_manifest_digest": verifier["manifest_digest"],
    }
    accepted_models = frozenset(
        value
        for value in (verifier["name"], verifier.get("remote_model"))
        if isinstance(value, str) and value
    )
    async with httpx.AsyncClient(base_url=verifier["base_url"], timeout=None) as client:

        async def caller(body: dict[str, Any]) -> dict[str, Any]:
            response = await client.post("/api/chat", json=body)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("Ollama response must be a JSON object")
            return value

        state = await durable_tool_calls.run(
            jobs=jobs,
            state_path=state_path,
            run_identity=run_identity,
            model=verifier["name"],
            accepted_models=accepted_models,
            tool=TOOL,
            options=OPTIONS,
            timeout_seconds=TIMEOUT_SECONDS,
            max_transport_attempts=MAX_TRANSPORT_ATTEMPTS,
            max_semantic_attempts=MAX_SEMANTIC_ATTEMPTS,
            concurrency=CONCURRENCY,
            caller=caller,
            validator=lambda job_id, arguments: _strict_validate(
                prepared_by_id[job_id], arguments
            ),
            repair_message=_repair_message,
        )
    installed_after = cascade._ollama_model(verifier["base_url"], verifier["name"])
    if installed_after.get("digest") != verifier["manifest_digest"]:
        raise ValueError("Ollama verifier changed during the recovery diagnostic")
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "llm-aggrefact-typed-tool-recovery-diagnostic",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "development_only": True,
        "test_accessed": False,
        "source_trial": {
            "registration_sha256": factcg._sha256_file(registration_path),
            "result_sha256": factcg._sha256_file(registered_result_path),
            "result_canonical_sha256": registered_result["result_sha256"],
            "observed_failures": len(failures),
            "failure_identity_sha256": factcg._canonical_sha256(list(failures)),
        },
        "transport": {
            "provider": verifier["provider"],
            "model": verifier["name"],
            "remote_model": verifier.get("remote_model"),
            "manifest_digest": verifier["manifest_digest"],
            "mode": "ollama_native_tool_call",
            "options": OPTIONS,
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_transport_attempts": MAX_TRANSPORT_ATTEMPTS,
            "max_semantic_attempts": MAX_SEMANTIC_ATTEMPTS,
            "concurrency": CONCURRENCY,
        },
        "development": _summarize(state),
        "runtime": {
            "ranker": ranker_runtime,
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "limitations": [
            "This diagnostic reuses only failures already observed during typed-reference v2.",
            "It is tuning evidence and cannot estimate held-out classification quality.",
            "The Ollama cloud alias identifies a manifest, not pinned remote weights.",
            "No external test split was accepted or accessed.",
        ],
    }
    result["result_sha256"] = factcg._canonical_sha256(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--base-registration", type=Path, required=True)
    parser.add_argument("--base-result", type=Path, required=True)
    parser.add_argument("--prior-registration", type=Path, required=True)
    parser.add_argument("--prior-result", type=Path, required=True)
    parser.add_argument("--registered-result", type=Path, required=True)
    parser.add_argument("--prior-state", type=Path, required=True)
    parser.add_argument("--dev", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            base_registration_path=args.base_registration.resolve(),
            base_result_path=args.base_result.resolve(),
            prior_registration_path=args.prior_registration.resolve(),
            prior_result_path=args.prior_result.resolve(),
            registered_result_path=args.registered_result.resolve(),
            prior_state_path=args.prior_state.resolve(),
            dev_path=args.dev.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
            project_root=args.project_root.resolve(),
        )
    )
    print(
        json.dumps(
            {
                "complete": result["development"]["complete_cases"],
                "cases": result["development"]["cases"],
                "result_sha256": result["result_sha256"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Run a preregistered live temporal-relation product preflight.

The preflight reopens frozen LongMemEval-S packs through the public retrieval
API and uses PRME's built-in Ollama resolver and TypeSafe Jev gate.  It records
only product output, provider audit hashes/usage, and the evidence needed for
manual review; credentials and raw provider responses are never persisted.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from typing import Any

from dotenv import dotenv_values
from pydantic import SecretStr

from benchmarks.diagnostics.longmemeval_s_compact import _clone_pack
from benchmarks.diagnostics.longmemeval_s_temporal_relation_full_regression import (
    _load_captures,
    _percentile,
    _sha256,
    _sha256_file,
    _valid_self_hash,
    _write,
)
from benchmarks.diagnostics.packing_reader import exclusive_state
from benchmarks.integrations.run_longmemeval_s_baseline import (
    USER_ID,
    _pack_config,
    _parse_date,
)
from prme import MemoryEngine
from prme.models.relevance import RetrievalReceipt
from prme.retrieval.temporal_relation_providers import (
    GATE_QUESTION_SHA256,
    RESOLVER_OPTIONS,
    RESOLVER_SYSTEM_PROMPT_SHA256,
    SCHEMA_REPAIR_PROMPT_SHA256,
)
from prme.retrieval.temporal_relations import TemporalRelationConfig


EXPECTED_CASE_ROLES = {
    "known_accepted",
    "known_rejected",
    "known_no_call",
    "novel_routed_shape",
    "ordinary_no_call",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_key(env_file: Path) -> SecretStr:
    for name in ("JEV_API_KEY", "TYPESAFE_API_KEY"):
        value = os.environ.get(name)
        if isinstance(value, str) and value.strip():
            return SecretStr(value.strip())
    values = dotenv_values(env_file)
    for name in ("JEV_API_KEY", "TYPESAFE_API_KEY"):
        value = values.get(name)
        if isinstance(value, str) and value.strip():
            return SecretStr(value.strip())
    raise RuntimeError(
        "set JEV_API_KEY or TYPESAFE_API_KEY in the environment or selected .env"
    )


def _git(repository_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _load_registration(path: Path, repository_root: Path) -> dict[str, Any]:
    registration = json.loads(path.read_text())
    cases = registration.get("cases")
    source = registration.get("source", {})
    provider = registration.get("providers", {})
    if (
        registration.get("schema_version") != 1
        or registration.get("kind")
        != "longmemeval-s-temporal-relation-live-preflight-registration"
        or registration.get("status") != "registered_before_live_provider_calls"
        or not isinstance(cases, list)
        or len(cases) != 12
        or len({case.get("question_id") for case in cases}) != 12
        or any(case.get("role") not in EXPECTED_CASE_ROLES for case in cases)
        or source.get("runner_sha256") != _sha256_file(Path(__file__))
    ):
        raise ValueError("live preflight registration differs")
    product_sources = source.get("product_sources_sha256")
    if not isinstance(product_sources, dict) or not product_sources:
        raise ValueError("registered product source hashes are missing")
    benchmark_sources = source.get("benchmark_sources_sha256")
    if not isinstance(benchmark_sources, dict) or not benchmark_sources:
        raise ValueError("registered benchmark source hashes are missing")
    for relative, expected in {**product_sources, **benchmark_sources}.items():
        if _sha256_file(repository_root / relative) != expected:
            raise ValueError(f"registered source differs: {relative}")
    expected_provider = {
        "resolver": {
            "provider": "ollama",
            "model": TemporalRelationConfig().resolver_model,
            "base_url": TemporalRelationConfig().resolver_base_url,
            "model_digest": TemporalRelationConfig().resolver_model_digest,
            "options": RESOLVER_OPTIONS,
            "system_prompt_sha256": RESOLVER_SYSTEM_PROMPT_SHA256,
            "schema_repair_prompt_sha256": SCHEMA_REPAIR_PROMPT_SHA256,
        },
        "gate": {
            "provider": "typesafe_jev",
            "model": TemporalRelationConfig().gate_model,
            "api_url": TemporalRelationConfig().gate_api_url,
            "threshold": TemporalRelationConfig().gate_threshold,
            "question_sha256": GATE_QUESTION_SHA256,
        },
    }
    if provider != expected_provider:
        raise ValueError("registered provider protocol differs")
    return registration


def _full_rows(path: Path, expected_sha256: str) -> dict[str, dict[str, Any]]:
    if _sha256_file(path) != expected_sha256:
        raise ValueError("full regression result file differs")
    result = json.loads(path.read_text())
    rows = result.get("rows")
    if (
        result.get("kind")
        != "longmemeval-s-temporal-relation-full-regression-result"
        or result.get("complete") is not True
        or result.get("questions") != 500
        or not _valid_self_hash(result)
        or not isinstance(rows, list)
    ):
        raise ValueError("full regression result differs")
    indexed = {row.get("question_id"): row for row in rows}
    if None in indexed or len(indexed) != 500:
        raise ValueError("full regression rows differ")
    return indexed  # type: ignore[return-value]


def _evidence_records(bundle: Any, evidence_ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(evidence_ids)
    records: list[dict[str, Any]] = []
    for values in bundle.sections.values():
        for candidate in values:
            node = candidate.node
            if str(node.id) in wanted:
                records.append(
                    {
                        "id": str(node.id),
                        "event_time": (
                            node.event_time.isoformat() if node.event_time else None
                        ),
                        "text": node.content,
                    }
                )
    return sorted(records, key=lambda item: item["id"])


def _relation_guidance(bundle: Any) -> str | None:
    guidance = bundle.context_guidance
    if not isinstance(guidance, str):
        return None
    marker = "TEMPORAL RELATION ("
    offset = guidance.find(marker)
    return guidance[offset:] if offset >= 0 else None


def _case_checks(
    *,
    specification: dict[str, Any],
    observed: dict[str, Any],
    baseline_context_sha256: str,
) -> dict[str, bool]:
    role = specification["role"]
    expected = specification["expected"]
    routed = observed["status"] != "not_routed"
    accepted = observed["status"] == "accepted"
    checks = {
        "routing_matches": routed == bool(expected["routed"]),
        "control_context_matches": (
            observed["control_context_sha256"] == baseline_context_sha256
        ),
        "receipt_matches": bool(observed["receipt_matches"]),
        "receipt_persisted": bool(observed["receipt_persisted"]),
        "no_provider_error": observed["status"] != "provider_error",
        "configuration_aligned": (
            observed["confirmation_protocol_aligned"] is True
            if routed
            else observed["confirmation_protocol_aligned"] is None
        ),
    }
    if role == "known_accepted":
        checks["known_accepted_core_matches"] = (
            accepted
            and observed["operation"] == expected["operation"]
            and observed["value"] == expected["value"]
            and observed["evidence_ids"] == expected["evidence_ids"]
        )
        checks["frozen_result_context_matches"] = (
            observed["result_context_sha256"]
            == expected["result_context_sha256"]
        )
    elif role == "known_rejected":
        checks["known_rejection_remains_unchanged"] = (
            not accepted
            and observed["result_context_sha256"] == baseline_context_sha256
        )
        checks["frozen_status_matches"] = observed["status"] == expected["status"]
    elif role in {"known_no_call", "ordinary_no_call"}:
        checks["no_call_is_exact"] = (
            observed["status"] == "not_routed"
            and observed["result_context_sha256"] == baseline_context_sha256
            and observed["temporal_receipt"] is None
        )
    return checks


def _case_passes(role: str, checks: dict[str, bool]) -> bool:
    optional = {"frozen_result_context_matches", "frozen_status_matches"}
    return all(value for name, value in checks.items() if name not in optional)


async def _run_case(
    *,
    specification: dict[str, Any],
    capture: dict[str, Any],
    baseline_root: Path,
    relation_config: TemporalRelationConfig,
) -> dict[str, Any]:
    question_id = specification["question_id"]
    query = capture["receipts"]["warm"]["query"]
    reference_time = _parse_date(capture["question_date"])
    with tempfile.TemporaryDirectory(prefix="prme-temporal-live-") as temporary:
        pack = Path(temporary) / question_id
        clone_method = await asyncio.to_thread(
            _clone_pack, baseline_root / "packs" / question_id, pack
        )
        config = _pack_config(pack)
        config = config.model_copy(
            update={
                "organizer": config.organizer.model_copy(
                    update={"opportunistic_enabled": False}
                ),
                "temporal_relation": relation_config,
            }
        )
        engine = await MemoryEngine.create(config)
        try:
            started = time.perf_counter()
            response = await engine.retrieve(
                query,
                user_id=USER_ID,
                reference_time=reference_time,
            )
            retrieval_seconds = time.perf_counter() - started
            candidate_ids = [str(candidate.node.id) for candidate in response.results]
            candidate_scores = [candidate.composite_score for candidate in response.results]
            if (
                candidate_ids != [row["node_id"] for row in capture["returned"]]
                or candidate_scores
                != [row["composite_score"] for row in capture["returned"]]
            ):
                raise ValueError("live product ranking differs from frozen baseline")
            receipt = await engine.get_retrieval_receipt(
                str(response.metadata.request_id), user_id=USER_ID
            )
            if not isinstance(receipt, RetrievalReceipt) or receipt.execution is None:
                raise ValueError("live product receipt is unavailable")
            metadata = response.metadata.temporal_relation
            temporal_receipt = receipt.execution.parameters.get("temporal_relation")
            rendered = response.bundle.render()
            rendered_sha256 = hashlib.sha256(rendered.encode()).hexdigest()
            if metadata is None:
                observed = {
                    "status": "not_routed",
                    "operation": None,
                    "value": None,
                    "evidence_ids": [],
                    "validation_errors": [],
                    "dropped_record_ids": [],
                    "confirmation_protocol_aligned": None,
                    "configuration_sha256": None,
                    "control_context_sha256": capture["context_sha256"],
                    "result_context_sha256": rendered_sha256,
                    "guidance_sha256": None,
                    "error_stage": None,
                    "error_type": None,
                    "elapsed_ms": None,
                    "resolver": None,
                    "gate": None,
                }
            else:
                observed = metadata.model_dump(mode="json")
            observed.update(
                {
                    "receipt_matches": (
                        temporal_receipt
                        == (metadata.model_dump(mode="json") if metadata else None)
                        and receipt.context_sha256 == rendered_sha256
                    ),
                    "receipt_persisted": response.metadata.receipt_persisted,
                    "temporal_receipt": temporal_receipt,
                }
            )
            checks = _case_checks(
                specification=specification,
                observed=observed,
                baseline_context_sha256=capture["context_sha256"],
            )
            evidence_ids = list(observed["evidence_ids"])
            return {
                "question_id": question_id,
                "role": specification["role"],
                "question_type": capture["question_type"],
                "abstention": capture["abstention"],
                "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
                "clone_method": clone_method,
                "status": observed["status"],
                "operation": observed["operation"],
                "value": observed["value"],
                "evidence_ids": evidence_ids,
                "validation_errors": observed["validation_errors"],
                "dropped_record_ids": observed["dropped_record_ids"],
                "confirmation_protocol_aligned": observed[
                    "confirmation_protocol_aligned"
                ],
                "configuration_sha256": observed["configuration_sha256"],
                "control_context_sha256": observed["control_context_sha256"],
                "result_context_sha256": observed["result_context_sha256"],
                "guidance_sha256": observed["guidance_sha256"],
                "context_changed": (
                    observed["control_context_sha256"]
                    != observed["result_context_sha256"]
                ),
                "result_tokens": response.bundle.tokens_used,
                "resolver": observed["resolver"],
                "gate": observed["gate"],
                "error_stage": observed["error_stage"],
                "error_type": observed["error_type"],
                "temporal_elapsed_ms": observed["elapsed_ms"],
                "retrieval_seconds": retrieval_seconds,
                "receipt_persisted": observed["receipt_persisted"],
                "receipt_matches": observed["receipt_matches"],
                "guidance": _relation_guidance(response.bundle),
                "evidence_records": _evidence_records(
                    response.bundle, evidence_ids
                ),
                "checks": checks,
                "automatic_case_passed": _case_passes(
                    specification["role"], checks
                ),
                "manual_review_required": (
                    specification["role"] == "novel_routed_shape"
                    and observed["status"] == "accepted"
                ),
            }
        finally:
            await engine.close()


def _summary(rows: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
    routed = [row for row in rows if row["status"] != "not_routed"]
    resolver_rows = [row for row in rows if row["resolver"] is not None]
    gate_rows = [row for row in rows if row["gate"] is not None]
    retrieval = [float(row["retrieval_seconds"]) for row in rows]
    resolver_latency = [float(row["resolver"]["elapsed_ms"]) for row in resolver_rows]
    gate_latency = [float(row["gate"]["elapsed_ms"]) for row in gate_rows]
    return {
        "cases": len(rows) + len(failures),
        "completed_rows": len(rows),
        "failures": len(failures),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "routed": len(routed),
        "accepted": sum(row["status"] == "accepted" for row in rows),
        "provider_errors": sum(row["status"] == "provider_error" for row in rows),
        "manual_reviews_required": sum(row["manual_review_required"] for row in rows),
        "automatic_cases_passed": sum(row["automatic_case_passed"] for row in rows),
        "usage": {
            "resolver_input_tokens": sum(
                row["resolver"]["input_tokens"] or 0 for row in resolver_rows
            ),
            "resolver_output_tokens": sum(
                row["resolver"]["output_tokens"] or 0 for row in resolver_rows
            ),
            "gate_input_tokens": sum(
                row["gate"]["input_tokens"] or 0 for row in gate_rows
            ),
            "gate_output_tokens": sum(
                row["gate"]["output_tokens"] or 0 for row in gate_rows
            ),
        },
        "latency": {
            "retrieval_median_seconds": statistics.median(retrieval) if retrieval else None,
            "retrieval_p95_seconds": _percentile(retrieval, 0.95) if retrieval else None,
            "resolver_median_ms": statistics.median(resolver_latency) if resolver_latency else None,
            "resolver_p95_ms": _percentile(resolver_latency, 0.95) if resolver_latency else None,
            "gate_median_ms": statistics.median(gate_latency) if gate_latency else None,
            "gate_p95_ms": _percentile(gate_latency, 0.95) if gate_latency else None,
        },
    }


async def run(
    *,
    registration_path: Path,
    baseline_root: Path,
    baseline_identity_path: Path,
    baseline_result_path: Path,
    full_regression_result_path: Path,
    env_file: Path,
    state_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh live preflight output required")
    repository_root = Path(__file__).resolve().parents[2]
    registration = _load_registration(registration_path, repository_root)
    question_ids, captures, baseline_result = _load_captures(
        baseline_root=baseline_root,
        baseline_identity_path=baseline_identity_path,
        baseline_result_path=baseline_result_path,
    )
    source = registration["source"]
    if (
        _sha256_file(baseline_identity_path) != source["baseline_identity_sha256"]
        or _sha256_file(baseline_result_path) != source["baseline_result_sha256"]
        or baseline_result["capture_manifest_sha256"]
        != source["capture_manifest_sha256"]
    ):
        raise ValueError("registered baseline differs")
    full_rows = _full_rows(
        full_regression_result_path, source["full_regression_result_sha256"]
    )
    specifications = registration["cases"]
    selected_ids = [case["question_id"] for case in specifications]
    if not set(selected_ids) <= set(question_ids):
        raise ValueError("registered preflight cases are absent from the baseline")
    for case in specifications:
        frozen = full_rows[case["question_id"]]
        expected = case["expected"]
        capture = captures[case["question_id"]]
        if (
            frozen["resolver_called"] != expected["routed"]
            or frozen["status"] != expected["offline_status"]
            or frozen["question_type"] != capture["question_type"]
            or case.get("question_type") != capture["question_type"]
            or case.get("abstention") != capture["abstention"]
            or case.get("query_sha256") != capture["question_sha256"]
            or case.get("baseline_context_sha256")
            != capture["context_sha256"]
        ):
            raise ValueError(f"registered case source differs: {case['question_id']}")

    relation_config = TemporalRelationConfig(
        enabled=True,
        gate_api_key=_load_key(env_file),
    )
    identity = {
        "kind": "longmemeval-s-temporal-relation-live-preflight-state",
        "registration_sha256": _sha256_file(registration_path),
        "execution_revision": _git(repository_root, "rev-parse", "HEAD"),
        "runner_sha256": _sha256_file(Path(__file__)),
        "baseline_identity_sha256": _sha256_file(baseline_identity_path),
        "baseline_result_sha256": _sha256_file(baseline_result_path),
        "capture_manifest_sha256": baseline_result["capture_manifest_sha256"],
        "full_regression_result_sha256": _sha256_file(full_regression_result_path),
        "configuration_sha256": relation_config.configuration_sha256,
    }
    with exclusive_state(state_path):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "identity": identity,
                "started_at": _utc_now(),
                "outcomes": {},
            }
        )
        if state.get("identity") != identity:
            raise ValueError("live preflight resume identity differs")
        if not set(state["outcomes"]) <= set(selected_ids):
            raise ValueError("live preflight state contains unrelated cases")
        _write(state_path, state)
        for case in specifications:
            question_id = case["question_id"]
            if question_id in state["outcomes"]:
                continue
            print(
                f"Live preflight starting {len(state['outcomes']) + 1}/"
                f"{len(specifications)} {question_id}",
                flush=True,
            )
            try:
                row = await _run_case(
                    specification=case,
                    capture=captures[question_id],
                    baseline_root=baseline_root,
                    relation_config=relation_config,
                )
                state["outcomes"][question_id] = {"row": row}
                print(
                    f"Live preflight completed {len(state['outcomes'])}/"
                    f"{len(specifications)} {question_id}: {row['status']}",
                    flush=True,
                )
            except Exception as exc:
                state["outcomes"][question_id] = {
                    "failure": {
                        "question_id": question_id,
                        "error_type": type(exc).__name__,
                    }
                }
                print(
                    f"Live preflight failed {len(state['outcomes'])}/"
                    f"{len(specifications)} {question_id}: {type(exc).__name__}",
                    flush=True,
                )
            _write(state_path, state)

    rows = [
        state["outcomes"][question_id]["row"]
        for question_id in selected_ids
        if "row" in state["outcomes"][question_id]
    ]
    failures = [
        state["outcomes"][question_id]["failure"]
        for question_id in selected_ids
        if "failure" in state["outcomes"][question_id]
    ]
    summary = _summary(rows, failures)
    automatic_gate_passed = (
        summary["cases"] == len(specifications)
        and not failures
        and summary["provider_errors"] == 0
        and summary["automatic_cases_passed"] == len(specifications)
    )
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-live-preflight-result",
        "claim_boundary": (
            "Preregistered live provider/product-path stability, safety, usage, "
            "and latency preflight over 12 fixed frozen packs. This is not an "
            "answer-quality benchmark, leaderboard score, or universal claim."
        ),
        "identity": identity,
        "completed_at": _utc_now(),
        "complete": summary["cases"] == len(specifications),
        "automatic_gate_passed": automatic_gate_passed,
        "manual_gate_pending": summary["manual_reviews_required"] > 0,
        **summary,
        "failure_rows": failures,
        "rows": rows,
    }
    result["result_sha256"] = _sha256(result)
    _write(output_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--baseline-identity", type=Path, required=True)
    parser.add_argument("--baseline-result", type=Path, required=True)
    parser.add_argument("--full-regression-result", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            baseline_root=args.baseline_root.resolve(),
            baseline_identity_path=args.baseline_identity.resolve(),
            baseline_result_path=args.baseline_result.resolve(),
            full_regression_result_path=args.full_regression_result.resolve(),
            env_file=args.env_file.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
        )
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "complete",
                    "automatic_gate_passed",
                    "manual_gate_pending",
                    "status_counts",
                    "usage",
                    "latency",
                    "result_sha256",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

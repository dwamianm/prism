"""Replay the temporal product path across all 500 frozen LongMemEval-S packs.

The 133 benchmark-temporal questions reuse their frozen development or
confirmation resolver and Jev decisions. Other questions use an inert
``unsupported`` resolver so the run can measure routing, request payloads,
context stability, receipts, and local overhead without making model calls or
reading reference answers.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from typing import Any

import httpx
from pydantic import SecretStr

from benchmarks.diagnostics.longmemeval_s_compact import _clone_pack
from benchmarks.diagnostics.packing_reader import exclusive_state
from benchmarks.integrations.run_longmemeval_s_baseline import (
    DATASET_SHA256,
    USER_ID,
    _pack_config,
    _parse_date,
)
from prme import MemoryEngine
from prme.models.relevance import RetrievalReceipt
from prme.retrieval.context_formatter import _detect_context_type
from prme.retrieval.query_analysis import analyze_query
from prme.retrieval.temporal_relation_models import GateAudit, ResolverAudit
from prme.retrieval.temporal_relation_providers import _gate_body, _resolver_body
from prme.retrieval.temporal_relations import (
    EvidenceRecord,
    RawResolution,
    ResolverResult,
    TemporalRelation,
    TemporalRelationConfig,
    TemporalRelationEnricher,
)
from prme.retrieval.tokenization import count_tokens


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode()


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(_canonical(value) + b"\n")
    os.replace(temporary, path)


def _valid_self_hash(value: dict[str, Any]) -> bool:
    copy = dict(value)
    claimed = copy.pop("result_sha256", None)
    return isinstance(claimed, str) and claimed == _sha256(copy)


def _rows_by_id(value: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{name} rows are missing")
    result = {row.get("question_id"): row for row in rows if isinstance(row, dict)}
    if None in result or len(result) != len(rows):
        raise ValueError(f"{name} question identities differ")
    return result  # type: ignore[return-value]


def _http_json_bytes(url: str, body: dict[str, Any]) -> int:
    return len(httpx.Request("POST", url, json=body).content)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * fraction) - 1]


class _ReplayOrUnsupportedResolver:
    def __init__(
        self,
        *,
        question_id: str,
        config: TemporalRelationConfig,
        row: dict[str, Any] | None,
        source: str,
    ) -> None:
        self.question_id = question_id
        self.config = config
        self.row = row
        self.source = source
        self.calls = 0
        self.request_bytes = 0
        self.prompt_tokens_estimate = 0
        self.record_count = 0
        self.record_characters = 0

    async def resolve(
        self,
        query: str,
        question_time: datetime,
        records: tuple[EvidenceRecord, ...],
    ) -> ResolverResult:
        self.calls += 1
        body = _resolver_body(self.config, query, question_time, records)
        self.request_bytes = _http_json_bytes(
            f"{self.config.resolver_base_url}/api/chat", body
        )
        messages = body["messages"]
        prompt = "\n".join(str(message["content"]) for message in messages)
        self.prompt_tokens_estimate = count_tokens(prompt, "cl100k_base")
        self.record_count = len(records)
        self.record_characters = sum(len(record.text) for record in records)
        resolution = (
            RawResolution.model_validate(self.row["resolution"])
            if self.row is not None
            else RawResolution(operation="unsupported", operands=[])
        )
        return ResolverResult(
            resolution=resolution,
            audit=ResolverAudit(
                provider=f"offline_{self.source}_replay",
                model=self.config.resolver_model,
                request_sha256=_sha256(body),
                response_sha256=_sha256(resolution.model_dump(mode="json")),
                attempts=1,
                schema_repairs=0,
                elapsed_ms=0,
            ),
        )


class _ReplayGate:
    def __init__(
        self,
        *,
        question_id: str,
        config: TemporalRelationConfig,
        row: dict[str, Any] | None,
        source: str,
    ) -> None:
        self.question_id = question_id
        self.config = config
        self.row = row
        self.source = source
        self.calls = 0
        self.request_bytes = 0

    async def assess(self, query: str, relation: TemporalRelation) -> GateAudit:
        self.calls += 1
        if self.row is None:
            raise ValueError(f"frozen gate decision is missing for {self.question_id}")
        probabilities = tuple(
            float(value) for value in self.row["operand_probabilities"]
        )
        if not probabilities:
            raise ValueError(f"frozen gate probabilities are missing for {self.question_id}")
        body = _gate_body(self.config, query, relation)
        self.request_bytes = _http_json_bytes(self.config.gate_api_url, body)
        return GateAudit(
            provider=f"offline_{self.source}_replay",
            model=self.config.gate_model,
            request_sha256=_sha256(body),
            response_sha256=_sha256(self.row),
            probabilities=probabilities,
            minimum_probability=min(probabilities),
            attempts=1,
            elapsed_ms=0,
        )


def _load_captures(
    *,
    baseline_root: Path,
    baseline_identity_path: Path,
    baseline_result_path: Path,
) -> tuple[list[str], dict[str, dict[str, Any]], dict[str, Any]]:
    identity = json.loads(baseline_identity_path.read_text())
    result = json.loads(baseline_result_path.read_text())
    capture_paths = sorted((baseline_root / "captures").glob("*.json"))
    if (
        identity.get("dataset_sha256") != DATASET_SHA256
        or identity.get("questions") != 500
        or result.get("kind") != "longmemeval-s-prme-baseline-capture-result"
        or not _valid_self_hash(result)
        or result.get("questions") != 500
        or result.get("failures") != 0
        or len(capture_paths) != 500
    ):
        raise ValueError("baseline capture identity differs")
    captures: dict[str, dict[str, Any]] = {}
    for path in capture_paths:
        capture = json.loads(path.read_text())
        question_id = capture.get("question_id")
        body = {key: value for key, value in capture.items() if key != "capture_sha256"}
        receipt = capture.get("receipts", {}).get("warm", {})
        query = receipt.get("query")
        pack_path = baseline_root / "packs" / path.stem
        if (
            capture.get("kind") != "longmemeval-s-prme-capture"
            or capture.get("complete") is not True
            or question_id != path.stem
            or not isinstance(question_id, str)
            or question_id in captures
            or capture.get("identity", {}).get("registration_sha256")
            != identity["registration_sha256"]
            or capture.get("capture_sha256") != _sha256(body)
            or capture.get("context_sha256")
            != hashlib.sha256(capture.get("context", "").encode()).hexdigest()
            or not isinstance(query, str)
            or hashlib.sha256(query.encode()).hexdigest()
            != capture.get("question_sha256")
            or receipt.get("context_sha256") != capture.get("context_sha256")
            or not pack_path.is_dir()
        ):
            raise ValueError(f"baseline capture differs for {path.stem}")
        captures[question_id] = capture
    question_ids = sorted(captures)
    capture_manifest = _sha256(
        [[question_id, captures[question_id]["capture_sha256"]] for question_id in question_ids]
    )
    pack_manifest = _sha256(
        [
            [question_id, captures[question_id]["pack"]["tree_sha256"]]
            for question_id in question_ids
        ]
    )
    if (
        capture_manifest != result.get("capture_manifest_sha256")
        or pack_manifest != result.get("pack_manifest_sha256")
    ):
        raise ValueError("baseline capture manifest differs")
    return question_ids, captures, result


def _load_development(
    *,
    registration_path: Path,
    probe_path: Path,
    gate_path: Path,
    prepared_path: Path,
) -> dict[str, dict[str, Any]]:
    registration = json.loads(registration_path.read_text())
    probe = json.loads(probe_path.read_text())
    gate = json.loads(gate_path.read_text())
    prepared = json.loads(prepared_path.read_text())
    source = registration.get("source", {})
    question_ids = registration.get("dataset", {}).get("question_ids")
    probe_rows = _rows_by_id(probe, "development resolver")
    gate_rows = _rows_by_id(gate, "development gate")
    prepared_rows = _rows_by_id(prepared, "development prepared")
    audits = _rows_by_id({"rows": prepared.get("audits")}, "development audits")
    if (
        registration.get("kind")
        != "longmemeval-s-temporal-relation-answer-registration"
        or not isinstance(question_ids, list)
        or len(question_ids) != 29
        or source.get("probe_sha256") != _sha256_file(probe_path)
        or source.get("jev_result_sha256") != _sha256_file(gate_path)
        or source.get("prepared_sha256") != _sha256_file(prepared_path)
        or not _valid_self_hash(probe)
        or not _valid_self_hash(gate)
        or not probe.get("complete")
        or not gate.get("complete")
        or set(probe_rows) != set(question_ids)
        or set(prepared_rows) != set(question_ids)
        or set(audits) != set(question_ids)
        or not set(gate_rows) <= set(question_ids)
    ):
        raise ValueError("development temporal inputs differ")
    return {
        question_id: {
            "source": "development",
            "resolver": probe_rows[question_id],
            "gate": gate_rows.get(question_id),
            "prepared": prepared_rows[question_id],
            "audit": audits[question_id],
            "expected_accepted": bool(audits[question_id]["hint_accepted"]),
        }
        for question_id in question_ids
    }


def _load_confirmation(
    *,
    registration_path: Path,
    resolver_path: Path,
    gate_path: Path,
    prepared_path: Path,
) -> dict[str, dict[str, Any]]:
    registration = json.loads(registration_path.read_text())
    resolver = json.loads(resolver_path.read_text())
    gate = json.loads(gate_path.read_text())
    prepared = json.loads(prepared_path.read_text())
    registration_sha = _sha256_file(registration_path)
    question_ids = registration.get("dataset", {}).get("question_ids")
    resolver_rows = _rows_by_id(resolver, "confirmation resolver")
    gate_rows = _rows_by_id(gate, "confirmation gate")
    prepared_rows = _rows_by_id(prepared, "confirmation prepared")
    audits = _rows_by_id({"rows": prepared.get("audits")}, "confirmation audits")
    if (
        registration.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-registration"
        or not isinstance(question_ids, list)
        or len(question_ids) != 104
        or not all(_valid_self_hash(value) for value in (resolver, gate, prepared))
        or resolver.get("registration_sha256") != registration_sha
        or gate.get("registration_sha256") != registration_sha
        or prepared.get("registration_sha256") != registration_sha
        or prepared.get("resolver_result_sha256") != _sha256_file(resolver_path)
        or prepared.get("jev_result_sha256") != _sha256_file(gate_path)
        or not resolver.get("complete")
        or not gate.get("complete")
        or set(resolver_rows) != set(question_ids)
        or set(gate_rows) != set(question_ids)
        or set(prepared_rows) != set(question_ids)
        or set(audits) != set(question_ids)
    ):
        raise ValueError("confirmation temporal inputs differ")
    return {
        question_id: {
            "source": "confirmation",
            "resolver": resolver_rows[question_id],
            "gate": gate_rows[question_id],
            "prepared": prepared_rows[question_id],
            "audit": audits[question_id],
            "expected_accepted": bool(gate_rows[question_id]["accepted"]),
        }
        for question_id in question_ids
    }


async def _run_case(
    *,
    question_id: str,
    capture: dict[str, Any],
    frozen: dict[str, Any] | None,
    baseline_root: Path,
) -> dict[str, Any]:
    query = capture["receipts"]["warm"]["query"]
    reference_time = _parse_date(capture["question_date"])
    relation_config = TemporalRelationConfig(
        enabled=True,
        gate_api_key=SecretStr("offline-replay-do-not-send"),
    )
    source = frozen["source"] if frozen is not None else "non_temporal_audit"
    resolver = _ReplayOrUnsupportedResolver(
        question_id=question_id,
        config=relation_config,
        row=frozen["resolver"] if frozen is not None else None,
        source=source,
    )
    gate = _ReplayGate(
        question_id=question_id,
        config=relation_config,
        row=frozen["gate"] if frozen is not None else None,
        source=source,
    )
    analysis = await analyze_query(query, reference_time=reference_time)
    broad_context_route = _detect_context_type(query, analysis) == "temporal"
    with tempfile.TemporaryDirectory(prefix="prme-temporal-full-regression-") as temp:
        pack = Path(temp) / question_id
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
            pipeline = engine._retrieval_pipeline
            if pipeline is None:
                raise ValueError("retrieval pipeline is unavailable")
            pipeline._temporal_relation_enricher = TemporalRelationEnricher(
                relation_config, resolver, gate
            )
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
                raise ValueError(f"product ranking differs for {question_id}")
            expected_context = (
                frozen["prepared"]["contexts"]["temporal_relation"]
                if frozen is not None
                else {
                    "context": capture["context"],
                    "sha256": capture["context_sha256"],
                    "tokens": capture["bundle_tokens"],
                }
            )
            context = response.bundle.render()
            if (
                context != expected_context["context"]
                or response.bundle.tokens_used != expected_context["tokens"]
                or hashlib.sha256(context.encode()).hexdigest()
                != expected_context["sha256"]
            ):
                raise ValueError(f"product context differs for {question_id}")
            metadata = response.metadata.temporal_relation
            receipt = await engine.get_retrieval_receipt(
                str(response.metadata.request_id), user_id=USER_ID
            )
            if not isinstance(receipt, RetrievalReceipt) or receipt.execution is None:
                raise ValueError(f"product receipt is unavailable for {question_id}")
            recorded = receipt.execution.parameters.get("temporal_relation")
            if receipt.context_sha256 != expected_context["sha256"]:
                raise ValueError(f"product receipt context differs for {question_id}")
            expected_accepted = bool(
                frozen is not None and frozen["expected_accepted"]
            )
            if metadata is None:
                if (
                    resolver.calls != 0
                    or gate.calls != 0
                    or recorded is not None
                    or expected_accepted
                ):
                    raise ValueError(f"product routing differs for {question_id}")
                status = "not_routed"
                operation = None
                context_changed = False
                aligned = None
            else:
                if resolver.calls != 1 or recorded != metadata.model_dump(mode="json"):
                    raise ValueError(f"product temporal receipt differs for {question_id}")
                if metadata.status in {"provider_error", "packing_rejected"}:
                    raise ValueError(f"product temporal stage failed for {question_id}")
                if (metadata.status == "accepted") != expected_accepted:
                    raise ValueError(f"product acceptance differs for {question_id}")
                if gate.calls != int(metadata.gate is not None):
                    raise ValueError(f"product gate calls differ for {question_id}")
                if frozen is None and metadata.status != "unsupported":
                    raise ValueError(
                        f"inert non-temporal resolver changed status for {question_id}"
                    )
                if expected_accepted:
                    assert frozen is not None
                    audit = frozen["audit"]
                    if (
                        list(map(str, metadata.evidence_ids))
                        != audit["relation_evidence_ids"]
                        or len(metadata.dropped_record_ids) != audit["dropped_records"]
                    ):
                        raise ValueError(
                            f"product evidence retention differs for {question_id}"
                        )
                status = metadata.status
                operation = metadata.operation
                context_changed = (
                    metadata.control_context_sha256
                    != metadata.result_context_sha256
                )
                aligned = metadata.confirmation_protocol_aligned
        finally:
            await engine.close()

    return {
        "question_id": question_id,
        "question_type": capture["question_type"],
        "abstention": capture["abstention"],
        "frozen_source": source,
        "clone_method": clone_method,
        "status": status,
        "operation": operation,
        "resolver_called": resolver.calls == 1,
        "gate_called": gate.calls == 1,
        "expected_accepted": expected_accepted,
        "context_changed": context_changed,
        "context_sha256": expected_context["sha256"],
        "context_tokens": expected_context["tokens"],
        "receipt_persisted": response.metadata.receipt_persisted,
        "confirmation_protocol_aligned": aligned,
        "broad_context_route": broad_context_route or resolver.calls == 1,
        "resolver_request_bytes": resolver.request_bytes,
        "resolver_prompt_cl100k_tokens_estimate": resolver.prompt_tokens_estimate,
        "resolver_record_count": resolver.record_count,
        "resolver_record_characters": resolver.record_characters,
        "gate_request_bytes": gate.request_bytes,
        "retrieval_seconds": retrieval_seconds,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(row["status"] for row in rows)
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        categories[row["question_type"]].append(row)
    routed = [row for row in rows if row["resolver_called"]]
    gated = [row for row in rows if row["gate_called"]]
    retrieval = [float(row["retrieval_seconds"]) for row in rows]
    request_bytes = [int(row["resolver_request_bytes"]) for row in routed]
    prompt_tokens = [
        int(row["resolver_prompt_cl100k_tokens_estimate"]) for row in routed
    ]
    return {
        "questions": len(rows),
        "contexts_matched": len(rows),
        "receipts_persisted": sum(row["receipt_persisted"] for row in rows),
        "contexts_changed": sum(row["context_changed"] for row in rows),
        "accepted": statuses["accepted"],
        "status_counts": dict(sorted(statuses.items())),
        "routing": {
            "resolver_calls": len(routed),
            "gate_calls": len(gated),
            "not_routed": statuses["not_routed"],
            "broad_context_route_calls": sum(row["broad_context_route"] for row in rows),
            "avoided_vs_broad_context_route": sum(
                row["broad_context_route"] and not row["resolver_called"] for row in rows
            ),
        },
        "frozen_sources": dict(sorted(Counter(row["frozen_source"] for row in rows).items())),
        "categories": {
            name: {
                "questions": len(items),
                "resolver_calls": sum(item["resolver_called"] for item in items),
                "accepted": sum(item["status"] == "accepted" for item in items),
                "changed_contexts": sum(item["context_changed"] for item in items),
            }
            for name, items in sorted(categories.items())
        },
        "resolver_payload": {
            "total_request_bytes": sum(request_bytes),
            "median_request_bytes": statistics.median(request_bytes),
            "p95_request_bytes": _percentile(
                [float(value) for value in request_bytes], 0.95
            ),
            "total_prompt_cl100k_tokens_estimate": sum(prompt_tokens),
            "median_prompt_cl100k_tokens_estimate": statistics.median(prompt_tokens),
            "p95_prompt_cl100k_tokens_estimate": _percentile(
                [float(value) for value in prompt_tokens], 0.95
            ),
        },
        "gate_payload": {
            "observed_calls": len(gated),
            "total_request_bytes": sum(row["gate_request_bytes"] for row in gated),
        },
        "local_retrieval": {
            "median_seconds": statistics.median(retrieval),
            "p95_seconds": _percentile(retrieval, 0.95),
        },
        "confirmation_protocol_aligned": all(
            row["confirmation_protocol_aligned"] is True for row in routed
        ),
    }


async def run(
    *,
    baseline_root: Path,
    baseline_identity_path: Path,
    baseline_result_path: Path,
    development_registration_path: Path,
    development_probe_path: Path,
    development_gate_path: Path,
    development_prepared_path: Path,
    confirmation_registration_path: Path,
    confirmation_resolver_path: Path,
    confirmation_gate_path: Path,
    confirmation_prepared_path: Path,
    state_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh full-regression output required")
    question_ids, captures, baseline_result = _load_captures(
        baseline_root=baseline_root,
        baseline_identity_path=baseline_identity_path,
        baseline_result_path=baseline_result_path,
    )
    development = _load_development(
        registration_path=development_registration_path,
        probe_path=development_probe_path,
        gate_path=development_gate_path,
        prepared_path=development_prepared_path,
    )
    confirmation = _load_confirmation(
        registration_path=confirmation_registration_path,
        resolver_path=confirmation_resolver_path,
        gate_path=confirmation_gate_path,
        prepared_path=confirmation_prepared_path,
    )
    if set(development) & set(confirmation):
        raise ValueError("development and confirmation questions overlap")
    frozen = {**development, **confirmation}
    temporal_ids = {
        question_id
        for question_id, capture in captures.items()
        if capture["question_type"] == "temporal-reasoning"
    }
    if set(frozen) != temporal_ids or len(temporal_ids) != 133:
        raise ValueError("frozen temporal coverage differs")
    for question_id, item in frozen.items():
        capture = captures[question_id]
        prepared = item["prepared"]
        if (
            prepared["question"] != capture["receipts"]["warm"]["query"]
            or prepared["question_date"] != capture["question_date"]
            or prepared["contexts"]["auditable"]["context"] != capture["context"]
        ):
            raise ValueError(f"frozen source differs for {question_id}")

    repository_root = Path(__file__).resolve().parents[2]
    identity = {
        "kind": "longmemeval-s-temporal-relation-full-regression-state",
        "product_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "runner_sha256": _sha256_file(Path(__file__)),
        "baseline_identity_sha256": _sha256_file(baseline_identity_path),
        "baseline_result_sha256": _sha256_file(baseline_result_path),
        "capture_manifest_sha256": baseline_result["capture_manifest_sha256"],
        "development_registration_sha256": _sha256_file(
            development_registration_path
        ),
        "development_probe_sha256": _sha256_file(development_probe_path),
        "development_gate_sha256": _sha256_file(development_gate_path),
        "development_prepared_sha256": _sha256_file(development_prepared_path),
        "confirmation_registration_sha256": _sha256_file(
            confirmation_registration_path
        ),
        "confirmation_resolver_sha256": _sha256_file(confirmation_resolver_path),
        "confirmation_gate_sha256": _sha256_file(confirmation_gate_path),
        "confirmation_prepared_sha256": _sha256_file(confirmation_prepared_path),
        "product_sources_sha256": {
            name: _sha256_file(
                repository_root / "src" / "prme" / "retrieval" / f"{name}.py"
            )
            for name in (
                "context_formatter",
                "pipeline",
                "packing",
                "temporal_relation_models",
                "temporal_relations",
                "temporal_relation_providers",
            )
        },
    }
    with exclusive_state(state_path):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {"identity": identity, "results": {}, "failures": []}
        )
        if state.get("identity") != identity:
            raise ValueError("full-regression resume identity differs")
        if not set(state["results"]) <= set(question_ids):
            raise ValueError("full-regression state contains unrelated questions")
        _write(state_path, state)
        for question_id in question_ids:
            if question_id in state["results"]:
                continue
            try:
                row = await _run_case(
                    question_id=question_id,
                    capture=captures[question_id],
                    frozen=frozen.get(question_id),
                    baseline_root=baseline_root,
                )
            except Exception as exc:
                state["failures"].append(
                    {"question_id": question_id, "error_type": type(exc).__name__}
                )
                _write(state_path, state)
                raise
            state["results"][question_id] = row
            _write(state_path, state)
            print(f"Full regression {len(state['results'])}/{len(question_ids)}", flush=True)

    rows = [state["results"][question_id] for question_id in question_ids]
    summary = _summary(rows)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-full-regression-result",
        "claim_boundary": (
            "Answer-blind product-path, routing, payload, receipt, and context "
            "regression over 500 frozen packs. Temporal decisions are frozen; "
            "other routed queries use an inert resolver. No model calls or "
            "universal quality, live cost, or latency claim."
        ),
        "identity": identity,
        "complete": len(rows) == 500 and not state["failures"],
        "failures": state["failures"],
        **summary,
        "rows": rows,
    }
    result["result_sha256"] = _sha256(result)
    _write(output_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--baseline-identity", type=Path, required=True)
    parser.add_argument("--baseline-result", type=Path, required=True)
    parser.add_argument("--development-registration", type=Path, required=True)
    parser.add_argument("--development-probe", type=Path, required=True)
    parser.add_argument("--development-gate", type=Path, required=True)
    parser.add_argument("--development-prepared", type=Path, required=True)
    parser.add_argument("--confirmation-registration", type=Path, required=True)
    parser.add_argument("--confirmation-resolver", type=Path, required=True)
    parser.add_argument("--confirmation-gate", type=Path, required=True)
    parser.add_argument("--confirmation-prepared", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(
        run(
            baseline_root=args.baseline_root.resolve(),
            baseline_identity_path=args.baseline_identity.resolve(),
            baseline_result_path=args.baseline_result.resolve(),
            development_registration_path=args.development_registration.resolve(),
            development_probe_path=args.development_probe.resolve(),
            development_gate_path=args.development_gate.resolve(),
            development_prepared_path=args.development_prepared.resolve(),
            confirmation_registration_path=args.confirmation_registration.resolve(),
            confirmation_resolver_path=args.confirmation_resolver.resolve(),
            confirmation_gate_path=args.confirmation_gate.resolve(),
            confirmation_prepared_path=args.confirmation_prepared.resolve(),
            state_path=args.state.resolve(),
            output_path=args.output.resolve(),
        )
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "questions",
                    "contexts_matched",
                    "contexts_changed",
                    "accepted",
                    "status_counts",
                    "routing",
                    "resolver_payload",
                    "result_sha256",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

"""Replay the frozen temporal confirmation through the shipped product path.

This runner makes no model calls and never reads reference answers. It reuses
the immutable resolver and Jev responses, reopens each frozen PRME pack, invokes
the public retrieval pipeline with replay providers, and requires every rendered
context to match the registered confirmation arm byte for byte.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from benchmarks.diagnostics import paired_context_eval as paired
from benchmarks.diagnostics.longmemeval_s_compact import (
    _case_checksum,
    _clone_pack,
    _sha256_file,
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
from prme.retrieval.temporal_relation_models import GateAudit, ResolverAudit
from prme.retrieval.temporal_relations import (
    RawResolution,
    ResolverResult,
    TemporalRelationConfig,
    TemporalRelationEnricher,
)


def _sha256(value: Any) -> str:
    return hashlib.sha256(paired.canonical(value)).hexdigest()


def _valid_self_hash(value: dict[str, Any]) -> bool:
    copy = dict(value)
    claimed = copy.pop("result_sha256", None)
    return isinstance(claimed, str) and claimed == _sha256(copy)


class _ReplayResolver:
    def __init__(self, question_id: str, row: dict[str, Any]) -> None:
        self.question_id = question_id
        self.row = row
        self.calls = 0

    async def resolve(self, query, question_time, records) -> ResolverResult:
        self.calls += 1
        return ResolverResult(
            resolution=RawResolution.model_validate(self.row["resolution"]),
            audit=ResolverAudit(
                provider="frozen_confirmation_replay",
                model="deepseek-v4.1-flash:cloud",
                request_sha256=_sha256(
                    {
                        "question_id": self.question_id,
                        "query": query,
                        "question_time": question_time.isoformat(),
                        "records": [record.model_dump(mode="json") for record in records],
                    }
                ),
                response_sha256=_sha256(self.row["resolution"]),
                attempts=1,
                schema_repairs=0,
                elapsed_ms=0,
            ),
        )


class _ReplayGate:
    def __init__(self, question_id: str, row: dict[str, Any]) -> None:
        self.question_id = question_id
        self.row = row
        self.calls = 0

    async def assess(self, query, relation) -> GateAudit:
        self.calls += 1
        probabilities = tuple(float(value) for value in self.row["operand_probabilities"])
        if not probabilities:
            raise ValueError("frozen gate result has no operand probabilities")
        return GateAudit(
            provider="frozen_confirmation_replay",
            model="jev-1.13.0",
            request_sha256=_sha256(
                {
                    "question_id": self.question_id,
                    "query": query,
                    "relation": relation.model_dump(mode="json"),
                }
            ),
            response_sha256=_sha256(self.row),
            probabilities=probabilities,
            minimum_probability=min(probabilities),
            attempts=1,
            elapsed_ms=0,
        )


def _rows_by_id(value: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"{name} rows are missing")
    result = {row.get("question_id"): row for row in rows if isinstance(row, dict)}
    if None in result or len(result) != len(rows):
        raise ValueError(f"{name} question identities differ")
    return result  # type: ignore[return-value]


def _validate_inputs(
    *,
    registration_path: Path,
    resolver_result_path: Path,
    jev_result_path: Path,
    paired_inputs_path: Path,
    source_identity_path: Path,
    source_cases_root: Path,
    baseline_identity_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    registration = json.loads(registration_path.read_text())
    resolver_result = json.loads(resolver_result_path.read_text())
    jev_result = json.loads(jev_result_path.read_text())
    prepared = json.loads(paired_inputs_path.read_text())
    source_identity = json.loads(source_identity_path.read_text())
    baseline_identity = json.loads(baseline_identity_path.read_text())
    registration_sha = _sha256_file(registration_path)
    if (
        registration.get("schema_version") != 2
        or registration.get("kind")
        != "longmemeval-s-temporal-relation-confirmation-registration"
        or not _valid_self_hash(resolver_result)
        or not _valid_self_hash(jev_result)
        or not _valid_self_hash(prepared)
        or resolver_result.get("registration_sha256") != registration_sha
        or jev_result.get("registration_sha256") != registration_sha
        or prepared.get("registration_sha256") != registration_sha
        or prepared.get("resolver_result_sha256") != _sha256_file(
            resolver_result_path
        )
        or prepared.get("jev_result_sha256") != _sha256_file(jev_result_path)
        or not resolver_result.get("complete")
        or resolver_result.get("failed_attempts")
        or not jev_result.get("complete")
        or jev_result.get("failures")
        or registration.get("source", {}).get("source_identity_sha256")
        != _sha256_file(source_identity_path)
        or registration.get("source", {}).get("baseline_identity_sha256")
        != _sha256_file(baseline_identity_path)
        or source_identity.get("questions") != 500
        or baseline_identity.get("questions") != 500
        or not source_cases_root.is_dir()
    ):
        raise ValueError("frozen temporal product-parity inputs differ")
    question_ids = prepared.get("question_ids")
    resolver_rows = _rows_by_id(resolver_result, "resolver")
    gate_rows = _rows_by_id(jev_result, "gate")
    prepared_rows = _rows_by_id(prepared, "paired inputs")
    audits = _rows_by_id({"rows": prepared.get("audits")}, "paired audits")
    if (
        not isinstance(question_ids, list)
        or len(question_ids) != 104
        or len(set(question_ids)) != len(question_ids)
        or any(
            set(values) != set(question_ids)
            for values in (resolver_rows, gate_rows, prepared_rows, audits)
        )
    ):
        raise ValueError("frozen product-parity question coverage differs")
    return registration, resolver_rows, gate_rows, prepared_rows, audits


async def _run_case(
    *,
    question_id: str,
    resolver_row: dict[str, Any],
    gate_row: dict[str, Any],
    prepared_row: dict[str, Any],
    prepared_audit: dict[str, Any],
    baseline_root: Path,
    source_cases_root: Path,
) -> dict[str, Any]:
    saved = json.loads((source_cases_root / f"{question_id}.json").read_text())
    if (
        saved.get("kind") != "longmemeval-s-monotonic-compact-case"
        or saved.get("question_id") != question_id
        or saved.get("case_sha256") != _case_checksum(saved)
    ):
        raise ValueError(f"saved source case differs for {question_id}")
    relation_config = TemporalRelationConfig(enabled=True)
    resolver = _ReplayResolver(question_id, resolver_row)
    gate = _ReplayGate(question_id, gate_row)
    with tempfile.TemporaryDirectory(prefix="prme-temporal-product-parity-") as temp:
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
            pipeline._temporal_relation_enricher = None
            kwargs = {
                "user_id": USER_ID,
                "reference_time": _parse_date(prepared_row["question_date"]),
            }
            control = await engine.retrieve(prepared_row["question"], **kwargs)
            expected_control = prepared_row["contexts"]["auditable"]
            if (
                control.bundle.render() != expected_control["context"]
                or control.bundle.tokens_used != expected_control["tokens"]
            ):
                raise ValueError(f"product control replay differs for {question_id}")

            pipeline._temporal_relation_enricher = TemporalRelationEnricher(
                relation_config, resolver, gate
            )
            candidate = await engine.retrieve(prepared_row["question"], **kwargs)
            expected_candidate = prepared_row["contexts"]["temporal_relation"]
            if (
                candidate.bundle.render() != expected_candidate["context"]
                or candidate.bundle.tokens_used != expected_candidate["tokens"]
            ):
                raise ValueError(f"product candidate replay differs for {question_id}")
            metadata = candidate.metadata.temporal_relation
            if metadata is None or resolver.calls != 1:
                raise ValueError(f"product temporal stage did not run for {question_id}")
            expected_accepted = bool(gate_row["accepted"])
            if (metadata.status == "accepted") != expected_accepted:
                raise ValueError(f"product acceptance differs for {question_id}")
            if metadata.status in {"provider_error", "packing_rejected"}:
                raise ValueError(f"product stage failed for {question_id}")
            if gate.calls != int(metadata.gate is not None):
                raise ValueError(f"product gate calls differ for {question_id}")
            if expected_accepted:
                if (
                    list(map(str, metadata.evidence_ids))
                    != prepared_audit["relation_evidence_ids"]
                    or len(metadata.dropped_record_ids)
                    != prepared_audit["dropped_records"]
                ):
                    raise ValueError(
                        f"product evidence retention differs for {question_id}"
                    )
            receipt = await engine.get_retrieval_receipt(
                str(candidate.metadata.request_id), user_id=USER_ID
            )
            if not isinstance(receipt, RetrievalReceipt) or receipt.execution is None:
                raise ValueError(f"product receipt is unavailable for {question_id}")
            recorded = receipt.execution.parameters.get("temporal_relation")
            if (
                recorded != metadata.model_dump(mode="json")
                or receipt.context_sha256
                != hashlib.sha256(candidate.bundle.render().encode()).hexdigest()
            ):
                raise ValueError(f"product receipt differs for {question_id}")
        finally:
            await engine.close()

    return {
        "question_id": question_id,
        "clone_method": clone_method,
        "status": metadata.status,
        "operation": metadata.operation,
        "value": metadata.value,
        "resolver_called": resolver.calls == 1,
        "gate_called": gate.calls == 1,
        "expected_accepted": bool(gate_row["accepted"]),
        "context_changed": (
            metadata.control_context_sha256 != metadata.result_context_sha256
        ),
        "control_context_sha256": metadata.control_context_sha256,
        "result_context_sha256": metadata.result_context_sha256,
        "evidence_ids": [str(value) for value in metadata.evidence_ids],
        "dropped_record_ids": [str(value) for value in metadata.dropped_record_ids],
        "confirmation_protocol_aligned": metadata.confirmation_protocol_aligned,
    }


async def run(
    *,
    registration_path: Path,
    resolver_result_path: Path,
    jev_result_path: Path,
    paired_inputs_path: Path,
    source_identity_path: Path,
    source_cases_root: Path,
    baseline_identity_path: Path,
    baseline_root: Path,
    state_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("fresh product-parity output required")
    registration, resolver_rows, gate_rows, prepared_rows, audits = (
        _validate_inputs(
            registration_path=registration_path,
            resolver_result_path=resolver_result_path,
            jev_result_path=jev_result_path,
            paired_inputs_path=paired_inputs_path,
            source_identity_path=source_identity_path,
            source_cases_root=source_cases_root,
            baseline_identity_path=baseline_identity_path,
        )
    )
    identity = {
        "kind": "longmemeval-s-temporal-relation-product-parity-state",
        "registration_sha256": _sha256_file(registration_path),
        "resolver_result_sha256": _sha256_file(resolver_result_path),
        "jev_result_sha256": _sha256_file(jev_result_path),
        "paired_inputs_sha256": _sha256_file(paired_inputs_path),
        "source_identity_sha256": _sha256_file(source_identity_path),
        "baseline_identity_sha256": _sha256_file(baseline_identity_path),
        "runner_sha256": _sha256_file(Path(__file__)),
        "product_sources_sha256": {
            name: _sha256_file(
                Path(__file__).parents[2]
                / "src"
                / "prme"
                / "retrieval"
                / f"{name}.py"
            )
            for name in (
                "pipeline",
                "packing",
                "temporal_relation_models",
                "temporal_relations",
                "temporal_relation_providers",
            )
        },
    }
    question_ids = registration["dataset"]["question_ids"]
    with exclusive_state(state_path):
        state = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {"identity": identity, "results": {}, "failures": []}
        )
        if state.get("identity") != identity:
            raise ValueError("product-parity resume identity differs")
        if not set(state["results"]) <= set(question_ids):
            raise ValueError("product-parity state contains unrelated questions")
        _write(state_path, state)
        for question_id in question_ids:
            if question_id in state["results"]:
                continue
            try:
                row = await _run_case(
                    question_id=question_id,
                    resolver_row=resolver_rows[question_id],
                    gate_row=gate_rows[question_id],
                    prepared_row=prepared_rows[question_id],
                    prepared_audit=audits[question_id],
                    baseline_root=baseline_root,
                    source_cases_root=source_cases_root,
                )
            except Exception as exc:
                state["failures"].append(
                    {"question_id": question_id, "error_type": type(exc).__name__}
                )
                _write(state_path, state)
                raise
            state["results"][question_id] = row
            _write(state_path, state)
            print(
                f"Product parity {len(state['results'])}/{len(question_ids)}",
                flush=True,
            )

    rows = [state["results"][question_id] for question_id in question_ids]
    statuses = Counter(row["status"] for row in rows)
    result = {
        "schema_version": 1,
        "kind": "longmemeval-s-temporal-relation-product-parity-result",
        "claim_boundary": (
            "Byte-exact product-path reproduction of frozen contexts and receipts; "
            "no new model calls, answer scoring, or universal quality claim."
        ),
        "identity": identity,
        "complete": len(rows) == len(question_ids) and not state["failures"],
        "failures": state["failures"],
        "questions": len(rows),
        "contexts_matched": len(rows),
        "accepted": statuses["accepted"],
        "changed_contexts": sum(row["context_changed"] for row in rows),
        "status_counts": dict(sorted(statuses.items())),
        "confirmation_protocol_aligned": all(
            row["confirmation_protocol_aligned"] for row in rows
        ),
        "rows": rows,
    }
    result["result_sha256"] = _sha256(result)
    _write(output_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--resolver-result", type=Path, required=True)
    parser.add_argument("--jev-result", type=Path, required=True)
    parser.add_argument("--paired-inputs", type=Path, required=True)
    parser.add_argument("--source-identity", type=Path, required=True)
    parser.add_argument("--source-cases-root", type=Path, required=True)
    parser.add_argument("--baseline-identity", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(
        run(
            registration_path=args.registration.resolve(),
            resolver_result_path=args.resolver_result.resolve(),
            jev_result_path=args.jev_result.resolve(),
            paired_inputs_path=args.paired_inputs.resolve(),
            source_identity_path=args.source_identity.resolve(),
            source_cases_root=args.source_cases_root.resolve(),
            baseline_identity_path=args.baseline_identity.resolve(),
            baseline_root=args.baseline_root.resolve(),
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
                    "accepted",
                    "changed_contexts",
                    "status_counts",
                    "result_sha256",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

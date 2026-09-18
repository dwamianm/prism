"""Frozen-pack validation for natural-language quantity aggregation planning.

The assay copies the completed quantity-aggregation v2 pack, runs fixed ready
and refusal questions through the public engine method, and records every plan
and result. It makes no model or provider calls.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from prme import MemoryEngine, PRMEConfig
from prme.config import EmbeddingConfig, ExtractionConfig, OrganizerConfig


REGISTRATION_KIND = "quantity-aggregation-text-planner-registration"
RESULT_KIND = "quantity-aggregation-text-planner-result"
SCHEMA_VERSION = 1
PRIMARY_OWNER = "quantity-aggregation-e2e-owner"
ASSUMPTIONS = (
    "owner_bound",
    "first_person_subject_exact",
    "positive_default_epistemic",
    "predicate_token_prefix_family",
    "unit_groups_are_never_converted",
)
RAISE_PREFIXES = (
    "raise",
    "raised",
    "raising",
    "help_raise",
    "helped_raise",
    "helping_raise",
)
RUN_PREFIXES = ("run", "ran", "running")
SPEND_PREFIXES = ("spend", "spent", "spending")

READY_CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "individual_fundraising_amount",
        "question": "How much did I raise?",
        "reason": "matched_amount_question",
        "action": "raise",
        "subject": "I",
        "prefixes": RAISE_PREFIXES,
        "units": (),
        "matched_quantity_records": 4,
        "groups": {
            "$": {
                "value_count": 3,
                "total": "2750",
                "minimum": "250",
                "maximum": "2000",
                "sample_values": ("250", "500", "2000"),
            },
            "usd": {
                "value_count": 1,
                "total": "100",
                "minimum": "100",
                "maximum": "100",
                "sample_values": ("100",),
            },
        },
    },
    {
        "name": "group_fundraising_amount",
        "question": "How much did we raise?",
        "reason": "matched_amount_question",
        "action": "raise",
        "subject": "we",
        "prefixes": RAISE_PREFIXES,
        "units": (),
        "matched_quantity_records": 1,
        "groups": {
            "$": {
                "value_count": 1,
                "total": "1000",
                "minimum": "1000",
                "maximum": "1000",
                "sample_values": ("1000",),
            },
        },
    },
    {
        "name": "distance_count",
        "question": "How many kilometers did I run?",
        "reason": "matched_count_question",
        "action": "run",
        "subject": "I",
        "prefixes": RUN_PREFIXES,
        "units": ("kilometers",),
        "matched_quantity_records": 1,
        "groups": {
            "kilometers": {
                "value_count": 1,
                "total": "5",
                "minimum": "5",
                "maximum": "5",
                "sample_values": ("5",),
            },
        },
    },
    {
        "name": "spending_amount",
        "question": "How much did I spend?",
        "reason": "matched_amount_question",
        "action": "spend",
        "subject": "I",
        "prefixes": SPEND_PREFIXES,
        "units": (),
        "matched_quantity_records": 1,
        "groups": {
            "$": {
                "value_count": 1,
                "total": "300",
                "minimum": "300",
                "maximum": "300",
                "sample_values": ("300",),
            },
        },
    },
    {
        "name": "total_shape",
        "question": "What's the total amount I raised?",
        "reason": "matched_amount_question",
        "action": "raised",
        "subject": "I",
        "prefixes": RAISE_PREFIXES,
        "units": (),
        "matched_quantity_records": 4,
        "groups": {
            "$": {
                "value_count": 3,
                "total": "2750",
                "minimum": "250",
                "maximum": "2000",
                "sample_values": ("250", "500", "2000"),
            },
            "usd": {
                "value_count": 1,
                "total": "100",
                "minimum": "100",
                "maximum": "100",
                "sample_values": ("100",),
            },
        },
    },
)

REFUSAL_CASES: tuple[dict[str, str], ...] = (
    {
        "name": "qualifier_refused",
        "question": "How much did I raise for charity?",
        "reason": "unsupported_shape",
    },
    {
        "name": "negation_refused",
        "question": "How much did I not raise?",
        "reason": "unsupported_shape",
    },
    {
        "name": "future_refused",
        "question": "How much will I raise?",
        "reason": "unsupported_shape",
    },
    {
        "name": "named_subject_refused",
        "question": "How much did Alice raise?",
        "reason": "unsupported_shape",
    },
    {
        "name": "unknown_action_refused",
        "question": "How much did I frobnicate?",
        "reason": "unsupported_action",
    },
    {
        "name": "unsupported_unit_refused",
        "question": "How many dollars did I raise?",
        "reason": "unsupported_unit",
    },
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _digest(value: Any) -> str:
    raw = value if isinstance(value, bytes) else _canonical(value)
    return hashlib.sha256(raw).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _pack_manifest(pack: Path) -> dict[str, str]:
    if not (pack / "memory.duckdb").is_file():
        raise ValueError("source pack is missing memory.duckdb")
    return {
        str(path.relative_to(pack)): _file_digest(path)
        for path in sorted(pack.rglob("*"))
        if path.is_file() and not path.name.endswith(".lock")
    }


def _source_identity(root: Path) -> dict[str, str]:
    paths = (
        "benchmarks/diagnostics/quantity_aggregation_text_planner.py",
        "src/prme/models/aggregation.py",
        "src/prme/retrieval/aggregation.py",
        "src/prme/retrieval/aggregation_planning.py",
        "src/prme/storage/engine.py",
    )
    return {path: _file_digest(root / path) for path in paths}


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value))


def register(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.registration.exists():
        raise ValueError("fresh registration path required")
    source_pack = args.source_run_dir / "pack"
    source_result = json.loads(args.source_result.read_text())
    if (
        source_result.get("kind") != "quantity-aggregation-e2e-result"
        or source_result.get("decision")
        != "accept_explicit_quantity_aggregation_product_path"
        or source_result.get("metrics", {}).get("passed") is not True
    ):
        raise ValueError("source quantity aggregation result is not an accepted pass")
    registration = {
        "schema_version": SCHEMA_VERSION,
        "kind": REGISTRATION_KIND,
        "status": "registered_before_execution",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Deterministic product-path validation over one previously completed "
            "authored pack; not held-out language coverage, extraction accuracy, "
            "answer quality, or competitive evidence."
        ),
        "source": {
            "implementation_sha256": _source_identity(root),
            "source_result_sha256": _file_digest(args.source_result),
            "source_result_self_sha256": source_result.get("result_sha256"),
            "source_pack_manifest": _pack_manifest(source_pack),
        },
        "protocol": {
            "owner": PRIMARY_OWNER,
            "ready_cases": _jsonable(READY_CASES),
            "refusal_cases": _jsonable(REFUSAL_CASES),
            "assumptions": list(ASSUMPTIONS),
            "gates": {
                "ready_cases_passed": len(READY_CASES),
                "refusal_cases_passed": len(REFUSAL_CASES),
                "refusal_scan_calls": 0,
                "exact_question_subject": True,
                "units_kept_separate": True,
                "provider_calls": 0,
            },
        },
    }
    _write(args.registration, registration)
    return registration


def _verify_registration(
    registration: dict[str, Any], args: argparse.Namespace, root: Path
) -> None:
    source = registration.get("source", {})
    protocol = registration.get("protocol", {})
    source_result = json.loads(args.source_result.read_text())
    if (
        registration.get("schema_version") != SCHEMA_VERSION
        or registration.get("kind") != REGISTRATION_KIND
        or registration.get("status") != "registered_before_execution"
        or source.get("implementation_sha256") != _source_identity(root)
        or source.get("source_result_sha256") != _file_digest(args.source_result)
        or source.get("source_result_self_sha256")
        != source_result.get("result_sha256")
        or source.get("source_pack_manifest")
        != _pack_manifest(args.source_run_dir / "pack")
        or protocol.get("owner") != PRIMARY_OWNER
        or protocol.get("ready_cases") != _jsonable(READY_CASES)
        or protocol.get("refusal_cases") != _jsonable(REFUSAL_CASES)
        or protocol.get("assumptions") != list(ASSUMPTIONS)
        or protocol.get("gates", {}).get("provider_calls") != 0
    ):
        raise ValueError("registered quantity planner inputs differ")


def _config(pack: Path) -> PRMEConfig:
    lexical = pack / "lexical"
    lexical.mkdir(parents=True, exist_ok=True)
    return PRMEConfig(
        database_url=None,
        encryption_enabled=False,
        db_path=str(pack / "memory.duckdb"),
        vector_path=str(pack / "vectors.usearch"),
        lexical_path=str(lexical),
        duckdb_threads=1,
        organizer=OrganizerConfig(opportunistic_enabled=False),
        embedding=EmbeddingConfig(
            provider="fastembed",
            model_name="BAAI/bge-small-en-v1.5",
            dimension=384,
            api_key=None,
        ),
        extraction=ExtractionConfig(
            provider="ollama",
            model="unused-no-provider-call",
            base_url="http://127.0.0.1:11434/v1",
            api_key=None,
            timeout=1.0,
            temperature=0.0,
            max_retries=0,
        ),
    )


def _group_view(group: Any) -> dict[str, Any]:
    return {
        "unit": group.normalized_values["unit"],
        "value_count": group.value_count,
        "total": str(group.total),
        "minimum": str(group.minimum),
        "maximum": str(group.maximum),
        "evidence_count": group.evidence_count,
        "sample_values": sorted(str(sample.value) for sample in group.samples),
    }


def _score_ready(case: dict[str, Any], result: Any, scan_calls: int) -> dict[str, Any]:
    plan = result.plan
    aggregation = result.aggregation
    actual_groups = (
        {group.normalized_values["unit"]: _group_view(group) for group in aggregation.groups}
        if aggregation is not None
        else {}
    )
    expected_groups = {
        unit: {
            **metrics,
            "sample_values": sorted(metrics["sample_values"]),
        }
        for unit, metrics in case["groups"].items()
    }
    comparable_groups = {
        unit: {
            key: value
            for key, value in metrics.items()
            if key not in {"evidence_count", "unit"}
        }
        for unit, metrics in actual_groups.items()
    }
    checks = {
        "status": plan.status == "ready",
        "reason": plan.reason == case["reason"],
        "action": plan.action == case["action"],
        "query_present": plan.query is not None,
        "subject_exact": plan.query is not None
        and plan.query.subjects == (case["subject"],),
        "prefixes_exact": plan.query is not None
        and plan.query.predicate_prefixes == tuple(case["prefixes"]),
        "units_exact": plan.query is not None
        and plan.query.units == tuple(case["units"]),
        "assumptions_exact": plan.assumptions == ASSUMPTIONS,
        "aggregation_present": aggregation is not None,
        "owner_exact": aggregation is not None
        and aggregation.user_id == PRIMARY_OWNER,
        "matched_quantity_records": aggregation is not None
        and aggregation.matched_quantity_records
        == case["matched_quantity_records"],
        "groups_exact": comparable_groups == expected_groups,
        "units_separate": len(actual_groups) == len(case["groups"]),
        "scan_executed": scan_calls > 0,
        "stored_set_exhaustive": aggregation is not None
        and aggregation.stored_set_exhaustive is True,
        "unit_conversion_none": aggregation is not None
        and aggregation.unit_conversion == "none",
    }
    return {
        "name": case["name"],
        "question": case["question"],
        "checks": checks,
        "passed": all(checks.values()),
        "scan_calls": scan_calls,
        "plan": plan.model_dump(mode="json"),
        "aggregation": (
            aggregation.model_dump(mode="json") if aggregation is not None else None
        ),
    }


def _score_refusal(
    case: dict[str, str], result: Any, scan_calls: int
) -> dict[str, Any]:
    checks = {
        "status": result.plan.status == "unsupported",
        "reason": result.plan.reason == case["reason"],
        "query_absent": result.plan.query is None,
        "aggregation_absent": result.aggregation is None,
        "no_scan": scan_calls == 0,
    }
    return {
        "name": case["name"],
        "question": case["question"],
        "checks": checks,
        "passed": all(checks.values()),
        "scan_calls": scan_calls,
        "plan": result.plan.model_dump(mode="json"),
        "aggregation": None,
    }


async def execute(
    registration_path: Path,
    result_path: Path,
    run_dir: Path,
    args: argparse.Namespace,
    root: Path,
) -> dict[str, Any]:
    if result_path.exists():
        raise ValueError("fresh result path required")
    registration = json.loads(registration_path.read_text())
    _verify_registration(registration, args, root)
    destination_pack = run_dir / "pack"
    if destination_pack.exists():
        raise ValueError("fresh run directory required")
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source_run_dir / "pack", destination_pack)
    copied_manifest = _pack_manifest(destination_pack)
    if copied_manifest != registration["source"]["source_pack_manifest"]:
        raise ValueError("copied quantity pack differs from registration")

    ready_rows: list[dict[str, Any]] = []
    refusal_rows: list[dict[str, Any]] = []
    scan_calls = 0
    async with MemoryEngine.open(_config(destination_pack)) as engine:
        original_scan_nodes = engine.scan_nodes

        async def tracked_scan_nodes(*scan_args, **scan_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            return await original_scan_nodes(*scan_args, **scan_kwargs)

        engine.scan_nodes = tracked_scan_nodes  # type: ignore[method-assign]
        for case in READY_CASES:
            before = scan_calls
            planned = await engine.aggregate_quantities_from_text(
                case["question"], user_id=PRIMARY_OWNER, batch_size=2
            )
            ready_rows.append(_score_ready(case, planned, scan_calls - before))
            print(f"Ready case: {case['name']}", flush=True)
        for case in REFUSAL_CASES:
            before = scan_calls
            planned = await engine.aggregate_quantities_from_text(
                case["question"], user_id=PRIMARY_OWNER, batch_size=2
            )
            refusal_rows.append(_score_refusal(case, planned, scan_calls - before))
            print(f"Refusal case: {case['name']}", flush=True)

    source_pack_unchanged = (
        _pack_manifest(args.source_run_dir / "pack")
        == registration["source"]["source_pack_manifest"]
    )
    ready_passed = sum(row["passed"] for row in ready_rows)
    refusal_passed = sum(row["passed"] for row in refusal_rows)
    refusal_scan_calls = sum(row["scan_calls"] for row in refusal_rows)
    gates = registration["protocol"]["gates"]
    passed = (
        ready_passed == gates["ready_cases_passed"]
        and refusal_passed == gates["refusal_cases_passed"]
        and refusal_scan_calls == gates["refusal_scan_calls"]
        and source_pack_unchanged
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": _file_digest(registration_path),
        "source_result_sha256": registration["source"]["source_result_sha256"],
        "source_pack_manifest_sha256": _digest(
            registration["source"]["source_pack_manifest"]
        ),
        "metrics": {
            "ready_cases": len(ready_rows),
            "ready_cases_passed": ready_passed,
            "refusal_cases": len(refusal_rows),
            "refusal_cases_passed": refusal_passed,
            "refusal_scan_calls": refusal_scan_calls,
            "provider_calls": 0,
            "source_pack_unchanged": source_pack_unchanged,
            "passed": passed,
        },
        "ready_cases": ready_rows,
        "refusal_cases": refusal_rows,
        "decision": (
            "accept_fail_closed_quantity_text_planner"
            if passed
            else "reject_fail_closed_quantity_text_planner"
        ),
        "limits": registration["claim_boundary"],
    }
    result["result_sha256"] = _digest(result)
    _write(result_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--source-run-dir", type=Path, required=True)
    parser.add_argument("--source-result", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.register:
        registration = register(args, root)
        print(json.dumps({"registered": True, "kind": registration["kind"]}))
        return
    if args.output is None or args.run_dir is None:
        raise ValueError("--output and --run-dir are required for execution")
    result = asyncio.run(
        execute(args.registration, args.output, args.run_dir, args, root)
    )
    print(
        json.dumps(
            {
                "passed": result["metrics"]["passed"],
                "decision": result["decision"],
                "result_sha256": result["result_sha256"],
            }
        )
    )
    if not result["metrics"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Preregistered end-to-end grounded quantity aggregation assay.

The assay ingests fixed natural-language sources into one durable memory pack,
then exercises the public ``aggregate_quantities`` API.  It measures one
authored product path; it is not held-out accuracy or competitive evidence.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from benchmarks.diagnostics.speech_act_extraction import _model_inventory
from prme import MemoryEngine, PRMEConfig, QuantityAggregationQuery
from prme.config import EmbeddingConfig, ExtractionConfig, OrganizerConfig
from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT, _CitedExtractionResult


REGISTRATION_KIND = "quantity-aggregation-e2e-registration"
RESULT_KIND = "quantity-aggregation-e2e-result"
SCHEMA_VERSION = 1
PRIMARY_OWNER = "quantity-aggregation-e2e-owner"
OTHER_OWNER = "quantity-aggregation-e2e-other"
SESSION_ID = "quantity-aggregation-e2e-v1"


CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "charity_bake_sale",
        "owner": PRIMARY_OWNER,
        "source": (
            "I'm looking for some tips on how to stay motivated to exercise "
            "regularly. I've been doing pretty well so far, but I want to keep "
            "the momentum going. By the way, I recently volunteered at a charity "
            "bake sale and it was amazing to see how much of an impact we can "
            "make - we raised $1,000 for the local children's hospital!"
        ),
        "expected": (("1000", "$", "$1,000", "positive", ("asserted", "observed")),),
    },
    {
        "name": "run_for_hunger",
        "owner": PRIMARY_OWNER,
        "source": (
            "I'm looking for some advice on finding volunteer opportunities in "
            "my area. I've been trying to attend at least one charity event per "
            "month, and it's been an incredible experience so far. By the way, I "
            "just ran 5 kilometers in the \"Run for Hunger\" charity event on March "
            "12th and raised $250 for a local food bank."
        ),
        "expected": (
            ("5", "kilometers", "5 kilometers", "positive", ("asserted", "observed")),
            ("250", "$", "$250", "positive", ("asserted", "observed")),
        ),
    },
    {
        "name": "fitness_challenge",
        "owner": PRIMARY_OWNER,
        "source": (
            "I'm planning to participate in another charity run soon, do you have "
            "any tips on how to stay motivated during training? By the way, I "
            "recently completed a charity fitness challenge in February and "
            "managed to raise $500 for the American Cancer Society, which was a "
            "great feeling!"
        ),
        "expected": (("500", "$", "$500", "positive", ("asserted", "observed")),),
    },
    {
        "name": "cycling_event",
        "owner": PRIMARY_OWNER,
        "source": (
            "I'm planning to participate in a charity cycling event next month "
            "and I want to make sure I'm prepared. Can you give me some tips on "
            "how to train for it? By the way, I've been getting more into charity "
            "events lately - I helped raise $2,000 for a local animal shelter on "
            "January 20th, and it was an amazing experience!"
        ),
        "expected": (("2000", "$", "$2,000", "positive", ("asserted", "observed")),),
    },
    {
        "name": "unrelated_spending",
        "owner": PRIMARY_OWNER,
        "source": "I spent $300 on groceries yesterday.",
        "expected": (("300", "$", "$300", "positive", ("asserted", "observed")),),
    },
    {
        "name": "negated_fundraising",
        "owner": PRIMARY_OWNER,
        "source": "I did not raise $700 for the library fundraiser.",
        "expected": (("700", "$", "$700", "negative", ("asserted", "observed")),),
    },
    {
        "name": "conditional_fundraising",
        "owner": PRIMARY_OWNER,
        "source": "If the sponsor agrees, I will raise $900 for the school.",
        "expected": (("900", "$", "$900", "positive", ("conditional",)),),
    },
    {
        "name": "approximate_fundraising",
        "owner": PRIMARY_OWNER,
        "source": "I raised about $600 for the community garden.",
        "expected": (),
    },
    {
        "name": "incompatible_currency_unit",
        "owner": PRIMARY_OWNER,
        "source": "I raised 100 USD for the music program.",
        "expected": (("100", "USD", "100 USD", "positive", ("asserted", "observed")),),
    },
    {
        "name": "other_owner_fundraising",
        "owner": OTHER_OWNER,
        "source": "I raised $10,000 for the science museum.",
        "expected": (("10000", "$", "$10,000", "positive", ("asserted", "observed")),),
    },
)


QUERY = {
    "predicate_prefixes": (
        "raised",
        "helped_raise",
    ),
    "units": ("$",),
    "group_by": ("unit",),
    "sample_limit": 100,
}

EXPECTED_AGGREGATION = {
    "matched_quantity_records": 4,
    "distinct_count": 1,
    "value_count": 4,
    "total": "3750",
    "minimum": "250",
    "maximum": "2000",
    "evidence_count": 4,
    "values": ("250", "500", "1000", "2000"),
}


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


def _source_identity(root: Path) -> dict[str, str]:
    paths = (
        "benchmarks/diagnostics/quantity_aggregation_e2e.py",
        "src/prme/ingestion/extraction.py",
        "src/prme/ingestion/schema.py",
        "src/prme/ingestion/grounding.py",
        "src/prme/ingestion/pipeline.py",
        "src/prme/ingestion/planning.py",
        "src/prme/models/aggregation.py",
        "src/prme/retrieval/aggregation.py",
        "src/prme/storage/engine.py",
    )
    return {path: _file_digest(root / path) for path in paths}


def register(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.registration.exists():
        raise ValueError("fresh registration path required")
    model_digest = _model_inventory(args.base_url).get(args.model)
    if model_digest is None:
        raise ValueError("registered model is unavailable")
    registration = {
        "schema_version": SCHEMA_VERSION,
        "kind": REGISTRATION_KIND,
        "status": "registered_before_provider_calls",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Authored end-to-end product-path assay over fixed natural-language "
            "sources; not held-out accuracy, answer quality, exhaustive unit "
            "coverage, or competitive evidence."
        ),
        "source": {
            "implementation_sha256": _source_identity(root),
            "cases_sha256": _digest(CASES),
            "query_sha256": _digest(QUERY),
            "expected_aggregation_sha256": _digest(EXPECTED_AGGREGATION),
            "prompt_sha256": hashlib.sha256(
                EXTRACTION_SYSTEM_PROMPT.encode()
            ).hexdigest(),
            "response_schema_sha256": _digest(
                _CitedExtractionResult.model_json_schema()
            ),
        },
        "model": {
            "provider": "ollama",
            "model": args.model,
            "model_digest": model_digest,
            "base_url": args.base_url,
            "temperature": 0.0,
            "max_retries": 3,
            "timeout_seconds": args.timeout,
        },
        "protocol": {
            "cases": len(CASES),
            "expected_quantities": sum(len(case["expected"]) for case in CASES),
            "primary_owner": PRIMARY_OWNER,
            "other_owner": OTHER_OWNER,
            "query": QUERY,
            "expected_aggregation": EXPECTED_AGGREGATION,
            "arm": (
                "one durable local pack; synchronous extraction for every fixed "
                "source; public aggregate_quantities query after ingestion"
            ),
            "resume": (
                "checkpoint before and after every source; admitted events are "
                "recovered by fixed owner, session, case marker, and source"
            ),
            "gates": {
                "completed_cases": len(CASES),
                "failed_attempts": 0,
                "case_contract_failures": 0,
                **EXPECTED_AGGREGATION,
                "stored_set_exhaustive": True,
                "source_extraction_coverage": "unknown",
                "semantic_equivalence": "normalized_exact_only",
                "real_world_coverage": "unknown",
                "unit_conversion": "none",
                "consistency": "complete_for_unchanged_store",
                "required_exclusions": (
                    "selector_mismatch",
                    "condition_filtered",
                    "unit_mismatch",
                ),
            },
        },
    }
    _write(args.registration, registration)
    return registration


def verify_registration(
    registration: dict[str, Any], root: Path
) -> SimpleNamespace:
    source = registration.get("source", {})
    model = registration.get("model", {})
    protocol = registration.get("protocol", {})
    expected_source = {
        "implementation_sha256": _source_identity(root),
        "cases_sha256": _digest(CASES),
        "query_sha256": _digest(QUERY),
        "expected_aggregation_sha256": _digest(EXPECTED_AGGREGATION),
        "prompt_sha256": hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest(),
        "response_schema_sha256": _digest(_CitedExtractionResult.model_json_schema()),
    }
    if (
        registration.get("schema_version") != SCHEMA_VERSION
        or registration.get("kind") != REGISTRATION_KIND
        or registration.get("status") != "registered_before_provider_calls"
        or source != expected_source
        or protocol.get("cases") != len(CASES)
        or protocol.get("expected_quantities")
        != sum(len(case["expected"]) for case in CASES)
        or protocol.get("query") != json.loads(json.dumps(QUERY))
        or protocol.get("expected_aggregation")
        != json.loads(json.dumps(EXPECTED_AGGREGATION))
        or model.get("provider") != "ollama"
        or model.get("temperature") != 0.0
        or model.get("max_retries") != 3
    ):
        raise ValueError("registered quantity aggregation inputs differ")
    base_url = model.get("base_url")
    model_name = model.get("model")
    model_digest = model.get("model_digest")
    timeout = model.get("timeout_seconds")
    if (
        not isinstance(base_url, str)
        or not isinstance(model_name, str)
        or not isinstance(model_digest, str)
        or not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or timeout <= 0
        or _model_inventory(base_url).get(model_name) != model_digest
    ):
        raise ValueError("registered model identity differs")
    return SimpleNamespace(
        provider="ollama",
        model=model_name,
        base_url=base_url,
        timeout=float(timeout),
        model_digest=model_digest,
    )


def _config(run_dir: Path, controls: SimpleNamespace) -> PRMEConfig:
    pack = run_dir / "pack"
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
            provider=controls.provider,
            model=controls.model,
            base_url=controls.base_url,
            api_key=None,
            timeout=controls.timeout,
            temperature=0.0,
            max_retries=3,
        ),
    )


def _node_view(node: Any) -> dict[str, Any]:
    return node.model_dump(
        mode="json",
        include={
            "id",
            "user_id",
            "node_type",
            "epistemic_type",
            "content",
            "metadata",
            "evidence_refs",
        },
    )


def _quantity_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    quantities = []
    for node in row.get("nodes", ()):
        if node.get("node_type") not in {"fact", "decision", "preference"}:
            continue
        metadata = node.get("metadata")
        if not isinstance(metadata, dict):
            continue
        quantity = metadata.get("quantity")
        if not isinstance(quantity, dict):
            continue
        quantities.append(
            {
                "node_id": node.get("id"),
                "value": str(quantity.get("value")),
                "unit": quantity.get("unit"),
                "source_text": quantity.get("source_text"),
                "predicate": metadata.get("predicate"),
                "polarity": metadata.get("polarity"),
                "epistemic_type": node.get("epistemic_type"),
            }
        )
    return quantities


def score_case(case: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    actual = _quantity_nodes(row)
    available = set(range(len(actual)))
    matches = []
    missing = []
    for value, unit, source_text, polarity, epistemic_types in case["expected"]:
        candidates = [
            index
            for index in available
            if Decimal(actual[index]["value"]) == Decimal(value)
            and actual[index]["unit"] == unit
            and actual[index]["source_text"] == source_text
            and actual[index]["polarity"] == polarity
            and actual[index]["epistemic_type"] in epistemic_types
        ]
        if not candidates:
            missing.append(
                {
                    "value": value,
                    "unit": unit,
                    "source_text": source_text,
                    "polarity": polarity,
                    "epistemic_types": epistemic_types,
                }
            )
            continue
        index = candidates[0]
        available.remove(index)
        matches.append(actual[index])
    policy_errors = []
    if row.get("grounding_policy") != "speech_act_v11":
        policy_errors.append("grounding_policy")
    if row.get("materialization_policy") != "speech_act_v12":
        policy_errors.append("materialization_policy")
    return {
        "name": case["name"],
        "owner": case["owner"],
        "source": case["source"],
        "expected_count": len(case["expected"]),
        "actual_count": len(actual),
        "matches": matches,
        "missing": missing,
        "unexpected": [actual[index] for index in sorted(available)],
        "policy_errors": policy_errors,
        "passed": not missing and not available and not policy_errors,
    }


def score(
    rows: dict[str, dict[str, Any]], aggregation: dict[str, Any]
) -> dict[str, Any]:
    cases = [
        score_case(case, rows[case["name"]])
        for case in CASES
        if case["name"] in rows
    ]
    groups = aggregation.get("groups", ())
    group = groups[0] if len(groups) == 1 else {}
    sample_values = sorted(
        (str(sample.get("value")) for sample in group.get("samples", ())),
        key=Decimal,
    )
    exclusions = aggregation.get("exclusions", {})
    required_exclusions = (
        "selector_mismatch",
        "condition_filtered",
        "unit_mismatch",
    )
    aggregation_checks = {
        "matched_quantity_records": aggregation.get("matched_quantity_records")
        == EXPECTED_AGGREGATION["matched_quantity_records"],
        "distinct_count": aggregation.get("distinct_count")
        == EXPECTED_AGGREGATION["distinct_count"],
        "one_group": len(groups) == 1,
        "unit": group.get("normalized_values", {}).get("unit") == "$",
        "value_count": group.get("value_count")
        == EXPECTED_AGGREGATION["value_count"],
        "total": group.get("total") == EXPECTED_AGGREGATION["total"],
        "minimum": group.get("minimum") == EXPECTED_AGGREGATION["minimum"],
        "maximum": group.get("maximum") == EXPECTED_AGGREGATION["maximum"],
        "evidence_count": group.get("evidence_count")
        == EXPECTED_AGGREGATION["evidence_count"],
        "sample_values": tuple(sample_values) == EXPECTED_AGGREGATION["values"],
        "samples_complete": group.get("samples_truncated") is False,
        "stored_set_exhaustive": aggregation.get("stored_set_exhaustive") is True,
        "source_extraction_coverage": aggregation.get("source_extraction_coverage")
        == "unknown",
        "semantic_equivalence": aggregation.get("semantic_equivalence")
        == "normalized_exact_and_predicate_prefix",
        "real_world_coverage": aggregation.get("real_world_coverage") == "unknown",
        "unit_conversion": aggregation.get("unit_conversion") == "none",
        "consistency": aggregation.get("consistency")
        == "complete_for_unchanged_store",
        "required_exclusions": all(exclusions.get(name, 0) >= 1 for name in required_exclusions),
    }
    metrics = {
        "completed_cases": len(cases),
        "case_contract_failures": sum(not case["passed"] for case in cases),
        "aggregation_check_failures": sum(not value for value in aggregation_checks.values()),
        "cases_passed": sum(case["passed"] for case in cases),
    }
    metrics["passed"] = (
        len(cases) == len(CASES)
        and metrics["case_contract_failures"] == 0
        and metrics["aggregation_check_failures"] == 0
    )
    return {
        "metrics": metrics,
        "aggregation_checks": aggregation_checks,
        "cases": cases,
    }


async def _find_admitted_event(engine: MemoryEngine, case: dict[str, Any]) -> str | None:
    events = await engine.get_events(
        case["owner"], session_id=SESSION_ID, limit=len(CASES) + 10
    )
    matches = [
        event
        for event in events
        if event.content == case["source"]
        and (event.metadata or {}).get("benchmark_case") == case["name"]
    ]
    if len(matches) > 1:
        raise ValueError(f"duplicate admitted events for {case['name']}")
    return str(matches[0].id) if matches else None


async def _completed_row(
    engine: MemoryEngine, case: dict[str, Any], event_id: str
) -> dict[str, Any] | None:
    extraction = await engine.get_extraction(event_id, user_id=case["owner"])
    plan = await engine._event_store.get_derivation_plan(
        event_id, user_id=case["owner"]
    )
    if extraction is None or plan is None:
        return None
    status = await engine.extraction_status(event_id, user_id=case["owner"])
    if status is None or status.status != "complete":
        return None
    nodes = await engine.get_event_nodes(event_id, user_id=case["owner"])
    return {
        "name": case["name"],
        "owner": case["owner"],
        "source": case["source"],
        "event_id": event_id,
        "grounding_policy": extraction.grounding_policy,
        "materialization_policy": plan.materialization_policy,
        "nodes": [_node_view(node) for node in nodes],
    }


async def execute(
    registration_path: Path,
    state_path: Path,
    result_path: Path,
    run_dir: Path,
    root: Path,
) -> dict[str, Any]:
    if result_path.exists():
        raise ValueError("fresh result path required")
    registration = json.loads(registration_path.read_text())
    controls = verify_registration(registration, root)
    identity = {
        "registration_sha256": _file_digest(registration_path),
        "model_digest": controls.model_digest,
        "run_dir": str(run_dir.resolve()),
    }
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        if (run_dir / "pack" / "memory.duckdb").exists():
            raise ValueError("fresh run directory required without resume state")
        state = {
            "identity": identity,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "current_case": None,
            "rows": {},
            "failed_attempts": [],
            "complete": False,
        }
    if state.get("identity") != identity:
        raise ValueError("quantity aggregation resume identity differs")
    wanted = {case["name"] for case in CASES}
    if not set(state.get("rows", {})) <= wanted:
        raise ValueError("quantity aggregation state contains unrelated cases")
    _write(state_path, state)

    async with MemoryEngine.open(_config(run_dir, controls)) as engine:
        engine._pipeline._retry_delays = ()
        for index, case in enumerate(CASES):
            name = case["name"]
            if name not in state["rows"]:
                state["current_case"] = name
                _write(state_path, state)
                try:
                    event_id = await _find_admitted_event(engine, case)
                    if event_id is None:
                        event_id = await engine.ingest(
                            case["source"],
                            user_id=case["owner"],
                            session_id=SESSION_ID,
                            metadata={"benchmark_case": name},
                            wait_for_extraction=True,
                        )
                    row = await _completed_row(engine, case, event_id)
                    if row is None:
                        await engine.retry_extraction(
                            event_id, user_id=case["owner"]
                        )
                        await engine.process_extractions(
                            user_id=case["owner"],
                            limit=1,
                            budget_ms=max(1000.0, controls.timeout * 1000 + 1000),
                        )
                        row = await _completed_row(engine, case, event_id)
                    if row is None:
                        raise RuntimeError("extraction did not complete")
                    state["rows"][name] = row
                    state["current_case"] = None
                except Exception as exc:
                    state["failed_attempts"].append(
                        {
                            "case": name,
                            "error_type": type(exc).__name__,
                            "at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    _write(state_path, state)
                    raise
                _write(state_path, state)
            print(f"Case {index + 1}/{len(CASES)}: {name}", flush=True)

        aggregation_model = await engine.aggregate_quantities(
            QuantityAggregationQuery.model_validate(QUERY),
            user_id=PRIMARY_OWNER,
            batch_size=2,
        )
        aggregation = aggregation_model.model_dump(mode="json")

    if _model_inventory(controls.base_url).get(controls.model) != controls.model_digest:
        raise ValueError("model digest changed during quantity aggregation assay")
    state["aggregation"] = aggregation
    state["complete"] = True
    state["current_case"] = None
    _write(state_path, state)

    scored = score(state["rows"], aggregation)
    scored["metrics"]["failed_attempts"] = len(state["failed_attempts"])
    scored["metrics"]["passed"] = (
        scored["metrics"]["passed"] and not state["failed_attempts"]
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "registration_sha256": identity["registration_sha256"],
        "model": registration["model"],
        **scored,
        "aggregation": aggregation,
        "decision": (
            "accept_explicit_quantity_aggregation_product_path"
            if scored["metrics"]["passed"]
            else "reject_explicit_quantity_aggregation_product_path"
        ),
        "limits": registration["claim_boundary"],
        "state_sha256": _file_digest(state_path),
    }
    result["result_sha256"] = _digest(result)
    _write(result_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--model", default="prme-qwen3.5:35b-a3b-8k")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-dir", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.register:
        value = register(args, root)
        print(
            json.dumps(
                {"protocol": value["protocol"], "model": value["model"]},
                indent=2,
            )
        )
        return
    if args.state is None or args.output is None or args.run_dir is None:
        raise ValueError("--state, --output, and --run-dir are required for execution")
    value = asyncio.run(
        execute(
            args.registration.resolve(),
            args.state.resolve(),
            args.output.resolve(),
            args.run_dir.resolve(),
            root,
        )
    )
    print(
        json.dumps(
            {
                "metrics": value["metrics"],
                "decision": value["decision"],
                "result_sha256": value["result_sha256"],
            },
            indent=2,
        )
    )
    if not value["metrics"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

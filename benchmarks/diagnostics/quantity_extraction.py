"""Preregistered extraction assay for source-grounded quantitative claims.

The assay checks that exact measured or counted values remain attached to the
claim they qualify, while numeric identifiers and unsupported notation do not
become aggregation inputs. It is authored development evidence, not a held-out
accuracy or competitive benchmark.
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

from benchmarks.diagnostics.entity_references import run as run_extraction
from benchmarks.diagnostics.speech_act_extraction import _model_inventory
from prme.ingestion.extraction import EXTRACTION_SYSTEM_PROMPT, _CitedExtractionResult
from prme.ingestion.schema import ExtractedFact, ExtractedQuantity, ExtractedRelationship


REGISTRATION_KIND = "quantity-extraction-registration"
RESULT_KIND = "quantity-extraction-result"
SCHEMA_VERSION = 1

CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "currency_target",
        "source": "I raised $500 for the animal shelter.",
        "expected": (("500", "$", "$500", ("$500", "animal shelter"), "positive", ("observed", "asserted")),),
    },
    {
        "name": "named_currency_target",
        "source": "I donated 250 USD to the food bank.",
        "expected": (("250", "USD", "250 USD", ("250 USD", "food bank"), "positive", ("observed", "asserted")),),
    },
    {
        "name": "count_target",
        "source": "I shipped 1234 packages for Project Atlas.",
        "expected": (("1234", "packages", "1234 packages", ("1234 packages", "Project Atlas"), "positive", ("observed", "asserted")),),
    },
    {
        "name": "decimal_measure",
        "source": "I drank 1.5 liters of water during the race.",
        "expected": (("1.5", "liters", "1.5 liters", ("1.5 liters", "water"), "positive", ("observed", "asserted")),),
    },
    {
        "name": "signed_measure",
        "source": "The battery delivered -3.25 volts during the test.",
        "expected": (("-3.25", "volts", "-3.25 volts", ("-3.25 volts",), "positive", ("observed", "asserted")),),
    },
    {
        "name": "dimensionless_count",
        "source": "My final score was 3.",
        "expected": (("3", "1", "3", ("3",), "positive", ("observed", "asserted")),),
    },
    {
        "name": "two_independent_measures",
        "source": "I ran 5 kilometers and raised $250 for the food bank.",
        "expected": (
            ("5", "kilometers", "5 kilometers", ("5 kilometers",), "positive", ("observed", "asserted")),
            ("250", "$", "$250", ("$250", "food bank"), "positive", ("observed", "asserted")),
        ),
    },
    {
        "name": "decimal_currency",
        "source": "I paid $12.50 for lunch.",
        "expected": (("12.50", "$", "$12.50", ("$12.50", "lunch"), "positive", ("observed", "asserted")),),
    },
    {
        "name": "compact_unit",
        "source": "I lifted 5kg during the training session.",
        "expected": (("5", "kg", "5kg", ("5kg",), "positive", ("observed", "asserted")),),
    },
    {
        "name": "negated_amount",
        "source": "I did not raise $500 for the shelter.",
        "expected": (("500", "$", "$500", ("$500", "shelter"), "negative", ("observed", "asserted")),),
    },
    {
        "name": "conditional_amount",
        "source": "If the campaign succeeds, I will donate $500 to the shelter.",
        "expected": (("500", "$", "$500", ("$500", "shelter"), "positive", ("conditional",)),),
    },
    {"name": "approximation", "source": "I raised about $500 for the shelter.", "expected": ()},
    {"name": "range", "source": "The budget is between $400 and $500.", "expected": ()},
    {"name": "software_version", "source": "I installed CUDA 12.4 yesterday.", "expected": ()},
    {"name": "calendar_date", "source": "The fundraiser happened on January 20th.", "expected": ()},
    {"name": "identifier", "source": "My ticket number is 1250.", "expected": ()},
    {"name": "clock_time", "source": "The call starts at 3:30 pm.", "expected": ()},
    {"name": "model_name", "source": "I bought an iPhone 15.", "expected": ()},
    {"name": "address", "source": "The office is at 500 Main Street.", "expected": ()},
    {"name": "ordinal", "source": "I finished in 2nd place.", "expected": ()},
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


def _source_identity(root: Path) -> dict[str, str]:
    paths = (
        "benchmarks/diagnostics/quantity_extraction.py",
        "benchmarks/diagnostics/entity_references.py",
        "src/prme/ingestion/extraction.py",
        "src/prme/ingestion/schema.py",
        "src/prme/ingestion/grounding.py",
        "src/prme/ingestion/pipeline.py",
        "src/prme/ingestion/planning.py",
    )
    return {path: _file_digest(root / path) for path in paths}


def register(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    if args.registration.exists():
        raise ValueError("fresh registration path required")
    inventory = _model_inventory(args.base_url)
    model_digest = inventory.get(args.model)
    if model_digest is None:
        raise ValueError("registered model is unavailable")
    registration = {
        "schema_version": SCHEMA_VERSION,
        "kind": REGISTRATION_KIND,
        "status": "registered_before_provider_calls",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": (
            "Authored development assay for source-grounded quantitative "
            "extraction; not held-out accuracy, answer quality, or a "
            "competitive result."
        ),
        "source": {
            "implementation_sha256": _source_identity(root),
            "cases_sha256": _digest(CASES),
            "prompt_sha256": hashlib.sha256(
                EXTRACTION_SYSTEM_PROMPT.encode()
            ).hexdigest(),
            "response_schema_sha256": _digest(
                _CitedExtractionResult.model_json_schema()
            ),
            "fact_schema_sha256": _digest(ExtractedFact.model_json_schema()),
            "quantity_schema_sha256": _digest(
                ExtractedQuantity.model_json_schema()
            ),
            "relationship_schema_sha256": _digest(
                ExtractedRelationship.model_json_schema()
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
            "arm": "fresh engine and one extraction per fixed source",
            "resume": "checkpoint after every completed case; no selective retries",
            "gates": {
                "completed_cases": len(CASES),
                "failed_attempts": 0,
                "missing_expected_quantities": 0,
                "unexpected_quantities": 0,
                "field_mismatches": 0,
                "policy_errors": 0,
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
        "prompt_sha256": hashlib.sha256(EXTRACTION_SYSTEM_PROMPT.encode()).hexdigest(),
        "response_schema_sha256": _digest(_CitedExtractionResult.model_json_schema()),
        "fact_schema_sha256": _digest(ExtractedFact.model_json_schema()),
        "quantity_schema_sha256": _digest(ExtractedQuantity.model_json_schema()),
        "relationship_schema_sha256": _digest(
            ExtractedRelationship.model_json_schema()
        ),
    }
    expected_gates = {
        "completed_cases": len(CASES),
        "failed_attempts": 0,
        "missing_expected_quantities": 0,
        "unexpected_quantities": 0,
        "field_mismatches": 0,
        "policy_errors": 0,
    }
    if (
        registration.get("schema_version") != SCHEMA_VERSION
        or registration.get("kind") != REGISTRATION_KIND
        or registration.get("status") != "registered_before_provider_calls"
        or source != expected_source
        or protocol.get("cases") != len(CASES)
        or protocol.get("expected_quantities")
        != sum(len(case["expected"]) for case in CASES)
        or protocol.get("gates") != expected_gates
        or model.get("provider") != "ollama"
        or model.get("temperature") != 0.0
        or model.get("max_retries") != 3
    ):
        raise ValueError("registered quantity extraction inputs differ")
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


def _quantities(row: dict[str, Any]) -> list[dict[str, Any]]:
    quantities = []
    for node in row.get("evaluated_nodes", []):
        if node.get("node_type") not in {"fact", "decision", "preference"}:
            continue
        metadata = node.get("metadata")
        if not isinstance(metadata, dict) or not isinstance(
            metadata.get("quantity"), dict
        ):
            continue
        quantity = metadata["quantity"]
        quantities.append(
            {
                "value": str(quantity.get("value")),
                "unit": quantity.get("unit"),
                "source_text": quantity.get("source_text"),
                "object": metadata.get("object"),
                "predicate": metadata.get("predicate"),
                "polarity": metadata.get("polarity"),
                "epistemic_type": node.get("epistemic_type"),
            }
        )
    return quantities


def score_case(case: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    actual = _quantities(row)
    expected = list(case["expected"])
    available = set(range(len(actual)))
    missing = []
    mismatches = []
    matches = []
    for target in expected:
        value, unit, source_text, fragments, polarity, epistemic = target
        candidates = [
            index
            for index in available
            if Decimal(actual[index]["value"]) == Decimal(value)
            and actual[index]["unit"] == unit
            and actual[index]["source_text"] == source_text
        ]
        if not candidates:
            missing.append(
                {"value": value, "unit": unit, "source_text": source_text}
            )
            continue
        index = candidates[0]
        available.remove(index)
        item = actual[index]
        field_errors = []
        object_value = item.get("object")
        if not isinstance(object_value, str) or any(
            fragment.casefold() not in object_value.casefold()
            for fragment in fragments
        ):
            field_errors.append("object")
        if item.get("polarity") != polarity:
            field_errors.append("polarity")
        if item.get("epistemic_type") not in epistemic:
            field_errors.append("epistemic_type")
        if field_errors:
            mismatches.append({"quantity": item, "fields": field_errors})
        else:
            matches.append(item)
    policy_errors = []
    if row.get("extraction_grounding_policy") != "speech_act_v10":
        policy_errors.append("grounding_policy")
    if row.get("materialization_policy") != "speech_act_v12":
        policy_errors.append("materialization_policy")
    return {
        "name": case["name"],
        "source": case["source"],
        "expected_count": len(expected),
        "actual_count": len(actual),
        "matches": matches,
        "missing": missing,
        "unexpected": [actual[index] for index in sorted(available)],
        "field_mismatches": mismatches,
        "policy_errors": policy_errors,
        "passed": not missing
        and not available
        and not mismatches
        and not policy_errors,
    }


def score(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    scored = [score_case(case, rows[case["name"]]) for case in CASES if case["name"] in rows]
    metrics = {
        "completed_cases": len(scored),
        "failed_attempts": 0,
        "missing_expected_quantities": sum(len(row["missing"]) for row in scored),
        "unexpected_quantities": sum(len(row["unexpected"]) for row in scored),
        "field_mismatches": sum(len(row["field_mismatches"]) for row in scored),
        "policy_errors": sum(len(row["policy_errors"]) for row in scored),
        "cases_passed": sum(row["passed"] for row in scored),
    }
    metrics["passed"] = metrics == {
        "completed_cases": len(CASES),
        "failed_attempts": 0,
        "missing_expected_quantities": 0,
        "unexpected_quantities": 0,
        "field_mismatches": 0,
        "policy_errors": 0,
        "cases_passed": len(CASES),
    }
    return {"metrics": metrics, "cases": scored}


async def execute(
    registration_path: Path,
    state_path: Path,
    result_path: Path,
    root: Path,
) -> dict[str, Any]:
    if result_path.exists():
        raise ValueError("fresh result path required")
    registration = json.loads(registration_path.read_text())
    controls = verify_registration(registration, root)
    identity = {
        "registration_sha256": _file_digest(registration_path),
        "model_digest": controls.model_digest,
    }
    state = (
        json.loads(state_path.read_text())
        if state_path.exists()
        else {
            "identity": identity,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "rows": {},
            "failed_attempts": [],
            "complete": False,
        }
    )
    if state.get("identity") != identity:
        raise ValueError("quantity extraction resume identity differs")
    wanted = {case["name"] for case in CASES}
    if not set(state.get("rows", {})) <= wanted:
        raise ValueError("quantity extraction state contains unrelated cases")
    _write(state_path, state)
    for index, case in enumerate(CASES):
        name = case["name"]
        if name not in state["rows"]:
            try:
                report = await run_extraction(
                    controls,
                    cases=[(name, case["source"], {})],
                )
                row = report["cases"][0]
                if row.get("case") != name or row.get("source") != case["source"]:
                    raise ValueError("extraction case identity differs")
                state["rows"][name] = row
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
        print(f"Case {index + 1}/{len(CASES)}", flush=True)
    if _model_inventory(controls.base_url).get(controls.model) != controls.model_digest:
        raise ValueError("model digest changed during quantity extraction")
    state["complete"] = True
    _write(state_path, state)
    scored = score(state["rows"])
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
        "decision": (
            "advance_to_cross_model_confirmation"
            if scored["metrics"]["passed"]
            else "reject_quantity_extraction_candidate"
        ),
        "state_sha256": _file_digest(state_path),
    }
    result["result_sha256"] = _digest(result)
    _write(result_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash:cloud")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.register:
        value = register(args, root)
        print(json.dumps({"protocol": value["protocol"], "model": value["model"]}, indent=2))
        return
    if args.state is None or args.output is None:
        raise ValueError("--state and --output are required for execution")
    value = asyncio.run(
        execute(
            args.registration.resolve(),
            args.state.resolve(),
            args.output.resolve(),
            root,
        )
    )
    print(json.dumps({"metrics": value["metrics"], "decision": value["decision"], "result_sha256": value["result_sha256"]}, indent=2))
    if not value["metrics"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

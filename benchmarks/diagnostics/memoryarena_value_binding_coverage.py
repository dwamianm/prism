"""Audit MemoryArena typed city-binding coverage without invoking a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.diagnostics.memoryarena_server import _plan_value_bindings
from benchmarks.integrations.run_memoryarena_travel import (
    _check_upstream,
    _converted_rows,
    _git,
    _identity,
    _load_dataset,
)


CITY_CATALOG = (
    "env/env_systems/travel_planner_env/database/background/"
    "citySet_with_states.txt"
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()


def load_catalog(path: Path) -> tuple[str, ...]:
    """Load exact qualified city forms from MemoryArena's independent catalog."""
    values = []
    for line in path.read_text().splitlines():
        city, separator, state = line.partition("\t")
        if not separator or not city.strip() or not state.strip():
            raise ValueError(f"malformed city catalog line: {line!r}")
        values.append(f"{city.strip()}({state.strip()})")
    if len(values) != len(set(values)):
        raise ValueError("city catalog contains duplicate qualified values")
    return tuple(values)


def _expected_presentations(value: str, catalog: tuple[str, ...]) -> tuple[str, ...]:
    positions = []
    for presentation in catalog:
        start = value.find(presentation)
        if start >= 0:
            positions.append((start, presentation))
    return tuple(presentation for _, presentation in sorted(positions))


def audit_rows(
    rows: list[dict[str, Any]], catalog: tuple[str, ...]
) -> dict[str, Any]:
    """Compare emitted bindings with catalog values in every reference plan."""
    field_count = 0
    qualified_field_count = 0
    compound_qualified_field_count = 0
    expected_occurrence_count = 0
    expected_binding_count = 0
    emitted_binding_count = 0
    record_count = 0
    missing = []
    unexpected = []

    for row in rows:
        records = [("base", 0, row["base_person"]["daily_plans"])]
        records.extend(
            ("answer", index, answer["daily_plans"])
            for index, answer in enumerate(row["answers"], start=1)
        )
        for record_kind, record_index, days in records:
            record_count += 1
            expected: list[str] = []
            plan_lines = []
            for day in days:
                value = day["current_city"]
                if not isinstance(value, str):
                    raise TypeError("current_city must be text")
                field_count += 1
                plan_lines.append(f"Current City: {value}")
                field_values = _expected_presentations(value, catalog)
                if field_values:
                    qualified_field_count += 1
                    if value.strip().casefold().startswith("from "):
                        compound_qualified_field_count += 1
                expected_occurrence_count += len(field_values)
                for presentation in field_values:
                    if presentation not in expected:
                        expected.append(presentation)

            plan = "\n".join(plan_lines)
            emitted = [item.presentation for item in _plan_value_bindings(plan)]
            expected_binding_count += len(expected)
            emitted_binding_count += len(emitted)
            missing_values = [value for value in expected if value not in emitted]
            unexpected_values = [value for value in emitted if value not in expected]
            identity = {
                "group_id": row["id"],
                "record_index": record_index,
                "record_kind": record_kind,
            }
            if missing_values:
                missing.append({**identity, "presentations": missing_values})
            if unexpected_values:
                unexpected.append({**identity, "presentations": unexpected_values})

    return {
        "record_count": record_count,
        "current_city_field_count": field_count,
        "qualified_field_count": qualified_field_count,
        "compound_qualified_field_count": compound_qualified_field_count,
        "expected_qualified_occurrence_count": expected_occurrence_count,
        "expected_distinct_binding_count": expected_binding_count,
        "emitted_distinct_binding_count": emitted_binding_count,
        "missing": missing,
        "unexpected": unexpected,
        "complete": (
            expected_occurrence_count > 0
            and expected_binding_count == emitted_binding_count
            and not missing
            and not unexpected
        ),
    }


def run(*, upstream: Path, output: Path) -> dict[str, Any]:
    _check_upstream(upstream)
    dataset, dataset_identity = _load_dataset()
    rows = _converted_rows(dataset, upstream)
    catalog_path = upstream / CITY_CATALOG
    coverage = audit_rows(rows, load_catalog(catalog_path))
    result = {
        "schema_version": 1,
        "kind": "memoryarena-value-binding-coverage-audit",
        "coverage": coverage,
        "gates": {"complete": coverage["complete"]},
        "evidence": {
            "dataset": dataset_identity,
            "upstream_revision": _git(upstream, "rev-parse", "HEAD"),
            "city_catalog": _identity(catalog_path),
            "adapter": _identity(
                Path(__file__).with_name("memoryarena_server.py")
            ),
            "audit": _identity(Path(__file__)),
        },
        "limits": [
            "This audits reference-plan current-city fields, not arbitrary generated text.",
            "The independent MemoryArena city catalog defines expected qualified values.",
            "Complete binding coverage does not prove lookup correctness or answer quality.",
        ],
    }
    result["evidence"]["content_sha256"] = hashlib.sha256(
        _canonical({"coverage": coverage, "dataset": dataset_identity})
    ).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_canonical(result) + b"\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(upstream=args.upstream, output=args.output), indent=2))


if __name__ == "__main__":
    main()

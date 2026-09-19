"""Audit exact structured presentation restoration on MemoryArena references."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from benchmarks.diagnostics.memoryarena_server import (
    _current_city_components,
    _plan_value_bindings,
)
from benchmarks.integrations.run_memoryarena_travel import (
    _check_upstream,
    _converted_rows,
    _git,
    _identity,
    _load_dataset,
)
from prme import ToolArgumentBindingUse, ToolArgumentResolution


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()


def audit_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Restore every record's qualified city components through the public API."""
    record_count = 0
    target_count = 0
    replacement_count = 0
    source_argument_pointer_count = 0
    failures = []

    for row in rows:
        records = [("base", 0, row["base_person"]["daily_plans"])]
        records.extend(
            ("answer", index, answer["daily_plans"])
            for index, answer in enumerate(row["answers"], start=1)
        )
        for record_kind, record_index, days in records:
            record_count += 1
            plan = "\n".join(
                f"Current City: {day['current_city']}" for day in days
            )
            bindings = _plan_value_bindings(plan)
            by_presentation = {item.presentation: item for item in bindings}
            source_id = uuid5(
                NAMESPACE_URL,
                f"memoryarena:{row['id']}:{record_kind}:{record_index}",
            )
            uses = tuple(
                ToolArgumentBindingUse(
                    json_pointer=f"/source/{index}",
                    operation="already_lookup",
                    presentation=binding.presentation,
                    lookup=binding.lookup,
                    kind=binding.kind,
                    source_node_ids=(source_id,),
                    source_references=(binding.reference,),
                )
                for index, binding in enumerate(bindings)
            )
            fields = []
            expected_fields = []
            targets = {}
            for field_index, day in enumerate(days):
                components = list(_current_city_components(day["current_city"]))
                lookup_components = []
                for component_index, component in enumerate(components):
                    binding = by_presentation.get(component)
                    if binding is None:
                        lookup_components.append(component)
                        continue
                    lookup_components.append(binding.lookup)
                    targets[
                        f"/fields/{field_index}/components/{component_index}"
                    ] = "city"
                fields.append({
                    "components": lookup_components,
                    "original_field": day["current_city"],
                })
                expected_fields.append({
                    "components": components,
                    "original_field": day["current_city"],
                })

            first_lookup = bindings[0].lookup if bindings else "unchanged"
            document = {
                "fields": fields,
                "untargeted_lookup": first_lookup,
                "free_text": f"Reference text containing {first_lookup} remains intact.",
            }
            original = json.loads(json.dumps(document))
            target_count += len(targets)
            try:
                restored = ToolArgumentResolution(
                    arguments={}, binding_uses=uses
                ).restore_presentations(document, targets)
                replacement_count += len(restored.replacements)
                source_argument_pointer_count += sum(
                    len(item.source_argument_pointers)
                    for item in restored.replacements
                )
                if restored.document["fields"] != expected_fields:
                    raise ValueError("restored structured fields differ")
                if restored.document["untargeted_lookup"] != first_lookup:
                    raise ValueError("untargeted complete value changed")
                if restored.document["free_text"] != document["free_text"]:
                    raise ValueError("free text changed")
                if document != original:
                    raise ValueError("caller document was mutated")
            except (TypeError, ValueError) as exc:
                failures.append({
                    "group_id": row["id"],
                    "record_index": record_index,
                    "record_kind": record_kind,
                    "error": str(exc),
                })

    return {
        "record_count": record_count,
        "target_count": target_count,
        "replacement_count": replacement_count,
        "source_argument_pointer_count": source_argument_pointer_count,
        "failure_count": len(failures),
        "failures": failures,
        "complete": target_count > 0 and replacement_count == target_count and not failures,
    }


def run(*, upstream: Path, output: Path) -> dict[str, Any]:
    _check_upstream(upstream)
    dataset, dataset_identity = _load_dataset()
    coverage = audit_rows(_converted_rows(dataset, upstream))
    result = {
        "schema_version": 1,
        "kind": "memoryarena-structured-presentation-audit",
        "coverage": coverage,
        "gates": {"complete": coverage["complete"]},
        "evidence": {
            "dataset": dataset_identity,
            "upstream_revision": _git(upstream, "rev-parse", "HEAD"),
            "adapter": _identity(
                Path(__file__).with_name("memoryarena_server.py")
            ),
            "audit": _identity(Path(__file__)),
            "value_binding_implementation": _identity(
                Path(__file__).parents[2]
                / "src/prme/models/value_bindings.py"
            ),
        },
        "limits": [
            "This is a deterministic reference-corpus mechanism audit with no model calls.",
            "It targets atomic current-city components, not arbitrary generated prose.",
            "Successful restoration does not establish answer-quality improvement.",
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

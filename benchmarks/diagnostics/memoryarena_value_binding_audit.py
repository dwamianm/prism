"""Audit exact qualified values and tool-boundary resolution in a travel trial.

The audit reads the registered MemoryArena cohort and immutable per-traveler
checkpoints.  It does not call a model or reinterpret itinerary validity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from benchmarks.integrations import run_memoryarena_travel as runner


SLOTS = (
    "breakfast",
    "lunch",
    "dinner",
    "accommodation",
    "transportation",
    "attraction",
)
_QUALIFIED_VALUE = re.compile(r"\([^()\n]+\)")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _leaf_values(value: Any, pointer: str = "") -> dict[str, Any]:
    """Return JSON scalar leaves under stable RFC 6901 pointers."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("Tool argument object keys must be strings")
            result.update(_leaf_values(child, f"{pointer}/{_pointer_token(key)}"))
        return result
    if isinstance(value, list):
        result = {}
        for index, child in enumerate(value):
            result.update(_leaf_values(child, f"{pointer}/{index}"))
        return result
    return {pointer: value}


def _qualified_arguments(arguments: Any) -> list[tuple[str, str]]:
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be objects")
    return [
        (pointer, value)
        for pointer, value in _leaf_values(arguments).items()
        if isinstance(value, str) and _QUALIFIED_VALUE.search(value)
    ]


def _expected_guidance(binding_uses: list[dict[str, Any]]) -> dict[str, Any] | None:
    values = set()
    for use in binding_uses:
        if not isinstance(use, dict):
            raise ValueError("Tool binding uses must be objects")
        kind = use.get("kind")
        presentation = use.get("presentation")
        if not isinstance(kind, str) or not isinstance(presentation, str):
            raise ValueError("Tool binding use is incomplete")
        values.add((kind, presentation))
    if not values:
        return None
    return {
        "schema_version": 1,
        "instruction": (
            "When presenting this tool result, preserve these exact source-backed "
            "values. This metadata is separate from the tool data."
        ),
        "values": [
            {"kind": kind, "presentation": presentation}
            for kind, presentation in sorted(values)
        ],
    }


def _truth(cohort: list[dict[str, Any]]) -> dict[tuple[int, int, int, str], str]:
    result = {}
    for group in cohort:
        base = {
            row.get("day", row.get("days")): row
            for row in group["base_person"]["daily_plans"]
        }
        for answer in group["answers"]:
            for row in answer["daily_plans"]:
                day = row.get("day", row.get("days"))
                base_row = base[day]
                for slot in SLOTS:
                    value = row[slot]
                    if (
                        _normalized(value) != _normalized(base_row[slot])
                        and _QUALIFIED_VALUE.search(value)
                    ):
                        result[(group["id"], answer["round_idx"], day, slot)] = value
    return result


def audit_value_bindings(
    cohort: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    *,
    registration_sha256: str,
) -> dict[str, Any]:
    """Return exact targeted counts after enforcing complete paired coverage."""
    expected = {
        (arm, group["id"], answer["round_idx"])
        for arm in ("native_full_history", "prme")
        for group in cohort
        for answer in group["answers"]
    }
    found: dict[tuple[str, int, int], dict[str, Any]] = {}
    for number, row in enumerate(checkpoints, start=1):
        if row.get("registration_sha256") != registration_sha256:
            raise ValueError(f"Checkpoint registration differs at line {number}")
        key = (row.get("arm"), row.get("group_id"), row.get("person_idx"))
        if key in found:
            raise ValueError(f"Duplicate checkpoint identity at line {number}")
        found[key] = row
    if set(found) != expected:
        missing = sorted(expected - set(found))
        extra = sorted(set(found) - expected)
        raise ValueError(f"Checkpoint coverage differs: missing={missing}, extra={extra}")

    truth = _truth(cohort)
    actual = {}
    qualified_calls = []
    qualified_travelers = set()
    executed_qualified = []
    executed_qualified_travelers = set()
    replacement_occurrences = []
    replacement_travelers = set()
    binding_use_occurrences = []
    binding_use_operations = {"replaced": 0, "already_lookup": 0}
    guidance_record_count = 0
    guidance_travelers = set()
    guidance_evidence_modes = set()
    resolution_record_count = 0
    for (arm, group_id, person_idx), checkpoint in found.items():
        if arm != "prme":
            continue
        person = checkpoint.get("person") or {}
        plan_by_day = {
            row.get("day", row.get("days")): row
            for row in (person.get("plan") or [])
            if isinstance(row, dict)
        }
        for key in truth:
            truth_group, truth_person, day, slot = key
            if (truth_group, truth_person) == (group_id, person_idx):
                actual[key] = plan_by_day.get(day, {}).get(slot)

        scratchpad = (checkpoint.get("scratchpad") or {}).get("scratchpad") or []
        trace_calls = []
        trace_results = []
        for step_index, step in enumerate(scratchpad):
            calls = step.get("tool_calls") or []
            results = step.get("tool_results") or []
            if len(calls) != len(results):
                raise ValueError(
                    "Saved tool calls and results differ for "
                    f"{group_id}/{person_idx}/{step_index}"
                )
            for call_index, (call, tool_result) in enumerate(
                zip(calls, results, strict=True)
            ):
                arguments = call.get("args") or {}
                trace_calls.append((call.get("name"), arguments))
                trace_results.append((tool_result.get("name"), tool_result.get("result")))
                values = _qualified_arguments(arguments)
                if values:
                    qualified_calls.append(
                        {
                            "group_id": group_id,
                            "person_idx": person_idx,
                            "step_index": step_index,
                            "call_index": call_index,
                            "tool": call.get("name"),
                            "argument_pointers": sorted(pointer for pointer, _ in values),
                        }
                    )
                    qualified_travelers.add((group_id, person_idx))

        resolutions = person.get("tool_argument_resolutions")
        if not isinstance(resolutions, list):
            raise ValueError(
                f"PRME checkpoint lacks resolution records for {group_id}/{person_idx}"
            )
        if len(resolutions) != len(trace_calls):
            raise ValueError(
                "Tool trace and resolution coverage differ for "
                f"{group_id}/{person_idx}: {len(trace_calls)} != {len(resolutions)}"
            )
        for resolution_index, (resolution, trace_call) in enumerate(
            zip(resolutions, trace_calls, strict=True)
        ):
            resolution_record_count += 1
            if not isinstance(resolution, dict):
                raise ValueError("Tool resolution records must be objects")
            tool_name = resolution.get("tool_name")
            original = resolution.get("original_arguments")
            resolved = resolution.get("resolved_arguments")
            replacements = resolution.get("replacements")
            if (
                not isinstance(original, dict)
                or not isinstance(resolved, dict)
                or not isinstance(replacements, list)
            ):
                raise ValueError("Tool resolution record is incomplete")
            if trace_call != (tool_name, original):
                raise ValueError(
                    "Saved model tool call differs from resolver input for "
                    f"{group_id}/{person_idx}/{resolution_index}"
                )
            trace_result_name, trace_result = trace_results[resolution_index]
            if trace_result_name != tool_name or not isinstance(trace_result, str):
                raise ValueError(
                    "Saved tool result differs from resolver call for "
                    f"{group_id}/{person_idx}/{resolution_index}"
                )

            original_leaves = _leaf_values(original)
            resolved_leaves = _leaf_values(resolved)
            changed = {
                pointer: (original_leaves.get(pointer), resolved_leaves.get(pointer))
                for pointer in original_leaves.keys() | resolved_leaves.keys()
                if original_leaves.get(pointer) != resolved_leaves.get(pointer)
            }
            declared = {}
            for replacement in replacements:
                if not isinstance(replacement, dict):
                    raise ValueError("Tool replacements must be objects")
                pointer = replacement.get("json_pointer")
                presentation = replacement.get("presentation")
                lookup = replacement.get("lookup")
                if not all(isinstance(item, str) for item in (pointer, presentation, lookup)):
                    raise ValueError("Tool replacement is incomplete")
                if pointer in declared:
                    raise ValueError("Tool replacement pointers must be unique")
                declared[pointer] = (presentation, lookup)
                replacement_occurrences.append(
                    {
                        "group_id": group_id,
                        "person_idx": person_idx,
                        "resolution_index": resolution_index,
                        "tool": tool_name,
                        "json_pointer": pointer,
                        "presentation": presentation,
                        "lookup": lookup,
                    }
                )
                replacement_travelers.add((group_id, person_idx))
            if changed != declared:
                raise ValueError(
                    "Resolver mutations differ from declared replacements for "
                    f"{group_id}/{person_idx}/{resolution_index}"
                )

            has_guidance_evidence = (
                "binding_uses" in resolution
                or "result_presentation_guidance" in resolution
            )
            guidance_evidence_modes.add(has_guidance_evidence)
            if has_guidance_evidence:
                binding_uses = resolution.get("binding_uses")
                guidance = resolution.get("result_presentation_guidance")
                if not isinstance(binding_uses, list):
                    raise ValueError("Tool resolution lacks binding-use evidence")
                declared_uses = set()
                for use in binding_uses:
                    if not isinstance(use, dict):
                        raise ValueError("Tool binding uses must be objects")
                    pointer = use.get("json_pointer")
                    operation = use.get("operation")
                    presentation = use.get("presentation")
                    lookup = use.get("lookup")
                    kind = use.get("kind")
                    if (
                        not all(
                            isinstance(item, str)
                            for item in (pointer, presentation, lookup, kind)
                        )
                        or operation not in binding_use_operations
                    ):
                        raise ValueError("Tool binding use is incomplete")
                    identity = (pointer, operation, presentation, lookup, kind)
                    if identity in declared_uses:
                        raise ValueError("Tool binding uses must be unique")
                    declared_uses.add(identity)
                    if operation == "replaced":
                        if changed.get(pointer) != (presentation, lookup):
                            raise ValueError("Replaced binding use differs from mutation")
                    elif (
                        original_leaves.get(pointer) != lookup
                        or resolved_leaves.get(pointer) != lookup
                    ):
                        raise ValueError("Lookup binding use differs from argument")
                    binding_use_operations[operation] += 1
                    binding_use_occurrences.append(
                        {
                            "group_id": group_id,
                            "person_idx": person_idx,
                            "resolution_index": resolution_index,
                            "tool": tool_name,
                            "json_pointer": pointer,
                            "operation": operation,
                            "kind": kind,
                            "presentation": presentation,
                            "lookup": lookup,
                        }
                    )

                expected_guidance = _expected_guidance(binding_uses)
                if guidance != expected_guidance:
                    raise ValueError("Saved tool-result guidance differs from binding uses")
                marker = "<prme_value_presentations>"
                if expected_guidance is None:
                    if marker in trace_result:
                        raise ValueError("Unmatched tool result contains presentation metadata")
                else:
                    guidance_record_count += 1
                    guidance_travelers.add((group_id, person_idx))
                    payload = json.dumps(
                        expected_guidance,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                    suffix = (
                        f"\n\n{marker}{payload}</prme_value_presentations>"
                    )
                    if not trace_result.endswith(suffix):
                        raise ValueError(
                            "Model-visible tool result lacks exact presentation metadata"
                        )

            for pointer, _ in _qualified_arguments(resolved):
                executed_qualified.append(
                    {
                        "group_id": group_id,
                        "person_idx": person_idx,
                        "resolution_index": resolution_index,
                        "tool": tool_name,
                        "json_pointer": pointer,
                    }
                )
                executed_qualified_travelers.add((group_id, person_idx))

    passed = sum(_normalized(actual.get(key)) == _normalized(value) for key, value in truth.items())
    if len(guidance_evidence_modes) > 1:
        raise ValueError("Tool resolution guidance evidence is incomplete")
    binding_use_evidence_available = guidance_evidence_modes == {True}
    return {
        "schema_version": 3,
        "kind": "memoryarena-tool-boundary-value-audit",
        "registration_sha256": registration_sha256,
        "coverage": {
            "complete": True,
            "checkpoint_count": len(found),
            "group_count": len(cohort),
            "person_count": sum(len(group["answers"]) for group in cohort),
        },
        "qualified_changed_values": {
            "passed": passed,
            "total": len(truth),
            "rate": passed / len(truth) if truth else 0.0,
            "observed": len(actual),
        },
        "qualified_tool_calls": {
            "count": len(qualified_calls),
            "traveler_count": len(qualified_travelers),
            "occurrences": qualified_calls,
        },
        "tool_boundary_resolution": {
            "record_count": resolution_record_count,
            "trace_coverage_complete": True,
            "replacement_count": len(replacement_occurrences),
            "replacement_traveler_count": len(replacement_travelers),
            "replacements": replacement_occurrences,
            "executed_qualified_argument_count": len(executed_qualified),
            "executed_qualified_traveler_count": len(
                executed_qualified_travelers
            ),
            "executed_qualified_arguments": executed_qualified,
            "binding_use_evidence_available": binding_use_evidence_available,
            "binding_use_count": len(binding_use_occurrences),
            "binding_use_operations": binding_use_operations,
            "binding_uses": binding_use_occurrences,
            "guided_result_count": guidance_record_count,
            "guided_result_traveler_count": len(guidance_travelers),
        },
        "limits": [
            "Exact normalized-string audit; it is not a semantic itinerary-validity judge.",
            "Qualified values are changed slots whose reference contains parentheses.",
            "Model-requested qualified calls are counted from immutable saved traces.",
            "Executed qualified arguments are counted recursively after exact resolution.",
            "When available, call-local presentation metadata is matched to saved model-visible tool results.",
        ],
    }


def _load_registered_cohort(
    registration_path: Path,
    *,
    upstream: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load only the immutable data boundary needed by this retrospective audit."""
    registration = json.loads(registration_path.read_bytes())
    if (
        registration.get("schema_version") != runner.SCHEMA_VERSION
        or registration.get("kind") != runner.REGISTRATION_KIND
        or registration.get("status") != "preregistered"
    ):
        raise ValueError("Invalid MemoryArena travel registration")
    runner._check_upstream(upstream)  # noqa: SLF001
    dataset, identity = runner._load_dataset()  # noqa: SLF001
    if identity != registration["dataset"]:
        raise ValueError("Registered dataset changed")
    raw_rows = [dict(row) for row in dataset]
    group_ids = [item["id"] for item in registration["cohort"]["groups"]]
    source_rows = [row for row in raw_rows if row["id"] in set(group_ids)]
    if _digest(runner._canonical(source_rows)) != registration["cohort"]["content_sha256"]:  # noqa: SLF001
        raise ValueError("Registered cohort changed")
    converted = runner._converted_rows(dataset, upstream)  # noqa: SLF001
    by_id = {row["id"]: row for row in converted}
    if any(group_id not in by_id for group_id in group_ids):
        raise ValueError("Registered cohort is unavailable")
    return registration, [by_id[group_id] for group_id in group_ids]


def run(
    *,
    upstream: Path,
    registration_path: Path,
    checkpoint_path: Path,
    result_path: Path | None,
) -> dict[str, Any]:
    registration_sha256 = _digest(registration_path.read_bytes())
    registration, cohort = _load_registered_cohort(
        registration_path, upstream=upstream
    )
    checkpoint_bytes = checkpoint_path.read_bytes()
    checkpoints = [
        json.loads(line)
        for line in checkpoint_bytes.decode().splitlines()
        if line.strip()
    ]
    result = audit_value_bindings(
        cohort,
        checkpoints,
        registration_sha256=registration_sha256,
    )
    result["evidence"] = {
        "registration_file": registration_path.name,
        "registration_sha256": registration_sha256,
        "person_checkpoints_file": checkpoint_path.name,
        "person_checkpoints_sha256": _digest(checkpoint_bytes),
        "audit_source_sha256": _digest(Path(__file__).read_bytes()),
        "registered_prme_revision": registration["source"]["prme_revision"],
        "upstream_revision": runner.UPSTREAM_REVISION,
    }
    if result_path is not None:
        result_bytes = result_path.read_bytes()
        result["evidence"]["paired_result_file"] = result_path.name
        result["evidence"]["paired_result_sha256"] = _digest(result_bytes)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--paired-result", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(
        upstream=args.upstream.resolve(),
        registration_path=args.registration.resolve(),
        checkpoint_path=args.checkpoints.resolve(),
        result_path=args.paired_result.resolve() if args.paired_result else None,
    )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload)
    print(payload, end="")


if __name__ == "__main__":
    main()

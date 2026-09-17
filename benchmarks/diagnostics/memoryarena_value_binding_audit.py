"""Audit exact qualified values and tool arguments in a travel trial.

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
    travelers = set()
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
        for step_index, step in enumerate(scratchpad):
            for call_index, call in enumerate(step.get("tool_calls") or []):
                arguments = call.get("args") or {}
                paths = [
                    key
                    for key, value in arguments.items()
                    if isinstance(value, str) and _QUALIFIED_VALUE.search(value)
                ]
                if paths:
                    qualified_calls.append(
                        {
                            "group_id": group_id,
                            "person_idx": person_idx,
                            "step_index": step_index,
                            "call_index": call_index,
                            "tool": call.get("name"),
                            "argument_keys": sorted(paths),
                        }
                    )
                    travelers.add((group_id, person_idx))

    passed = sum(_normalized(actual.get(key)) == _normalized(value) for key, value in truth.items())
    return {
        "schema_version": 1,
        "kind": "memoryarena-value-binding-audit",
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
            "traveler_count": len(travelers),
            "occurrences": qualified_calls,
        },
        "limits": [
            "Exact normalized-string audit; it is not a semantic itinerary-validity judge.",
            "Qualified values are changed slots whose reference contains parentheses.",
            "Tool-call inspection covers complete top-level string arguments in saved traces.",
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

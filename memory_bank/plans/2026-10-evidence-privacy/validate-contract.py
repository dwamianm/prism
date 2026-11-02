#!/usr/bin/env python3
"""Validate PV-01 artifacts without executing any proposed privacy behavior."""

import ast
from collections import Counter
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ERRORS: list[str] = []
GROUPS = {
    "admission_policy": ("AP", 8),
    "access": ("AC", 8),
    "copied_content_leakage": ("CL", 8),
    "erasure_recovery": ("ER", 8),
    "encryption_migration": ("EM", 8),
    "sanitization": ("SN", 12),
}
SANITIZATION_TITLES = [
    "recognized credential", "contact identifier", "placeholder continuity",
    "cross-owner/project isolation", "configured sensitive topic",
    "condition/negation preservation", "unsupported language",
    "supported obfuscation", "detector failure", "timeout",
    "pre-write side effects", "generated output reintroduction",
]
REQUIREMENTS = {f"E{i}" for i in range(1, 10)}


def check(condition: bool, message: str) -> None:
    if not condition:
        ERRORS.append(message)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def read_json(name: str) -> dict:
    return json.loads(
        (HERE / name).read_text(), object_pairs_hook=unique_object,
        parse_constant=invalid_constant,
    )


def ids(rows: list[dict], label: str) -> set[str]:
    values = [row["id"] for row in rows]
    check(len(values) == len(set(values)), f"Duplicate {label} IDs")
    return set(values)


def validate() -> None:
    inventory = read_json("content-copy-inventory.json")
    registry = read_json("sanitizer-contract.json")
    manifest = read_json("privacy-scenarios.json")
    publication = read_json("publication.json")
    for artifact in (inventory, registry, manifest):
        check(artifact["schema_version"] == 1, "Unexpected contract schema")
    copies = inventory["copies"]
    copy_ids = ids(copies, "copy")
    check(
        {row["surface"] for row in copies} == set(inventory["architecture_surfaces"]),
        "Architecture surface is unclassified or absent",
    )
    anchors = 0
    for row in copies:
        for field in ("current_fields", "source_refs", "current_access", "current_retention",
                      "required_treatment", "retention", "erasure_owners"):
            check(bool(row[field]), f"{row['id']}: missing {field}")
        check(set(row["layouts"]) <= set(inventory["layouts"]), f"{row['id']}: layout")
        check(set(row["current_access"]) <= set(inventory["principals"]), f"{row['id']}: principal")
        check(row["retention"] in inventory["retention_policies"], f"{row['id']}: retention")
        check(set(row["erasure_owners"]) <= set(inventory["erasure_owners"]), f"{row['id']}: erasure owner")
        check(set(row["requirements"]) <= REQUIREMENTS, f"{row['id']}: requirement")
        for reference in row["source_refs"]:
            path = ROOT / reference["path"]
            check(path.is_file(), f"{row['id']}: missing source path {reference['path']}")
            if path.is_file():
                lines = path.read_text().splitlines()
                line = reference["line"]
                check(0 < line <= len(lines) and reference["anchor"] in lines[line - 1],
                      f"{row['id']}: stale source anchor {reference['path']}:{line}")
            anchors += 1

    categories = registry["categories"]
    category_ids = ids(categories, "category")
    recognizer_ids = ids(registry["recognizers"], "recognizer")
    check(registry["languages"] == ["en"], "V1 language matrix changed")
    check(set(registry["actions"]) == {"allow", "redact", "pseudonymize", "block"}, "V1 actions changed")
    used_recognizers = set()
    for category in categories:
        check(category["languages"] == ["en"], f"{category['id']}: language")
        check(set(category["supported_actions"]) <= set(registry["actions"]), f"{category['id']}: actions")
        for preset in ("balanced", "strict"):
            check(category[preset] in category["supported_actions"], f"{category['id']}: preset action")
        if category["mandatory"]:
            check(category["supported_actions"] == ["block"], "Mandatory category can relax block")
        check(bool(category["recognizer_ids"]), f"{category['id']}: no recognizer")
        used_recognizers.update(category["recognizer_ids"])
    check(used_recognizers == recognizer_ids, "Recognizer registry is not closed")
    for recognizer in registry["recognizers"]:
        check(recognizer["category"] in category_ids, f"{recognizer['id']}: category")
        category = next(c for c in categories if c["id"] == recognizer["category"])
        check(recognizer["id"] in category["recognizer_ids"], f"{recognizer['id']}: mismatched category mapping")
        check(recognizer["language"] == "en" and recognizer["version"] == 1, f"{recognizer['id']}: version/language")
        check(0 <= recognizer["score_threshold"] <= 1 and bool(recognizer["scope"]), f"{recognizer['id']}: scope/threshold")

    cases = manifest["cases"]
    ids(cases, "scenario")
    check(Counter(c["group"] for c in cases) == {g: n for g, (_, n) in GROUPS.items()}, "Original 40 plus 12 group counts changed")
    check(Counter(c["cohort"] for c in cases) == {"original_40": 40, "sanitization_addition": 12}, "Cohort replacement")
    check(set(manifest["requirements"]) == REQUIREMENTS, "E1-E9 requirement set changed")
    check(manifest["groups"] == {g: n for g, (_, n) in GROUPS.items()}, "Declared group count differs")
    covered, copied = set(), set()
    for group, (prefix, count) in GROUPS.items():
        check({c["id"] for c in cases if c["group"] == group} == {f"{prefix}{n:02}" for n in range(1, count + 1)}, f"{group}: frozen slot IDs changed")
    for case in cases:
        check(case["status"] == "not_run", f"{case['id']}: runtime result invented in contract")
        check(bool(case["setup"]) and bool(case["expected"]), f"{case['id']}: missing scenario/result")
        check(bool(case["requirements"]) and set(case["requirements"]) <= REQUIREMENTS, f"{case['id']}: requirement")
        check(bool(case["copy_ids"]) and set(case["copy_ids"]) <= copy_ids, f"{case['id']}: copy mapping")
        check(bool(case["implementation_owners"]) and set(case["implementation_owners"]) <= set(manifest["issues"]), f"{case['id']}: issue mapping")
        check(set(case["adapter_subchecks"]) <= set(manifest["execution_contract"]["adapters"]), f"{case['id']}: adapter")
        if case["group"] == "sanitization":
            check(case["title"] == SANITIZATION_TITLES[int(case["id"][2:]) - 1], f"{case['id']}: named PV-13 scenario changed")
            check(set(case["category_ids"]) <= category_ids, f"{case['id']}: category mapping")
        covered.update(case["requirements"])
        copied.update(case["copy_ids"])
    check(covered == REQUIREMENTS, "Missing E1-E9 scenario coverage")
    check(copied == copy_ids, f"Inventory copies missing scenarios: {sorted(copy_ids - copied)}")
    execution = manifest["execution_contract"]
    check(execution["backends"] == ["duckdb", "postgresql"] and execution["total_per_backend"] == 52 and execution["backend_case_executions"] == 104, "Backend execution counts changed")
    check(execution["original_per_backend"] == 40 and execution["sanitization_per_backend"] == 12, "Execution cohorts changed")
    check(execution["layouts"] == {"duckdb": ["directory", "single_file"], "postgresql": ["schema"]}, "Required layout subchecks changed")
    quality = manifest["separate_quality_sets"]
    check(quality["storage_parity"]["cases"] == 24 and quality["sealed_behavior"]["cases"] == 24, "Separate quality-set count changed")
    check(list(quality["sealed_behavior"]["groups"].values()) == [6, 6, 6, 6], "Sealed quality group count changed")
    check(set(manifest["issues"]) == set(publication["issues"]), "Published issue set changed")
    for key, issue in manifest["issues"].items():
        check(issue["issue"] == publication["issues"][key]["number"], f"{key}: issue number")
        check(issue["depends_on"] == publication["dependencies"][key], f"{key}: dependency changed")
        check(bool(issue["requirements"]) and set(issue["requirements"]) <= REQUIREMENTS, f"{key}: requirement mapping")

    # Validate links in new/updated plan docs and the new RFC. Older RFC/index
    # links outside this ticket remain outside the acceptance check.
    markdown = [HERE / name for name in ("README.md", "architecture.md", "PV-01-plan.md")]
    markdown.append(ROOT / "docs/RFC-0019-Evidence-Privacy.md")
    link_count = 0
    for path in markdown:
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", path.read_text()):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            destination = path.parent / unquote(parsed.path) if parsed.path else path
            check(destination.is_file(), f"{path.name}: broken local link {target}")
            if parsed.fragment and destination.is_file():
                headings = re.findall(r"^#+\s+(.+)$", destination.read_text(), re.MULTILINE)
                slugs = {re.sub(r"[^\w\- ]", "", h.lower()).replace(" ", "-") for h in headings}
                check(parsed.fragment in slugs, f"{path.name}: missing local anchor {target}")
            link_count += 1
    ast.parse(Path(__file__).read_text())
    if ERRORS:
        print("FAIL: contract structure/traceability")
        for error in ERRORS:
            print(f"- {error}")
        sys.exit(1)
    print(f"PASS: {len(copies)} copy classes, {anchors} exact source anchors, {len(inventory['architecture_surfaces'])} architecture surfaces")
    print(f"PASS: {len(categories)} categories, {len(recognizer_ids)} versioned recognizers, en-only closed action matrix")
    print("PASS: original 40 (8 x 5) plus 12 named sanitation cases; 104 planned backend-case executions")
    print(f"PASS: E1-E9 and every copy mapped; 9 published issues and 26 unchanged dependency edges; {link_count} local links")
    print("NOT RUN: privacy behavior, detector metrics, 104 backend executions, 24-query parity and 24 sealed cases. These belong to implementation/PV-11.")


if __name__ == "__main__":
    try:
        validate()
    except (KeyError, ValueError, TypeError, OSError, IndexError) as exc:
        print(f"FAIL: malformed contract artifact: {exc}")
        sys.exit(1)

"""PV-11 (#327) scenario gate: map the 52 frozen scenarios to test results per backend.

Usage:
    python docs/plans/2026-10-evidence-privacy/pv11-gate.py RESULTS.xml [OUTPUT.json]

RESULTS.xml is a pytest JUnit report of tests/test_privacy_*.py run with
PRME_TEST_DATABASE_URL set. A case maps to the tests whose names contain its
ID, plus the explicit entries in pv11-case-map.json. A backend execution passes
only when it has at least one mapped test on that backend and every mapped test
there passed. A missing backend is reported as a gap, never as a pass, except a
case listed as backend_independent with a reason, reported under that status.

Each case's adapter subchecks are checked against adapter_subchecks in the
case map: an adapter is proven when its mapped tests ran and all passed on at
least one backend, proven_by_refusal when the map marks it as a refusal, and
otherwise missing or failed. async_python is proven by the case's own tests.
"""

import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent


def results(path):
    outcomes = {}
    for case in ElementTree.parse(path).getroot().iter("testcase"):
        module = case.get("classname", "").replace(".", "/") + ".py"
        node = f"{module}::{case.get('name')}"
        status = "passed"
        for child in case:
            if child.tag in {"failure", "error"}:
                status = "failed"
            elif child.tag == "skipped":
                status = "xfailed" if "xfail" in (child.get("type") or "") + (child.get("message") or "") else "skipped"
        outcomes[node] = status
    return outcomes


def backend(node):
    parameters = node.split("[", 1)[1] if "[" in node else ""
    return "postgresql" if "postgres" in parameters else "duckdb"


def main():
    outcomes = results(sys.argv[1])
    manifest = json.loads((HERE / "privacy-scenarios.json").read_text())
    mapping = json.loads((HERE / "pv11-case-map.json").read_text())
    overrides, independent = mapping["overrides"], mapping.get("backend_independent", {})
    adapter_map = mapping.get("adapter_subchecks", {})
    rows = []
    for case in manifest["cases"]:
        identifier = case["id"]
        pattern = re.compile(r"::test_\w*" + re.escape(identifier) + r"(?![0-9])")
        explicit = set(overrides.get(identifier, {}).get("tests", ()))
        nodes = sorted(node for node in outcomes
                       if pattern.search(node) or node.split("[", 1)[0] in explicit)
        executions = {}
        for name in ("duckdb", "postgresql"):
            mapped = [node for node in nodes if backend(node) == name]
            ran = [node for node in mapped if outcomes[node] != "skipped"]
            if not ran and name == "postgresql" and identifier in independent:
                status = "backend_independent"
            elif not ran:
                status = "gap"
            elif all(outcomes[node] == "passed" for node in ran):
                status = "passed"
            elif any(outcomes[node] == "failed" for node in ran):
                status = "failed"
            else:
                status = "expected_failure"
            executions[name] = {"status": status, "tests": {node: outcomes[node] for node in mapped}}
            if status == "backend_independent":
                executions[name]["reason"] = independent[identifier]
        adapters = {}
        for adapter in case.get("adapter_subchecks", []):
            if adapter == "async_python":
                passed = [name for name, value in executions.items() if value["status"] == "passed"]
                adapters[adapter] = {"status": "proven" if passed else "missing", "backends": passed,
                                     "tests": "the case's own mapped tests"}
                continue
            entry = adapter_map.get(identifier, {}).get(adapter)
            if entry is None:
                adapters[adapter] = {"status": "missing", "backends": [], "tests": {}}
                continue
            prefixes = set(entry["tests"])
            mapped = {node: outcomes[node] for node in outcomes if node.split("[", 1)[0] in prefixes}
            ran = {node: status for node, status in mapped.items() if status != "skipped"}
            if not ran or set(prefixes) - {node.split("[", 1)[0] for node in ran}:
                status = "missing"
            elif all(value == "passed" for value in ran.values()):
                status = "proven_by_refusal" if entry["kind"] == "refusal" else "proven"
            else:
                status = "failed"
            adapters[adapter] = {"status": status, "backends": sorted({backend(node) for node in ran}),
                                 "tests": mapped}
        rows.append({"id": identifier, "cohort": case["cohort"], "group": case["group"], "title": case["title"],
                     "mapping": "explicit" if identifier in overrides else "name",
                     "adapter_subchecks": adapters, "executions": executions})
    summary = Counter(row["executions"][name]["status"] for row in rows for name in ("duckdb", "postgresql"))
    adapter_summary = Counter(value["status"] for row in rows for value in row["adapter_subchecks"].values())
    quality = {}
    for name, entry in mapping.get("quality_sets", {}).items():
        prefixes = set(entry["tests"])
        tests = {node: outcomes[node] for node in outcomes if node.split("[", 1)[0] in prefixes}
        ran = [status for status in tests.values() if status != "skipped"]
        quality[name] = {"status": "missing" if not ran else "passed" if set(ran) == {"passed"} else "failed",
                         "tests": tests}
    report = {"schema_version": 2, "manifest": "privacy-scenarios.json", "results": Path(sys.argv[1]).name,
              "backend_case_executions": sum(summary.values()), "summary": dict(summary),
              "adapter_subchecks": sum(adapter_summary.values()), "adapter_summary": dict(adapter_summary),
              "quality_sets": quality, "cases": rows}
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['backend_case_executions']} backend-case executions: {dict(summary)}")
    print(f"{report['adapter_subchecks']} adapter subchecks: {dict(adapter_summary)}")
    print("quality sets: " + ", ".join(f"{name} {value['status']}" for name, value in quality.items()))
    for row in rows:
        statuses = {name: row["executions"][name]["status"] for name in ("duckdb", "postgresql")}
        if set(statuses.values()) != {"passed"}:
            print(f"  {row['id']} {row['title']}: {statuses}")
        open_adapters = {name: value["status"] for name, value in row["adapter_subchecks"].items()
                         if value["status"] not in {"proven", "proven_by_refusal"}}
        if open_adapters:
            print(f"  {row['id']} adapters: {open_adapters}")


if __name__ == "__main__":
    main()

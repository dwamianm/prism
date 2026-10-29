"""PV-11 (#327) scenario gate: map the 52 frozen scenarios to test results per backend.

Usage:
    python docs/plans/2026-10-evidence-privacy/pv11-gate.py RESULTS.xml [OUTPUT.json]

RESULTS.xml is a pytest JUnit report of tests/test_privacy_*.py run with
PRME_TEST_DATABASE_URL set. A case maps to the tests whose names contain its
ID, plus the explicit entries in pv11-case-map.json. A backend execution passes
only when it has at least one mapped test on that backend and every mapped test
there passed. A missing backend is reported as a gap, never as a pass, except a
case listed as backend_independent with a reason, reported under that status.
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
        rows.append({"id": identifier, "cohort": case["cohort"], "group": case["group"], "title": case["title"],
                     "mapping": "explicit" if identifier in overrides else "name",
                     "adapter_subchecks": case.get("adapter_subchecks", []), "executions": executions})
    summary = Counter(row["executions"][name]["status"] for row in rows for name in ("duckdb", "postgresql"))
    report = {"schema_version": 1, "manifest": "privacy-scenarios.json", "results": Path(sys.argv[1]).name,
              "backend_case_executions": sum(summary.values()), "summary": dict(summary), "cases": rows}
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(report, indent=2) + "\n")
    print(f"{report['backend_case_executions']} backend-case executions: {dict(summary)}")
    for row in rows:
        statuses = {name: row["executions"][name]["status"] for name in ("duckdb", "postgresql")}
        if set(statuses.values()) != {"passed"}:
            print(f"  {row['id']} {row['title']}: {statuses}")


if __name__ == "__main__":
    main()

"""Audit pinned native travel scoring with authored inputs, without model calls.

Only the upstream data loader is replaced by an authored in-memory cohort.
The evaluator's source and scoring functions execute unchanged. This is not a
dataset benchmark or a corrected substitute for the upstream scoring metric.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types

from benchmarks.diagnostics.hindsight_capture import digest, write

UPSTREAM_COMMIT = "6cd9de14b71915e39ac742a20dc33785e14b6aab"
SLOTS = ("breakfast", "lunch", "dinner", "accommodation", "transportation", "attraction")


def validate_coverage(expected, submissions):
    """Require exact registered group/person coverage before native evaluation.

    Failed plans must remain present as null plans. Missing, duplicate and extra
    rows are rejected instead of silently shrinking or changing denominators.
    ``expected`` maps group IDs to the complete registered person-ID sequence.
    It must be frozen independently of submissions and their outcomes.
    """
    if not expected:
        raise ValueError("A nonempty registered cohort is required")
    for group, people in expected.items():
        if (type(group) is not int or not people
                or any(type(person) is not int for person in people)
                or len(set(people)) != len(people)):
            raise ValueError("Invalid registered group/person identities")
    found = {}
    for row in submissions:
        group = row["id"]
        if type(group) is not int or group not in expected or group in found:
            raise ValueError("Duplicate, unknown or invalid submitted group")
        people = []
        for person in row["persons"]:
            identity = person["person_idx"]
            if type(identity) is not int or "plan" not in person:
                raise ValueError("Each submitted person needs an ID and explicit plan")
            people.append(identity)
        if len(people) != len(set(people)) or set(people) != set(expected[group]):
            raise ValueError("Submitted persons differ from the registered group")
        found[group] = people
    if set(found) != set(expected):
        raise ValueError("Submitted groups differ from the registered cohort")


def authored_cohort():
    rows = []
    for group in (11, 22):
        base = [{"day": 1, **{slot: "Old choice" for slot in SLOTS}}]
        answer = [{"day": 1, **{slot: "Cedar Lodge" for slot in SLOTS}}]
        rows.append({
            "id": group,
            "base_person": {"daily_plans": base},
            "answers": [
                {"round_idx": person, "daily_plans": copy.deepcopy(answer)}
                for person in (1, 2)
            ],
        })
    return rows


def run(upstream):
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=upstream, text=True
    ).strip()
    if commit != UPSTREAM_COMMIT or subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=upstream
    ).strip():
        raise ValueError("Use the pinned, clean upstream checkout")
    folder = upstream / "env/env_systems/travel_planner_env"
    cohort = authored_cohort()
    expected = {row["id"]: [1, 2] for row in cohort}
    complete = [
        {"id": row["id"], "persons": [
            {"person_idx": person["round_idx"], "plan": person["daily_plans"]}
            for person in row["answers"]
        ]}
        for row in cohort
    ]
    partial = copy.deepcopy(complete[:1])
    partial[0]["persons"] = partial[0]["persons"][:1]
    failed = copy.deepcopy(complete)
    for row in failed:
        for person in row["persons"]:
            if (row["id"], person["person_idx"]) != (11, 1):
                person["plan"] = None
    prefix = copy.deepcopy(complete)
    for row in prefix:
        for person in row["persons"]:
            person["plan"] = [{"day": 1, **{slot: "C" for slot in SLOTS}}]
    cases = {"complete_exact": complete, "omitted_failures": partial,
             "explicit_failures": failed, "one_character_prefix": prefix}
    # Import the real evaluator with only its data-loading dependency substituted.
    # No dataset, scoring function, global threshold or parser is modified.
    name = "_prme_authored_travel_audit"
    names = (name, name + ".data_loader", name + ".eval")
    if any(module in sys.modules for module in names):
        raise RuntimeError("Audit import namespace is already occupied")
    package = types.ModuleType(name)
    package.__path__ = [str(folder)]
    loader = types.ModuleType(name + ".data_loader")
    loader.load_travel_data = lambda: copy.deepcopy(cohort)
    sys.modules[name] = package
    sys.modules[name + ".data_loader"] = loader
    spec = importlib.util.spec_from_file_location(name + ".eval", folder / "eval.py")
    evaluator = importlib.util.module_from_spec(spec)
    sys.modules[name + ".eval"] = evaluator
    try:
        spec.loader.exec_module(evaluator)
        outcomes = {}
        with tempfile.TemporaryDirectory(prefix="prme-travel-scoring-") as directory:
            for case, rows in cases.items():
                path = Path(directory) / (case + ".jsonl")
                path.write_text("".join(json.dumps(row) + "\n" for row in rows))
                try:
                    validate_coverage(expected, rows)
                    coverage = True
                except ValueError:
                    coverage = False
                with redirect_stdout(io.StringIO()):
                    scores = evaluator.evaluate(str(path))
                outcomes[case] = {"native_scores": scores,
                                  "registered_coverage_passed": coverage,
                                  "submission": rows}
        return {
            "complete": True, "upstream_commit": commit,
            "evaluator_sha256": digest((folder / "eval.py").read_bytes()),
            "audit_sha256": digest(Path(__file__).read_bytes()),
            "authored_cohort": cohort, "registered_groups": 2,
            "registered_persons": 4, "outcomes": outcomes,
            "limits": [
                "Authored scoring audit only; no dataset, actor or memory quality result.",
                "Only load_travel_data is substituted; native evaluator executes unchanged.",
                "Coverage validation does not repair the native prefix similarity metric.",
            ],
        }
    finally:
        for module in names:
            sys.modules.pop(module, None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite audit evidence")
    write(args.output, run(args.upstream))


if __name__ == "__main__":
    main()

"""Judge a fast cycle report against a baseline by the committed rules.

``benchmarks/fixtures/fast-cycle/rules.json`` lists every measure with the
direction that is good, whether it gates, and how much a live run may differ
(``live``, absolute and relative to the baseline value). The tolerance is exact
(zero) when the run is deterministic: the store stage uses no model, and an
ingest stage whose extraction came wholly from the cache repeats the model
output of the baseline's, so any difference is the code's. An ingest stage
that asked the model (a changed prompt or model, or an empty cache) differs
from run to run, so it gets the rule's ``live`` tolerance.

A measure passes when it is the same, better, or worse by no more than its
tolerance. A gated measure worse than that fails the comparison. A watched
measure (one the rules do not gate, such as each answer's rank) is listed when
it moves and never fails. ``--accept`` lists measures whose worsening the
change means to cause, so the comparison names them and does not fail on them.

Exit status: 0 pass, 1 a gated measure failed, 2 the reports cannot be
compared (another fixture, chat, backend or format).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from benchmarks.fast_cycle.store import FIXTURES

RULES = FIXTURES / "rules.json"
RULES_KIND = "prme-fast-cycle-rules"
REPORT_KIND = "prme-fast-cycle-report"
SCHEMA_VERSION = 1
GROUPS = ("storage", "meaning", "results")
VERDICT_ORDER = ("FAIL", "worse, accepted", "worse (watched)", "worse, within tolerance", "moved", "better")


def load_rules(path: Path = RULES) -> dict:
    rules = json.loads(path.read_text())
    if rules.get("kind") != RULES_KIND or rules.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path} is not a fast cycle rules file")
    for name, rule in rules["measures"].items():
        if rule["group"] not in GROUPS or rule["good"] not in {"higher", "lower", None}:
            raise ValueError(f"{path}: the rule for {name} needs a group of {GROUPS} and a good direction")
        if rule["gate"] and rule["good"] is None:
            raise ValueError(f"{path}: {name} cannot gate without a good direction")
    return rules


def split_id(measure_id: str) -> tuple[str, str, str]:
    """A measure ID is ``scope/name`` or ``scope/name:qualifier``, such as ``store/answer_rank:rust``."""
    scope, _, rest = measure_id.partition("/")
    name, _, qualifier = rest.partition(":")
    return scope, name, qualifier


def rule_for(rules: dict, measure_id: str) -> dict:
    name = split_id(measure_id)[1]
    if name not in rules["measures"]:
        raise ValueError(f"The rules have no entry for the measure {name}")
    return rules["measures"][name]


def label_for(rules: dict, measure_id: str) -> str:
    scope, _, qualifier = split_id(measure_id)
    return f"{rule_for(rules, measure_id)['label']}{': ' + qualifier if qualifier else ''} [{scope}]"


def grouped(rules: dict, stages: dict) -> dict:
    """The stages' measures by group, keyed by measure ID."""
    groups: dict[str, dict] = {group: {} for group in GROUPS}
    found = {f"store/{name}": value for name, value in stages.get("store", {}).get("measures", {}).items()}
    for chat, measures in stages.get("ingest", {}).get("measures", {}).items():
        found.update({f"{chat}/{name}": value for name, value in measures.items()})
    for measure_id, value in found.items():
        groups[rule_for(rules, measure_id)["group"]][measure_id] = value
    return groups


def tolerance(rule: dict, baseline: float | None, *, live: bool) -> float:
    if not live:
        return 0.0
    allowed = rule.get("live", {})
    return max(allowed.get("abs", 0), allowed.get("rel", 0) * abs(baseline or 0))


def _worse_by(rule: dict, baseline, value) -> float:
    """How much worse the value is than the baseline, in the measure's units; negative when better."""
    # An answer that is not found has no rank, which is worse than any rank.
    if rule.get("none_is") == "worst":
        baseline = float("inf") if baseline is None else baseline
        value = float("inf") if value is None else value
        if baseline == value:
            return 0.0
    return (value - baseline) if rule["good"] == "lower" else (baseline - value)


def judge(rule: dict, baseline, value, allowed: float, *, accepted: bool = False) -> str:
    if baseline == value:
        return "same"
    if baseline is None or value is None:
        if rule.get("none_is") != "worst":
            return "not compared (no value)"
    if rule["good"] is None:
        return "moved"
    worse = _worse_by(rule, baseline, value)
    if worse <= 0:
        return "better"
    if worse <= allowed:
        return "worse, within tolerance"
    if not rule["gate"]:
        return "worse (watched)"
    return "worse, accepted" if accepted else "FAIL"


def _cannot_compare(baseline: dict, report: dict) -> list[str]:
    """Reasons the two reports measure different things."""
    reasons = []
    for report_, name in ((baseline, "baseline"), (report, "report")):
        if report_.get("kind") != REPORT_KIND or report_.get("schema_version") != SCHEMA_VERSION:
            reasons.append(f"the {name} is not a fast cycle report of this format")
    if reasons:
        return reasons
    base, new = baseline["stages"], report["stages"]
    if "store" in base and "store" in new:
        for key, what in (("fixture_sha256", "store fixture"), ("backend", "store backend")):
            if base["store"][key] != new["store"][key]:
                reasons.append(f"the {what} differs ({base['store'][key][:12]} against {new['store'][key][:12]})"
                               if key.endswith("sha256") else
                               f"the {what} differs ({base['store'][key]} against {new['store'][key]})")
    if "ingest" in base and "ingest" in new:
        for chat, entry in base["ingest"]["conversations"].items():
            other = new["ingest"]["conversations"].get(chat)
            if other and other["conversation_sha256"] != entry["conversation_sha256"]:
                reasons.append(f"the chat {chat} differs")
    return reasons


def compare(baseline: dict, report: dict, rules: dict, accept: frozenset[str] = frozenset()) -> dict:
    """Judge every measure the two reports share; returns the rows, the verdict and what was not compared."""
    reasons = _cannot_compare(baseline, report)
    if reasons:
        return {"status": "not comparable", "reasons": reasons, "rows": [], "skipped": [], "unchanged": 0}
    base, new = baseline["measures"], report["measures"]
    live = any(entry.get("ingest", {}).get("mode") == "live" for entry in (baseline["stages"], report["stages"]))
    rows, skipped, unchanged = [], [], 0
    for group in GROUPS:
        for measure_id in sorted(set(base[group]) | set(new[group])):
            if measure_id not in base[group] or measure_id not in new[group]:
                skipped.append((measure_id, "baseline" if measure_id in base[group] else "report"))
                continue
            rule = rule_for(rules, measure_id)
            scope, name, _ = split_id(measure_id)
            allowed = tolerance(rule, base[group][measure_id], live=live and scope != "store")
            verdict = judge(rule, base[group][measure_id], new[group][measure_id], allowed,
                            accepted=bool({measure_id, name} & accept))
            if verdict == "same":
                unchanged += 1
                continue
            rows.append({"id": measure_id, "group": group, "label": label_for(rules, measure_id),
                         "baseline": base[group][measure_id], "value": new[group][measure_id],
                         "tolerance": allowed, "verdict": verdict})
    failed = [row for row in rows if row["verdict"] == "FAIL"]
    return {"status": "FAIL" if failed else "PASS", "live": live, "rows": rows, "skipped": skipped,
            "unchanged": unchanged, "failed": [row["id"] for row in failed]}


def number(value) -> str:
    if value is None:
        return "none"
    return f"{value:.3f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def render(result: dict, baseline: dict, report: dict) -> str:
    """The comparison as text: the verdict first, then each measure that moved."""
    lines = [f"Baseline {baseline['label']} against {report['label']}"]
    if result["status"] == "not comparable":
        return "\n".join([*lines, "NOT COMPARABLE", *(f"- {reason}" for reason in result["reasons"])]) + "\n"
    missing = sorted(baseline["stages"].keys() ^ report["stages"].keys())
    kind = "live (noise tolerances)" if result["live"] else "exact (no model call in the ingest stage, or none run)"
    failed = result["failed"]
    lines += [f"{result['status']}: {len(failed)} measure(s) failed, {len(result['rows'])} moved, "
              f"{result['unchanged']} unchanged. Tolerance: {kind}."]
    if missing:
        lines.append(f"Partial: {', '.join(missing)} is in only one of the reports and was not compared.")
    for group in GROUPS:
        # The failures first, then the other moves from the worst to the best.
        group_rows = sorted((row for row in result["rows"] if row["group"] == group),
                            key=lambda row: VERDICT_ORDER.index(row["verdict"]) if row["verdict"] in VERDICT_ORDER
                            else len(VERDICT_ORDER))
        if not group_rows:
            continue
        lines += ["", f"{group.capitalize()}:"]
        for row in group_rows:
            change = ""
            if isinstance(row["baseline"], (int, float)) and isinstance(row["value"], (int, float)):
                change = f" ({row['value'] - row['baseline']:+.3g})"
            allowed = f", tolerance {number(row['tolerance'])}" if row["tolerance"] else ""
            lines.append(f"- {row['verdict']}: {row['label']}: {number(row['baseline'])} to "
                         f"{number(row['value'])}{change}{allowed}")
    for side in ("baseline", "report"):
        scopes = list(dict.fromkeys(split_id(measure_id)[0] for measure_id, where in result["skipped"] if where == side))
        count = sum(where == side for _, where in result["skipped"])
        if count:
            lines += ["", f"Not compared: {count} measure(s) of {', '.join(scopes)} only in the {side}."]
    return "\n".join(lines).rstrip() + "\n"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("baseline", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--rules", type=Path, default=RULES)
    parser.add_argument("--accept", action="append", default=[], metavar="MEASURE",
                        help="A measure name or ID whose worsening this change means to cause; repeatable")


def execute(args: argparse.Namespace) -> int:
    baseline, report = (json.loads(path.read_text()) for path in (args.baseline, args.report))
    result = compare(baseline, report, load_rules(args.rules), frozenset(args.accept))
    sys.stdout.write(render(result, baseline, report))
    return {"PASS": 0, "FAIL": 1}.get(result["status"], 2)

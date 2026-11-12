"""Fit the unknown status rules on LoCoMo and test them on LongMemEval-S (task P1, PROTOCOL.md).

    python3 research/unknown-status-2026-11/analyze.py /tmp/p1 > research/unknown-status-2026-11/results.json

Reads the four signal files collect.py writes (<benchmark>-<mode>.jsonl). Pure Python, no engine import.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

MAX_FALSE = 0.02
BAR_HIT, BAR_FALSE = 0.40, 0.02
COS_GRID = [round(0.30 + 0.005 * i, 3) for i in range(141)]  # 0.300 .. 1.000
GAP_GRID = [round(0.005 * i, 3) for i in range(101)]  # 0.000 .. 0.500
MISS_GRID = [1, 2, 3, 4, 5]
SHARE_GRID = [round(0.05 * i, 2) for i in range(20)]  # 0.00 .. 0.95


def best_cos(row: dict) -> float:
    return max((item["cos"] for item in row["top"]), default=0.0)


def missing(row: dict) -> int:
    return sum(1 for value in row["df"].values() if value == 0)


def walked(row: dict) -> bool:
    return bool(row["route"]) or row["belief_source"] is not None


def named_speaker(row: dict) -> str | None:
    """The one known speaker the question names by a whole word, else None."""
    named = [name for name in row["speakers"]
             if re.search(rf"\b{re.escape(name.lower())}\b", row["question"].lower())]
    return named[0] if len(named) == 1 else None


def owner_gap(row: dict) -> float | None:
    """Best cosine overall minus best cosine among the named speaker's results; None without one named speaker."""
    name = named_speaker(row)
    if name is None:
        return None
    own = max((item["cos"] for item in row["top"] if item["speaker"] == name), default=0.0)
    return best_cos(row) - own


def best_share(row: dict) -> float | None:
    if not row["terms"]:
        return None
    return max((item["covered"] / len(row["terms"]) for item in row["top"]), default=0.0)


# Each rule: (name, signals, grid(s), predicate(row, *thresholds)).
RULES = [
    ("R1 weak match", 1, [COS_GRID], lambda r, t: best_cos(r) < t),
    ("R2 missing words", 1, [MISS_GRID], lambda r, t: missing(r) >= t),
    ("R3 no address and weak match", 2, [COS_GRID], lambda r, t: not walked(r) and best_cos(r) < t),
    ("R4 wrong owner", 2, [GAP_GRID], lambda r, t: (g := owner_gap(r)) is not None and g > t),
    ("R5 weak coverage", 1, [SHARE_GRID], lambda r, t: (s := best_share(r)) is not None and s <= t),
    ("R1 or R2", 2, [COS_GRID, MISS_GRID], lambda r, t, u: best_cos(r) < t or missing(r) >= u),
    ("R4 or R2", 3, [GAP_GRID, MISS_GRID],
     lambda r, t, u: ((g := owner_gap(r)) is not None and g > t) or missing(r) >= u),
]


def confusion(rows: list[dict], marked) -> dict:
    tp = sum(1 for r in rows if r["label"] == "unknown" and marked(r))
    fn = sum(1 for r in rows if r["label"] == "unknown" and not marked(r))
    fp = sum(1 for r in rows if r["label"] == "answerable" and marked(r))
    tn = sum(1 for r in rows if r["label"] == "answerable" and not marked(r))
    return {"hit": tp, "miss": fn, "false_unknown": fp, "kept": tn,
            "unknown_share": tp / (tp + fn) if tp + fn else None,
            "false_share": fp / (fp + tn) if fp + tn else None,
            "unknown_share_95": wilson(tp, tp + fn), "false_share_95": wilson(fp, fp + tn)}


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def grid_points(grids):
    if len(grids) == 1:
        return [(t,) for t in grids[0]]
    return [(t, u) for t in grids[0] for u in grids[1]]


def fit(rows: list[dict], rule) -> dict | None:
    """The thresholds with the highest unknown share at a false share of at most 2% (ties: lower false share,
    then the first grid point)."""
    name, _signals, grids, predicate = rule
    best = None
    for point in grid_points(grids):
        result = confusion(rows, lambda r: predicate(r, *point))
        if result["false_share"] is None or result["false_share"] > MAX_FALSE:
            continue
        key = (result["unknown_share"], -result["false_share"])
        if best is None or key > best[0]:
            best = (key, point, result)
    if best is None:
        return None
    return {"thresholds": list(best[1]), **best[2]}


def choose(fits: dict) -> str:
    """Highest LoCoMo unknown share; among rules within 2 points of it, the one with fewer signals, then the higher
    share."""
    ranked = {name: f for name, f in fits.items() if f is not None}
    top = max(f["unknown_share"] for f in ranked.values())
    near = [rule for rule in RULES if rule[0] in ranked and ranked[rule[0]]["unknown_share"] >= top - 0.02]
    return min(near, key=lambda rule: (rule[1], -ranked[rule[0]]["unknown_share"]))[0]


def load(folder: Path, benchmark: str, mode: str) -> list[dict]:
    path = folder / f"{benchmark}-{mode}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def example(row: dict, rule_name: str) -> dict:
    return {"question_id": row["question_id"], "category": row["category"], "question": row["question"],
            "best_cos": best_cos(row), "missing_terms": [t for t, v in row["df"].items() if v == 0],
            "walked": walked(row), "owner_gap": owner_gap(row), "rule": rule_name}


def main(folder: Path) -> None:
    out = {"protocol": "PROTOCOL.md", "modes": {}}
    for mode in ("classic", "address_first"):
        locomo = load(folder, "locomo", mode)
        lme = load(folder, "longmemeval", mode)
        fits = {rule[0]: fit(locomo, rule) for rule in RULES}
        chosen = choose(fits)
        tests = {}
        for rule in RULES:
            f = fits[rule[0]]
            if f is None:
                continue
            tests[rule[0]] = confusion(lme, lambda r, rule=rule, f=f: rule[3](r, *f["thresholds"]))
        rule = next(r for r in RULES if r[0] == chosen)
        marked = lambda r: rule[3](r, *fits[chosen]["thresholds"])  # noqa: E731
        lme_test = tests[chosen]
        passed = lme_test["unknown_share"] >= BAR_HIT and lme_test["false_share"] <= BAR_FALSE
        examples = {
            "hit": next((example(r, chosen) for r in lme if r["label"] == "unknown" and marked(r)), None),
            "false_unknown": next((example(r, chosen) for r in lme if r["label"] == "answerable" and marked(r)),
                                  None),
            "miss": next((example(r, chosen) for r in lme if r["label"] == "unknown" and not marked(r)), None),
            "locomo_hit": next((example(r, chosen) for r in locomo if r["label"] == "unknown" and marked(r)), None),
            "locomo_false_unknown": next(
                (example(r, chosen) for r in locomo if r["label"] == "answerable" and marked(r)), None),
            "locomo_miss": next((example(r, chosen) for r in locomo if r["label"] == "unknown" and not marked(r)),
                                None),
        }
        out["modes"][mode] = {
            "locomo_questions": {"unknown": sum(r["label"] == "unknown" for r in locomo),
                                 "answerable": sum(r["label"] == "answerable" for r in locomo)},
            "lme_questions": {"unknown": sum(r["label"] == "unknown" for r in lme),
                              "answerable": sum(r["label"] == "answerable" for r in lme)},
            "fits_on_locomo": fits, "chosen": chosen, "tests_on_lme": tests,
            "verdict_bar_met": passed, "examples": examples,
        }
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main(Path(sys.argv[1]))

"""Analyses asked for after the run: probability thresholds, the exact wrong-retirement pairs, the R4 split.

No model call: everything comes from the logged decisions and the cache entries they name.

(a) For each model's W1, the exact (old, new) pair behind every wrong retirement, with the label probabilities,
    and a sweep of "retire when p(no longer true) >= t" for t in 0.6 to 0.9, replayed through W1 (candidates and
    order unchanged; only the rule changes).
(b) The read judge with "yes" needing p(yes) >= p: unknown-right (of 7) and false unknowns (of 60) for p in
    0.4 to 0.9. The R1.judge decisions are logged in blocks of 10, one per question, in row order.
(c) R4: how many questions stopped at rank 1, at rank 2 or 3, and went to R2b, with the median added ms of each.

The chat transport logs only the five most likely first tokens, so a letter's probability is known only when the
letter is among them; the count of such decisions is reported and the sweep skips the rest (`skipped`).

    uv run python research/decision-recursion-2026-11/analyze.py --results research/decision-recursion-2026-11/results
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

import write_experiment as W  # noqa: E402

from decide import Decider, render, systemone_request  # noqa: E402
from prompts import OPTIONS, TEMPLATES, content_words, fmt_time  # noqa: E402

LABELS = "ABCDEFGHIJ"


def load_log(run_dir: Path, name: str, result: str) -> list[dict]:
    """The decisions of the run that wrote ``result`` (read.json or write.json). A resumed run appended to a log
    a cut-off run had already written, so only the last (model_calls + cache_hits) lines belong to it."""
    path = run_dir / name
    if not path.exists():
        return []
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    summary = json.loads((run_dir / result).read_text())
    total = summary["model_calls"] + summary["cache_hits"]
    if len(lines) < total:
        raise SystemExit(f"{path} holds {len(lines)} decisions, the run made {total}")
    return lines[-total:]


def probabilities(run_dir: Path, entry: dict, n: int) -> dict[int, float] | None:
    """P(option i) for every option letter this cache entry lets us see, or None when one is missing."""
    cached = json.loads((run_dir / "cache" / f"{entry['key']}.json").read_text())
    if "probabilities" in cached:
        return {LABELS.index(k): v for k, v in cached["probabilities"].items() if k in LABELS[:n]}
    top = {}
    for token, logprob in cached.get("top", []):
        token = token.strip()
        if len(token) == 1 and token in LABELS[:n]:
            top[LABELS.index(token)] = logprob
    if len(top) < n:
        return None
    high = max(top.values())
    total = sum(math.exp(v - high) for v in top.values())
    return {i: math.exp(v - high) / total for i, v in top.items()}


class CacheDecider:
    """Answers W1 from the run's own cache, by computing the prompt key exactly as Decider does. Never calls a
    model. In mode "model" it returns the stored choice; in mode "threshold:<t>" it retires a fact when the
    probability of "no longer true" is at least t. It records every (old, new) pair it was asked about."""

    def __init__(self, run_dir: Path, model: str):
        backend = "systemone" if model.startswith("clef") else "ollama"
        self.decider = Decider(backend, model, run_dir / "cache", run_dir / "unused.jsonl")
        self.mode = "model"
        self.missed = 0
        self.calls: list[tuple[str, list[str], dict]] = []
        self._position = 0

    def mark(self):
        return self._position

    def since(self, mark):
        return []

    def _entry_path(self, question: str, options: list[str]) -> Path:
        if self.decider.backend == "systemone":
            prompt = json.dumps(systemone_request(self.decider.model, question, options), sort_keys=True)
        else:
            prompt = render(question, options)
        return self.decider.cache_dir / (self.decider._key(prompt, len(options)) + ".json")

    def decide(self, question: str, options: list[str], tag: str = ""):
        self._position += 1
        self.calls.append((question, options, {}))
        path = self._entry_path(question, options)
        if not path.exists():
            self.missed += 1
            return 0, None
        entry = json.loads(path.read_text())
        probs = probabilities(self.decider.cache_dir.parent, {"key": path.stem}, len(options))
        cut = options.index("no longer true") if "no longer true" in options else 1
        if self.mode.startswith("threshold:"):
            threshold = float(self.mode.split(":")[1])
            return (cut if probs is not None and probs[cut] >= threshold else 0), (probs or {}).get(cut)
        return entry["choice"], entry["confidence"]


def fact_ids_by_text(facts: list[W.Fact]) -> dict[str, str]:
    return {f.text(): f.id for f in facts}


def pairs_of(decider: CacheDecider, facts: list[W.Fact]) -> list[tuple[str, str]]:
    """The (old, new) fact ids of each W1 judgment the decider answered, read back from the prompt text."""
    by_text = fact_ids_by_text(facts)
    pairs = []
    for question, _, _ in decider.calls:
        old_text = question.split("\nNew statement (", 1)[0].split("): ", 1)[1]
        new_text = question.split("\nNew statement (", 1)[1].split("): ", 1)[1].split("\n\nAfter the new statement")[0]
        pairs.append((by_text[old_text], by_text[new_text]))
    return pairs


def sweep_write(run_dir: Path, model: str, facts, key: dict) -> dict:
    """The model's own W1, its exact wrong-retirement pairs, and the threshold sweep replayed through W1."""
    base = CacheDecider(run_dir, model)
    base_result = W.simulate(base, facts, recursive=False)
    correct_ids = set(key["summary"]["retired"])
    by_id = {f.id: f for f in facts}
    wrong = sorted(set(base_result["retired"]) - correct_ids)
    detail = {}
    for fact_id in wrong:
        info = base_result["retired"][fact_id]
        old, new = by_id[fact_id], by_id[info["by"]]
        question = TEMPLATES["W1.still_true"].format(date_old=fmt_time(old.time), old=old.text(),
                                                    date_new=fmt_time(new.time), new=new.text())
        path = base._entry_path(question, OPTIONS["W1.still_true"])
        entry = json.loads(path.read_text()) if path.exists() else {}
        probs = probabilities(run_dir, {"key": path.stem}, 3) if path.exists() else None
        detail[fact_id] = {"triple": old.triple, "text": old.text(), "by": info["by"], "by_text": new.text(),
                           "date_old": fmt_time(old.time), "date_new": fmt_time(new.time),
                           "chosen": OPTIONS["W1.still_true"][entry["choice"]] if entry else None,
                           "probabilities": None if probs is None else [round(probs[i], 4) for i in range(3)],
                           "contestable": bool(key["facts"][fact_id].get("contestable"))}
    sweep = {}
    for threshold in (0.6, 0.7, 0.8, 0.9):
        decider = CacheDecider(run_dir, model)
        decider.mode = f"threshold:{threshold}"
        result = W.simulate(decider, facts, recursive=False)
        got = set(result["retired"])
        sweep[threshold] = {"retired_correctly": len(got & correct_ids), "retired_wrongly": len(got - correct_ids),
                            "wrong_ids": sorted(got - correct_ids), "prompts_not_cached": decider.missed,
                            "judgments": decider.mark()}
    return {"own_rule": {"retired_correctly": len(set(base_result["retired"]) & correct_ids),
                         "retired_wrongly": len(wrong), "prompts_not_cached": base.missed,
                         "judgments": base.mark()},
            "wrong_pairs": detail, "sweep": sweep}


def sweep_read(run_dir: Path) -> dict:
    read = json.loads((run_dir / "read.json").read_text())
    rows = read["rows"]
    logged = load_log(run_dir, "read_decisions.jsonl", "read.json")
    r1 = [e for e in logged if e["tag"] == "R1.judge"]
    if len(r1) != 10 * len(rows):
        raise SystemExit(f"expected {10 * len(rows)} judge decisions, found {len(r1)}")
    groups = {}
    for row_index, row in enumerate(rows):
        block = r1[10 * row_index:10 * row_index + 10]
        groups[row["id"]] = (row, block)
    should_unknown = {row["id"] for row in rows if row["group"] in ("cat_store", "unseen")}
    # The cat through ingest() is neither of the 7 nor among the 60 the table counts; it is dropped here too.
    scored_ids = {row["id"] for row in rows if row["group"] != "cat_ingest"}
    sweep = {}
    for threshold in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        unknown_right, false_unknown, skipped, seen = 0, 0, 0, 0
        for question_id, (row, block) in groups.items():
            if question_id not in scored_ids:
                continue
            # A record answers or partly answers the question unless it chose "yes" with too little probability:
            # "yes" below the threshold becomes "no"; "partly" and "no" are unchanged.
            has_answer, complete = False, True
            for entry in block:
                probs = probabilities(run_dir, entry, 3)
                if probs is None:
                    complete = False
                    continue
                label = OPTIONS["R1.judge"][entry["choice"]]
                if label == "partly" or (label == "yes" and probs[0] >= threshold):
                    has_answer = True
            if not complete:
                skipped += 1
                continue
            seen += 1
            if question_id in should_unknown:
                unknown_right += not has_answer
            else:
                false_unknown += not has_answer
        sweep[threshold] = {"unknown_right_of_7": unknown_right, "false_unknown": false_unknown,
                            "questions_scored": seen, "questions_skipped": skipped}
    return sweep


def r4_split(run_dir: Path) -> dict:
    read = json.loads((run_dir / "read.json").read_text())
    groups: dict[str, list] = {}
    for row in read["rows"]:
        groups.setdefault(row["arms"]["R4"].get("path", "?"), []).append(row["arms"]["R4"]["added_ms"])
    return {path: {"questions": len(ms), "median_ms": round(statistics.median(ms), 1), "max_ms": round(max(ms), 1)}
            for path, ms in sorted(groups.items())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    args = parser.parse_args()
    root = Path(args.results)
    facts = W.load_facts()
    key = W.load_key(facts)
    output = {}
    for run_dir in sorted(p for p in root.iterdir() if p.is_dir() and (p / "read.json").exists()
                          and (p / "write.json").exists() and p.name.endswith("run1")):
        model = run_dir.name.rsplit("-run1", 1)[0]
        write = json.loads((run_dir / "write.json").read_text())
        output[model] = {"write_sweep": sweep_write(run_dir, write["model"] or model, facts, key),
                         "read_sweep": sweep_read(run_dir), "r4_split": r4_split(run_dir)}
        print(f"== {model}")
        for threshold, s in output[model]["write_sweep"]["sweep"].items():
            print(f"   write t={threshold}: correct {s['retired_correctly']}/10 wrong {s['retired_wrongly']} "
                  f"{s['wrong_ids']} uncached {s['prompts_not_cached']} of {s['judgments']}")
        for fact_id, d in output[model]["write_sweep"]["wrong_pairs"].items():
            print(f"   wrong {fact_id} {d['triple']!r} by {d['by']} {d['by_text']!r}: {d['chosen']} "
                  f"p={d['probabilities']} (still true, no longer true, cannot tell)")
        for threshold, s in output[model]["read_sweep"].items():
            print(f"   read p_yes>={threshold}: unknown right {s['unknown_right_of_7']}/7 false unknown "
                  f"{s['false_unknown']}/60 skipped {s['questions_skipped']}")
        for path, s in output[model]["r4_split"].items():
            print(f"   R4 {path}: {s['questions']} questions, median {s['median_ms']} ms")
    (root / "analysis.json").write_text(json.dumps(output, indent=1) + "\n")


if __name__ == "__main__":
    main()

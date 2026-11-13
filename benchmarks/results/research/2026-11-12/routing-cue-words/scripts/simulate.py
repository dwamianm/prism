"""Task 27 Part A: predict a candidate router rule's effect from measured arms, with no new run.

For each question, the candidate route is compared with the version 3 route:
- same route: the outcome is the routed arm's (v3);
- no route: the outcome is the unrouted arm's (exact: an unrouted question takes the same path as in that arm);
- another route: unknown, listed, needs a real run.
Usage: python simulate.py <repo> <causes.json> <unrouted_label> <routed_label>
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(sys.argv[1])
sys.path.insert(0, str(REPO / "src"))
from prme.retrieval import relations as rel  # noqa: E402

R = Path.home() / "Sites/prism/data/mid-tier-v1/reports"
F = ["question_id", "category", "group", "annotated", "packed", "top_25", "all_packed", "all_returned", "projected"]
rows = {r["id"]: r for r in json.loads(Path(sys.argv[2]).read_text())["rows"]}


def load(label):
    report = json.loads((R / label / "report.json").read_text())
    return {row[0]: dict(zip(F, row)) for bench in ("locomo", "longmemeval") for row in report["rows"][bench]}


off, on = load(sys.argv[3]), load(sys.argv[4])

YES_NO = re.compile(r"^\W*(?:do|does|did|is|are|was|were|has|have|had|can|could|will|would|should|am)\b", re.I)


def route_with(question, skip):
    """route_question with a hook: skip(word, kind, words, index) True drops that word's contribution."""
    words = rel._words(question)
    if not words or rel._TEMPORAL_RE.search(question):
        return None
    known = rel.concept_map(None)
    possessed, rest = set(), set()
    negative = any(word in rel._NEGATIVE_WORDS for word in words)
    for index, raw in enumerate(words):
        word = raw.removesuffix("'s")
        before = words[index - 1] if index > 0 else ""
        owned = before in rel._POSSESSIVE_WORDS or (
            before.endswith("'s") and rel.role_relation(before.removesuffix("'s")) is None
            and rel.singular(before.removesuffix("'s")) not in known)
        found = set()
        people = rel.role_relation(word)
        if people is not None:
            if not skip(word, "role", words, index, question):
                found.add(rel._step(*people))
        elif rel.singular(word) in known:
            if not skip(word, "concept", words, index, question):
                found.update(known[rel.singular(word)])
        elif word not in rel._STOPWORDS and word not in rel._NAME_WORDS:
            if not skip(word, "cue", words, index, question):
                for name, cues in rel.DERIVED_CUES.items():
                    if name not in rel.UNROUTED_RELATIONS and rel.stem(word) in cues:
                        found.add(rel._step(name, polarity="negative" if name == "prefers" and negative else "positive"))
            stated = rel._PREDICATE_WORDS.get(word)
            if stated is not None and not skip(word, "predicate_word", words, index, question):
                found.add(rel._step(stated.relation, stated.role, "negative" if stated.negative else "positive"))
            owned = False
        (possessed if owned else rest).update(found)
    if not possessed and not rest:
        return None
    if possessed and rest - possessed:
        return (tuple(sorted(possessed)), tuple(sorted(rest - possessed)))
    return (tuple(sorted(possessed | rest)),)


assert all(route_with(r["question"], lambda *a: False) == rel.route_question(r["question"]) for r in rows.values())

EVERYDAY_ISSUE = {"take", "call", "part", "present", "sign", "lead", "like", "want", "keep", "run"}


def stems_in(word, stems):
    return rel.stem(word) in stems


RULES = {
    "issue stop list (cue + predicate word)": lambda w, k, ws, i, q: k in ("cue", "predicate_word")
    and stems_in(w, {rel.stem(x) for x in EVERYDAY_ISSUE} | {"tak", "lik", "keep", "runn"}),
    "issue list + use": lambda w, k, ws, i, q: k in ("cue", "predicate_word")
    and stems_in(w, {rel.stem(x) for x in EVERYDAY_ISSUE | {"use", "using", "used"}} | {"tak", "lik", "runn", "us"}),
    "no derived cue (predicate words, roles, concepts stay)": lambda w, k, ws, i, q: k == "cue",
    "no cue, no predicate word": lambda w, k, ws, i, q: k in ("cue", "predicate_word"),
    "yes/no unrouted": lambda w, k, ws, i, q: bool(YES_NO.search(q)),
    "cue only beside role or concept word": None,  # filled below
}


def beside(w, k, ws, i, q):
    if k not in ("cue", "predicate_word"):
        return False
    known = rel.concept_map(None)
    return not any(rel.role_relation(x.removesuffix("'s")) or rel.singular(x.removesuffix("'s")) in known for x in ws)


RULES["cue only beside role or concept word"] = beside


def evaluate(name, skip, sets=("locomo", "longmemeval-12")):
    changed_unrouted, changed_other, gains, losses = [], [], {}, {}
    for qid, row in rows.items():
        if row["set"] not in sets:
            continue
        new = route_with(row["question"], skip)
        old = rel.route_question(row["question"])
        if new == old:
            continue
        if new is not None:
            changed_other.append(qid)
            continue
        changed_unrouted.append(qid)
        if qid in off and off[qid]["annotated"]:
            for m in ("top_25", "packed", "all_packed", "projected"):
                d = (off[qid][m] or 0) - (on[qid][m] or 0)
                if d > 0:
                    gains.setdefault(m, []).append(qid)
                elif d < 0:
                    losses.setdefault(m, []).append(qid)
    return {"rule": name, "unrouted": len(changed_unrouted), "other_route": len(changed_other),
            "other_route_ids": changed_other, "gains_vs_v3": gains, "losses_vs_v3": losses}


def probe_effect(skip):
    out = {}
    for qid, row in rows.items():
        if row["set"] in ("locomo", "longmemeval-12"):
            continue
        new, old = route_with(row["question"], skip), rel.route_question(row["question"])
        if new != old:
            key = row["set"].split(" ")[0]
            out.setdefault(key, []).append((row["id"], "unrouted" if new is None else "other"))
    return out


if __name__ == "__main__":
    for name, skip in RULES.items():
        result = evaluate(name, skip)
        print(f"\n## {name}: unrouted {result['unrouted']}, other route {result['other_route']}")
        for m in ("top_25", "packed", "all_packed", "projected"):
            g, l = result["gains_vs_v3"].get(m, []), result["losses_vs_v3"].get(m, [])
            print(f"   {m}: recovers {len(g)} {g[:12]} | gives up {len(l)} {l[:12]}")
        if result["other_route_ids"]:
            print(f"   other route (needs a run): {result['other_route_ids'][:20]}")
        pe = probe_effect(skip)
        for key, items in pe.items():
            print(f"   {key}: {len(items)} changed: {[i for i, _ in items][:8]}{' ...' if len(items) > 8 else ''}")

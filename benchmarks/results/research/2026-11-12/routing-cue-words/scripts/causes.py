"""Task 27 Part A: which word routes each question under ROUTING_VERSION 3, and why.

Re-runs route_question's word loop with attribution and checks that the
attributed route equals route_question's own result on every question.
Usage: python causes.py <repo> <out.json>
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(sys.argv[1])
OUT = Path(sys.argv[2])
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from prme.retrieval import relations as rel  # noqa: E402

MAIN = Path.home() / "Sites/prism"


def attribute(question: str):
    """(route, causes): causes is a list of {word, kind, relation, detail} for every step found."""
    words = rel._words(question)
    if not words:
        return None, [], "empty"
    if rel._TEMPORAL_RE.search(question):
        return None, [], "temporal"
    known = rel.concept_map(None)
    negative = any(word in rel._NEGATIVE_WORDS for word in words)
    causes = []
    for raw in words:
        word = raw.removesuffix("'s")
        people = rel.role_relation(word)
        if people is not None:
            causes.append({"word": word, "kind": "role", "relation": people[0], "detail": people[1]})
        elif rel.singular(word) in known:
            for step in known[rel.singular(word)]:
                causes.append({"word": word, "kind": "concept", "relation": step.relation,
                               "detail": ",".join(step.roles) or None})
        elif word not in rel._STOPWORDS and word not in rel._NAME_WORDS:
            for name, cues in rel.DERIVED_CUES.items():
                if name not in rel.UNROUTED_RELATIONS and rel.stem(word) in cues:
                    causes.append({"word": word, "kind": "cue", "relation": name, "detail": rel.stem(word)})
            stated = rel._PREDICATE_WORDS.get(word)
            if stated is not None:
                causes.append({"word": word, "kind": "predicate_word", "relation": stated.relation,
                               "detail": stated.role})
    route = rel.route_question(question)
    found = {step.relation for steps in (route or ()) for step in steps}
    assert found == {cause["relation"] for cause in causes}, (question, route, causes)
    return route, causes, "routed" if route else "no_word"


def locomo():
    data = json.loads((MAIN / "data/benchmarks/locomo/locomo10.json").read_text())
    cats = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
    for conv in data:
        for n, q in enumerate(conv["qa"]):
            if q["category"] in cats:
                yield f"{conv['sample_id']}-q{n:04d}", q["question"], cats[q["category"]]


def longmemeval():
    ids = [row["question_id"] for row in json.loads((REPO / "benchmarks/slices/mid-v1.json").read_text())
           ["longmemeval"]["order"][:12]]
    rows = {row["question_id"]: row for row in
            json.loads((MAIN / "data/benchmarks/longmemeval/longmemeval_s_cleaned.json").read_text())}
    for qid in ids:
        yield qid, rows[qid]["question"], rows[qid]["question_type"]


def probes(name):
    data = json.loads((REPO / f"benchmarks/conversations/{name}.json").read_text())
    for probe in data["key"]["probes"]:
        yield probe["id"], probe["question"], None


def store(name):
    data = json.loads((REPO / f"benchmarks/fixtures/fast-cycle/{name}.json").read_text())
    for q in data["key"]["questions"]:
        yield q["id"], q["question"], None


def blind(name):
    data = json.loads((REPO / f"benchmarks/fixtures/graph-oracle/{name}.json").read_text())
    for q in data["questions"]:
        yield q["id"], q["question"], q["type"]


def paraphrases():
    data = json.loads((REPO / "benchmarks/fixtures/graph-oracle/blind-paraphrases-v1.json").read_text())
    for qid, texts in data["paraphrases"].items():
        for i, text in enumerate(texts):
            yield f"{qid}#p{i}", text, None


SETS = {
    "locomo": locomo, "longmemeval-12": longmemeval,
    "chat-v1": lambda: probes("chat-v1"), "defects-v1": lambda: probes("defects-v1"),
    "member-v1": lambda: probes("member-v1"),
    "fast-cycle:store-v1": lambda: store("store-v1"), "fast-cycle:corrections-v1": lambda: probes("corrections-v1"),
    "blind-v1 (spent)": lambda: blind("blind-v1"), "blind-v2 (spent)": lambda: blind("blind-v2"),
    "blind-paraphrases-v1 (spent)": paraphrases,
}

rows = []
for set_name, source in SETS.items():
    for qid, question, category in source():
        route, causes, status = attribute(question)
        rows.append({"set": set_name, "id": qid, "category": category, "question": question, "status": status,
                     "route": rel.route_as_list(route) if route else None, "causes": causes})

summary = {}
for set_name in SETS:
    sub = [r for r in rows if r["set"] == set_name]
    by_kind_word = Counter()
    for r in sub:
        for key in {(c["kind"], c["word"], c["relation"]) for c in r["causes"]}:
            by_kind_word[key] += 1
    summary[set_name] = {
        "questions": len(sub), "routed": sum(r["route"] is not None for r in sub),
        "temporal_skipped": sum(r["status"] == "temporal" for r in sub),
        "causes": [{"kind": k, "word": w, "relation": rel_, "questions": n}
                   for (k, w, rel_), n in by_kind_word.most_common()],
    }
OUT.write_text(json.dumps({"routing_version": rel.ROUTING_VERSION, "summary": summary, "rows": rows}, indent=1))
for set_name, s in summary.items():
    print(f"{set_name}: {s['routed']}/{s['questions']} routed, temporal skipped {s['temporal_skipped']}")

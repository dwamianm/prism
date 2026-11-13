"""Task 27 Part A: per set, how many questions each candidate rule unroutes or routes differently."""
import json, sys
sys.path.insert(0, "/tmp/task27/rules")
from prme.retrieval import relations as rel
from t27sim_route import route_with

ISSUE = {"take", "call", "part", "present", "sign", "lead", "like", "want", "keep", "run"}
STOP = {rel.stem(w) for w in ISSUE} | {"tak", "lik", "runn"}
USE = {"use", "us", "using"}
RULES = {
    "issue-stoplist": lambda w, k, *a: k in ("cue", "predicate_word") and rel.stem(w) in STOP,
    "issue-plus-use": lambda w, k, *a: k in ("cue", "predicate_word") and rel.stem(w) in STOP | USE,
    "use-no-want": lambda w, k, *a: k in ("cue", "predicate_word") and rel.stem(w) in (STOP - {"want"}) | USE,
    "no-derived-cue": lambda w, k, *a: k == "cue",
}
rows = json.load(open("/tmp/task27/causes-v3.json"))["rows"]
sets = list(dict.fromkeys(r["set"] for r in rows))
print("| Rule | " + " | ".join(sets) + " |")
print("|---|" + "---:|" * len(sets))
detail = {}
for name, skip in RULES.items():
    cells = []
    for s in sets:
        unrouted = other = 0
        for r in rows:
            if r["set"] != s:
                continue
            old, new = rel.route_question(r["question"]), route_with(r["question"], skip)
            if new == old:
                continue
            if new is None:
                unrouted += 1
            else:
                other += 1
            if s not in ("locomo", "longmemeval-12"):
                detail.setdefault(name, []).append(f"{s} {r['id']}: {'unrouted' if new is None else rel.route_as_list(new)}")
        cells.append(f"{unrouted} / {other}")
    print(f"| {name} | " + " | ".join(cells) + " |")
print("\nCells: questions that become unrouted / that get another route.\n")
for name in ("use-no-want",):
    print(f"{name}, fast-tier questions whose route changes:")
    for d in detail.get(name, []):
        if "spent" not in d:
            print("  ", d)

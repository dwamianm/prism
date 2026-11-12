"""Task 21 bar (a): compare the main side and the walk side, read by read and receipt by receipt."""
import gzip, json, sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/task21/a")
a_side, b_side = root / "main", root / "walk"


def load(path):
    reads, receipts = [], []
    with gzip.open(path, "rt") as handle:
        for line in handle:
            row = json.loads(line)
            (reads if row["kind"] == "read" else receipts).append(row)
    return reads, receipts


def first_difference(x, y, where=""):
    if type(x) is not type(y):
        return where or "/"
    if isinstance(x, dict):
        for key in sorted(set(x) | set(y)):
            if key not in x or key not in y:
                return f"{where}/{key} (missing on one side)"
            found = first_difference(x[key], y[key], f"{where}/{key}")
            if found:
                return found
        return None
    if isinstance(x, list):
        if len(x) != len(y):
            return f"{where} (length {len(x)} vs {len(y)})"
        for i, (p, q) in enumerate(zip(x, y)):
            found = first_difference(p, q, f"{where}[{i}]")
            if found:
                return found
        return None
    return None if x == y else where


summary = {}
for path in sorted(a_side.glob("*.jsonl.gz")):
    other = b_side / path.name
    if not other.exists():
        continue
    name = path.name.removesuffix(".jsonl.gz")
    ra, ca = load(path)
    rb, cb = load(other)
    assert [r["question"] for r in ra] == [r["question"] for r in rb]
    counts = {"reads": len(ra), "ranked_same": 0, "packed_same": 0, "context_same": 0, "response_same": 0,
              "receipts": f"{len(ca)}/{len(cb)}", "receipt_same": 0, "receipt_fields_same": 0, "first_diffs": []}
    for x, y in zip(ra, rb):
        counts["ranked_same"] += x["ranked"] == y["ranked"]
        counts["packed_same"] += x["packed"] == y["packed"]
        counts["context_same"] += x["context_sha"] == y["context_sha"]
        same = x["response"] == y["response"]
        counts["response_same"] += same
        if not same and len(counts["first_diffs"]) < 3:
            counts["first_diffs"].append((x["question"], first_difference(x["response"], y["response"])))
    for x, y in zip(ca, cb):
        counts["receipt_same"] += x["receipt"] == y["receipt"]
        counts["receipt_fields_same"] += x["fields"] == y["fields"]
        if x["receipt"] != y["receipt"] and len(counts["first_diffs"]) < 6:
            counts["first_diffs"].append(("receipt " + str(x["query"]), first_difference(x["receipt"], y["receipt"])))
    summary[name] = counts
    print(name, json.dumps({k: v for k, v in counts.items() if k != "first_diffs"}))
    for diff in counts["first_diffs"]:
        print("   ", diff)
(root / "compare.json").write_text(json.dumps(summary, indent=1))

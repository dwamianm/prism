"""Compare stored receipts of two same-pack measures (last N retrieval rows; request id and source hashes masked)."""
import json, sys, duckdb
from pathlib import Path
R = Path("/tmp/task24/probes")
PROBES = {"defects-v1": 7, "chat-v1": 18, "member-v1": 12}
A, B = sys.argv[1], sys.argv[2]

def mask(obj):
    if isinstance(obj, dict):
        return {k: ("MASKED" if k == "source_files_sha256" else mask(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(v) for v in obj]
    return obj

def rows(c, side):
    con = duckdb.connect(str(R / c / f"sp-{side}" / "pack" / "memory.duckdb"), read_only=True)
    got = con.sql("select payload::varchar from operations where op_type='RETRIEVAL_REQUEST' order by created_at").fetchall()
    out = []
    for (p,) in got[-PROBES[c]:]:
        d = json.loads(p); rid = d["request_id"]
        raw = d.pop("receipt").replace(rid, "RID"); d.pop("request_id"); d.pop("receipt_checksum")
        out.append((d["query"], raw, json.dumps(mask(json.loads(raw)), sort_keys=True), json.dumps(d, sort_keys=True)))
    return out

for c in PROBES:
    a, b = rows(c, A), rows(c, B)
    raw = sum(x[1] == y[1] for x, y in zip(a, b))
    masked = sum(x[2] == y[2] for x, y in zip(a, b))
    meta = sum(x[3] == y[3] for x, y in zip(a, b))
    print(f"{c} {A} vs {B}: raw identical {raw}/{len(a)}, masked identical {masked}/{len(a)}, log fields {meta}/{len(a)}")

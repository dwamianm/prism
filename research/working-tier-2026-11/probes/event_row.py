"""Probe: why one events row insert costs about 0.7 ms (indexes, JSON metadata, table size)."""
import duckdb, json, os, shutil, sys, time, uuid
from datetime import datetime, timezone
src = sys.argv[1]
d = "/tmp/p6/probe/event_row"
shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
shutil.copy(src, d + "/m.duckdb")
c = duckdb.connect(d + "/m.duckdb")
cols = "id, timestamp, role, content, content_hash, user_id, session_id, scope, metadata, created_at, event_time"
row = c.execute(f"SELECT {cols} FROM events LIMIT 1").fetchone()
def q(v):
    v = sorted(v); n = len(v); return {"p50": round(v[n // 2], 3), "p95": round(v[int(n * .95)], 3)}
def run(table, n=400, meta=True):
    t_all = []
    for i in range(n):
        vals = list(row); vals[0] = str(uuid.uuid4()); vals[8] = row[8] if meta else None
        t = time.perf_counter()
        c.execute(f"INSERT INTO {table} ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?)", vals)
        t_all.append((time.perf_counter() - t) * 1000)
    return q(t_all)
out = {"rows": c.execute("select count(*) from events").fetchone()[0]}
out["events_as_is"] = run("events")
out["events_as_is_no_metadata"] = run("events", meta=False)
c.execute("CREATE TABLE ev_noidx AS SELECT * FROM events")
out["copy_no_index_same_rows"] = run("ev_noidx")
c.execute("CREATE TABLE ev_pk (id UUID PRIMARY KEY, timestamp TIMESTAMPTZ, role VARCHAR, content TEXT, content_hash VARCHAR, user_id VARCHAR, session_id VARCHAR, scope VARCHAR, metadata JSON, created_at TIMESTAMPTZ, event_time TIMESTAMPTZ)")
c.execute("INSERT INTO ev_pk SELECT * FROM events")
out["copy_primary_key_only"] = run("ev_pk")
c.execute("CREATE INDEX i1 ON ev_pk(user_id)")
out["copy_pk_plus_user_index"] = run("ev_pk")
c.execute("CREATE INDEX i2 ON ev_pk(scope)")
out["copy_pk_user_scope"] = run("ev_pk")
c.execute("CREATE INDEX i3 ON ev_pk(content_hash)"); c.execute("CREATE INDEX i4 ON ev_pk(user_id, session_id)"); c.execute("CREATE INDEX i5 ON ev_pk(timestamp)")
out["copy_all_five"] = run("ev_pk")
print(json.dumps(out, indent=1))

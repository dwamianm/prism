"""Probe: what one events row costs in DuckDB 1.4.4, by statement form (file copy of the 20,500 pack)."""
import duckdb, json, os, shutil, sys, time, uuid
src = sys.argv[1]
d = "/tmp/p6/probe/insert_forms"
shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
shutil.copy(src, d + "/m.duckdb")
c = duckdb.connect(d + "/m.duckdb")
cols = "id, timestamp, role, content, content_hash, user_id, session_id, scope, metadata, created_at, event_time"
row = c.execute(f"SELECT {cols} FROM events LIMIT 1").fetchone()
def q(v):
    v = sorted(v); n = len(v); return {"p50": round(v[n // 2], 3), "p95": round(v[int(n * .95)], 3)}
def fresh(k):
    out = []
    for _ in range(k):
        vals = list(row); vals[0] = str(uuid.uuid4()); out.append(vals)
    return out
out = {}
t = []
for _ in range(300):
    v = fresh(1)[0]; s = time.perf_counter(); c.execute(f"INSERT INTO events ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?)", v); t.append((time.perf_counter() - s) * 1000)
out["one_row_params"] = q(t)
t = []
for _ in range(300):
    s = time.perf_counter(); c.execute("SELECT 1").fetchone(); t.append((time.perf_counter() - s) * 1000)
out["select_1"] = q(t)
t = []
for _ in range(300):
    s = time.perf_counter(); c.execute("SELECT ?::VARCHAR, ?::TIMESTAMPTZ", ["a", row[1]]).fetchone(); t.append((time.perf_counter() - s) * 1000)
out["select_two_params"] = q(t)
c.execute("CREATE TEMP TABLE tiny (a VARCHAR)")
t = []
for _ in range(300):
    s = time.perf_counter(); c.execute("INSERT INTO tiny VALUES (?)", ["x"]); t.append((time.perf_counter() - s) * 1000)
out["temp_table_one_param"] = q(t)
c.execute("CREATE TABLE tiny_file (a VARCHAR)")
t = []
for _ in range(300):
    s = time.perf_counter(); c.execute("INSERT INTO tiny_file VALUES (?)", ["x"]); t.append((time.perf_counter() - s) * 1000)
out["file_table_one_param_autocommit"] = q(t)
t = []
c.execute("BEGIN")
for _ in range(300):
    s = time.perf_counter(); c.execute("INSERT INTO tiny_file VALUES (?)", ["x"]); t.append((time.perf_counter() - s) * 1000)
c.execute("COMMIT")
out["file_table_one_param_in_txn"] = q(t)
for k in (10, 50):
    t = []
    for _ in range(60):
        rows = fresh(k)
        sql = f"INSERT INTO events ({cols}) VALUES " + ",".join(["(?,?,?,?,?,?,?,?,?,?,?)"] * k)
        flat = [x for r in rows for x in r]
        s = time.perf_counter(); c.execute(sql, flat); t.append((time.perf_counter() - s) * 1000)
    out[f"multi_values_{k}_per_statement"] = q(t)
    out[f"multi_values_{k}_per_row"] = {key: round(val / k, 3) for key, val in q(t).items()}
print(json.dumps(out, indent=1))

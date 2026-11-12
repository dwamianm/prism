"""Probe: (1) one durable append with values as SQL literals; (2) accept-thread latency while another thread runs Python."""
import duckdb, json, os, shutil, sys, threading, time, uuid
src = sys.argv[1]
d = "/tmp/p6/probe/gil"
shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
shutil.copy(src, d + "/m.duckdb")
db = duckdb.connect(d + "/m.duckdb")
cols = "id, timestamp, role, content, content_hash, user_id, session_id, scope, metadata, created_at, event_time"
row = db.execute(f"SELECT {cols} FROM events LIMIT 1").fetchone()
def q(v):
    v = sorted(v); n = len(v); return {"n": n, "p50": round(v[n // 2], 3), "p95": round(v[int(n * .95)], 3), "max": round(v[-1], 3)}
def lit(v):
    if v is None: return "NULL"
    if hasattr(v, "isoformat"): return f"TIMESTAMPTZ '{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"
c = db
def append_params():
    vals = list(row); vals[0] = str(uuid.uuid4())
    c.execute("BEGIN TRANSACTION")
    c.execute(f"INSERT INTO events ({cols}) VALUES (?,?,?,?,?,?,?,?,?,?,?)", vals)
    c.execute("INSERT INTO event_materializations (event_id) VALUES (?)", [vals[0]])
    o = c.execute("UPDATE extraction_work_sequence SET next_ordinal = next_ordinal + 1 RETURNING next_ordinal - 1").fetchone()[0]
    c.execute("INSERT INTO event_extractions (event_id, work_order) VALUES (?, ?)", [vals[0], o])
    c.execute("COMMIT")
def append_literal():
    vals = list(row); vals[0] = str(uuid.uuid4())
    c.execute("BEGIN TRANSACTION")
    c.execute(f"INSERT INTO events ({cols}) VALUES (" + ",".join(lit(v) for v in vals) + ")")
    c.execute(f"INSERT INTO event_materializations (event_id) VALUES ('{vals[0]}')")
    c.execute("INSERT INTO event_extractions (event_id, work_order) SELECT '" + vals[0] + "', next_ordinal FROM extraction_work_sequence")
    c.execute("UPDATE extraction_work_sequence SET next_ordinal = next_ordinal + 1")
    c.execute("COMMIT")
def dict_only():
    x = {}; x[("a", "b")] = row
out = {}
for name, fn in (("append_params", append_params), ("append_literal", append_literal), ("dict_only", dict_only)):
    t = []
    for _ in range(400):
        s = time.perf_counter(); fn(); t.append((time.perf_counter() - s) * 1000)
    out[name + "_idle"] = q(t)
stop = False
def busy():
    x = 0
    while not stop:
        for i in range(10000): x += i * i
busy_thread = threading.Thread(target=busy); busy_thread.start()
for name, fn in (("append_params", append_params), ("append_literal", append_literal), ("dict_only", dict_only)):
    t = []
    def run():
        for _ in range(200):
            s = time.perf_counter(); fn(); t.append((time.perf_counter() - s) * 1000); time.sleep(0.002)
    th = threading.Thread(target=run); th.start(); th.join()
    out[name + "_busy_python_thread"] = q(t)
for interval in (0.001, 0.0005):
    sys.setswitchinterval(interval)
    t = []
    def run():
        for _ in range(200):
            s = time.perf_counter(); append_literal(); t.append((time.perf_counter() - s) * 1000); time.sleep(0.002)
    th = threading.Thread(target=run); th.start(); th.join()
    out[f"append_literal_busy_switch_{interval}"] = q(t)
stop = True; busy_thread.join()
print(json.dumps(out, indent=1))

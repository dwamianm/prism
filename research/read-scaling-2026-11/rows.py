import duckdb
for s in ("400", "4000", "20500"):
    con = duckdb.connect(f"/tmp/p3/rows/{s}/memory.duckdb", read_only=True)
    tables = [r[0] for r in con.execute("select table_name from information_schema.tables where table_schema='main' order by 1").fetchall()]
    out = []
    for t in tables:
        n = con.execute(f'select count(*) from "{t}"').fetchone()[0]
        if n:
            out.append(f"{t}={n}")
    print(s, " ".join(out))
    con.close()

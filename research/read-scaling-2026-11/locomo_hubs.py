import duckdb, glob
for f in sorted(glob.glob("/tmp/p3/locomo/*.duckdb"))[:4]:
    con = duckdb.connect(f, read_only=True)
    rows = con.execute("""
        with d as (select id, count(*) c from (select source_id id from edges union all select target_id from edges) group by id)
        select left(n.content, 30), c, n.metadata->>'identity_status' from d join nodes n using (id) where n.node_type='entity' order by c desc limit 3""").fetchall()
    print(f.split("/")[-1][7:14], rows)
    con.close()

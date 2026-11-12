import duckdb, glob
for f in sorted(glob.glob("/tmp/p3/locomo/*.duckdb")):
    con = duckdb.connect(f, read_only=True)
    nodes = con.execute("select count(*) from nodes").fetchone()[0]
    ents = con.execute("select count(*) from nodes where node_type='entity'").fetchone()[0]
    edges = con.execute("select count(*) from edges").fetchone()[0]
    deg = con.execute("""
        with d as (select id, count(*) c from (select source_id id from edges union all select target_id from edges) group by id)
        select round(avg(c),1), max(c), quantile_cont(c, 0.9) from d join nodes n using (id) where n.node_type='entity'""").fetchone()
    print(f.split("/")[-1][7:14], f"nodes {nodes}, entities {ents}, edges {edges}, entity degree mean {deg[0]} p90 {deg[2]} max {deg[1]}")
    con.close()

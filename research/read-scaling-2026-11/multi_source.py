"""P3 estimate for the candidate fix (SQL only, not built): one walk from all seeds, ids and fewest depth only."""
import re, sys, time, duckdb
db, question_words = sys.argv[1], sys.argv[2:]
con = duckdb.connect(db, read_only=True)
user = con.execute("select user_id from nodes group by 1 order by count(*) desc limit 1").fetchone()[0]
seeds = [r[0] for r in con.execute("select id, content from nodes where node_type='entity' and user_id=?", [user]).fetchall()
         if set(question_words) & set(re.findall(r"\w+", con.execute("select content from nodes where id=?", [r[0]]).fetchone()[0].lower()))]
alias = ("NOT (e.edge_type = 'relates_to' AND COALESCE(json_extract_string(e.metadata, '$.relation'), '') = 'alias' "
         "AND COALESCE(json_extract_string(e.metadata, '$.identity_verified'), '') = 'false')")
seed_list = ", ".join(f"'{s}'::UUID" for s in seeds)
query = f"""
WITH RECURSIVE walk AS (
    SELECT s AS id, 0 AS depth FROM unnest([{seed_list}]) t(s)
    UNION
    SELECT CASE WHEN e.source_id = w.id THEN e.target_id ELSE e.source_id END, w.depth + 1
    FROM walk w JOIN edges e ON (e.source_id = w.id OR e.target_id = w.id) AND {alias}
    LEFT JOIN nodes o ON o.id = CASE WHEN e.source_id = w.id THEN e.target_id ELSE e.source_id END
    WHERE w.depth < 3 AND (o.id IS NULL OR o.user_id = ?)
)
SELECT w.id, MIN(w.depth) FROM walk w JOIN nodes n ON n.id = w.id
WHERE n.user_id = ? AND n.lifecycle_state IN ('tentative', 'stable', 'contested') GROUP BY w.id
"""
for attempt in range(3):
    t = time.perf_counter(); rows = con.execute(query, [user, user]).fetchall(); ms = 1000 * (time.perf_counter() - t)
print(f"{db.split('/')[-2]}: seeds {len(seeds)}, reached nodes {len(rows)}, one multi-source walk {ms:.0f} ms (3rd run)")

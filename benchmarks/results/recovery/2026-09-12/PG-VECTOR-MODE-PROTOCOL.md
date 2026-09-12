# PostgreSQL vector-mode probe

Registered before timing results. Tests have reproduced filtered ANN starvation;
this probe measures the tradeoff of the exact fix under the planner's ordinary
settings rather than forcing HNSW. It is not a memory-QA benchmark.

Use private temporary schemas on the local test database, with 1,000 then 10,000
authored distractor rows plus three eligible rows. Vectors have 384 coordinates
but vary in only two dimensions. The distractors are closer to the query and
belong to another scope under the same owner. Preserve the ordinary node filter
indexes and build HNSW after population. Record server/extension versions and
planner settings without recording database credentials or endpoints.

For selective-scope and all-scope queries, compare exact mode with approximate
search allowed in the same process. Warm each arm twice and measure 20 queries
per arm, alternating mode order. Record every returned ID list, elapsed time and
the actual query plan. The selective exact arm must return the three expected
neighbors. Approximate mode may return fewer, or the planner may choose an exact
plan; preserve that result instead of forcing a preferred conclusion.

The corpus is sparse, synthetic and small, with serial warm queries on one host.
Timings do not establish production latency, general index recall, a real-model
quality gain, or superiority to another memory product. Use them to make the
correctness/cost tradeoff explicit; do not promote a universal size threshold.

## Construction-limit follow-up

The 1,000-row run completed. The 10,000-row run exited 1 during index creation,
before query measurement, because the server could not allocate a roughly 64-MB
shared-memory segment. Preserve both artifacts. Add `--serial-build`, which
sets `max_parallel_maintenance_workers=0` inside the index-build transaction only.
Rerun both sizes with this construction setting and unchanged query planner
settings. Label these follow-up runs separately; do not substitute a successful
retry for the original environmental failure or pool timings across builds.

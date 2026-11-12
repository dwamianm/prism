# Write rate of caller-stated facts: protocol, written before any measurement

Task P2 from prme-manager, 2026-11-11. Base: `origin/address-first-memory` at d8cd0a26. Thesis section 5
(`memory_bank/THESIS-2026-11-10-EMBODIED-MEMORY.md`) sets the robot budgets: accept under 1 ms, searchable within 1 s.
The CHANGELOG entry for stated facts and `docs/RELATIONS.md` ("Write rate") record 4.6 stated facts a second at 400
observations through `ingest_batch()` with the product defaults, about 200 ms each.

## Questions

1. What limits `ingest(assertions=...)` to about 4.6 facts a second? Time per stage, SQL statements per fact, and
   index work per fact, with the top costs named and measured.
2. What is the smallest change that makes caller-stated facts searchable fast enough for a robot? At most one
   prototype change is built and measured.

## Workload

- A synthetic robot world, generated from a fixed seed (20261111): 500 objects (25 adjectives times 20 nouns, such
  as "red mug") and 120 places (10 rooms times 12 surfaces, such as "garage shelf").
- One observation is one source: the sentence "The {object} is on the {place}." with one assertion
  `{"subject": object, "subject_type": "product", "relation": "located_in", "object": place,
  "object_type": "location", "status": "current"}`, the same shape as the contract scorecard's stated S1 scene
  (`tests/contract/scenes.py`, `churn_stated`). Each observation moves one object, picked at random, to a place
  other than its current one. Event times advance 10 seconds per observation.
- The same observation list is written by every path, so the stored records can be compared.
- "Stored observations" means the stated-fact sources already written to the store through the path under test.
  Store sizes: 400, 4,000 and 20,000.

## Configuration

- The product defaults (`memory_mode=classic`), with the pack paths of `prme.client.config_from_directory`, built the
  way `tests/contract/harness.py` `product_config` builds them (no `PRME_` variable read). The local default
  embedding model (FastEmbed `BAAI/bge-small-en-v1.5`), loaded and warmed once before timing.
- Providers blocked as in the fleet rules (`OPENAI_API_KEY=` `ANTHROPIC_API_KEY=sk-ant-blocked`, base URLs at port 9,
  `PRME_EXTRACTION_API_KEY=`). The extraction provider is replaced by a guard that fails on any call, as the contract
  harness does, so a model call is a failed run.
- Machine: Apple M5 Pro laptop, 18 cores, 48 GB, Python 3.11.6, DuckDB 1.4.4, tantivy-py 0.25.0, usearch 2.26.2.
  A robot's own hardware is slower; numbers here are an upper bound for it.

## Measurements

### M1. Current path, sustained rate and stage profile

- The current path is the one the contract harness and the 4.6 figure use: `MemoryEngine.ingest_batch(messages,
  wait_for_extraction=True)` in batches of 100, then `process_pending()` until nothing is pending, so that each
  source's raw note is indexed too.
- One continuous run writes 20,500 observations. Rate windows: observations 400 to 599, 4,000 to 4,199 and 20,000 to
  20,499 (the store holds 400, 4,000 and 20,000 when each window starts). For each window: facts a second, and
  milliseconds per fact, wall clock.
- Stage profile, per fact, in each window, by timers wrapped around the engine's own functions: assertion check
  (`_prepare_assertions` with `read_held`), event append, extraction work claims and status reads, the structured
  extraction record, plan preparation (`_prepare_plan` with `_populate`: entity lookups, claim merge, supersedence),
  embedding (`PlanningIndexes.prepare`), plan record, vector staging, lexical staging, the graph commit, index
  eviction, mention links, group links, and raw note processing (`process_pending`). Stages that nest are reported
  inside their parent, and the unattributed rest is reported as such.
- SQL statements per fact: calls to `execute`, `executemany` and `sql` on every DuckDB connection the engine holds,
  counted through a proxy, in total and by stage.
- Index work per fact: texts embedded, embedding calls, vector adds, lexical documents added and lexical commits.

### M2. Current path, accept without waiting

At 400 stored: `ingest(content, assertions=..., wait_for_extraction=False)`, one call per observation. Accept
latency (the call's own time, p50, p95, max), and for 20 sampled observations the time from accept until
`retrieve()` returns the observation's claim. This shows what the current path gives a caller who does not wait.

### M3. The prototype

- At most one change, chosen from M1's top costs and described in the report before it is measured. It lives on this
  research branch only. It is opt-in, and the current path stays the default.
- Measured the same way as M1 (same observations, same windows, same counters).
- Stream test at each store size: observations offered at 200 a second (a batch every 50 ms of 10 observations, or
  the change's own natural batch, stated in the report) for 10 seconds. Accept latency per call, and for every offered
  observation the time from its accept until its claim is committed to the graph, the vector index and the lexical
  index. `retrieve()` is asked "Where is the {object} now?" for 20 sampled observations right after the commit, and
  must return the observation's claim among its results.

### M4. Equivalence

- For the same observations, the current path's pack and the prototype's pack are compared at 400 and 4,000 stored,
  and at 20,000 if both packs exist:
  - stored records: every node (type, content, lifecycle state, confidence, salience, scope, validity window,
    metadata) and every edge (type, ends, validity, confidence), apart from generated ids and creation or update
    times. Ids are matched through the observation each record comes from.
  - the vector index and the lexical index hold the same documents for the same matched nodes.
  - `retrieve()` for 25 questions ("Where is the {object} now?" for 20 sampled objects, and 5 questions naming a
    place) at a fixed reference time: the same matched records at the same ranks, the same scores, and the same
    receipts, apart from generated ids, request ids and times.
- The comparison is first run with the current path against itself (two fresh builds of the same observations). It
  must report no difference there; if it does, the normalization is wrong and is fixed before the prototype is
  compared.

## Bars (fixed by prme-manager before any measurement)

The verdict is "in the engine" only when all three hold:

1. Stated facts are accepted at 200 or more a second, sustained at 20,000 stored (M3, window 20,000 to 20,499, and
   the stream test at 20,000 keeps up with no growing backlog).
2. Each fact is searchable by `retrieve()` within 1 second of its accept (M3 stream test: the maximum over every
   offered observation, at every store size, is under 1 second).
3. The stored records and the `retrieve()` results (ranks, scores, receipts) match the current path on the same facts,
   apart from generated ids and times (M4).

Otherwise the verdict is "not now", with the numbers.

The thesis budget of accept under 1 ms is reported against the measured accept latency, but it is not one of the
bars above.

## Limits of the run

- No model call, no paid API, providers blocked.
- One sample per measurement; the store and the observations are deterministic, the clock is not.
- The prototype gets at most two fix-and-validate cycles; after that the run stops and reports.
- A decision model is not needed: this is a write path with no judgment to make. The report says so.

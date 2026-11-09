# Adversarial review: issue #432 exclude_archived_profiles

Status: COMPLETE

- Checklist: 8 (independent adversarial reviewer)
- Base commit: `327d246d11e0ac20bfd4f215c7379ee4f185836d` (main)
- Reviewed snapshot: `1406ba513827ffc0c0d545335ac65218e764a2a2e4c53f2cbe6ed19dd8b5b0b8`
  (uncommitted + untracked)
- Revalidated snapshot: `7aba025203e6850f40efa66686efe7166d97d77a58919e5707149858c1be95a5`
  (10 files, 286 changed lines; base unchanged)
- Review tier: L, at most 4 scenarios (plus at most 2 added on revalidation)

The change is a small, default-off read filter. On the first snapshot the
predicate placement was sound: on both backends it sat in the `WHERE` before
`ORDER BY`/`LIMIT`, the protected facade filtered before its sort and slice,
and the default-off path was byte-identical. What follows is the first-pass
review plus its revalidation.

## Revalidation (snapshot 7aba0252)

Delta since the first snapshot: `/v1/nodes` `state` became a repeatable list,
`/v1/stats` gained a repeatable `state`, the two SQL predicates became one
shared constant per module with `node_type = 'summary'` and a JSON boolean type
check, the facade gained the same node-type condition, and the test file gained
the multi-state HTTP cases, the before-the-limit case and an archived
non-profile FACT case.

- **Scenario 1 (HTTP flags inert): fixed.** `GET /v1/nodes` now takes
  `state: list[str] | None = Query(default=None)`
  (`src/prme/api/routes.py:776`, `:796-801`) and `GET /v1/stats` takes the same
  plus `exclude_archived_profiles` (`routes.py:1202-1228`), so a multi-state page
  and a filtered count can both be requested. The test shows 6 profiles
  unfiltered against 1 filtered over a multi-state `/v1/nodes` page and a
  filtered count 5 below the unfiltered one
  (`tests/test_archived_profile_filter.py:116-147`), and both doc tables now say
  the flag matters when the states include `archived`
  (`documentation/http-api.md:314,318,404-405`). Verdict resolved.
- **Scenario 2 (filter absent from scan/MCP/CLI enumeration surfaces): stands,
  out of scope by adjudication.** The snapshot does not touch `scan_nodes`
  (`engine.py:3239-3246`, `client.py:970-980`, `read_graph.py:67-102`,
  `routes.py:687-724`, `mcp/server.py:1133-1140`, `cli.py:266-297`), so
  `GET /v1/nodes/scan` and `memory_scan_nodes` still page through retired
  profiles with no way to pass the flag. A follow-up issue carries it. The
  finding is unchanged and still quiet at Low-Medium impact.
- **Scenario 3 (predicate divergence, PG unexecuted): fixed, verified.** One
  constant per module now carries `node_type = 'summary'` plus a JSON type
  check (`src/prme/storage/duckpgq_graph.py:58-62`,
  `src/prme/storage/pg/graph_store.py:57-61`), used by both the listing and the
  count on each backend, and the facade has the matching condition
  (`src/prme/privacy/read_graph.py:12-21`). I re-ran the DuckDB expression
  against a live DuckDB: JSON boolean `true` is excluded; JSON string `"true"`,
  number, `NULL` metadata, `[]`, an archived non-SUMMARY node with the marker,
  and a stable SUMMARY node are all kept, which is exactly the facade's reading.
  The PostgreSQL form (`jsonb_typeof(metadata -> 'entity_profile') = 'boolean'`)
  has the same result by construction, and the new test inherits the
  `["duckdb", "postgres"]` parametrization (`tests/test_durable_ingestion.py:264-288`),
  so its PostgreSQL variant executes in CI (`ci.yml:128-133`) and skips only
  locally. Verdict resolved.
- **Scenario 4 (archived profile without a successor is hidden): accepted.** The
  CHANGELOG now states the filter is by lifecycle state, not by replacement
  (`CHANGELOG.md:12`), and `http-api.md` says it leaves out archived entity
  profile SUMMARY nodes. The DuckDB and protocol docstrings
  (`duckpgq_graph.py:348-350`, `graph_store.py:233-236,258-261`) still describe
  only the marker; one clarifying clause there would close the residual, but the
  decision is recorded. Verdict accepted with that one line.
- **Test gap from the first pass: closed for what it claimed.** The before-the-
  limit behaviour is now asserted (`tests/test_archived_profile_filter.py:80-94`),
  the archived non-profile FACT survives the filter (`:50-55`, `:69-71`), and
  an invalid `state` returns 422 (`:149-150`).
- **Failed attacks (metadata shapes, marker loss, LIMIT/ordering): unchanged.**
  The shared constant keeps the LIMIT-in-WHERE property and the PG `$idx`
  bookkeeping is still untouched because the predicate binds no parameter.

### New scenario N1: `state` changes shape from a scalar to an array on `/v1/nodes` (new)

- **Trigger**: a client generated from the previous OpenAPI document
  (`state?: string`) regenerated against the new one (`state?: string[]`); or a
  caller that already sent `?state=stable&state=archived` and relied on the
  previous last-value-wins binding.
- **Mechanism**: `state: str | None = None` became
  `state: list[str] | None = Query(default=None)` (`routes.py:776`). Starlette
  binds a scalar query parameter to the last repeated value, so the old endpoint
  silently used `archived` only; the new one returns the union of both states,
  and the shared `limit` now divides across them.
- **Likelihood**: Low. The runtime form a correct client sends (`state=archived`)
  still works, and no test snapshots the OpenAPI document; only generated typed
  clients and malformed callers are affected.
- **Impact**: Low. A regenerated client needs a signature change; the widened
  page is the intended semantics.
- **Detection**: quiet.
- **Cheapest mitigation**: one CHANGELOG clause saying `state` is repeatable,
  and describe the union semantics in the `http-api.md` row (S).
- **Verdict**: Accept with one line.
- **Label**: newly introduced.

### New scenario N2: `/v1/stats` still turns every count failure into a silent zero, now on two more request shapes (pre-existing mechanism, newly reachable)

- **Trigger**: `GET /v1/stats?state=archived` or
  `?exclude_archived_profiles=true` against a `GraphStore` whose `count_nodes`
  rejects the new keywords (a third-party implementation, or a transient
  backend error), or any error on the new parameterized path.
- **Mechanism**: `routes.py:1230-1233` catches `Exception`, logs a warning and
  leaves `node_count = 0`, while `GET /v1/nodes` with the same kwargs returns
  500. The route cannot distinguish "no nodes" from "the count failed".
- **Likelihood**: Low. Both shipped backends accept the keywords (the new test
  exercises both in CI).
- **Impact**: Medium. A monitoring or dashboard call reads 0, and
  `?state=archived&exclude_archived_profiles=true` returning 0 is exactly the
  false signal ("no archived profiles") this feature exists to remove; a client
  cannot tell it from a real zero.
- **Detection**: quiet (a warning log only, never the response).
- **Cheapest mitigation**: return 503, or report `degraded: true` in the
  response, when the count raises; small change to one `except` clause (S).
- **Verdict**: Follow-up.
- **Label**: pre-existing mechanism, newly reachable through the new parameters.

## Checklist results (first pass, unchanged where not noted above)

- Premise challenge: holds for the Python API; the HTTP gap is fixed by the
  repeatable `state` (scenario 1, resolved).
- Sibling entry points: `scan_nodes`/`GET /v1/nodes/scan`/MCP/CLI still lack it
  (scenario 2, follow-up). Retrieval, organizer and reingest paths were checked
  and are not affected (none lists archived nodes through `query_nodes` with a
  limit; `replaced_sources.py:99-103` counts then reads with `limit=total`).
- Guard loopholes: predicate agreement restored and verified on DuckDB; the
  PostgreSQL form is consistent by construction and now runs in CI (scenario 3,
  resolved).
- Existing data/installs: NULL, `[]`, JSON `null`, JSON string and scalar
  metadata do not raise on either backend; the marker survives reingest,
  retention, manual archive, publication and migration (see failed attacks).
- State/lifecycle: the filter is by state, not replacement (scenario 4,
  accepted and documented).
- Idempotency/retries: read-only.
- Ordering/timing/consistency: filter before LIMIT on both SQL paths and before
  sort/slice on the facade; `_owned_view` still runs after LIMIT (pre-existing).
  The new test pins the before-the-limit behaviour.
- Scale: the JSON extraction is non-sargable, but only when the requested states
  include `archived`; no new table scan is added for a default request.
- Partial failure: see N2.
- Backward compatibility: `GraphStore` Protocol gained defaulted keywords
  (`graph_store.py:205,249`); the `/v1/nodes` `state` type change is N1.
- Blast radius and side effects: default-off; no writer, indexer, receipt or
  migration change.
- Detection: every remaining scenario is quiet; N2 is the one with a response
  that actively misleads.

## Scenarios from the first pass (status updated on revalidation)

### 1. The HTTP flags are inert on the endpoints that shipped them (new) — resolved on 7aba0252

Kept for the record: `/v1/nodes` could not express a multi-state page and
`/v1/stats` had no state filter, so both flags were no-ops. The new repeatable
`state` on both endpoints and the multi-state HTTP test close it; the
`/v1/stats` documentation row now states the condition under which the flag
matters.

### 2. The filter is absent from every enumeration surface (new) — follow-up, out of scope

- **Trigger**: `GET /v1/nodes/scan?user_id=maya&state=archived&state=superseded`
  (`routes.py:687-724`), `memory_scan_nodes(user_id="maya",
  lifecycle_states=["archived"])`, `prme nodes --state archived`, or
  `MemoryClient.scan_nodes(..., lifecycle_states=[...])`.
- **Mechanism**: the option exists only on `query_nodes`/`count_nodes`.
  `MemoryEngine.scan_nodes` and `MemoryClient.scan_nodes` have closed signatures
  (`engine.py:3239-3246`, `client.py:970-980`), the facade's `scan_nodes` has no
  parameter (`read_graph.py:67-102`), and the route, MCP tool and CLI build
  their calls without one (`routes.py:687-724`, `mcp/server.py:1133-1140`,
  `cli.py:266-297`).
- **Likelihood**: Medium-High, because `docs/INTEGRATION.md:1975-1983` sends
  integrators to exactly those surfaces for complete traversal.
- **Impact**: Low-Medium (noise and wasted pages/context, no crowd-out of older
  memories because the order is by id).
- **Detection**: quiet.
- **Cheapest mitigation**: thread one defaulted keyword through the scan path and
  the three callers (M), or scope it explicitly in a follow-up issue (S).
- **Verdict**: Follow-up (adjudicated).
- **Label**: newly introduced gap.

### 3. Predicate divergence and PostgreSQL unexecuted (new) — resolved on 7aba0252

Kept for the record: the first snapshot's SQL matched the JSON string `"true"`
while the facade required a boolean, and no test executed either PostgreSQL
fragment. The shared typed constants and the inherited
`["duckdb", "postgres"]` parametrization remove both halves; I verified the
DuckDB predicate against live DuckDB as described in the revalidation.

### 4. Any archived profile is hidden, including one with no replacement (new) — accepted

- **Trigger**: an entity whose related records drop below two, then
  `consolidate_knowledge()` runs and retires the profile with no replacement
  (`engine.py:4965-4968`, `retire_profile` at `engine.py:4839-4846`); also a
  manual archive or retention expiry (`retention.py:293,376`).
- **Mechanism**: the predicate tests `archived` plus the marker, not "replaced by
  a newer profile". After the trigger, the entity's only profile is archived and
  a filtered listing shows none for that entity.
- **Likelihood**: Low-Medium.
- **Impact**: Low-Medium, silent.
- **Detection**: quiet.
- **Cheapest mitigation**: one docstring/CHANGELOG sentence saying the filter is
  by state, not replacement.
- **Verdict**: Accept (adjudicated; CHANGELOG now says it, the backend
  docstrings could repeat it).
- **Label**: newly introduced.

## Attacks that failed (three hardest, and why)

1. **Odd or legacy metadata breaking the new JSON predicate.** DuckDB's
   `metadata JSON` column rejects malformed JSON at insert, and live queries
   with `NULL`, `[]`, JSON `null`, a JSON string, a string-valued
   `entity_profile` and a scalar all evaluate without error; PostgreSQL's `->>`
   and `jsonb_typeof` return NULL for non-objects. The column has been
   `JSON`/`JSONB` since the original schemas (`src/prme/storage/schema.py:126`,
   `src/prme/storage/pg/schema.py:162`) and no migration rewrites it.
2. **Archived profiles losing the marker so the filter misses them.** `reingest`
   spreads the existing metadata and adds its key
   (`storage/reingest.py:223-228`, used at `:270` and `:318`), retention and
   manual archive only set `lifecycle_state` (`retention.py:293,376`,
   `engine.py:4839-4846`), publication only sets the state
   (`profile_publication.py:173,271`), consolidation summaries carry a different
   marker, and migrations never touch metadata. The marker has been
   `entity_profile: True` since the first profile writer (`4db92205`).
3. **The filter running after the LIMIT, so a page comes back short or the count
   disagrees.** The predicate is inside the `WHERE` on both backends; the DuckDB
   positional call and the sync signature line up (`duckpgq_graph.py:357-373` vs
   `:1162-1184`); PostgreSQL's `$idx` bookkeeping is untouched because the
   predicate binds no parameter, so `LIMIT ${idx}` still names the limit; the
   facade filters before sorting and slicing (`read_graph.py:127-151`). The new
   test pins the full-page case (`tests/test_archived_profile_filter.py:80-94`).

## Pre-mortem headline

The retired-profile flood that motivated #432 is gone from `query_nodes` (and
now from the HTTP multi-state page), but it is still in every enumeration
surface the docs recommend, and `/v1/stats` can still report 0 for "filtered
count" and 0 for "the count failed" with the same response.

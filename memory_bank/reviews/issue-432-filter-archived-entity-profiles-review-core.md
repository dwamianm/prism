# Core Review (merged checklists 1, 3, 4, 5)

Issue: #432 filter archived entity profiles
Base: main at 327d246d11e0ac20bfd4f215c7379ee4f185836d
Reviewed snapshot: 1406ba513827ffc0c0d545335ac65218e764a2a2e4c53f2cbe6ed19dd8b5b0b8
Revalidated snapshot: 7aba025203e6850f40efa66686efe7166d97d77a58919e5707149858c1be95a5 (base unchanged)
Review tier: L
Status: COMPLETE

## Revalidation (snapshot 7aba0252)

Scope checked: the 10 changed files only, against the findings below; the prior
review was not repeated. I read the new diffs for `routes.py`, `read_graph.py`,
`duckpgq_graph.py`, `pg/graph_store.py`, the protocol/engine docstrings, the
docs/CHANGELOG and the rewritten test, and verified the DuckDB predicate
semantics directly against the locked DuckDB 1.4.4 in an in-memory connection.

Finding status:
- Finding 1 (stats no-op) - RESOLVED. `GET /v1/stats` now takes a repeatable
  `state: list[str] | None = Query(default=None)` and passes
  `lifecycle_states` to `count_nodes` (`src/prme/api/routes.py:1202-1231`); the
  ValueError is converted to 422 before the swallowing `try`
  (`routes.py:1218-1228`). `GET /v1/nodes` now accepts the same repeatable
  `state` (`routes.py:773-803`), so the filter can act on a multi-state page. A
  single value still parses as a one-element list, so existing callers are
  unaffected. The HTTP test now asserts `filtered node_count == plain - 5`
  under all states, which is a real effect.
- Finding 2 (before-the-limit proof) - RESOLVED.
  `tests/test_archived_profile_filter.py` adds a limit-5 query over all states
  and asserts the page is full (5) and holds no archived profile, with a
  `newest` sanity check that archived rows are in the unfiltered newest window.
  A post-limit implementation would return a short page there.
- Finding 3 (privacy facade untested) - STILL OPEN, Consider. The facade's copy
  of the filter (`src/prme/privacy/read_graph.py:12-18`, `130`, `160`) is
  unchanged in substance (now private and SUMMARY-scoped) and no test reaches a
  protected store; the new tests run with privacy off, so
  `engine._ordinary_graph` returns the raw store
  (`src/prme/storage/engine.py:3148-3158`). As before, I verified by reading
  that `project_generated_node` preserves `lifecycle_state` and the boolean
  `entity_profile` marker (`src/prme/privacy/journals.py:95-110`, `176`), so it
  is correct today.
- Finding 4 (predicate duplication/divergence) - RESOLVED. One
  `ARCHIVED_PROFILE_EXCLUSION_SQL` per backend, used by both the list and the
  count (`src/prme/storage/duckpgq_graph.py:55-62`, `1247`, `1286`;
  `src/prme/storage/pg/graph_store.py:54-61`, `436`, `486`). It adds
  `node_type = 'summary'` and a JSON type check. Verified in DuckDB 1.4.4:
  `json_type(json, path)` returns `BOOLEAN` for JSON `true`, `VARCHAR` for the
  string `"true"`, `NULL` for a missing key or NULL metadata, and `NULL` for
  JSON null; the `COALESCE(..., 'NULL')` default is non-matching. PostgreSQL
  `jsonb_typeof(metadata -> 'entity_profile')` gives `boolean`/`string`/`null`/
  NULL the same way, so both backends now match the Python `is True` helper and
  the string `"true"` no longer matches anywhere.
- Finding 5 (HTTP exposure did not reach the multi-state scenario) - RESOLVED
  by the repeatable `state` on `/v1/nodes` and `/v1/stats`. `/v1/nodes/scan`
  still lacks the flag; it is cursor-paged, so it has no fixed-page defect and
  is out of scope.
- Finding 6 (naming/perf nits) - RESOLVED except one trivial nit: the helper is
  now `_is_excluded_profile` (`src/prme/privacy/read_graph.py:12`) and the
  default count path keeps `len(page)` (`read_graph.py:157-164`); the `node`
  parameter is still untyped, as the comparable `privacy/journals.py:76` helper
  is.
- Docs/CHANGELOG: accurate now. Both routes' `state` and the
  `exclude_archived_profiles` rows are updated in
  `documentation/http-api.md:311-318`, `401-405`; the CHANGELOG says the filter
  is by lifecycle state, not by replacement (`CHANGELOG.md:12`).
- Adversarial: the new test writes an archived non-profile FACT with NULL
  metadata, so the node-type term and the NULL-metadata path are exercised and
  the FACT must survive the filter. Default listing/count still add no SQL and
  the test still compares the default read to an explicit `False`.

No new Must Fix or Should Fix finding in the revalidated scope.

Residual risks (no action required for this ticket):
- `json_type(json, path)` was verified on DuckDB 1.4.4, the locked version;
  the package floor is `duckdb>=1.4.0`, so a resolved 1.4.x that lacks the
  path overload would fail at query time. Low risk given the lock.
- The test's `newest` assertion relies on `ORDER BY created_at DESC` with no id
  tiebreak (`src/prme/storage/duckpgq_graph.py:1256-1262`); the fixture's
  sequential awaited writes make ties unlikely.
- The `/v1/nodes` `state` OpenAPI type changes from string to array. Runtime
  single-value callers still work (verified by reading the FastAPI
  `Query(default=None)` list handling and by the worker's
  `tests/test_api.py` run); generated clients will see an array.
- The manual API table still labels `state` as `string` while OpenAPI reports an
  array; the description explains repetition.

## Checklist
1. Correctness & Logic (incl. sentinel checks)
3. Code Quality (complexity, naming, dead code, error handling, test coverage, performance)
4. Pattern Consistency & DRY (full-configuration comparison against analogues)
5. Redundancy & Unnecessary Code

## Verdict (original snapshot, findings 3 and the trivial nit still apply)

The engine and store behavior meets the three acceptance criteria on the path the
test exercises: the opt-in flag is applied inside `query_nodes`/`count_nodes` in
both backends before `LIMIT`, the default `False` adds no SQL and no Python
branch, and the new test shows 1 kept profile plus every memory with the flag and
6 profiles without it, with the filtered count equal to the filtered list. No
Must Fix finding. The defects below are about the HTTP surface and about how much
of the new behavior the test actually proves; none of them changes the default
read or count.

What I read and verified beyond the diff: the DuckDB and PostgreSQL node DDL
(`src/prme/storage/schema.py:119-130`, `src/prme/storage/pg/schema.py:140`);
profile archival on both backends (`src/prme/storage/profile_publication.py:173-175`,
`269-274`); the existing entity-profile predicate in profile publication
(`profile_publication.py:144-151`, `244-251`); the privacy projection of a
profile (`src/prme/privacy/journals.py:84-110`, `176`) and the read facade
(`src/prme/privacy/read_graph.py`); the `MemoryEngine` forwarding
(`src/prme/storage/engine.py:3136-3146`, `3148-3158`, `3224-3235`); the client
(`src/prme/client.py:966-968`); and the route layer
(`src/prme/api/routes.py:693-716`, `773-809`, `1202-1235`).

## Findings (original snapshot; 1, 2, 4, 5, 6 resolved in revalidation)

### 1. Should Fix: `GET /v1/stats`'s `exclude_archived_profiles` can never change the count
`src/prme/api/routes.py:1205`, `1216-1221`; `src/prme/storage/duckpgq_graph.py:1266-1276`;
`src/prme/storage/pg/graph_store.py:465-471`; `src/prme/types.py:284-288`;
`documentation/http-api.md:404`; `CHANGELOG.md:12`.

The stats route passes only `user_id` (plus the new flag) to `engine.count_nodes`.
`count_nodes` on both backends defaults `lifecycle_states` to
`ACTIVE_LIFECYCLE_STATES` (tentative/stable/contested), and archived profiles are
`archived`, so they are already excluded before the new predicate runs. The route
exposes no `state`/`lifecycle_states` parameter (compare `GET /v1/nodes`, which
does, `routes.py:797-803`), so `exclude_archived_profiles=true` on `/v1/stats` is
an unconditional no-op. The new test encoded that no-op as the expectation:
`tests/test_archived_profile_filter.py:107-114` asserted the filtered and plain
`node_count` are equal. The doc row ("Leave archived entity profile SUMMARY nodes
out of the count") and the CHANGELOG sentence ("`GET /v1/stats` accept it as a
query parameter") therefore described behavior that could not happen.

Resolved in the revalidation: the stats route now takes `state` and passes
`lifecycle_states`, and the test asserts a real difference.

### 2. Consider: the test did not prove the "before the limit" claim that is the point of the fix
`tests/test_archived_profile_filter.py:49-55`, `69-72`, `90-105` (original
snapshot); the claim is in `CHANGELOG.md:12` and `documentation/http-api.md:317`.

Every query in the original test used `limit=1000`, and the fixture produces far
fewer than 1000 nodes. An implementation that fetched `LIMIT n` rows first and
dropped archived profiles afterwards would have passed that test unchanged, yet
that is exactly the regression the issue describes.

Resolved in the revalidation: a limit-5 page over all states now asserts the page
is full (5) with no archived profile, plus an archived non-profile FACT that must
survive.

### 3. Consider: the privacy read facade's copy of the filter is untested
`src/prme/privacy/read_graph.py:12-18`, `116-150`, `152-161`.

`engine.query_nodes`/`count_nodes` route a protected store through
`AdmittedGraphView` (`src/prme/storage/engine.py:3148-3158`), which implements the
same filter in Python over projected nodes. This is new behavior on a privacy read
path and no test reaches it: the new test's engine has no privacy policy, so
`_ordinary_graph` returns the raw graph store. I read the projection and the
filter is correct today because `project_generated_node` preserves both
`lifecycle_state` and the boolean `entity_profile` marker
(`src/prme/privacy/journals.py:95-110`, `176`). The risk is silent divergence if
projection ever drops either field, which would make the filter a no-op only in
protected stores. A protected-store test that rebuilds a profile once and lists
across lifecycle states with the flag would close this. Rated Consider because the
code is a direct match of the surrounding `query_nodes` filter loop and I verified
the projection keeps both inputs. Still open after revalidation.

### 4. Consider: the SQL predicate is duplicated and diverges from the canonical entity-profile predicate
`src/prme/storage/duckpgq_graph.py:1234-1241` and `1278-1283`;
`src/prme/storage/pg/graph_store.py:423-430` and `478-483` (original snapshot).

Each backend repeated the identical predicate string in the list and the count
query, and the count's correctness ("counts match the filtered list") depends on
the two staying byte-identical. The canonical "is an entity profile" checks
elsewhere also require `node_type == SUMMARY`
(`src/prme/retrieval/profile_sources.py:43-46`,
`src/prme/models/profile.py:141-143`, `src/prme/retrieval/owner_profile.py:66-68`,
`src/prme/privacy/journals.py:84-86`), which the original predicate omitted. The
SQL also matched the JSON string `"true"` while the Python helper used
`is True`.

Resolved in the revalidation: one shared constant per backend, plus
`node_type = 'summary'` and the JSON type check that makes the string form stop
matching (verified against DuckDB 1.4.4 and PostgreSQL `jsonb_typeof` semantics).

### 5. Consider: the HTTP exposure did not reach the scenario the issue describes
`src/prme/api/routes.py:797-803` (single `state`), `693-716` (`/nodes/scan`
accepts a `state` list but had no flag); `documentation/http-api.md:313-317`.

The defect ("a newest-first read across lifecycle states") needs a multi-state
read. `GET /v1/nodes` mapped its single `state` parameter to one lifecycle state,
so the flag could only act with `state=archived`. The one HTTP route that could
express a multi-state read was `GET /v1/nodes/scan`, which was left unchanged.

Resolved in the revalidation: both `/v1/nodes` and `/v1/stats` accept a repeatable
`state`. `/v1/nodes/scan` still lacks the flag, but it is cursor-paged and has no
fixed-page defect, so that is out of scope.

### 6. Consider: minor style/performance nits in the facade
`src/prme/privacy/read_graph.py:12-13`, `156-160` (original snapshot).

`is_excluded_profile` was module-public-named with an untyped `node` parameter,
and `count_nodes` added a per-node loop on the default path. The revalidation
makes the helper private and restores `len(page)` on the default count path; the
parameter remains untyped, matching `privacy/journals.py:76`.

## Check results (clean items, one line each)

1. Correctness: NULL/missing/false `metadata` keeps rows on both backends and
   matches the Python `is True` check for the boolean/null/false/1 cases the
   engine writes (plus the revalidated JSON type check that also rejects the JSON
   string form); default `False` adds no predicate, so default listing and
   counting are unchanged; kwargs forwarding through `engine`/client/protocol is
   correct and keyword-only; PostgreSQL placeholder numbering is not disturbed by
   the parameterless condition; the `stats` 422 is raised outside the swallowing
   `try`; no utility called from the changed code returns a failure sentinel that
   the new code ignores.
3. Code quality: no dead branches, no swallowed errors added, no new dependency,
   complexity is a single added condition and one query parameter each; test
   coverage gaps are finding 3 (open) and the now-closed finding 2; performance is
   unchanged on the default path and one JSON predicate per row on the opt-in
   path.
4. Pattern consistency: parameter added to the `GraphStore` protocol
   (`src/prme/storage/graph_store.py:190-266`), both implementations, the engine
   docstring (`src/prme/storage/engine.py:3229`), the client passthrough
   (`src/prme/client.py:966-968`), both API routes and both docs, matching the
   `scan_nodes` filter style (`conditions.append(...)`); the repeatable `state`
   now matches `GET /v1/nodes/scan`'s list form; CLI (`src/prme/cli.py:1607-1611`)
   and MCP (`src/prme/mcp/server.py:1098-1104`) do not expose comparable
   `query_nodes` filters either, so leaving them unchanged is consistent.
5. Redundancy: no existing filter parameter covers this case; the shared SQL
   constant removes the per-file duplication; no dead surface remains in the
   revalidated scope.

## Adversarial scenarios checked

- `metadata` NULL, empty object, key absent, `entity_profile: false`, `null`,
  integer `1`, and the JSON string `"true"`: row kept on both backends and in the
  Python facade; only the JSON boolean `true` is excluded (revalidated).
- Archived non-profile node: kept; the new test now writes an archived FACT with
  NULL metadata and asserts it survives, which exercises the node-type and
  NULL-metadata terms.
- Archived profile whose metadata was replaced by `reingest()`
  (`src/prme/storage/reingest.py:270`, `318`): no `entity_profile` marker remains,
  so it is kept, which is the correct reading of the marker.
- Concurrent rebuild during a page read: list and count are separate statements;
  the page can straddle a commit, as documented for `scan_nodes`, and the count
  can differ from a later list. Not new.
- Empty `lifecycle_states` list reaches the pre-existing `IN ()` construction and
  is unaffected by this change.
- PostgreSQL path: JSONB `jsonb_typeof`/`->>` and the unchanged `$n` numbering
  checked by reading `src/prme/storage/pg/graph_store.py:338-490`; its suite is
  skipped locally without `PRME_TEST_DATABASE_URL`, so the PG variant of the new
  test is unexecuted on this machine (worker-reported).

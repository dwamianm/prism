# Issue #432 — Filter archived entity profiles: Security Review (Checklist 2)

Status: COMPLETE

- Repo: /Users/dwamianm/Sites/prism
- Base commit: 327d246d11e0ac20bfd4f215c7379ee4f185836d (main)
- Reviewed snapshot: 7aba025203e6850f40efa66686efe7166d97d77a58919e5707149858c1be95a5
  (revalidated; previously 1406ba513827ffc0c0d545335ac65218e764a2a2e4c53f2cbe6ed19dd8b5b0b8)
- Review tier: L
- Reviewer: independent security reviewer (checklist 2)
- Scope of this report: the security checklist only. Functional/acceptance review is the
  other reviewer's deliverable.

## Assigned checklist

1. SQL injection vectors in the new predicates and parameter binding on both backends.
2. Tenant/ownership scoping: the filter must not widen what a caller can see, and must behave
   correctly through the protected privacy facade.
3. Authorization and input validation on GET /v1/nodes and GET /v1/stats, including the new
   boolean parameter.
4. Secrets/PII in logs or responses; unsafe deserialization; metadata JSON parsing errors
   (invalid JSON, non-object JSON, NULL) across legacy records.
5. Do the new API parameters change any exposure of protected or other-tenant nodes?

## Revalidation — snapshot 7aba0252

Scope: only the delta since the first reviewed snapshot. The base commit and all previously
verified conclusions carry over.

Changes in the delta: one module constant per backend shared by listing and count
(`ARCHIVED_PROFILE_EXCLUSION_SQL`, DuckDB `src/prme/storage/duckpgq_graph.py:55-63`, PostgreSQL
`src/prme/storage/pg/graph_store.py:54-62`), a JSON-boolean type check plus `node_type = 'summary'`,
a private `_is_excluded_profile` that also requires SUMMARY
(`src/prme/privacy/read_graph.py:12-21`), the facade count using `len(page)` when the option is off
(`:153-162`), and a repeatable `state` parameter on both HTTP endpoints with `state` on
`/v1/stats` now forwarded to `count_nodes` (`src/prme/api/routes.py:777`, `:1206-1229`).

Rechecked in the delta:

- **SQL injection.** Both constants are module-level literals with no interpolation, no `?`/`$n`
  placeholders, and no user value; appending them adds no parameter and does not move the
  PostgreSQL `$idx` counter (`pg/graph_store.py:438-445` still binds `limit` at the index after
  the last parameter condition). The only new user input, the repeated `state` values, is
  converted to `LifecycleState` enum members before use (`routes.py:797-800`, `:1224-1227`); an
  invalid member raises `HTTPException(422)` before any store call, so no raw string reaches a
  query. PASS, unchanged from the first review.
- **Predicate/facade consistency (closes former Consider 1).** I executed the new DuckDB
  constant against edge rows: only `node_type='summary'`, `lifecycle_state='archived'` with a
  JSON boolean `true` is dropped; JSON string `"true"` (`json_type` = `VARCHAR`), number `1`
  (`UBIGINT`), JSON `null`, SQL NULL, array metadata, a non-summary node, and a non-archived node
  are all kept. That is exactly `node_type is SUMMARY and lifecycle_state is ARCHIVED and
  metadata.get("entity_profile") is True` in `read_graph.py:12-21`. PostgreSQL
  `jsonb_typeof(...) = 'boolean'` plus `->> = 'true'` is the same condition by SQL/JSON semantics.
  The two backends and the protected facade now agree, including on the previously divergent
  string case.
- **Tenant scoping through `state`.** `state` only feeds `lifecycle_states`; it never influences
  `user_id`. `_user_id()` still runs first and still returns 403 on a bound-identity mismatch
  (`routes.py:153-161`, `:785-787`, `:1209-1210`), and `_owner()` still runs in the facade
  (`read_graph.py:121`, `:154`). On a protected store an unscoped `count_nodes`/`query_nodes`
  still goes through `_ordinary_graph(None)` and raises `PrivacyDenied` (`engine.py:3145`,
  `:3234`), so `stats` still reports 0 rather than a cross-tenant count. On an unprotected store
  with the legacy global key (no bound identity), `state=archived` on `/v1/stats` now returns a
  cross-tenant archived *count*; that deployment already exposes unscoped archived *content*
  through `/v1/nodes?state=archived`, so this adds no new exposure class and no node content.
- **Validation of the state list.** Each element is validated as `LifecycleState`; one bad element
  fails the whole request with 422 before the store is called (no partial query). A single
  `state=archived` still behaves as before, so the change is backward compatible. The detail
  string echoes the caller's own values, the same pre-existing pattern the single-value parameter
  used.
- **Default path.** With `exclude_archived_profiles` absent or false, no condition is appended in
  either backend (the constants are only referenced inside `if exclude_archived_profiles:`), and
  the facade count is back to `len(page)` (`read_graph.py:157-161`). The default query, parameter
  list, and row set are unchanged.
- **Metadata edge cases.** Re-verified after the type check: `json_type` on missing paths, SQL
  NULL, JSON `null`, arrays, and non-object values never raises; `COALESCE(..., 'NULL')` keeps
  such rows (empirically confirmed for DuckDB). PostgreSQL `jsonb_typeof` returns `'string'`,
  `'number'`, `'null'`, or NULL for those cases, all kept. Invalid JSON still cannot be stored:
  the columns are `JSON`/`JSONB` and validate at insert.
- **New tests.** `tests/test_archived_profile_filter.py:108-150` now covers a multi-state HTTP
  listing, a `state=archived` HTTP count where the filter changes the result, an archived
  non-profile FACT that survives the filter (`:50-71`), and an invalid `state` returning 422
  (`:149-150`). No cross-tenant test was added (see Consider 3).

New findings from the delta: no Must Fix, no Should Fix. One new Consider (repeated `state`
values), below. Former Consider 1 and 2 are resolved by this delta.

## Summary table (current snapshot)

| Area | Result | Details |
|---|---|---|
| SQL injection — DuckDB | PASS | Shared constant `ARCHIVED_PROFILE_EXCLUSION_SQL` is a literal with no placeholder: `src/prme/storage/duckpgq_graph.py:55-63`, used at `:1247` and `:1286`. Parameter order unchanged; `limit` still appended last (`:1256`). |
| SQL injection — PostgreSQL | PASS | Same constant pattern, no `$n`, does not touch `idx`, so `LIMIT ${idx}` still binds `limit`: `src/prme/storage/pg/graph_store.py:54-62`, `:436`, `:486`, `:441-447`. No user string reaches SQL text. |
| Tenant/ownership scoping (storage) | PASS | The option only appends a `NOT (...)` to an `AND`-joined WHERE, so it can only remove rows. `user_id` handling unchanged. |
| Protected privacy facade | PASS | `_is_excluded_profile` is applied after projection and before `matches[:limit]` (`src/prme/privacy/read_graph.py:119-162`); `_owner()` still runs; the predicate now matches the SQL exactly (SUMMARY + ARCHIVED + JSON boolean true); projected profile journals keep `entity_profile=True` (`src/prme/privacy/journals.py:96-120`). No path adds a node. |
| API authorization | PASS | Both endpoints stay on the API-key-protected `router` (`src/prme/api/routes.py:164-169`); identity binding unchanged (`:153-161`). No new route or identity source. |
| Input validation — new boolean | PASS | FastAPI/pydantic coerces `true/false/1/0` and returns 422 otherwise; forwarded only when truthy and never interpolated into SQL (`:788-789`, `:1228-1229`). |
| Input validation — repeatable `state` | PASS | Each item must be a `LifecycleState`; failure is a single 422 before any store call (`:796-801`, `:1222-1228`). Single-value callers unaffected. Duplicates are not deduped (Consider 5). |
| PII in logs / responses | PASS | No new logging or response fields; `_node_to_response` and `StatsResponse` unchanged. The filter removes rows only; the 422 detail echoes only the caller's own `state` values. |
| Unsafe deserialization | PASS | No new `pickle`/`eval`/`json.loads` on request data. |
| Metadata JSON edge cases | PASS | NULL / non-object / JSON `null` / missing key: kept, no error (DuckDB executed; PG by `jsonb_typeof`/COALESCE semantics). Malformed JSON cannot exist in a `JSON`/`JSONB` column. |
| Exposure of protected / other-tenant nodes | PASS | Strictly narrowing; default path unchanged. `state=archived` on `/v1/stats` adds a count only, never content, and stays owner-scoped with a bound key. |
| MCP / other surfaces | N/A | `src/prme/mcp/server.py:1798` and the CLI still call without the option; no new parameter reachable there. |

## Findings

### Consider 1 — RESOLVED in snapshot 7aba0252

The SQL predicates now require a JSON boolean (`json_type(...) = 'BOOLEAN'` and the
`jsonb_typeof(...) = 'boolean'`) and the facade predicate requires the Python `True` identity, so
a JSON string `"true"` no longer matches in either path. Verified by executing the DuckDB constant
against string/number/null/array/missing-key rows; PostgreSQL is equivalent by SQL/JSON semantics.

### Consider 2 — RESOLVED in snapshot 7aba0252

`/v1/stats` now accepts a repeatable `state` parameter and forwards `lifecycle_states`
(`src/prme/api/routes.py:1206-1229`), so the filter can change the count when `archived` is
requested, and `documentation/http-api.md:404-405` says exactly that. The default (no `state`)
count is still active-only, so the option is still a no-op there; the documentation now states the
condition instead of implying an unconditional effect.

### Consider 3 — the new filter is still untested for tenant separation and the protected facade

`tests/test_archived_profile_filter.py:47-150` covers listing, counting, HTTP multi-state, the
archived non-profile FACT, and the 422 path, all with one owner on an unprotected engine. Nothing
asserts that `exclude_archived_profiles` cannot surface another owner's node, and nothing exercises
`AdmittedGraphView` with the option, so the Python and SQL implementations are not compared by a
test. I verified both properties by reading the code and by executing the SQL constant, but the
regression guard is still missing for the security-relevant pair.

### Consider 4 (informational) — per-row JSON evaluation before `LIMIT`

The predicate is evaluated for every row matched by the other conditions, before `LIMIT`, which is
the intended semantic (filter before limit) and matches the cost of the JSON predicates already in
these queries (`duckpgq_graph.py:593`, `pg/graph_store.py:536`). It now evaluates two JSON
functions per row instead of one. `GET /v1/nodes` has no upper bound on `limit` either before or
after this change, so no new denial-of-service class is introduced.

### Consider 5 (new) — repeated `state` values are not deduped or capped

`src/prme/api/routes.py:797-800` and `:1223-1227` convert the list item by item without
deduplication, so `?state=archived&state=archived&...` produces a `lifecycle_state IN (...)` list
with one placeholder per repetition. Every element is a validated enum value, so there is no
injection or tenant impact, and in practice the HTTP request-line limit bounds the list. Deduping
(or capping) the list would remove the only unbounded input this change adds to the query builder.

## Adversarial scenarios checked (current snapshot)

| Scenario | Result |
|---|---|
| `GET /v1/nodes?exclude_archived_profiles=1 OR 1=1` (and other non-boolean strings) | 422 from FastAPI validation; nothing reaches SQL. |
| SQL fragment in `state`, `type`, `user_id`, or `exclude_archived_profiles` | `state`/`type` must parse as enums (422 otherwise), the boolean is parsed, `user_id` stays a bound parameter. |
| `GET /v1/stats?state=<invalid>` | 422 before `count_nodes` is called; no partial count. |
| `GET /v1/stats?state=archived` with a bound per-user key | Counts only the bound owner; `_user_id` returns 403 on a foreign `user_id`. |
| Unscoped `count_nodes()`/`query_nodes()` on a protected store | Still `PrivacyDenied` via `_ordinary_graph`; `stats` still reports 0. |
| Protected store + filter | Projected first, then skipped; profile journals preserve `entity_profile=True`; every returned row still passes `disclose`. |
| `metadata` NULL / array / JSON `null` / missing key / string `"true"` / number `1` | Kept in both backends and in the facade (DuckDB executed; see Revalidation). |
| Malformed JSON metadata in a legacy record | Cannot exist: `JSON`/`JSONB` columns reject malformed input at insert. |
| Default request (option absent or false) | Same SQL text, same params, same row set; facade count is `len(page)` again. |

## Unresolved risks / verification limits

- PostgreSQL semantics for `jsonb_typeof`/`->>` on non-object values were reasoned from
  PostgreSQL's documented behavior, not executed: the PG tests are skipped locally without
  `PRME_TEST_DATABASE_URL`. The PostgreSQL delta is a constant, parameter-free string in an
  existing query and cannot carry injection, but the exact boolean type check was not run on a
  live server.
- DuckDB behavior for arrays, scalars, `null`, NULL, malformed input, duplicate keys, and the new
  `json_type` check was verified empirically against the installed DuckDB.
- The facade's filter depends on the projection keeping `entity_profile=True`; verified for
  generated profile journals (`src/prme/privacy/journals.py:96-120`). A protected summary without a
  journal would take another projection branch and might not be excluded (narrowing failure, not
  exposure).
- No Must Fix or Should Fix items were found in the security scope.

## Evidence read

- `src/prme/storage/duckpgq_graph.py:51-64`, `:303-400`, `:1163-1300`
- `src/prme/storage/pg/graph_store.py:51-63`, `:359-495`
- `src/prme/storage/graph_store.py:190-262`
- `src/prme/privacy/read_graph.py:1-162`
- `src/prme/privacy/reads.py:99-102`, `:217-270`
- `src/prme/privacy/journals.py:70-140`
- `src/prme/storage/engine.py:3136-3146`, `:3224-3235`, `:5002`
- `src/prme/api/routes.py:120-169`, `:766-808`, `:1196-1240`
- `src/prme/storage/schema.py:105-170`, `src/prme/storage/pg/schema.py:155-183`
- `src/prme/models/nodes.py:17-40`, `src/prme/models/base.py:14-32`, `src/prme/types.py:10-25`, `:284-288`
- `src/prme/storage/profile_publication.py:120-300`
- `tests/test_archived_profile_filter.py:1-151`
- `documentation/http-api.md:309-320`, `:399-407`; `docs/INTEGRATION.md:694-700`; `CHANGELOG.md:10-14`

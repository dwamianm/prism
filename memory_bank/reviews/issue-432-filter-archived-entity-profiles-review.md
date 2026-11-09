# Issue #432 review: leave archived entity profiles out of a node listing

## Review Scope

- Repository: `/Users/dwamianm/Sites/prism`
- Base branch and commit: `main` at `327d246d11e0ac20bfd4f215c7379ee4f185836d`
- Reviewed snapshot: `7aba025203e6850f40efa66686efe7166d97d77a58919e5707149858c1be95a5`
- Review tier: L (10 files, 286 changed lines)
- Predicates: `NEW_SYMBOLS=true`, `SECURITY=true`, `DATA=false`, `INFRA=false`, `PERSISTED_FIELD=false`
- Merged checklists: 3, 4 and 5 are covered by the core report; checklists 6 and 7 are not applicable
- Required independent reviewers: core (1, 3, 4, 5), security (2), adversarial (8). All three ran on the first snapshot and revalidated the delta on the final snapshot.
- Reports: `issue-432-filter-archived-entity-profiles-review-core.md`, `-security.md`, `-adversarial.md`

## Files Changed

- `CHANGELOG.md`
- `docs/INTEGRATION.md`
- `documentation/http-api.md`
- `src/prme/api/routes.py`
- `src/prme/privacy/read_graph.py`
- `src/prme/storage/duckpgq_graph.py`
- `src/prme/storage/engine.py`
- `src/prme/storage/graph_store.py`
- `src/prme/storage/pg/graph_store.py`
- `tests/test_archived_profile_filter.py` (new)

## Approach Summary

Profile publication archives the previous SUMMARY node by setting `lifecycle_state='archived'` and keeps `metadata.entity_profile=true` (`src/prme/storage/profile_publication.py:173,271`), so a newest-first read across lifecycle states becomes a run of retired profiles. The change adds an opt-in `exclude_archived_profiles` argument to `query_nodes()` and `count_nodes()` on both DuckDB and PostgreSQL. Each backend has one `ARCHIVED_PROFILE_EXCLUSION_SQL` constant used by both the listing and the count, so the two cannot drift. The predicate requires `node_type = 'summary'`, `lifecycle_state = 'archived'`, and a JSON boolean `entity_profile` marker, which matches the Python facade and the rest of the codebase. The privacy `AdmittedGraphView` applies the same rule in Python over projected nodes. `GET /v1/nodes` and `GET /v1/stats` accept the flag, and both take a repeatable `state` parameter so a caller can request the multi-state page the flag applies to. The default read and count add no predicate and are unchanged.

## Must Fix

None.

## Should Fix

None after revalidation. The one original Should Fix (the `/v1/stats` flag was a no-op because the route had no lifecycle filter) was resolved by adding the repeatable `state` parameter to `/v1/stats` and to `/v1/nodes`.

## Consider

- The privacy facade's copy of the filter still has no test. The facade is correct today because the profile journal projection preserves `lifecycle_state` and the boolean `entity_profile` marker, but a protected-store test would guard against future projection changes.
- The `node` parameter of `_is_excluded_profile` is untyped, matching the comparable helper in `privacy/journals.py`.
- Repeated `state` values are validated but not deduplicated or capped. The HTTP request-line limit bounds the list, and every element is an enum member, so there is no injection or tenancy impact.
- The manual `state` row in `documentation/http-api.md` describes a repeatable string while OpenAPI reports an array. The description explains repetition.
- The opt-in predicate evaluates two JSON functions per matched row before the limit, the intended semantic and comparable to existing JSON predicates in these queries.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| SQL injection, both backends | PASS | Module constant literals with no placeholders or interpolation; PostgreSQL `$idx` bookkeeping and `LIMIT` binding unchanged. |
| Tenant and ownership scoping | PASS | The predicate can only remove rows; `user_id` handling and the privacy boundary are unchanged. |
| Protected privacy facade | PASS | Filtering happens after projection and before the limit; `_owner()` still runs; the Python predicate now matches the SQL, including the JSON boolean type. |
| API authorization | PASS | Both endpoints remain on the API-key-protected router; identity binding is unchanged. |
| Input validation | PASS | The boolean is parsed, each `state` value must be a `LifecycleState`, and an invalid value returns 422 before any store call. |
| Metadata edge cases | PASS | NULL, non-object, JSON null, missing key, string `"true"` and numeric values are kept without error on both backends and the facade. |
| Exposure | PASS | Strictly narrowing; the default path is the same query and row set. |

No Must Fix or Should Fix security finding.

## Pattern Consistency Assessment

The parameter was added to the `GraphStore` protocol, both implementations, the engine docstring, the client passthrough, both API routes and both docs, matching the existing `scan_nodes` filter style. The repeatable `state` matches the `GET /v1/nodes/scan` list form. One shared SQL constant per backend replaces the duplicated predicate text. No inconsistency remains in the changed scope.

## Redundancy Check

No existing filter covered this case. The shared constant removes the duplicated predicate, and the default facade count path keeps `len(page)`. The only dead surface would have been the original `/v1/stats` parameter, which the revalidation made effective.

## Wiring Findings

`GET /v1/nodes` and `GET /v1/stats` both forward the flag and the repeatable state list. The engine and client forward keyword arguments unchanged. The docs name the condition under which the flag matters. The new test runs on both backends through the parametrized config fixture, so its PostgreSQL variant executes in CI and skips only locally without `PRME_TEST_DATABASE_URL`.

## Data Safety and Read/Write Proof

Not applicable: the change adds no persisted field, schema change, migration or write path. It is a read filter that only removes rows when the caller opts in.

## Break Scenarios (adversarial) and Adjudication

| # | Scenario | Label | Likelihood | Impact | Verdict | Resolution |
|---|---|---|---|---|---|---|
| 1 | HTTP flags inert because neither endpoint could request the multi-state page the filter applies to | newly introduced | High | Low to Medium (diagnosis trap) | Fix now | Resolved: repeatable `state` on both endpoints, real differences asserted in the HTTP test |
| 2 | Filter absent from `scan_nodes`, `GET /v1/nodes/scan`, MCP `memory_scan_nodes` and `prme nodes` | newly introduced gap | Medium-High | Low-Medium | Follow-up | Filed as #513 |
| 3 | SQL predicates matched the JSON string `"true"` while the facade required a boolean, and PostgreSQL was unexecuted | newly introduced | Low to Medium | Medium | Fix now | Resolved: shared typed constants on both backends, facade aligned, PostgreSQL variant runs in CI |
| 4 | Any archived profile is hidden, including one archived without a successor | newly introduced | Low-Medium | Low-Medium | Accept | Accepted: the issue asks for archived profiles, and the CHANGELOG now says the filter is by lifecycle state, not replacement |
| N1 | `/v1/nodes` `state` changed from a scalar to an array in OpenAPI | newly introduced | Low | Low | Accept | Accepted: single values still work at runtime; CHANGELOG and the docs describe the repeatable form |
| N2 | `/v1/stats` turns every count failure into a silent zero, now reachable through the new parameters | pre-existing mechanism, newly reachable | Low | Medium | Follow-up | Filed as #514 |

Pre-mortem headline (adversarial): "The retired-profile flood that motivated #432 is gone from `query_nodes` (and now from the HTTP multi-state page), but it is still in every enumeration surface the docs recommend, and `/v1/stats` can still report 0 for "filtered count" and 0 for "the count failed" with the same response."

## Follow-ups Raised

- #513: extend the archived-profile listing filter to `scan_nodes`, `GET /v1/nodes/scan`, MCP `memory_scan_nodes` and `prme nodes`.
- #514: report a failed node count on `GET /v1/stats` instead of returning zero.

## Resolution Status

| Item | Status |
|---|---|
| Acceptance: filtered listing shows one profile and every memory, unfiltered six profiles | Met (`tests/test_archived_profile_filter.py`) |
| Acceptance: filtered count matches the filtered list | Met |
| Acceptance: default listing and counting byte for byte unchanged | Met (default query compared to explicit `False`) |
| Filter applied inside the query before the limit, both backends | Met, with a limit-5 regression assertion |
| HTTP listing and count expose the filter | Met, including a repeatable `state` parameter |
| Core Should Fix (stats no-op) | Resolved |
| Security Must Fix / Should Fix | None |
| Adversarial Fix now items | Resolved |
| Adversarial Follow-up items | #513, #514 |
| Consider: privacy facade test coverage | Open, non-blocking |

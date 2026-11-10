# Issue 523 Security Audit (Agent 2, independent)

Status: COMPLETE

- Base commit: `7f8b489820a007addd29fd79e76c4322618d6a38` (origin/issue-517-organize-incremental-converges)
- Reviewed snapshot: `8126027abc080b61ab7ff363ecd545559759909cc14d0d4678188134b1626c1b` (working tree, uncommitted changes included)
- Branch: `issue-523-alias-resolve-incremental`
- Scope: incremental `alias_resolve` for one owner scope, the `content_equals_any` whole-content filter on DuckDB, PostgreSQL and `AdmittedGraphView`, and the per-scope organizer watermarks it reuses.
- Method: read the 14-file diff against the base, then followed the callers and callees (engine facade, privacy admission, vector index ownership, merge/proposal publication, watermark storage). I did not edit source or tests and did not rerun the worker's passing commands.

## Verdict

No **Must Fix** security defect. Tenant isolation, admission/privacy, authorization, SQL injection and sensitive-data handling hold up. Two scale/availability concerns exist: one is specific to this change on privacy-admitted stores (bounded but potentially very slow), and two are bounded/inherited patterns (the shared incremental loop and the unbudgeted resolve half). None of them leaks or crosses a tenant boundary, and the organizer is explicit-invocation only.

## Area Table

| Area | Result | Details |
|---|---|---|
| Tenant isolation | PASS | Scoped scan binds `user_id`; name lookup binds `user_id` and `scope`; vector search filters by owner in SQL and in `ProtectedVectorSearch`; pair gating plus a pre-merge `(user_id, scope)` revalidation; merge/proposal stores require an owner and both nodes' owner and scope. Empty user id is a real scope and cannot collide with the operator scope. See details below. |
| Privacy/admission | PASS | `content_equals_any` on `AdmittedGraphView.query_nodes` runs on **already projected** nodes and every returned node is projected and disclosed; a hidden record never reaches the filter. Error paths return fixed codes/counts, not content. |
| Authorization | PASS | No new endpoint, CLI flag, tool or job name; `alias_resolve` was already an explicit job. Incremental mode is gated by `OrganizerConfig.incremental_enabled` (default `False`). `organize()` scope rules and the `feedback_apply` guard are untouched. Unscoped alias runs still refuse cross-owner pairs. |
| Injection | PASS | Every new SQL fragment is parameterized: literal `?` placeholders plus bound values on DuckDB, `= ANY($n::text[])` with an array param on PostgreSQL. Only the placeholder count and an integer `$idx` are interpolated. `_escape_like`/`content_contains_any` path unchanged. |
| DoS / scale | PASS with findings | No unbounded page loop and no unbounded alias-form list. Findings F1-F3: per-entity admitted-view enumeration (new amplification), the shared incremental loop making no cursor progress when one page cannot fit half the budget (inherited from #517), and `resolve_aliases` not being time-budgeted (bounded by page size times lookup limit). All operator-triggered. |
| Sensitive data in logs/results | PASS | New debug/warning messages carry node ids or fixed strings only; merge/link info lines carry node ids, alias type and confidence; `details` carries counts plus a `[timestamp, node id]` resume cursor. In a protected scope `ScopedLogger` suppresses messages and tracebacks. |
| Secrets/PII in tests/fixtures | PASS | New test literals are language names and UUID-suffixed synthetic user ids; no secrets, credentials or real personal data. |

## Tenant isolation detail

- Scoped scan: `engine.scan_changed_nodes(user_id=user_id)` (`src/prme/organizer/jobs.py:838-846`) binds `user_id` in SQL (`src/prme/storage/duckpgq_graph.py:456-458`, `src/prme/storage/pg/graph_store.py:524-527`) and applies the owned-view check (`duckpgq_graph.py:433`, `pg/graph_store.py:561-563`). On a protected store it routes through `AdmittedGraphView.scan_changed_nodes` (`src/prme/privacy/read_graph.py:160-186`), whose `_all_nodes` scans only `context.user_id` (`read_graph.py:105-115`).
- Name lookup: `engine.query_nodes(user_id=entity.user_id, scope=entity.scope, ...)` (`src/prme/organizer/alias_resolution.py:376-383`). Both filters are bound values; the admitted-view branch scopes to `context.user_id` (`read_graph.py:108-111`). The lookup uses the changed node's own owner, so even an unscoped operator run never asks for a cross-owner name match.
- Vector search: `index.search(entity.content, entity.user_id, scope=[entity.scope.value])` (`alias_resolution.py:190-195`). The index enforces `vm.user_id = ? AND n.user_id = vm.user_id AND n.scope IN (...)` (`src/prme/storage/vector_index.py:383-396`). On a protected store `ProtectedVectorSearch.search` rejects a mismatched owner (`src/prme/privacy/reads.py:425-429`). The durable twin is re-read with `get_node(other_id, user_id=entity.user_id)` (`alias_resolution.py:211`; ownership check `src/prme/storage/engine.py:2954-2962, 3004-3010`).
- Pair gating: `compatible_admitted_pair(alias_pair_allowed, ...)` (`alias_resolution.py:394-395, 212`; `src/prme/privacy/journals.py:792-822`), then `resolve_aliases` revalidates `(node_a.user_id, node_a.scope) != (node_b.user_id, node_b.scope)` before any merge or link (`alias_resolution.py:473-479`). This is defense in depth even if a custom backend's index ignored its owner filter.
- Publication: `merge_nodes` requires a non-empty owner, selects both nodes with `user_id=$`, and `_prepare` checks both owners and the shared scope (`src/prme/storage/organizer_merge.py:49-53, 95-102`, DuckDB load at `156-164`, PostgreSQL at `199-207`). `propose_alias` likewise requires an owner and both nodes' owner (`src/prme/storage/alias_proposal.py:256-261, 455-470`). `resolve_aliases` rechecks before calling either.
- Watermarks: `scope_key` maps an unset user id to the operator scope `""` and every user id (including `""`) to `"u:" + user_id`, so the two cannot collide (`src/prme/organizer/watermarks.py:41-52`; covered by `tests/test_organizer_incremental.py:172`). Reads and writes bind `scope_key` and `job` (`duckpgq_graph.py:496-499, 532-542`; `pg/graph_store.py:570-612`). The row holds a timestamp and a node id only (`watermarks.py:1-11`).

Note F4 (latent, not exploitable): `_alias_resolve_incremental` passes `**({"user_id": user_id} if user_id else {})` to `resolve_aliases` (`jobs.py:878-880`), so an empty-string scope drops the ownership narrowing on the two `get_node` calls and reads by id across owners. The candidate list can only contain nodes owned by `""` (the scan and name lookup bind `user_id=""`), and the `(user_id, scope)` revalidation at `alias_resolution.py:473-479` still refuses any other owner, so no cross-owner merge or link is reachable. The same falsy-`""` pattern is pre-existing in the full-scan path (`jobs.py:768`).

## Privacy / admission detail

- On the admitted view the new filter compares `node.content.strip().casefold()` against a set built from the caller's values (`read_graph.py:128-131, 154-155`). The nodes it sees are produced by `_all_nodes` -> `scan_nodes`, which calls `context.project_node` for every row and drops `None` before appending (`read_graph.py:80-103`). A record the admitted view hides therefore never reaches the equality test, so the filter cannot be used to probe for hidden content. The result is returned through `context.disclose(matches[:limit])` (`read_graph.py:158`).
- The review checklist asked whether the filter runs on stored values before projection. It does not on the admitted view: it runs on the projected node, which is strictly safer for disclosure. On the SQL backends the predicate runs on stored values, but those are only reached through the facade for an unprotected store or with a bound owner filter.
- `engine.query_nodes` is `@privacy_disclosure` (`engine.py:3135-3146`) and its result is projected again by `project_public_result` (`src/prme/privacy/reads.py:585-601, 622-624`), so a protected caller sees a projection either way.
- Error paths do not disclose content: `find_aliases_in_page` returns `(candidates, completed, True)` (`alias_resolution.py:384-388, 420-424`), `_alias_resolve_incremental` returns `details={"error": "index_unavailable"}` on an index failure (`jobs.py:814-822`) and counts only otherwise (`jobs.py:912-928`).

## Injection detail

- DuckDB: `content_equals_any` contributes `IN (?, ?, ...)` with one bound parameter per value (`duckpgq_graph.py:1381-1386`). PostgreSQL: `LOWER(TRIM(content)) = ANY($idx::text[])` with the list bound as one array parameter (`pg/graph_store.py:428-431`). Neither interpolates a value; only the `?` count and the integer parameter index are built into the string.
- The alias forms passed in are derived by dictionary lookups against the fixed `KNOWN_ALIASES` table and are at most about four entries (`alias_resolution.py:149-161`), so the `IN` list length is bounded and memory content is never concatenated into SQL.
- `content_contains_any`'s `LIKE ... ESCAPE '\'` path and `_escape_like` are unchanged (`duckpgq_graph.py:1372-1379, 1295-1301`; `pg/graph_store.py:421-426`).
- Scan and watermark parameters (`user_id`, `after_id`, `updated_after`, `updated_at_or_before`, `limit`, `scope_key`, `job`) are all bound (`duckpgq_graph.py:449-489`, `pg/graph_store.py:515-559`). `after_id` is cast `::uuid`/`::UUID` but still bound.

## Findings

### F1 - Should Fix (scale, privacy-admitted stores): one name lookup costs a full enumeration of the owner's admitted scope, once per changed entity

- `alias_resolution.py:376-388` issues one `engine.query_nodes(..., content_equals_any=...)` per changed entity.
- On a protected store that call is `AdmittedGraphView.query_nodes`, which walks every page of the owner's scope via `_all_nodes` (`read_graph.py:105-115, 132-158`) and calls `context.project_node` for every node (a privacy-record read per node, `src/prme/privacy/reads.py:217-262`) before filtering and sorting in memory.
- The full scan called `query_nodes` once per run; this path calls it up to `_INCREMENTAL_PAGE_SIZE` (500) times per page. The RFC documents only the per-page scan cost (`docs/RFC-0015-Self-Organizing-Memory.md:519-522`), not this per-entity amplification.
- Security impact: none on disclosure (each lookup stays owner-scoped and projected). Availability impact: a large protected scope can spend minutes per entity, and the wall-clock check between entities (`jobs.py:856-861`) cannot interrupt a single lookup. Recommendation: push `content_equals_any` down through `AdmittedGraphView.scan_nodes` (and the backends) so a lookup is a keyset page, or cap entities per page below 500 on protected stores. Operator-triggered; not a blocker for this ticket.

### F2 - Consider (inherited from #517, shared incremental loop): a scope that cannot fit one page in half the budget makes no cursor progress

- `scan_budget_ms = budget_ms / 2` (`jobs.py:824`). The examined loop sets `stopped=True` when the scan has already consumed that budget (`jobs.py:856-861`); `remaining_ms` is then clamped to 1 ms (`jobs.py:864-866`).
- In `find_aliases_in_page`, phase 1's first name lookup can proceed inside that 1 ms, but phase 2's budget check then fails before any entity completes (`alias_resolution.py:361-427`), returning `completed=0`. `done` is empty, `last_cursor` stays `None`, and `_resume` is not written (`jobs.py:888-896, 926-927`); `_run_incremental` then leaves the prior watermark in place (`jobs.py:104-120`). The next run repeats the same page.
- This is the same shape as the already-merged deduplicate loop (`jobs.py:653-692`), so it is not a regression introduced by #523, but a protected store where one `scan_changed_nodes` page exceeds `budget_ms/2` will stall forever. Recommendation: record a resume cursor for the scanned-but-unexamined page, or advance the cursor when zero entities were completed. Availability only; no isolation impact.

### F3 - Consider (scale/DoS, bounded): the resolve half of the page is not time-budgeted

- The scan half stops on its half-budget, but `resolve_aliases` runs after it with no budget (`jobs.py:877-880`). One page can yield up to page size times `_ALIAS_NAME_LOOKUP_LIMIT` candidate pairs (`alias_resolution.py:309, 382, 398-406`), and each pair is resolved with two `get_node` reads plus a merge/proposal transaction (`alias_resolution.py:462-542`).
- The outer loop stops after at most the current page once over budget (`jobs.py:904-910`), so the work is bounded (roughly 250k pairs worst case at a 500-entity page) rather than unbounded, and each merge makes real progress. A pathological store with hundreds of identically named entities can still overrun the job budget and starve later jobs in the same `organize()` call. Recommendation: check the deadline inside `resolve_aliases` so its remainder can resume. Operator-triggered only.

### F4 - Consider (latent, not exploitable): empty-string owner drops the `get_node` ownership narrowing in `resolve_aliases`

- See the Tenant isolation note above. `scope_key` treats `""` as a distinct tenant (`watermarks.py:46-52`), but `resolve_aliases` receives `user_id` only when it is truthy (`jobs.py:879`); for `""` the two `get_node` calls are unscoped (`alias_resolution.py:466-467`). The `(user_id, scope)` revalidation at `alias_resolution.py:473-479` keeps this from merging across owners, so there is no isolation break. Pre-existing in the full-scan path.

## Negative results worth recording

- No new endpoint, CLI option, MCP tool or job name: `alias_resolve` is already in `ALL_JOBS` and the dispatch table (`jobs.py:38, 169`), the HTTP `/v1/nodes` route and `prme nodes` build their kwargs explicitly and do not accept `content_equals_any` (`src/prme/api/routes.py:773-809`, `src/prme/cli.py:265-297`), and the opportunistic pass never runs pairwise jobs (`src/prme/organizer/maintenance.py:12-15, 142-206`).
- No unscoped global read on a protected store: an unscoped alias run calls `generation_search(engine, user_id=None)` and `_ordinary_graph(None)`, both of which raise `PrivacyDenied` when the store has a selected or protected range (`src/prme/privacy/journals.py:688-703`, `engine.py:3186-3196`), and the failure is reported as `index_unavailable` without disclosure.
- The SQL backends are the only two `GraphStore` implementations (`src/prme/storage/duckpgq_graph.py:65`, `src/prme/storage/pg/graph_store.py:73`); both implement the new filter, and `AdmittedGraphView` is the only read facade, so `content_equals_any` cannot be silently dropped by a third backend.
- The full-scan `find_aliases` refactor preserves behavior: the new `_string_alias_candidate` keeps abbreviation-before-case precedence and still gates on `compatible_admitted_pair` and the same vector-ownership recheck (`alias_resolution.py:164-217, 262-304`), so no security regression in the option-off path.

## Limits of this audit

- I did not execute the test suites; the worker's results are taken as given and spot-checked against the source. The PostgreSQL schema grant note in the CHANGELOG (runtime login needs INSERT/UPDATE/DELETE on `organizer_watermarks`) is unchanged by this diff.
- The protected-store behaviour above was reasoned from the admission code, not exercised live; the worker's privacy-fence tests passed on both backends.

# Security Review — issue dwamianm/prism#423 (profile rebuild scope scan)

- Status: COMPLETE
- Reviewer: security (checklist 2)
- Base branch: main, base commit 52d99de1be90477abcfa7256088fd8359fea2eb5
- Reviewed snapshot identifier: b352281961d1caf228350041f22588d6b0773c7fa775a93ad18535362fc1c596
- Branch: issue-423-profile-rebuild-scope-scan
- Diff reviewed: `git diff main -- . ':!.env.issue423-tmp'` (14 files; `.env.issue423-tmp` ignored and never read)

## Assigned checklist

1. New filters (`content_contains_any`, `speaker`, `evidence_events`, `get_by_user(speaker=...)`)
   cannot widen a read beyond the owner/scope already passed (`user_id`, `scope`,
   `AdmittedGraphView._owner`) and cannot expose another user's or scope's nodes.
2. SQL construction is parameterized everywhere (DuckDB `?`, PG `$n`), including `json_each`
   and `?|`; LIKE wildcards are escaped.
3. `speaker` filter reading `speaker_reference.speaker` does not bypass metadata
   sanitization or protected-node projection.
4. Deadline path cannot publish a partial profile or leave data inconsistent.
5. Logging added or reused does not leak content or secrets.

## Result table

| Area | Result | Details |
|---|---|---|
| Tenant isolation: `DuckPGQGraphStore.scan_nodes` filters | PASS | Every filter is ANDed after `user_id = ?` (src/prme/storage/duckpgq_graph.py:567-592) and `scope = ?`/`node_type = ?` (570-573); the `user_id` guard at 556-561 still rejects `""`/`None` unless `operator_unscoped`. Filters can only shrink the page. |
| Tenant isolation: `PgGraphStore.scan_nodes` filters | PASS | Same shape: `user_id = $n` (src/prme/storage/pg/graph_store.py:502-504), `scope`/`node_type` (505-508), filters appended after (509-523). `_owned_view` still applied (533-535). |
| Tenant isolation: `EventStore.get_by_user(speaker=...)` | PASS | `conditions = ["user_id = ?"]` stays first and unconditional; `speaker` is an extra AND (src/prme/storage/event_store.py:660-678). PG equivalent keeps `user_id = $1` (src/prme/storage/pg/event_store.py:511-534). |
| Scope confinement in the rebuild | PASS | `consolidate_knowledge` builds `ReadContext(..., scopes=(scope,))` and passes `scope=` to every read (src/prme/storage/engine.py:4657-4658, 4690-4693); `absorb` re-checks `node.scope != scope` (engine.py:4721). `turn_events` passes `scopes=[scope]` (engine.py:4768-4770). |
| `AdmittedGraphView` owner/scope containment | PASS | `_owner()` rejects any `user_id` other than the context owner and `operator_unscoped` (src/prme/privacy/read_graph.py:21-24, 58-63); every returned node still goes through `project_node` (77-84). New filters are forwarded, not used as authority (74-75). |
| Public surfaces for the new filters | PASS | The HTTP route (src/prme/api/routes.py:693-716) and MCP tool (src/prme/mcp/server.py:1098-1140) do not accept the new parameters; the sync client does not either (src/prme/client.py:970-980). Only the Python engine API and the organizer can pass them. |
| SQL parameterization, DuckDB | PASS | `content_contains_any` uses one `?` per value (duckpgq_graph.py:574-578); `speaker` builds its `IN (...)` slot list from `?` (579-586); `evidence_events` binds the whole id array as one `?::JSON` and joins `json_each` on it (587-592). No value is interpolated. |
| SQL parameterization, PostgreSQL | PASS | `LIKE ANY($n::text[])`, two `= ANY($n::text[])` for the speaker keys, and `evidence_refs ?| $n::text[]`; indices are built with `len(params)` (pg/graph_store.py:509-523). `evidence_refs` and `metadata` are `JSONB` (src/prme/storage/pg/schema.py:140, 162, 169), so `?|` is the jsonb operator. asyncpg uses `$n`, so `?\|` is not a placeholder. |
| LIKE wildcard escaping | PASS | `_escape_like` escapes `\`, `%`, `_` in that order (duckpgq_graph.py:1133-1140; pg/graph_store.py:246-253) and is reused unchanged. DuckDB query adds `ESCAPE '\'` (574-577), matching the existing `query_nodes` path (1209-1216); PG relies on the documented default backslash escape, same as the existing path (410-415). No pattern can become a wildcard-only scan, and patterns are parameters, so no injection. |
| `speaker` filter vs. sanitization/projection | PASS | The predicate reads raw `metadata` before projection, but the returned nodes are always projected (`read_graph.py:77-84`, `src/prme/privacy/reads.py:217-270`) and protected writes store nodes equal to their projection (`src/prme/ingestion/planning.py:205-253`). `project_admitted_node` and `project_direct_node` keep the normalized `prme_speaker_v1` and the validated `speaker_reference` (src/prme/ingestion/admitted_statements.py:61-95, 145-212), so the filter selects only values the projection would disclose anyway. No `PrivacyDenied` path is skipped: `project_node` still runs per node. |
| Protected sources from the graph-bound read | FAIL | `absorb(await graph.get_nodes(sorted(bound), user_id=user_id))` at src/prme/storage/engine.py:4806-4807 bypasses the `source_availability` strip that every other source read applies at engine.py:4696-4700. See Finding 1. |
| Deadline: no partial publication | PASS | The deadline is checked between pages in `scan_pages` (engine.py:4688-4689) and `turn_events` (4766-4767), before the graph-bound read (4792-4793), and at the top of the per-name publish loop (4866-4868), so an expiry raises before `build_profile`, embedding and `publish_profile`. Every source read returns a complete list before `absorb` is called, so no half-read list can reach a build. Callers use a single name per rebuild (`src/prme/organizer/owner_profiles.py:239-243`), so no cross-entity partial state. All deadlines are `time.monotonic()` (owner_profiles.py:252-253, 281, 286; src/prme/storage/engine.py:5190-5193; src/prme/organizer/maintenance.py:190-197). |
| Logging / PII | PASS | No logging added or removed (diff scan). `ProfileBuildDeadlineExceeded` carries no message (engine.py:181-187) and `_rebuild` returns `None` without logging on it (owner_profiles.py:244-245); the pre-existing `logger.warning(..., exc_info=True)` is unchanged. |
| Unsafe deserialization | PASS | Only `json.dumps` of UUID strings crosses into SQL (duckpgq_graph.py:592); no new `eval`/`pickle`/`yaml.load`. Existing `json.loads` on stored evidence is unchanged. |
| New-filter negative tests (cross-tenant/cross-scope) | CONSIDER | The added tests assert the positive matches only (tests/test_node_enumeration.py:68-92, tests/test_pg_graph_store.py:212-238, tests/test_pg_event_store.py:103-115). A second-owner row in the same fixture would lock in the AND behavior. Low risk: the `user_id = ?`/`scope = ?` predicates are unconditional. |

## Findings

### Should Fix

**1. Graph-bound profile sources skip the protected `source_availability` strip, so a protected rebuild can fail at plan validation.**
`src/prme/storage/engine.py:4806-4807`

The change replaced the single whole-scope scan with several narrowed reads. Nodes from `scan_pages` are stripped of `metadata["source_availability"]` when the context is protected (engine.py:4696-4700), exactly as the old loop did. The new claims read that the whole scope used to supply, `absorb(await graph.get_nodes(sorted(bound), user_id=user_id))`, does not go through `scan_pages` and is not stripped.

Why that matters: for a protected store, `AdmittedGraphView.get_nodes` -> `ReadContext.project_node` attaches `source_availability` to each projected node (src/prme/privacy/reads.py:264-270). `ProfilePublication` validation then compares every source node with its own manifest projection (`node_checksum(project_manifest_node(node, manifest)) != node_checksum(node)`, src/prme/privacy/journals.py:243-247, invoked from src/prme/models/profile.py:187-189). The projection functions never emit `source_availability` (src/prme/ingestion/admitted_statements.py:145-212), so any such node reaching `plan.sources` makes the checksum differ and raises `PrivacyDenied("privacy_invalid_result")`. The repository already keeps this invariant explicit with `without_availability`/`generation_inputs` (journals.py:706-736), which strip the key before derivation.

Reachability: `absorb` deduplicates by id (engine.py:4723-4727), so the bug only bites a node that the earlier content, speaker and evidence reads did not already return stripped. For protected stores the evidence read is skipped entirely (engine.py:4781-4784), so a claim bound to the person's entity whose text does not contain the person's name and that is not stored under their `speaker` is bound-only. In that configuration the build fails closed (no data exposure, the previous profile stays), but it is a regression in a path the old whole-scope scan handled, and it is the one new read that departs from the strip invariant.

Suggested fix: strip `source_availability` inside `absorb` (before `absorbed.add`), or wrap the `get_nodes` result the same way `scan_pages` does, so every source read shares one invariant. A protected-store test with a first-person/third-party claim bound to the entity would cover it.

### Consider

**2. The admitted view now pushes `content_contains_any`/`speaker` into a pre-projection SQL filter, unlike `query_nodes`.**
`src/prme/privacy/read_graph.py:71-76`, `src/prme/storage/duckpgq_graph.py:574-586`

`query_nodes` on the admitted view filters on already-projected values in Python (read_graph.py:128-131), while the new `scan_nodes` filters run on the stored `content`/`metadata` before `project_node`. Today this is not exploitable: protected write paths store nodes that already equal their projection (`planning.py:205-253` applies `project_admitted_graph` and `final_generated_bundle`), and every returned node is still projected and disclosed, so nothing unadmitted is returned. It is a latent inference channel if a protected node ever diverges from its projection (older/migrated data, or a future write path that skips validation): a caller could learn that a term appears in the undisclosed stored text. Worth a comment stating the invariant, or a post-projection check for the admitted path. No action required for this change.

**3. Case-folding parity between the SQL prefilter and `mentions_entity`.**
`src/prme/storage/engine.py:4751-4752` vs `src/prme/organizer/profiles.py:70-86`

The narrowing prefilter uses database `LOWER(content) LIKE`, while the final selection uses `mentions_entity` (Unicode-aware, case-insensitive literal matching). For non-ASCII names whose case mapping differs between Python and DuckDB/PostgreSQL `lower()`, a record the old whole-scope scan would have selected cannot be reached, because the SQL prefilter removes it first. This is a correctness/coverage risk (a source can be missed and a profile retired), not a security defect; it is a dependency of checklist item 2 only. Not verified against a concrete non-ASCII case.

## Verified assumptions

- `_escape_like` is applied to every LIKE value on both backends; the DuckDB and PostgreSQL prefilter expressions match the existing `query_nodes` construction (duckpgq_graph.py:1209-1216, pg/graph_store.py:410-415), which is already in production.
- `evidence_refs` is `JSONB` in PostgreSQL (pg/schema.py:169) and `JSON` in DuckDB (src/prme/storage/schema.py:133), so `?|` and `json_each` are valid and both sides of the DuckDB join compare the same JSON-string cast (duckpgq_graph.py:587-592).
- `evidence_events` and `after_id` are normalized with `str(UUID(...))` before binding, so no caller string reaches the SQL text (duckpgq_graph.py:592, 595; pg/graph_store.py:522, 526).
- `_rebuild` catches `ProfileBuildDeadlineExceeded` before the generic `Exception` clause, so a deadline is not logged as a failure (owner_profiles.py:244-248).
- The graph-bound read and `event_speaker` keep owner checks (`edge.user_id == user_id`, engine.py:4804; `event.user_id == user_id and event.scope == scope`, engine.py:4861).

## Summary

The tenant-isolation and SQL-construction parts of the change hold up: every new filter is parameterized and ANDed inside the existing owner/scope predicate on both backends, the admitted view still enforces its owner and projects every returned node, the deadline path is read-only until after the last deadline check and cannot publish a partial profile, and no new logging touches content or secrets. One regression needs fixing: the new graph-bound source read hands protected nodes to the profile plan without the `source_availability` strip, which can fail a protected rebuild at plan validation (fail-closed, no data exposure).

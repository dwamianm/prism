# Review: issues #438, #443 and #444, owner-scoped graph reads

## Review Scope

- Base commit: dcd9dafa (main); rebased onto 41005d66 (#445, issue #437) before shipping, resolving the chain read, chain route, HTTP API docs and CHANGELOG in favor of the store-level rule while keeping #445's starting-node check.
- Reviewed snapshot: 8ea89123dc1276c40ab4170624c156da65a1e3231147c8698b1226b27d4853d8 (17 files, 1,280 changed lines, uncommitted tree on `issue-438-owner-scoped-graph-reads`)
- Snapshot after review fixes: 28e5c3e476fcf8b987d4a21781230bfd833a70eedc809d249c785c976100f2c7 (17 files, 1,373 lines)
- Tier: L (predicates: SECURITY, NEW_SYMBOLS). No INFRA, DATA or PERSISTED_FIELD: no schema, index, migration or stored field changes; every change is on the read side, plus one operator report that reads ids and owners.
- Roster: light review per the owner's standing review budget (two reviewers per issue). The core reviewer (Opus) carried checklists 1, 3, 4 and 5 and the merged security (2) and data safety (7) checklists. The adversarial reviewer (Opus) carried checklist 8. Checklist 6 (infrastructure) was not applicable.
- The reviewers' full reports were working files; their findings are recorded below.
- Review fixes changed `src/prme/storage/owner_reads.py` (pointer clearing limited to retired nodes), the scoped chain step order in both stores, docstrings, tests, CHANGELOG and the HTTP API docs. They were revalidated with the new tests on DuckDB and PostgreSQL and the full suite rather than a second review round.

## Files Changed

- `src/prme/storage/owner_reads.py` (new): the shared rule. `named_successors`, `hide_foreign_successors`, the `CrossUserLinks` report types and the crossing-link SQL predicates both stores use.
- `src/prme/storage/duckpgq_graph.py`, `src/prme/storage/pg/graph_store.py`: `user_id` on `get_node`, `get_nodes`, `get_neighborhood`, `get_neighborhood_with_depth` and `get_supersedence_chain`; the owner guard on each recursive step; pointer clearing on every read given a user; `find_cross_user_links()`.
- `src/prme/storage/graph_store.py`: protocol signatures and docs.
- `src/prme/privacy/read_graph.py`: the admitted view accepts and checks the same `user_id` keyword and passes the reader to the plain store.
- `src/prme/storage/engine.py`: owner passed by `_owned_node`, `get_nodes`, `get_neighborhood`, `get_supersedence_chain`, `_check_oscillation`, `contradict`, `resolve_contradiction` and `evaluate_condition`; new `find_cross_user_links()`.
- `src/prme/organizer/oscillation.py`, `src/prme/retrieval/candidates.py`, `src/prme/retrieval/snapshots.py`, `src/prme/retrieval/replaced_sources.py`, `src/prme/api/routes.py`: callers pass the user.
- `src/prme/cli.py`: `prme cross-user-links`.
- `tests/test_owner_scoped_graph_reads.py` (new), `tests/test_replaced_source_suppression.py` (fake store accepts `user_id`).
- `CHANGELOG.md`, `documentation/http-api.md`, `documentation/cli.md`.

## Approach Summary

The maintainer decided on 2026-11-05 to stop adding owner checks caller by caller and to put the rule in the graph stores' own reads: a read given a user never steps onto, returns or names another user's node. With a user, each step of the recursive neighborhood CTE joins the node it reaches and drops the step when another user owns it; a missing node passes, as before, so a store without crossing links reads the same. The scoped chain reads every SUPERSEDES link of the current node newest first (the order the admitted view follows through `get_edges`) and follows the first whose node is not another user's. Reads given a user clear a retired node's `superseded_by` when it names another user's node. Unscoped reads are unchanged.

Alternatives considered: filtering in each caller (the #433, #437 and #406 pattern the maintainer rejected because each fix found more callers); clearing the pointer at serialization only (the #443 suggestion; leaves the walks and `replaced_sources` uncovered); clearing it on every node including active ones (rejected after review, finding F1).

## Must Fix

None.

## Should Fix

- F1 (core, data safety): `scan_nodes(user_id=...)` returned a copy with a cleared pointer, and `ingest()` planning (`src/prme/ingestion/planning.py:111-124`) keeps scanned nodes as plan references that `_validate_dependencies` (`src/prme/storage/derivation.py:43-47`) compares with the stored row, so an active node carrying a foreign pointer would make its source fail as stale on every replan. The profile and consolidation publications compare their active sources the same way. Resolved: pointers are cleared only on retired nodes (superseded, deprecated, archived), which is the state every writer that sets `superseded_by` leaves; an active node with a pointer (direct writes only) reads as stored and is listed by `prme cross-user-links`. Test `test_an_active_node_with_a_pointer_reads_as_stored` checks the checksum matches the stored row. Alternative rejected: masking the current rows inside the three dependency validators, which widens the change into the derivation, profile and consolidation publication code. The remaining case is follow-up #451.
- F2 (core, tests): `get_event_nodes`, neighborhood results and chain results were not tested for pointer clearing. Resolved: tests for event nodes, a neighborhood read with `include_superseded=True`, and the chain route (backward chain that lists the crossed node). `get_session_neighbors` reads active nodes only, so after F1 its clearing cannot act; it is kept for consistency.

## Consider

- F3 (core) / scenario 6 (adversarial): the scoped chain step had no order. Resolved: `ORDER BY created_at DESC, id` on the scoped step in both stores, matching the admitted view; `test_a_fork_of_the_users_own_links_follows_the_newest_link`.
- F4 (core): the general HTTP API sentence said requests without a user get the stored pointer, which is not true of the chain and neighborhood routes. Resolved.
- F5 (core): `_owned_node` reads twice when a node has a successor (kept to preserve the ownership warning log); post-filters left in callers are no-ops kept as defense in depth. Accepted.
- F6 (core): other reads that can still name another user's node; see follow-ups.

## Security Audit Results

| Area | Result | Details |
|------|--------|---------|
| Tenant isolation, traversal | PASS | Step guard and output filter on both backends; a start node another user owns returns nothing. |
| Tenant isolation, chain | PASS | Foreign start returns nothing; foreign links are skipped; the result is the user's nodes only. |
| `superseded_by` naming | PASS (retired nodes) | Cleared on every read given a user; active nodes with a pointer are follow-up #451. |
| SQL injection | PASS | Every value is bound; only fixed fragments are interpolated. |
| NULL owner bypass | PASS | `nodes.user_id` is NOT NULL in both schemas. |
| Existence oracle | PASS | Foreign nodes read as missing, as `_owned_node` already did. |
| Protected stores | PASS | The admitted view checks `user_id` with `_owner` and passes the reader down. |
| Operator report | Accepted | `find_cross_user_links` reads ids and owners only, without the privacy read facade, like `prme stats`. |

## Pattern Consistency Assessment

Both stores implement the same semantics and share predicates and types from `owner_reads.py`. The `user_id` keywords follow the existing `query_nodes`/`scan_nodes` owner keyword; the admitted view checks it with `_owner` as it does for its other owner-scoped reads. The CLI command follows `cmd_stats` and reuses `add_common`, `work_limit` and `_format_table`.

## Redundancy Check

No existing helper cleared or reported crossing links. The helper module is used by both stores.

## Wiring Findings

The CLI command is registered in `build_parser`. The engine method, the protocol and both stores are wired. The new test file is collected by the default run.

## Data Safety and Read/Write Proof

No schema, index or persisted field. No write path writes a cleared node back: merges, supersedence, consolidation retirement and derivation re-read rows inside their transactions, and `update_node` writes named fields only. The dependency validators only see active nodes from scoped reads, which are now returned as stored (F1).

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "Months later, a tenant's `/v1/retrieve` bundle still named another tenant's node in `contradicts_id`: the root fix lived in store methods that the retrieval resolution and the raw-SQL relation walk never called with a user."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|----------|-------|------------|---------|-----------|
| 1 | `/v1/retrieve` can still name another user's node: `contradicts_id` and node resolution without a user | pre-existing | L | Follow-up | `contradicts_id` is fixed by #445 (#437), which this branch is rebased onto. The resolution reads are active-only, where the pointer rule does not act after F1; the remaining read paths go in #449. |
| 2 | The relation walk's raw SQL steps through another user's entity | pre-existing, off by default | L | Follow-up | Its own SQL outside the graph store methods; filed as #449. |
| 3 | `engine.get_edges` and `prme edges --user-id` return edges naming another user's node | pre-existing | L | Follow-up | Edge reads were outside this change's node-read rule; filed as #450. |
| 4 | A cleared pointer leaves a superseded node with no successor, which packing reads differently | newly introduced, intended | L | Accept | Cosmetic, deterministic, only on crossing pointers; the alternative (clearing the lifecycle too) would rewrite more of the read model. |
| 5 | Per-step owner join and the full-count report at scale | newly introduced | L | Accept | Measured: scoped 2-hop and 3-hop walks within the unscoped times on 100,000 nodes and 300,000 edges; the report is an operator command. |
| 6 | Scoped chain link order undefined | pre-existing | L | Fix now | One `ORDER BY` per step; done. |
| 7 | A custom graph store without the new keywords raises TypeError | newly introduced | L | Accept | No in-repo store or double breaks; CHANGELOG notes the protocol change. |
| 8 | `_owned_node` re-read race and the `evaluate_condition` fallback | newly introduced | L | Accept | Needs a concurrent delete; the result is a not-found response or a cleared pointer, never a foreign id. |

## Follow-ups Raised

- #449: retrieval reads outside the graph store methods (node resolution without a user, the relation walk's raw SQL) can still pass through or name another user's node (scenarios 1 and 2).
- #450: edge reads for a user (`engine.get_edges`, `prme edges --user-id`) return edges that name another user's node (scenario 3).
- #451: an active node whose `superseded_by` names another user's node is read as stored (F1 remainder).

## Resolution Status

| Item | Status |
|------|--------|
| F1 | Resolved (retired-node rule); remainder #451 |
| F2 | Resolved (tests) |
| F3 / scenario 6 | Resolved (`ORDER BY`) |
| F4 | Resolved (docs) |
| F5 | Accepted |
| F6 / scenarios 1, 2 | Follow-up #449 |
| Scenario 3 | Follow-up #450 |
| Scenarios 4, 5, 7, 8 | Accepted |

## Verification

- New tests: `tests/test_owner_scoped_graph_reads.py`, 30 passed on DuckDB and a live PostgreSQL (pgvector 16), also with `-n 4`. All 12 DuckDB tests of the first version failed with the source change stashed.
- Targeted suites before the review fixes (replaced sources, oscillation, graph neighborhood tenancy, privacy quality sets, graph seed count, CLI, API): 184 passed, 17 skipped.
- `uv run ruff check src/ tests/`: clean. CI's public API mypy check: clean.
- Offline evidence gate not run: a scan of 1,756 saved benchmark packs (LoCoMo gate packs, LongMemEval-S, mid tier, extracted and probe packs; 775,009 edges, 3,594 `superseded_by` pointers) found no link that crosses users, so the new guards cannot act on them. On 50 of those packs, 7,200 walks (60 entities per pack, 1 to 3 hops) returned the same node sets with and without the owner; the CTE's row order differed in 3,134 walks, and two unscoped runs differed in 3,104, so the order was already not stable and retrieval does not depend on it.
- Full suite on the final revision with PostgreSQL (`PRME_TEST_DATABASE_URL` set, `-n auto`), paid providers blocked, `.env` moved aside for `tests/test_derivation_version.py` and restored after: 8424 passed, 157 skipped, 3 xfailed, 3 failed under load. All three pass alone: `test_independent_engines_converge_on_one_summary[duckdb]` ("Concurrent consolidation staging did not settle"; also failed in the DuckDB-only full run before the rebase, 3 of 3 passes alone) and `test_PV11_storage_parity_24_fixed_queries` on both backends (its 5-minute custody window ran out; alone it took 272 s on `main` and 250 s on this branch). Earlier DuckDB-only full run, before the rebase: 7063 passed, 1510 skipped, 2 xfailed, 2 failed (the consolidation test above, and `test_engine_check_uses_atomic_penalty`, which called the private `_check_oscillation` without the new owner argument; fixed in the test).

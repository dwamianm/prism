# Review: issue #406, the snapshot job's user scope

## Review Scope

- Base commit: 2418444d (main)
- Reviewed snapshot: e5ff00e34c866f5a6b85cc32bdd16695d43bde83867b59eb703e8b536255c120 (3 files, 139 lines)
- Final snapshot after review fixes: fab3f5e042e106035f563deab56ad50800eac239fdcca5f0f9a99267e7dc3816 (5 files, 158 lines)
- Tier: L (helper predicates: NEW_SYMBOLS; SECURITY added by me, because the change is about tenant isolation)
- Roster: light review per the owner's standing review budget (two reviewers per issue). The core reviewer (Opus) carried checklists 1, 3, 4, 5 and the merged security checklist 2. The adversarial reviewer (Opus) carried checklist 8. Checklists 6 (infrastructure) and 7 (data) were not applicable: no wiring, config, schema or persisted field changes.
- The reviewers' full reports were working files; their findings are recorded below.
- Review fixes (budget, docstring, CHANGELOG, test) were small and suggested by the reviewers; I revalidated them with targeted tests, a mutation check and the full suite rather than a second review round.

## Files Changed

- `src/prme/organizer/jobs.py`: pass `user_id` to `generate_entity_snapshot`; the job stops at half of the remaining budget.
- `src/prme/retrieval/snapshots.py`: with a `user_id`, refuse another user's entity, filter neighbors by owner, and on an unprotected graph keep only the user's edges between the user's nodes.
- `src/prme/storage/engine.py`: `snapshot()` docstring documents `user_id` and the new refusal.
- `tests/test_organizer_tenancy.py`: three tests (scoped job, unscoped job, foreign entity refusal).
- `CHANGELOG.md`: Fixed entry.

## Approach Summary

Passing `user_id` alone did not scope the read on a plain store: `_ordinary_graph(user_id)` returns the raw graph store unless privacy reads are protected (`src/prme/storage/engine.py:3116-3126`), and the raw traversal has no owner filter. The snapshot function therefore applies the owner filter itself when given a user, matching `MemoryEngine.get_neighborhood` and `get_edges` (`engine.py:3140-3158`). The protected path is unchanged apart from now being reached at all by the scoped job. The unscoped path is unchanged.

## Must Fix

None.

## Should Fix

- S1 (core): CHANGELOG entry. Resolved.

## Consider

- C1 (core) / S4 (adversarial): a neighbor owned by the user but reached through another user's edge stays in the scoped snapshot while the edge is dropped. Accepted, see adjudication.
- C2: test did not isolate the edge-owner clause. Resolved: added an edge bob owns between two of alice's nodes; removing the clause now fails the scoped test (mutation check run).
- C3: `engine.snapshot` docstring. Resolved.
- C4: the job on a protected store is not tested directly. Not done; the protected `engine.snapshot` path is covered by `tests/test_privacy_source_reads.py`, which passed in the full suite.
- C5: `EdgeType` and `MemoryEdge` imports moved to the module top. Resolved. The empty `get_nodes([])` call is left as is (both backends return early).

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Other user's neighbors in a scoped snapshot | PASS | filtered at snapshots.py:147-150 |
| Other user's edges, or edges touching their nodes | PASS | snapshots.py:170-188 |
| Other user's entity through `engine.snapshot(user_id=...)` | PASS | now refused as not found |
| Existence or type oracle | PASS | same message as a missing node, checked before the type |
| Protected read path | PASS | edge filter skipped there; the admitted view already enforces it |
| Unscoped run | N/A | unchanged, global by design |

## Pattern Consistency Assessment

The neighbor rule matches `MemoryEngine.get_neighborhood`; the edge rule matches `MemoryEngine.get_edges` plus the endpoint ownership the protected view enforces. Raising `ValueError` for a foreign entity matches the function's existing contract.

## Redundancy Check

No duplicate source logic. The two test helpers are local to the tenancy test module, as the existing helpers in `tests/test_entity_snapshots.py` are local to theirs.

## Wiring Findings

No new config, routes or registrations. The job was already registered in `ALL_JOBS` and `DEFAULT_JOBS`.

## Data Safety and Read/Write Proof

Not applicable: the change is read-only and adds no persisted field.

## Break Scenarios (adversarial)

Pre-mortem headline: "The scoped snapshot job stopped failing silently on protected stores and started eating the organize budget for snapshots nobody keeps."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| S1 | On protected stores the scoped job now does real work and can starve `consolidate` and `index_compaction` | newly introduced | M | Fix now | Contained: the job now stops at half the remaining budget, as `deduplicate` and `alias_resolve` do (`jobs.py:454`, `jobs.py:500`). Its output is discarded, so fewer snapshots per pass costs nothing. |
| S2 | Retrieval graph expansion keeps neighbors without an owner check (`candidates.py:305-338`) | pre-existing | L | Follow-up #433 | Sibling path outside this issue; needs its own tests in retrieval. |
| S3 | `GET /nodes/{id}/neighborhood` reads the raw graph and skips the protected view (`routes.py:1065`) | pre-existing | L to M | Follow-up #433 | Same root cause as S2 (callers of the raw traversal add no owner filter); grouped in one issue. |
| S4 | A user's own node reached through another user's edge stays in the scoped snapshot | newly introduced | L | Accept | No other user's content is shown and AC1 holds. Re-checked the alternative (derive neighbors from the filtered edges): it would diverge from `get_neighborhood`'s alias and validity rules and from `MemoryEngine.get_neighborhood`, which keeps the same node. Such edges come only from legacy or direct graph writes. |
| S5 | `engine.snapshot(id, user_id=other)` now raises instead of returning the other user's snapshot | newly introduced | L | Accept | Intended: it closes a cross-user read, the protected path already behaved this way, and no API, MCP, CLI or client surface calls it. Docstring and CHANGELOG updated. |
| S6 | One extra `get_nodes` per entity; a hub entity can overrun the budget | mostly pre-existing | L | Accept | The traversal and edge reads were already unbounded; one more query of the same size. Revisit with #93. |
| S7 | The job swallows every exception without recording what failed | pre-existing | L today | Follow-up #434 | Matters once #93 stores snapshots; outside this issue. |

Tally: Fix now 1, Follow-up 3 (two issues), Accept 3, Dismissed 0.

## Follow-ups Raised

- #433: Retrieval graph expansion and the API neighborhood route can return another user's nodes (S2, S3).
- #434: The organizer's snapshot job records failures only as a count, which would hide denied reads once snapshots are stored (S7).

## Resolution Status

| Finding | Status |
|---|---|
| Core S1 CHANGELOG | Resolved |
| Core C1 | Accepted (as adversarial S4) |
| Core C2 test of the edge-owner clause | Resolved, mutation checked |
| Core C3 docstring | Resolved |
| Core C4 protected job test | Not done, covered indirectly |
| Core C5 imports | Resolved |
| Adversarial S1 | Fixed |
| Adversarial S2, S3 | Follow-up #433 |
| Adversarial S4, S5, S6 | Accepted |
| Adversarial S7 | Follow-up #434 |

## Verification

- Targeted: `tests/test_organizer_tenancy.py`, `tests/test_entity_snapshots.py`, `tests/test_organizer.py`: 98 passed.
- Without the source change, the scoped and refusal tests fail; without the edge-owner clause, the scoped test fails.
- Full suite on the final snapshot, paid providers blocked, local `.env` moved aside for the run and restored: `uv run pytest -q -n auto --dist worksteal`, 6971 passed, 1486 skipped, 2 xfailed, exit 0.
- `uv run ruff check` on the changed Python files: clean.

# Review: issue #433, graph neighborhood reads scoped to the user

## Review Scope

- Base commit: 0ea465bc (main)
- Reviewed snapshot: c69f162a207478a89e4a10135e0d071b08dbe8fa8a7ecc3a0396734b8386dbd5 (4 files)
- Final snapshot after review fixes: 86d851bbe8568eb92cca4316e8329c7da7b2e82cab1aceac5ce42686901116ca (5 files, 210 lines; source unchanged since the reviewed snapshot)
- Tier: L (helper predicates: SECURITY, NEW_SYMBOLS). No DATA, INFRA or PERSISTED_FIELD: the change adds a read-side filter and changes no schema, config, index or stored field.
- Roster: light review per the owner's standing review budget (two reviewers per issue). The core reviewer (Opus) carried checklists 1, 3, 4, 5 and the merged security checklist 2. The adversarial reviewer (Opus) carried checklist 8. Checklists 6 (infrastructure) and 7 (data) were not applicable.
- The reviewers' full reports were working files; their findings are recorded below.
- Review fixes were test, documentation and CHANGELOG changes only (no source change after review); I revalidated them with the targeted tests, a check that the new protected-store test fails on main's route, and the full suite rather than a second review round.

## Files Changed

- `src/prme/retrieval/candidates.py`: `_generate_graph_candidates` skips a neighbor another user owns, before it enters `node_map`, so it is never a candidate and `_seed_reach` never walks through it.
- `src/prme/api/routes.py`: `GET /v1/nodes/{id}/neighborhood` reads through `engine._ordinary_graph(reader)` and keeps only the reader's nodes. The reader is the request's user, or the starting node's owner when the request has none.
- `tests/test_graph_neighborhood_tenancy.py`: retrieval expansion with and without the graph seed count, the other user's own expansion, `engine.retrieve()`, the route without identity (including a superseded starting node), and the route on a protected store.
- `documentation/http-api.md`, `CHANGELOG.md`.

## Approach Summary

The plain graph store's traversal has no owner filter, and these two callers did not add one. Both now filter by owner after the traversal, the idiom #406 used in `snapshots.py` and `MemoryEngine.get_neighborhood` / `get_nodes` / `get_edges` use. The route does not call `MemoryEngine.get_neighborhood` directly because that method's start check uses `include_superseded=False`, which would make a superseded starting node return nothing on a plain store; the route keeps its own `include_superseded=True` start check and otherwise does what the engine method does. Alternative rejected: an owner predicate inside both backends' recursive CTEs and the `GraphStore` protocol, which touches every traversal caller (recorded as a follow-up instead).

## Must Fix

None.

## Should Fix

None.

## Consider

- C1 (core): the test comment about the other user's expansion did not match its assertion. Resolved: the other user now has an Acme entity and the test checks their expansion keeps their fact and only their nodes.
- C2 (core) / scenario 7 (adversarial): the route's protected-store path was untested. Resolved: new protected-store test (an owner node joined by an unadmitted direct-write edge and a crossing edge to a foreign node); it fails on main's route (the unadmitted node is returned) and passes with the fix.
- C3 (core): `documentation/http-api.md` did not describe the owner rule. Resolved.
- C4 (core): the PR must say why the offline evidence gate cannot change. Resolved in the PR: a scan of the 2,246 saved gate packs found no edge whose endpoints have different owners.

## Security Audit Results

| Area | Result | Details |
|------|--------|---------|
| Cross-tenant read, retrieval | PASS | `candidates.py` skips any neighbor whose `user_id` differs from the retrieval user before candidates, `node_map` and `_seed_reach`. |
| Cross-tenant read, API route | PASS | Filtered to the reader in every case, including a request without identity (previously unfiltered). |
| Privacy admitted view | PASS | The route reads through `_ordinary_graph`; `PrivacyDenied` maps to 422 (`src/prme/api/app.py:88-91`). |
| Identity spoofing | PASS | `owner` comes from `_user_id(request)`; a bound identity wins. |
| Existence oracle | PASS (unchanged) | A foreign or missing node gives the same 404/422 as before; the start check is unchanged. |
| Injection, config, env | N/A | No new SQL, config or environment reads. |
| Denial of service | Pre-existing | `max_hops` is unbounded on the route for plain stores; follow-up. |

## Pattern Consistency Assessment

Same post-filter idiom as `snapshots.py` (#406) and the engine's owner-scoped graph reads. The route matches `MemoryEngine.get_neighborhood` except the deliberate superseded start check. The filter runs in Python above either backend's traversal, so DuckDB, PostgreSQL and single-file packs behave the same; on protected stores the admitted view already admits only the owner's nodes, so the filter is a no-op there.

## Redundancy Check

No new helper, type or constant. The route inlines the three lines of `MemoryEngine.get_neighborhood` on purpose (documented in the comment) rather than adding a flag to the engine method.

## Wiring Findings

No new routes, config, environment values or registrations. The new test file is collected by the default pytest run.

## Data Safety and Read/Write Proof

Not applicable: no writes, schema, indexes or persisted fields. Receipts replay from recorded scores and are unaffected.

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "Legacy multi-tenant store: graph expansion and /neighborhood stopped leaking, but the same crossing links still surfaced another tenant's nodes through /chain and `prme chain`, and nobody saw it because the new filter drops them without a trace."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|----------|-------|------------|---------|-----------|
| 1 | `/v1/nodes/{id}/chain`, `MemoryEngine.get_supersedence_chain` and `prme chain` follow a crossing supersedence link to another user's node | pre-existing | M | Follow-up | A sibling read the issue does not name; fixing it changes another route and an engine method. Filed with scenario 4. |
| 2 | Crossing edges are hidden but never reported or repaired | pre-existing data | M | Follow-up | Needs a diagnostic or repair command; a new `CandidateDiagnostics` field would change receipts. Filed with scenario 3. |
| 3 | Plain traversal walks through another user's node to reach the user's own nodes | pre-existing | M | Follow-up | No foreign content is disclosed; closing it needs an owner predicate in both backends' recursive CTEs. |
| 4 | A crossing CONTRADICTS edge puts another user's node id on `contradicts_id` (`pipeline.py:894-918`) | pre-existing | L | Follow-up | Id only, no content; a sibling path outside this issue. Filed with scenario 1. |
| 5 | Callers without identity now see only the starting node owner's neighbors | newly introduced, intended | L to M | Accept | This is the issue's intent; such a caller can already name any user on other routes, and the CHANGELOG and docs state it. Alternatives re-checked: refusing such requests (a wider behavior change than the issue asks) or keeping the unfiltered read (the leak itself). |
| 6 | Unbounded `max_hops` on the route | pre-existing | L | Follow-up | Bounding it is an API contract change. |
| 7 | Protected-store route untested; a superseded start returns nothing there | newly introduced divergence (intended) | L | Fix now | Test added, CHANGELOG and docs state the protected behavior. |

## Follow-ups Raised

- #437: supersedence chain reads and contradiction annotations can expose another user's node across a crossing link (scenarios 1 and 4).
- #438: graph traversal still walks through another user's nodes, and crossing links are never reported (scenarios 2 and 3).
- #439: the neighborhood route accepts an unbounded `max_hops` (scenario 6).

## Resolution Status

| Item | Status |
|------|--------|
| C1 test comment | Resolved |
| C2 / scenario 7 protected route test | Resolved |
| C3 docs | Resolved |
| C4 offline gate reasoning | Resolved in PR |
| Scenarios 1 and 4 | Follow-up #437 |
| Scenarios 2 and 3 | Follow-up #438 |
| Scenario 6 | Follow-up #439 |
| Scenario 5 | Accepted |

## Verification

- New tests: 4 passed, 3 skipped (PostgreSQL without a database). Without the source change the retrieval and route tests fail, and the protected-store test fails against main's route (the unadmitted node is returned).
- Targeted run (API, identity, object ids, organizer tenancy, filter forwarding, determinism, receipts, seed count, neighborhood depth): 169 passed, 14 skipped, before the review fixes.
- Full suite, paid providers blocked, `.env` moved aside for `tests/test_derivation_version.py` and restored after: 7050 passed, 1495 skipped, 2 xfailed.
- `uv run ruff check src/ tests/`: clean.
- Offline evidence gate not run: a scan of the 2,246 saved gate packs (LoCoMo and LongMemEval-S, 775,009 edges) found no edge whose endpoints have different owners, so the new filter cannot act on them.

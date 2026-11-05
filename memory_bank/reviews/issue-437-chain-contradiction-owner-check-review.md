# Review: issue #437, supersedence chains and contradiction annotations scoped to the user

## Review Scope

- Base commit: 7f806096 (main)
- Reviewed snapshot: 6baa06e59f7a07f735a80573b5f01031ffaf8ec74c710ecc230b3107e7a1b9e0 (8 files, 290 lines)
- Final snapshot after review fixes: f0d289463a61712d239bb30a5a55dc80592b9400ec90e5e996e4ae9450844d55 (8 files, 312 lines, before this review file)
- Tier: L (helper predicates: SECURITY added for tenant isolation, NEW_SYMBOLS detected). No DATA, INFRA or PERSISTED_FIELD: the change adds read-side owner checks and one batched node read, and changes no schema, config, index or stored field.
- Roster: light review per the owner's standing review budget (two reviewers per issue). The core reviewer (Opus) carried checklists 1, 3, 4, 5 and the merged security checklist 2, plus a data safety screen. The adversarial reviewer (Opus) carried checklist 8. Checklists 6 (infrastructure) and 7 (data) were not applicable.
- The reviewers' full reports were working files; their findings are recorded below.
- Review fixes were the reviewers' own proposed XS mitigations (adversarial 2 and 4, core C2), plus tests and CHANGELOG wording. I revalidated them with the targeted tests and the full suite rather than a second review round.

## Files Changed

- `src/prme/api/routes.py`: `GET /v1/nodes/{id}/chain` uses the neighborhood route's reader rule (the request's user, or the starting node's owner) and reads through `MemoryEngine.get_supersedence_chain(user_id=reader)`, so a protected store goes through the reader's admitted view.
- `src/prme/storage/engine.py`: `MemoryEngine.get_supersedence_chain` with a `user_id` returns nothing for a starting node the user does not own, and ends the chain before the first node another user owns. Without a user it is the unscoped operator read, as before.
- `src/prme/retrieval/pipeline.py`: Stage 5.5 looks up CONTRADICTS counterparts in one batch and keeps only edges whose counterpart the user owns (another user's node, or one the store no longer returns, is skipped).
- `tests/test_chain_contradiction_tenancy.py`, `tests/test_cli.py`: engine read, route without identity and with keys, route on a protected store, `prme chain --user-id`, retrieval annotation with an outgoing and an incoming crossing edge, and the same after the other user's node is deleted.
- `documentation/http-api.md`, `documentation/cli.md`, `CHANGELOG.md`.

## Approach Summary

The plain graph store follows SUPERSEDES and CONTRADICTS links with no owner filter. The chain route now reads through the engine's owner-scoped chain read, with the same reader rule as the #433 neighborhood route. The engine read truncates at the first foreign node instead of skip-filtering, which matches `AdmittedGraphView.get_supersedence_chain` (it stops when a node does not project) and never walks through another user's node, the concern #438 tracks for neighborhood traversal. The contradiction pass keeps only owned counterparts, the rule `get_assertion_state` in `src/prme/retrieval/temporal.py` uses. Alternatives considered: skip-filtering the chain (main's authenticated behavior; rejected because it walks through the foreign node and disagrees with the admitted view), and an owner predicate inside both backends' chain SQL (rejected as wider than the issue; the Python post-check behaves the same on DuckDB, PostgreSQL and single-file packs).

## Must Fix

None.

## Should Fix

None.

## Consider

- C1 (core) dangling counterpart id kept: fixed (same as adversarial 2).
- C2 (core) engine chain has no start owner check: fixed (same as adversarial 4).
- C3 (core) pin the dangling behavior in a test: done (the retrieval test deletes the other user's node and asserts the id stays out).
- C4 (core) the annotation ignores `edge.user_id` and scope, which the provenance and assertion state readers check: not taken. Tenant isolation holds through the counterpart's owner; adding scope or edge-owner rules would change single-tenant output for no isolation gain.

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Chain route with a user | PASS | Start checked with the user; chain read scoped and truncated. |
| Chain route without identity | PASS | Reader is the start node's owner; before, the raw chain was returned unfiltered. |
| Chain route on a protected store | PASS | Now read through the admitted view; before, the raw store was read. |
| `prme chain --user-id` | PASS | Uses the scoped engine read. Without a user the operator read is unchanged, and a protected store still refuses it. |
| Retrieval `contradicts_id` | PASS | Another user's counterpart, or an erased one, is skipped; the next owned edge is used. |
| Injection, config, outbound | N/A | Parameterized graph reads only; no new configuration. |

## Pattern Consistency Assessment

Same reader rule and start check as the #433 neighborhood route. The route calls the engine read rather than inlining `_ordinary_graph` plus a filter, because the engine chain read has no active-only start check that would hide a superseded starting node. The engine read now checks the start owner as `get_neighborhood` and the admitted view do. The contradiction rule matches `get_assertion_state`.

## Redundancy Check

No new helper duplicates an existing one. On a protected store the engine truncation repeats the admitted view's own stop, which is harmless.

## Wiring Findings

No new routes, settings or entry points. The CLI already passed `--user-id` to the engine read.

## Data Safety and Read/Write Proof

Read-only. One batched `get_nodes(include_superseded=True)` runs only when contested candidates have CONTRADICTS edges, with sorted ids. No new durable field, so no writer or reader proof applies.

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "The chain route stopped listing the foreign node but still gave out its id through `superseded_by`, and an erasure brought back an id that the fix had hidden."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | A chain node's `superseded_by` still names another user's node (also in GET /v1/nodes/{id}, MCP and CLI dicts) | pre-existing, sibling field | Low to medium | Follow-up #443 | A different field on every node surface; fixing it changes `_node_to_response` for all routes. CHANGELOG now states the field is unchanged. |
| 2 | A dangling counterpart (another user's node erased) brings its id back into `contradicts_id` | pre-existing behavior the first draft kept | Low | Fix now | The issue asks to drop a counterpart the user does not own, as `temporal.py` does, which also drops dangling ids. XS change. The saved packs hold no CONTRADICTS edge, so no benchmark retrieval changes. |
| 3 | A forked chain whose first successor is foreign hides the owner's other branch; the authenticated route no longer returns owned nodes after a foreign one | newly introduced narrowing | Low | Follow-up #444 | No data leaks. Not walking through a foreign node is the intended rule; the plain store's fork choice is already `LIMIT 1` without order. An owner-aware walk belongs with the other plain-store supersedence walks in scenario 5. |
| 4 | The scoped engine chain read does not check the start node's owner | newly relevant | Low | Fix now | XS; route and CLI already checked, direct SDK callers did not. |
| 5 | Another user's node content still shapes ranking and confidence through the oscillation check and replaced-source suppression | pre-existing, sibling | Low | Follow-up #444 | Influence only, no disclosure; separate code paths from this issue. |
| 6 | Nothing reports dropped crossing links | pre-existing | Medium | Follow-up (existing #438) | #438 already asks for a way to find links that cross users. |

## Follow-ups Raised

- #443: a node's `superseded_by` can name another user's node on node responses (scenario 1).
- #444: supersedence walks on a plain store follow links into another user's nodes: the oscillation check, replaced-source suppression and a forked chain (scenarios 3 and 5).
- Scenario 6 is covered by #438.

## Resolution Status

| Finding | Status |
|---|---|
| Adversarial 2 / core C1 | Fixed |
| Adversarial 4 / core C2 | Fixed |
| Core C3 | Fixed (test) |
| Core C4 | Not taken, reason above |
| Adversarial 1 | Follow-up #443 |
| Adversarial 3 and 5 | Follow-up #444 |
| Adversarial 6 | Covered by #438 |

## Verification

- New tests fail on main without the source change (all five tenancy tests) and pass with it.
- Offline evidence gate not run: a scan of the 1,746 saved packs under `data/` (the 10 LoCoMo gate packs, the 500 LongMemEval-S master packs, the 500 regenerated LongMemEval-S control packs, and the `ingest()`, mid tier and probe packs; 775,009 edges, 3,594 of them SUPERSEDES) found no edge whose endpoints have different owners, no CONTRADICTS edge and no contested node, so neither change can act on them.
- Targeted run before review (new tests, CLI, neighborhood tenancy, contested visibility): 67 passed, 9 skipped (PostgreSQL without a database).
- Full suite on the final source, paid providers blocked, `.env` moved aside for `tests/test_derivation_version.py` and restored after: 7056 passed, 1499 skipped, 2 xfailed.
- `uv run ruff check src/ tests/`: clean.

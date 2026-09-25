# Code Review: issue-86-session-expansion-neighbors-packing-tier

## Plan (written before implementation and before any evidence gate result)

Root cause: `expand_session_context` creates a neighbor it adds with `paths=["SESSION_CONTEXT"]` and
`path_count=1`, and when `SESSION_CONTEXT` joins a candidate another path already found it appends
the path but leaves `path_count` alone (`session_context.py`, the new-neighbor and existing-candidate
branches). `pack_context` orders every candidate with `path_count >= 2` (tier 3) before any
single-path one (tier 4) and compares the tier before the score (`packing.py`, `priority`). With a
500-turn vector search and lenient BM25 most turns are multi-path, so a single-path neighbor of a
strong match waits behind hundreds of turns. Episode and evidence context, the two sibling stages,
already raise `path_count` when their path joins (`episode_context.py`, `evidence_context.py`).

Approach: an opt-in `PackingConfig.session_context_packing`, unset by default, so the default
retrieval, packing and receipts do not change (epic #77 rule).
- `"trigger_tier"`: `SESSION_CONTEXT` counts toward `path_count` when it joins an existing
  candidate, and a neighbor that expansion added takes the multi-path tier when its trigger is in
  that tier or above.
- `"adjacent"`: the same, and as soon as a trigger is packed with its text, the neighbors it
  brought in are tried next, nearest first, at text-bearing levels only, and each that fits is placed
  beside the trigger in session order. A neighbor that does not fit keeps its own later turn.
- Expansion records each neighbor's trigger (the highest-ranked one whose window holds it) and
  offset as working state that is never serialized (`RetrievalCandidate.session_context_link`,
  `exclude=True`, like `context_relevance`). A neighbor that is itself a trigger keeps its own place.
- Receipts that record the setting use schema version 21 under either formula.
- The evidence gate counts, per question and category, the packed records that session expansion
  reached, found alone, or scored, and `gate-compare` reports them before and after.

Alternatives considered and why they were not taken:
- Change the default directly: ruled out by the epic #77 rule in `CLAUDE.md` (defaults change only
  after the paired DeepSeek answer run) and by acceptance criterion 3.
- Read the trigger from score provenance (`ScoreAdjustment.source_node_id` of the `session_decay`
  operation): `session_context.py` replaces a candidate's provenance only when the inherited score
  is higher, so an existing single-path neighbor with a higher own score (criterion 1's second case)
  has no record of its trigger, and with several triggers only the last promotion is kept.
- Give an added neighbor `path_count=2`: `path_count` is documented as the number of backends that
  produced the candidate (`models.py`, `RetrievalCandidate.path_count`) and its paths would list one.
- Record the setting only in the execution descriptor's parameters (the open #85 pull request's
  approach for a `PRMEConfig` field): `PackingConfig` ignores unknown keys (no `extra="forbid"`
  in `config.py`) and is serialized into every receipt's `packing`, so an older reader would drop
  the setting silently and the checksum would change. #83 bumped the receipt version for the same
  reason, and #111, the closest analogue (a `PackingConfig` field), used version 17.

Shared state touched: `RetrievalCandidate.path_count` (visible in HTTP results and bundles) changes
only with the setting on; the receipt `packing` object and schema version change only with the
setting on; gate reports gain a `session_context` row field and summary key (`gate-compare` accepts
reports written before them). `reranker.original_anchor` mirrors the balanced head rule and runs
before expansion, so with the reranker and the setting both on, the packer's head can differ from
the reranker's anchor.

Prediction stated before the gate results were read (recorded while the runs were in progress):
- `trigger_tier`: small. Most LoCoMo turns are already multi-path, so the change mostly adds the
  single-path neighbors of the top 20 triggers to tier 3 at 0.6 of their trigger's fused score. I
  expect LoCoMo all-evidence share within 1 point per category and a possible small LongMemEval-S
  multi-session loss from long neighbor turns entering the multi-path tier.
- `"adjacent"`: many more packed records arrive through session expansion (up to six per packed
  trigger). I expect LoCoMo single-hop and temporal within 1 point, a LoCoMo multi-hop loss of more
  than 1 point (its evidence is scattered across sessions and needs breadth), and a LongMemEval-S
  multi-session loss of more than 1 point (neighbor turns are long and use the budget).

**Plan change after review and the first gate run.** The first implementation of `"adjacent"`
pulled a packed trigger's whole window (up to six turns) in right after it. The first gate run
(commit `a796a0b0`) measured it against the defaults: LoCoMo all evidence packed -5.5 points (-7.3 to
-3.9; multi-hop -11.7) and LongMemEval-S -15.7 points (-19.6 to -12.3; multi-session -21.5,
single-session-preference -33.3). That is the prediction above, and larger: the window crowds out
everything else, the #111 pattern at packing time (break scenario 1). `"adjacent"` now pulls only the
turn right after a trigger and then the one right before it, of the trigger's node type, and places
every packed window member beside the others in session order whichever is packed first (which also
fixes the correctness Must Fix and break scenarios 6 and 7). `"trigger_tier"` did not change; on the
first run it gained LoCoMo +0.9 points (+0.1 to +1.8) with no category loss and left LongMemEval-S
unchanged (+0.4, +0.0 to +1.1).

**Second plan change, after the second gate run** (commit `34a0d9ea`). Even the capped pull lost
LongMemEval-S evidence: -8.5 points (-11.3 to -5.7; multi-session -16.5, single-session-preference
-16.7, temporal-reasoning -7.9), while LoCoMo moved +0.1 (-1.2 to +1.3; multi-hop -3.2, single-hop
+1.4). LongMemEval-S assistant turns are long, so packing the turn after a trigger early spends the
budget that other evidence needed. The loss comes from choosing records out of priority order, not
from where they appear, so `"adjacent"` is now placement only: it packs exactly the records
`"trigger_tier"` packs (a test checks this for three orderings and six budgets) and places each
window's packed members together in session order. The issue's "pack the neighbor next to it" is met
as placement; pulling the neighbor in early was measured twice and rejected, and both results are
recorded here and in the pull request.

## Files Changed
- `src/prme/retrieval/config.py`: `PackingConfig.session_context_packing`
  (`Literal["trigger_tier", "adjacent"] | None`, unset by default, omitted when unset, `[HYPOTHESIS]`).
- `src/prme/retrieval/models.py`: `SessionContextLink` (`trigger_id`, `offset`) and
  `RetrievalCandidate.session_context_link`, serialized only when set.
- `src/prme/retrieval/session_context.py`: with the setting on, SESSION_CONTEXT adds one to
  `path_count` when it joins a candidate, and each neighbor that is not a top-K trigger is linked
  to the highest-ranked trigger whose window holds it, with its offset.
- `src/prme/retrieval/packing.py`: `base_tier` and `tier_of` (a linked single-path neighbor joins
  tier 3 when its trigger is in tiers 0 to 3); `_try_include` takes a position and leaves exclusion
  to its caller; under `"adjacent"`, `_pack` places the packed members of each window together in
  session order, whichever is packed first. Docstrings now number the five tiers as the code does.
- `src/prme/models/relevance.py`: receipt schema version 21 under either formula; `make_receipt`
  picks it only when session expansion is on, and drops the setting otherwise.
- `src/prme/config_audit.py`: the setting is effective only with session expansion on.
- `benchmarks/diagnostics/product_packing.py`: per-question `session_context` counts (reached,
  added, promoted), summary and per-category comparison, Markdown, and a refusal when session
  expansion is off.
- Tests: new `tests/test_session_context_packing.py`, new pinned fixture
  `tests/fixtures/relevance/receipt-v20-weighted.json` (written by `main` at `67e71701`),
  `tests/test_product_packing_diagnostic.py`, `tests/test_config_audit.py`.
- Docs: RFC-0005 (Sections 4.5, the path definitions, 7.2 receipts, 8), RFC-0006 (Section 5 and
  conformance), RFC-0017, `docs/PACKING.md`, `docs/HTTP-API.md`, `docs/INTEGRATION.md`,
  `documentation/configuration.md`, `AGENTS.md`, `BENCHMARKS.md`, `CHANGELOG.md`.

## Approach Summary
See the plan and its change above. Production defaults are unchanged: `PRMEConfig()` leaves the
setting unset, and with it unset expansion, packing, results, bundles and receipts are byte for byte
what `main` produces (the defaults gate run reproduces the published 84.0% / 94.7% all-evidence
figures, and the full suite, including every pinned receipt fixture, passes).

## Must Fix
1. (Correctness) Under balanced or density order a short neighbor could be packed before its
   trigger and stay apart from it. Resolved: window members are placed together in session order
   whichever is packed first (`_pack`), tested with balanced order.
2. (Patterns) No pinned version 20 receipt although the version rules were rewritten. Resolved:
   `receipt-v20-weighted.json` written by `main`, checked by checksum.
3. (Patterns, Wiring) Docs and RFC text for the setting and version 21, including RFC-0006's
   "MUST be implemented in priority order". Resolved.

## Should Fix
1. (Correctness, Adversarial 2) Neighbors of instructions, pins, tasks and bounded evidence were
   pulled ahead of those tiers. Resolved: `"adjacent"` no longer packs anything early (second plan
   change), so every record keeps its priority; a test checks that it packs the same records as
   `"trigger_tier"`.
2. (Redundancy) `exclude_on_failure` did not cover blank text. Resolved: the parameter is gone;
   `_try_include` never excludes and the main loop does.
3. (Redundancy) Version 21 constant used in some places only. Resolved: removed; 21 is written
   like 20.
4. (Patterns, Redundancy) With session expansion off the setting changed nothing yet wrote version
   21 and was reported effective. Resolved in `make_receipt` and the config audit; tested.
5. (Patterns, Quality, Security) Error messages named versions 15 to 19 for version 21 receipts.
   Resolved.
6. (Patterns) Config audit tested only dormant. Resolved.
7. (Patterns) No gate replay with the setting on. Resolved.
8. (Quality) Nearest-first test passed with any order; tie order untested. Resolved by removing
   the early pull it tested; placement is tested under score and balanced order.
9. (Quality) Docstrings numbered the tiers three ways. Resolved.
10. (Quality, Wiring) Linking never tested through the built-in neighbor query. Resolved: an
    engine test with `session_context_top_k=1` checks links and path counts (DuckDB here,
    PostgreSQL in CI).
11. (Wiring) BENCHMARKS.md did not list the new gate counts. Resolved.

## Consider
- Accepted: `pack_context` is long; the new logic is in small nested helpers that share its state,
  like the existing ones.
- Accepted: the balanced reserved head is still picked by `path_count >= 2`, so a lifted neighbor
  (one path) is never the head. The head is meant to be the strongest record found by two paths.
- Accepted: a trigger inside another trigger's window keeps its own place, so two adjacent triggers'
  windows can render as two runs.
- Accepted: neighbors in another section than their trigger are appended in packing order.
- Accepted: `added` and `promoted` gate counts are approximate when episode, evidence projection or
  augmentation stages run (off by default and off in the gate), since those copy provenance.
- Accepted: no pinned version 21 fixture; version 21 is new, and round-trip tests cover both
  formulas. The first release that writes it should pin one.
- Accepted: an empty `PRME_PACKING__SESSION_CONTEXT_PACKING` value is invalid; the configuration
  reference says to remove the variable.
- Resolved: removed the dead `in required` check; kept a cheap self-link guard.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII | PASS | The link holds a node ID of the caller's own candidate and an offset. |
| Scope and tenant isolation | PASS | Links come from the owner- and scope-filtered neighbor query; packing only reorders its input. |
| Authorization | PASS | No new endpoint; a request cannot set the option. |
| Input validation | PASS | `Literal` setting; invalid environment values fail at startup. |
| Deserialization | PASS | Receipts parse through frozen Pydantic models that forbid extra fields. |
| Resource use | PASS | At most two extra fit attempts per packed trigger. |
| Network binds | N/A | No change. |

## Pattern Consistency Assessment
Follows #111 (a `PackingConfig` field omitted when unset, a receipt version, a config-audit gate, a
gate refusal, a pinned fixture and the same docs) and #83 (a new receipt version and a pinned
previous-version receipt). The path-count raise mirrors `episode_context.py` and
`evidence_context.py`. The gate observations follow the open #85 pull request's shape, so the two
touch neighboring lines in `product_packing.py` and whichever merges second needs a small rebase.

## Redundancy Check
No new dependency. The version constant, the `exclude_on_failure` parameter and the dead required
check were removed; test helpers reuse `_graph` and `tests/test_rank_fusion.candidate`.

## Wiring Findings
The setting reaches expansion, packing and receipts through `effective_packing_config`, including
per-request copies; the environment variable parses; the HTTP and MCP receipt readers accept version
21; the evidence gate's `--set` and the DeepSeek harness's variant `--set` reach it; CI collects the
new test file. Docs updated as listed above.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7, verbatim): "Adjacent session packing passes the paired test on LoCoMo
while LongMemEval-S multi-session answers quietly drop: the #111 crowding pattern, now at packing
time."

| # | Scenario | Label | Likelihood / impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | `adjacent` fills a 4K context with one conversation's window | newly introduced | Medium-High / High | Fix now | Confirmed on the gate (LongMemEval-S -15.7 with the whole window, -8.5 with the nearest turns). `adjacent` is now placement only and packs the same records as `trigger_tier`; tested for three orderings and six budgets. |
| 2 | Neighbors of pins, tasks and instructions jump the queue | newly introduced | Low-Medium / Medium | Fix now | Closed by the same change: nothing is packed out of priority order. |
| 3 | Session windows count extracted entities and facts, so "nearest" is not a turn | pre-existing, made worse | Medium (extraction stores) / Medium | Follow-up #195 | With the early pull gone, `adjacent` only orders records by these offsets. The window itself counting derived records predates this change and needs a store query change. |
| 4 | `trigger_tier` puts a single-path trigger's existing neighbors ahead of it | newly introduced | Medium-Low / Low-Medium | Accept | This is the issue's "raise path_count when SESSION_CONTEXT joins", and it matches episode and evidence context, which raise the count whatever the anchor's tier. Re-checked the alternative (raise only for multi-path triggers): it would make `path_count` mean different things in sibling stages. The neighbor has two independent paths; the gate shows no category loss. |
| 5 | A neighbor follows only its first trigger, even when that trigger is gone | newly introduced | Low-Medium / Low | Follow-up #196 | Needs every link kept in rank order (a model change); the setting is off by default. |
| 6 | Saved-candidate diagnostics cannot reproduce a context built with the setting | newly introduced | Medium / Low (loud) | Fix now | The link is serialized when set; a test round-trips saved candidates. |
| 7 | Repacking moves neighbors away from a reserved trigger | newly introduced | Low / Low | Fix now | Placement applies to reserved records too; a test repacks with a reserved record. |
| 8 | An older release cannot read a version 21 receipt | pre-existing pattern | Low / Medium (loud) | Accept | Same contract as versions 19 and 20; the changelog says to unset the variable rather than downgrade. |

Tally: Fix now 4, Follow-up 2, Accept 2, Dismissed 0.

## Follow-ups Raised
- #195: session expansion windows count extracted entities and facts, not just neighboring turns
  (scenario 3, pre-existing).
- #196: session context packing follows only a neighbor's highest-ranked trigger, even when that
  trigger is not in the context (scenario 5).

## Evidence gate (4K, all runs on `eca1004e`, clean tree, before the rebase onto #194)

Current defaults against each value. Intervals resample questions.

| Benchmark | Defaults | `trigger_tier` and `adjacent` | Change (95% interval) | Wins / losses |
|---|---:|---:|---|---:|
| LoCoMo all evidence packed | 1,290/1,536 (84.0%) | 1,304/1,536 (84.9%) | +0.9 (+0.1 to +1.8) | 28 / 14 |
| LoCoMo multi-hop | 49.6% | 49.6% | 0.0 (-2.8 to +2.8) | 9 / 9 |
| LoCoMo open-domain | 57.6% | 57.6% | 0.0 (-4.3 to +4.3) | 2 / 2 |
| LoCoMo single-hop | 95.2% | 96.8% | +1.5 (+0.7 to +2.5) | 14 / 1 |
| LoCoMo temporal | 92.2% | 92.5% | +0.3 (-0.9 to +1.6) | 3 / 2 |
| LongMemEval-S all evidence packed | 445/470 (94.7%) | 447/470 (95.1%) | +0.4 (+0.0 to +1.1) | 2 / 0 |
| LongMemEval-S multi-session | 89.3% | 90.1% | +0.8 (+0.0 to +2.5) | 1 / 0 |
| LongMemEval-S single-session-user | 96.9% | 98.4% | +1.6 (+0.0 to +4.7) | 1 / 0 |
| Other LongMemEval-S categories | | | 0.0 | 0 / 0 |

Packed records through session expansion (reached, share of packed records / found by no other
path / scored by a session decay), defaults to either value: LoCoMo 59,802 (45.9%) / 9 / 32,771 to
70,362 (52.8%) / 3,232 / 43,932; LongMemEval-S 10,904 (54.7%) / 0 / 2,431 to 11,246 (55.6%) / 1 /
2,782. Per category in the committed comparisons. `adjacent` changes the order of 1,540 of 1,540
LoCoMo and 498 of 500 LongMemEval-S contexts against `trigger_tier`, and no record. The defaults run
reproduces the published defaults figures (1,290/1,536 and 445/470, 84.5 and 39.9 records per
context) and every context of the earlier defaults run on `34a0d9ea`. With the defaults before
2026-09-25 (`scoring.fusion="weighted"`, `packing.context_format="auditable"`,
`packing.multipath_ordering="balanced"`) the gate on `eca1004e` reproduces all 2,040 saved contexts
(1,540/1,540 and 500/500). After rebasing onto #194 (#85, an opt-in that changes no default), the
defaults run on the rebased branch (`caf84acc`) reproduces every context of the `eca1004e` defaults
run.

Rejected versions of `adjacent`, measured on earlier revisions of this branch against the defaults:
whole-window pull, LoCoMo -5.5 (-7.3 to -3.9), LongMemEval-S -15.7 (-19.6 to -12.3); nearest turn
after and before, LoCoMo +0.1 (-1.2 to +1.3; multi-hop -3.2), LongMemEval-S -8.5 (-11.3 to -5.7;
multi-session -16.5, single-session-preference -16.7, temporal-reasoning -7.9).

Prediction check: `trigger_tier` was predicted to stay within 1 point per LoCoMo category with a
possible LongMemEval-S multi-session loss; it gained single-hop by 1.5 points (more than predicted,
in the good direction) and multi-session did not lose. The early-pull prediction (LoCoMo multi-hop
and LongMemEval-S multi-session losses of more than 1 point) held, which is why the pull was dropped.

Simulations (`python -m scripts.run_simulations`): 74/74 under the current defaults, the previous
defaults, and each value of the setting.

## Resolution Status

| Finding | Severity | Status |
|---|---|---|
| Neighbor packed before its trigger stays apart | Must Fix | Resolved |
| No pinned version 20 receipt | Must Fix | Resolved |
| Docs and RFC text | Must Fix | Resolved |
| Pins and instructions displaced by neighbors | Should Fix | Resolved (no early pull) |
| `exclude_on_failure` gap | Should Fix | Resolved |
| Version constant used inconsistently | Should Fix | Resolved |
| Setting without session expansion | Should Fix | Resolved |
| Version 21 error messages | Should Fix | Resolved |
| Audit tested only dormant | Should Fix | Resolved |
| No gate replay with the setting | Should Fix | Resolved |
| Nearest-first test | Should Fix | Resolved (test removed with the pull) |
| Tier numbering in docstrings | Should Fix | Resolved |
| Built-in neighbor query untested | Should Fix | Resolved |
| BENCHMARKS.md gate fields | Should Fix | Resolved |
| Break scenarios 1, 2, 6, 7 | Fix now | Resolved |
| Break scenarios 3, 5 | Follow-up | #195, #196 |
| Break scenarios 4, 8 | Accept | Recorded above |

# Code Review: issue-111-rrf-session-expansion-crowding

## Files Changed
- `src/prme/retrieval/config.py`: `PackingConfig.session_context_rank_fusion_score_decay`, an opt-in
  fraction in (0, 1], unset by default and left out of serialized settings when unset. The
  `session_context_score_decay` description points to it.
- `src/prme/retrieval/session_context.py`: each trigger's decay is the rank fusion decay when that is
  set and the trigger's score provenance is formula version 2; otherwise `session_context_score_decay`.
  Docstrings updated.
- `src/prme/models/relevance.py`: receipt schema version 17. `make_receipt` writes it for rank fusion
  when the decay is set; version 17 requires the decay and every `session_decay` coefficient in its
  provenance must equal it; versions 1 to 16 cannot record it; weighted receipts drop it.
- `src/prme/config_audit.py`: activation gate, effective when the decay is set.
- `benchmarks/diagnostics/product_packing.py`: the evidence gate refuses the decay without rank
  fusion, as it already refuses `scoring.rrf_k`.
- Tests: new `tests/test_rank_fusion_session_context.py`, new pinned fixture
  `tests/fixtures/relevance/receipt-v16-rrf.json` (written by `main` at `f194a77e`),
  `tests/test_config_audit.py`, `tests/test_product_packing_diagnostic.py`.
- Docs: RFC-0005 (Sections 4.5 and 7.2), RFC-0006, RFC-0017, `docs/PACKING.md`, `docs/HTTP-API.md`,
  `docs/INTEGRATION.md`, `documentation/configuration.md`, `AGENTS.md`, `CHANGELOG.md`.

## Approach Summary
Root cause: `expand_session_context` gives a neighbor `trigger.composite_score * 0.85`. A rank-fused
score is `(k + 1) / (k + r)` for rank r on both channels, so with `k = 60` 0.85 of first place
outranks every candidate from about twelfth place down, and the neighbors of the top 20 triggers
land between about twelfth and thirty-fifth place. In the 4K reader contexts on `main`, 38% of
LongMemEval-S packed records (7.2 per question) and 58% of LoCoMo packed records were promoted
session neighbors.

Final approach: an explicit, opt-in rank fusion session decay. The offline evidence gate favors 0.6.
Unset, nothing changes for either formula.

Alternatives measured on the gate (reader format, score order, rank fusion; all evidence packed,
LoCoMo / LongMemEval-S; the full table is in the PR):
- Rank position inheritance (neighbor rank = trigger rank / 0.85): LongMemEval-S 73.4% at 4K against
  83.2% today. Rejected.
- Fewer triggers (`session_context_top_k` 10 or 5): LongMemEval-S unchanged at 83.2% at 4K, because
  the crowding comes from the top triggers. Rejected.
- Rank offsets of 20, 40 and 60 places: at best equal to a 0.6 decay (offset 60: 85.1% / 86.8% at
  4K), with a new adjustment semantics. Rejected.
- Decay sweep 0.5 to 0.75: 0.6 is the best or tied at 4K, 8K and 16K (4K: 85.2% / 86.8%; 8K: 90.2% /
  91.9%; 16K: 94.5% / 96.0%). At every budget it keeps expansion's LoCoMo gain and matches or beats
  turning expansion off on LongMemEval-S.
- Turning session expansion off under rank fusion: loses LoCoMo (82.4% against 84.6% at 4K).
- Changing `session_context_score_decay`: would change the weighted production default.
- Packing a neighbor next to its trigger: tracked in #86.

Plan change during review (step 16a): the plan approved in Phase 2 made 0.6 the default under rank
fusion. Break scenario 1 showed that this would silently change the `prme-reader-rrf` DeepSeek
variant, which already has a complete first pair and confirmation measured with 0.85, while its
settings (and so its identity in the harness) stay the same. The approach changed to an explicit
option with an unchanged default, which the epic #77 rules ask for anyway. That also closed break
scenarios 2, 4 and 5 and let the field be bounded, since no older receipt carries it.

## Must Fix
None.

## Should Fix (all resolved)
- Weighted receipts read back a customized dormant decay as a different value (Agents 1, 3, 7).
  Resolved: `make_receipt` drops it from weighted receipts, and the unset value is omitted, so the
  receipt equals its read-back. Tested with a weighted engine that sets the decay.
- The weighted-receipt fill was never pinned by a test (Agent 3). Resolved with the approach change:
  there is no fill; the engine test asserts `saved.packing == config.packing`.
- The 0.6 description needed "on both channels" and the single-channel ceiling (Agent 3). Resolved in
  the field description and RFC-0005.
- An existing rank fusion test called `expand_session_context` without the flag (Agents 4, 5).
  Resolved: the flag is gone; the decay follows each trigger's provenance, and unset means 0.85, so
  that test matches the pipeline again.
- The evidence gate accepted the decay without rank fusion (Agents 4, 6). Resolved in `gate_config`
  with a test, mirroring the `rrf_k` guard.
- The DeepSeek harness would pool old and new rank fusion pairs as one variant (Agent 6). Resolved
  by the approach change: the default is unchanged, and a variant that sets the decay has its own
  settings. The general gap (same settings, different code) is #143.

## Consider
- Duplicate or default-only tests (Agents 3, 5): rewritten. Tests use values distinct from both
  defaults (0.5, 0.7, 0.55); the twelfth-place test is the only one about 0.6 itself.
- Module docstring promise "remaining higher than unrelated results" (Agents 3, 7): now scoped to the
  weighted formula.
- Error message wording (Agent 3): matches the sibling messages and the tests match the full phrase.
- Receipt decay not checked against the applied coefficients (Agents 1, 2): version 17 now checks it.
- Config audit gate cannot see `session_context_window` or a per-request `weights=` (Agents 3, 6):
  it now reports the decay effective when set, which is the case a per-request rank fusion uses.
- Docs line wrapping and the `[HYPOTHESIS]` tag in the configuration table (Agent 4): rewritten.
- Pre-existing, not in scope: a non-integer `schema_version` in a corrupted stored receipt raises
  `TypeError` rather than a validation error (Agent 2, C3).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII | PASS | No new logging; the receipt adds one numeric setting |
| Who can set the decay | PASS | Operator config or environment only; HTTP and MCP cannot set packing fields or weights |
| Receipt ownership | PASS | Unchanged |
| NaN, infinity, out-of-range values | PASS | `allow_inf_nan=False` and (0, 1] bounds; tested |
| Loading malformed receipts | PASS | Clean validation errors; version 17 without the decay or with a contradicting coefficient is rejected |
| Injection, deserialization | N/A / PASS | Pydantic JSON validation only |
| Credentials in fixtures | PASS | Synthetic IDs and text |

## Pattern Consistency Assessment
Follows the version 14, 15 and 16 bumps: Literal, `make_receipt` branch and comment, version rules,
pinned earlier-version fixture with checksum, config audit entry, and the same documentation set.
The unset field is omitted when serialized, like `ScoringWeights.fusion` and `rrf_k` and
`ReceiptCandidate.semantic_relevance`, so no earlier receipt, gate provenance or benchmark snapshot
changes.

## Redundancy Check
The new field is needed: changing `session_context_score_decay` would change the weighted default,
and a hard-coded constant would be neither configurable nor recorded. Test duplication was removed.

## Wiring Findings
Reachable through `PRMEConfig.packing` and `PRME_PACKING__SESSION_CONTEXT_RANK_FUSION_SCORE_DECAY`
(tested), both backends share the pipeline, per-request `weights=RRF` on a weighted engine takes the
decay (tested), receipts read back identically through the engine and over HTTP (tested), the audit CLI lists it, CI picks up the
new test file. Snapshot comparisons in `product_packing.compare` and `packing_reader` are unaffected
because the unset field is not serialized.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Rank fusion made default on DeepSeek pairs that predate #111; the
shipped 0.6 session decay was never answer-tested, and the harness refuses to test it."

| # | Scenario | Trigger | Likelihood | Impact | Label | Verdict | Reasoning |
|---|---|---|---|---|---|---|---|
| 1 | Complete DeepSeek pairs for `prme-reader-rrf` measured 0.85; a 0.6 default under rank fusion keeps the same settings, so the harness pools them and refuses new pairs | Next default decision on epic #77 | High | A default flip ships an untested decay, or the new decay can never be measured | Newly triggered, harness gap pre-existing | Fix now | Approach changed to an unset-by-default option; the general harness gap is #143 |
| 2 | 0.6 chosen on reader format and score order applies to every rank fusion configuration | `PRME_SCORING__FUSION=rrf` alone | Medium | Unmeasured packing changes | New | Fix now | Opt-in now; the default-packing gate numbers are in the PR |
| 3 | A receipt records one decay while its neighbors took another | A direct caller without the flag | Low | Self-contradicting audit trail | New | Fix now | Decay follows trigger provenance; version 17 checks every coefficient |
| 4 | The new decay has no bounds | A typo such as 6 | Low | Neighbors outrank triggers | New | Fix now | Bounded to (0, 1]; no earlier receipt carries it |
| 5 | `session_context_score_decay` silently ignored under rank fusion | Existing tuned configs | Low | Lost tuning | New | Fix now | Unset means it still applies |
| 6 | Weighted receipt comment false for a customized dormant decay | Environment variable set globally | Low | Receipt differs from read-back | New | Fix now | Weighted receipts drop it |

## Follow-ups Raised
None. The pre-existing harness gap behind scenario 1 is already tracked in #143.

## Evidence gate (offline)
Before: `main` at `f194a77e`. After: this change at `d29f0f80`. Share of questions with all annotated
evidence packed.

| Run | LoCoMo | LongMemEval-S | LongMemEval-S multi-session | Contexts identical to before |
|---|---:|---:|---:|---:|
| Defaults | 983/1536 (64.0%) to 983/1536 (64.0%) | 403/470 (85.7%) to 403/470 (85.7%) | 90/121 to 90/121 | 2040/2040 |
| Rank fusion, reader, score order, 4K, decay unset | 1299/1536 (84.6%) to 1299/1536 (84.6%) | 391/470 (83.2%) to 391/470 (83.2%) | 77/121 to 77/121 | 2040/2040 |
| Rank fusion, reader, score order, 8K, decay unset | 1385/1536 (90.2%) to 1385/1536 (90.2%) | 416/470 (88.5%) to 416/470 (88.5%) | 90/121 to 90/121 | 2040/2040 |
| Rank fusion, reader, score order, 4K, decay 0.6 | 84.6% to 85.2% (+0.6, -0.3 to +1.4) | 83.2% to 86.8% (+3.6, +1.3 to +6.0) | 63.6% to 72.7% (+9.1, +3.3 to +15.7) | |
| Rank fusion, reader, score order, 8K, decay 0.6 | 90.2% to 90.2% (+0.1, -0.2 to +0.3) | 88.5% to 91.9% (+3.4, +1.7 to +5.1) | 74.4% to 81.8% (+7.4, +3.3 to +12.4) | |
| Rank fusion, default packing, 4K, decay 0.6 | 72.4% to 72.3% (-0.1, -1.7 to +1.5) | 88.1% to 88.3% (+0.2, -1.5 to +1.9) | 73.6% to 75.2% (+1.7, -2.5 to +5.8) | |

Changes are percentage points with `gate-compare`'s paired 95% intervals. With default packing
(balanced order, auditable format), 0.6 moves LoCoMo multi-hop from 25.5% to 31.6% (+1.8 to +10.6)
and single-hop from 88.0% to 85.9% (-4.3 to +0.0), so it is neutral overall there. All "decay 0.6"
contexts are identical to the config-only sweep runs of `session_context_score_decay=0.6` under rank
fusion.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Weighted receipt read-back of a customized decay | Should Fix / Break 6 | Resolved |
| Weighted fill untested | Should Fix | Resolved (no fill) |
| "On both channels" wording | Should Fix | Resolved |
| Old rank fusion test without the flag | Should Fix | Resolved (flag removed) |
| Gate accepts the decay without rank fusion | Should Fix | Resolved |
| Harness pools old and new rank fusion pairs | Should Fix / Break 1 | Resolved (opt-in); general gap in #143 |
| Unmeasured packing configurations | Break 2 | Resolved (opt-in, numbers in PR) |
| Receipt decay not checked against coefficients | Break 3 / Consider | Resolved |
| Unbounded decay | Break 4 / Consider | Resolved |
| Weighted decay silently ignored | Break 5 | Resolved |
| Test duplication, naming, docstring, messages, docs wrapping | Consider | Resolved |
| Audit gate precision | Consider | Resolved (gate on the value) |
| Non-integer schema_version raises TypeError | Consider, pre-existing | Not in scope |

Tests on the final tree: `uv run pytest -q` 4259 passed, 863 skipped (PostgreSQL variants skip
without `PRME_TEST_DATABASE_URL`); `uv run ruff check src/ tests/` and the strict mypy public API
check pass.

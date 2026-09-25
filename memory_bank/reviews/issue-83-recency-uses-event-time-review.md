# Code Review: issue-83-recency-uses-event-time

## Plan (written before implementation and before any evidence gate run)

Root cause: the weighted formula's recency outside current-state questions reads
`node.updated_at or node.created_at` against the request clock (`scoring.py`, `compute_composite_score`).
For imported history (past `event_time`, ingestion today, a past `reference_time`) every age is
negative and clamps to 0, so recency is a flat 1.0 and carries no information. Current-state
questions already measure event time back from the newest candidate, and rank fusion (the product
default since #177) uses event time and applies recency only to current-state questions.

Approach: an opt-in `ScoringWeights.recency_time="event_time"` for the weighted formula. When set,
the weighted formula reads one clock for recency: the memory's event time, else `valid_from`, else
`created_at`, never `updated_at`. Questions that are not current-state measure it back from the
request's reference time; current-state questions keep their anchor (the newest candidate) and the
same clock, which also decides the current-update multiplier. Unset, nothing changes. Rank fusion
already reads event time, so the setting is dropped there with a warning, as weighted scoring drops
the rank fusion settings. Receipts that record it use a new schema version 20.

Prediction stated before the gate run (acceptance criterion 3): event-time recency favors late
sessions. With `recency_lambda` 0.02 a turn 150 days before the reference time scores about 0.05
instead of 1.0, a swing of up to 0.10 in the composite. I expect losses of more than 1 point of
all-evidence share in LoCoMo multi-hop, temporal and single-hop, and in LongMemEval-S multi-session and
temporal-reasoning. LongMemEval-S knowledge-update may gain.

**Plan change after review.** Break scenario 2 contradicted the plan's clock: `valid_from` is the
start of a claim's real-world validity (`engine.py:706`, `mcp/server.py:189-190`), the reader format
refuses to show it as a time (`packing.py:637-641`), and a future `valid_from` could become the
current-state anchor. The clock is now `_stated_time` (event time, else `created_at`), the clock rank
fusion's recency boost and tie-break already use. This deviates from the issue's suggested
`valid_from` fallback; the reasons are recorded here and in the pull request.

## Files Changed
- `src/prme/retrieval/config.py`: `ScoringWeights.recency_time` (`Literal["event_time"] | None`,
  unset by default, omitted when unset); `WEIGHTED_ONLY_SETTINGS`; the before-validator (renamed
  `drop_settings_the_fusion_ignores`) drops weighted-only settings under rank fusion with a warning, as
  it drops rank fusion settings under weighted scoring; the after-validator rejects the pair;
  `version_id` appends the setting only when set.
- `src/prme/retrieval/scoring.py`: `compute_composite_score` dates memories by `_stated_time` when the
  setting is on, on every question; `_weighted_time_source` picks the same clock for the
  current-state anchor and the current-update check in `score_and_rank`; `validate_rank_fusion_request`
  rejects copied settings the scorer would ignore.
- `src/prme/models/relevance.py`: receipt schema version 20 (weighted only, admits the version 12 to 14
  features, records no rank fusion relevance, skipped floor or rank fusion session decay);
  `RANK_FUSION_RECEIPT_VERSIONS` names versions 15 to 19; `make_receipt` selects 20 and requires an
  execution descriptor.
- `benchmarks/diagnostics/product_packing.py`: the gate refuses a weighted-only setting without
  weighted fusion (one loop for both directions).
- Tests: new `tests/test_event_time_recency.py`; new pinned fixtures `receipt-v14-weighted.json` and
  `receipt-v19-rrf.json` written by `main` at `335ee82b`; `tests/test_product_packing_diagnostic.py`.
- Docs: RFC-0005 (recency clock, version 20), RFC-0006, `docs/PACKING.md`, `docs/HTTP-API.md`,
  `docs/INTEGRATION.md`, `documentation/configuration.md`, `AGENTS.md`, `CHANGELOG.md`.

## Approach Summary
See the plan above and its change after review. Production defaults are unchanged: `PRMEConfig()`
scores with rank fusion, which drops the setting, and `ScoringWeights()` leaves it unset. With the
setting unset every weighted and rank fusion path computes exactly what it did on `main` (the gate
reproduces all 2,040 saved contexts under the previous defaults, and pinned receipts written by
`main` read back byte for byte).

## Must Fix
None from any agent.

## Should Fix (all resolved)
1. Version 20 error messages pointed at versions 16 and 17 (Agent 3 S1, Agent 4 F7). Now "Version 20
   records no rank fusion relevance" and "... no rank fusion session decay".
2. Docs said rank fusion "already reads event time" (Agents 1, 3). Reworded: its recency boost and
   tie-break use this clock; its current-update eligibility still reads `updated_at` (follow-up).
3. "Event time" meant two clocks, and `valid_from` is not a statement time (Agents 3, 4, 7). The
   setting now uses `_stated_time`; the duplicate helper is gone.
4. RFC-0006, INTEGRATION.md receipt list and CHANGELOG missing version 20 (Agents 3, 4, 6). Added.
5. No pinned version 19 receipt although the rank fusion receipt rules were rewritten (Agent 4 F4).
   Pinned `receipt-v19-rrf.json` (PRMEConfig's default scoring and packing) written by `main`.
6. Derived nodes without an event time look newest to event-time recency (Agent 1 #1). Documented in
   the field description, RFC-0005 and configuration.md; follow-up raised.

## Consider (resolved or recorded)
- Resolved: validator renamed; named `RANK_FUSION_RECEIPT_VERSIONS`; three explicit branches in
  `compute_composite_score`; merged gate loops; "either format" to "any format"; RFC sentence on
  unchanged times made precise; tests import `CURRENT`, `UPDATE` and `_copies`; duplicate weighted pins
  replaced by a pin of the product default's bytes; redundant version test removed; version 12 to 14
  test uses one message; module-level import; compact format covered; end-to-end test of the issue's
  own scenario (past event time, past reference time); invalid copied `recency_time` values rejected
  (Agent 2 low, Agent 6 C5).
- Not changed: a stray rank fusion opt-in on a version 20 receipt (Agent 1 #3) cannot happen: the
  receipt revalidates its `ScoringWeights`, which rejects rank fusion settings under weighted scoring
  (test `test_version_20_cannot_carry_copied_rank_fusion_settings`).
- Not changed: `WEIGHTED_ONLY_SETTINGS` has one member, so the receipt and scorer checks name the
  field directly; a property like `rank_fusion_opt_ins` can come with a second weighted-only setting.
- Not changed: no CI simulation step for the setting (Agents 4, 6). It is opt-in, weighted only and
  measured to lose evidence; local simulation runs are recorded below.
- Not changed: the gate refuses `scoring.recency_time=null` without weighted fusion, as it does for
  the rank fusion settings (Agent 1 #7).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets and personal data | PASS | New messages are fixed strings or setting names |
| Authorization and receipt ownership | PASS | `GET /v1/retrievals/{id}` unchanged |
| Input validation | PASS | HTTP and MCP accept no scoring weights; env and code only |
| Stored receipts | PASS | Versions 1 to 19 validate exactly as before; pinned v14 and v19 bytes |
| Tenant isolation | PASS | No user or scope filter changed |
| Timezones | PASS | Naive times read as UTC; the scoring clock is aware UTC |
| Copied invalid settings | Resolved | `validate_rank_fusion_request` rejects them |

## Pattern Consistency Assessment
The change follows #168 (commit `9b6b329f`) at every registration point: field with `exclude_if`,
before-validator drop with warning, after-validator backstop, `version_id` suffix only when set,
scorer backstop for `model_copy`, new receipt version with provenance-match rule, gate refusal,
pinned fixtures, env tests, docs set and changelog. `config_audit` is not touched because the field
carries no `[HYPOTHESIS]` tag, like `rrf_tie_break`.

## Redundancy Check
The duplicate time helper was removed in favor of `_stated_time`. The new receipt version is needed:
`ScoringWeights` ignores unknown keys, so an older reader would silently drop the setting and change
the receipt checksum (Agent 5 R5). Test helpers `_node` and `_imported` are new because the existing
helpers cannot express "stated in the past, ingested today".

## Wiring Findings
Wired end to end (Agent 6): env and `.env`, engine config, per-request `weights=`, query shifts,
learned multipliers, `WeightTuner`, `feedback_apply`, receipts on DuckDB and PostgreSQL, the HTTP
receipt route, ranking profile applicability, the gate and the DeepSeek harness identity. A learned
ranking profile built under the other value of the setting reports `base_scoring_mismatch`, which is
correct.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "The opt-in event-time recency held. The incident came from two places:
the clock it left alone (the rank fusion default still gives the current-update boost to whichever old
memory the organizer promoted last), and the `valid_from` fallback it added, which treats "valid since
2019" or "valid from next year" as the time a memory was stated."

| # | Scenario | Label | Likelihood / impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | Under the rank fusion default, current-update eligibility reads `updated_at`, so a promoted older update can take the 1.3 multiplier | pre-existing | High / medium-high, silent | Follow-up #183 | Fixing it changes the default path, which needs the epic #77 rule; this issue is opt-in only |
| 2 | The `valid_from` fallback treats a validity start as a statement time; a future one becomes the current-state anchor | newly introduced | Low-medium / high | Fix now | Contradicted the plan; the clock is now `_stated_time`; tests cover past and future validity |
| 3 | Entity, consolidation and profile nodes have no event time, so against a past reference time they keep full recency while their sources decay | newly introduced (under the opt-in) | Medium / medium | Follow-up #184, documented now | Needs those organizer and ingestion paths to date nodes by their sources, which also moves rank fusion's clock |
| 4 | Evaluations of the setting are confounded with reverting rank fusion | newly introduced (process) | Medium / medium | Fix now | The gate compares weighted against weighted; the PR states the setting is not a default candidate while rank fusion is the default |
| 5 | A downgrade or mixed fleet cannot read version 20 receipts | existing pattern | Low-medium / medium, loud | Accept | Same as versions 15 to 19; changelog says to unset the variable instead of downgrading |
| 6 | "As of T" questions give memories dated after T full recency while earlier ones decay | newly introduced (under the opt-in) | Low / medium | Follow-up #185 | Needs a decision on how a time after the reference time should count; related to #170 |
| 7 | Toggling the setting makes an active ranking profile inapplicable | newly triggered, by design | Low / low-medium | Accept | The profile was evaluated under a different formula; documented in configuration.md |

Accept records: (5) re-checked alternatives: an unversioned field would be silently dropped by older
readers and change checksums, which is worse than a loud refusal; no mechanism avoids it.
(7) is the intended contract of profile applicability.

## Follow-ups Raised
- #183: Rank fusion gives the current-update boost to the memory the organizer touched last (scenario 1).
- #184: Derived memories without an event time look newest to event-time recency (scenario 3, Agent 1 #1).
- #185: Memories dated after the reference time get full recency under event-time recency (scenario 6).

## Evidence gate (offline)
Share of questions with all annotated evidence packed, at 3,996 tokens, all four runs on commit
`e0216e7c` (clean tree; this branch's code commit before it was rebased onto #181 and #182, which
change no retrieval code). Both sides score with the weighted formula, so the comparison isolates the
setting; the product default (rank fusion) drops it. `gate-compare` intervals resample questions.
The "before" run with the previous defaults reproduces all 2,040 saved 2026-09-23 contexts (1540/1540
and 500/500), so the unset path is unchanged.

**Previous defaults** (`scoring.fusion="weighted"`, `auditable`, `balanced`), without and with
`scoring.recency_time="event_time"`:

| Benchmark / category | Without | With | Change (95% interval) | Wins / losses |
|---|---:|---:|---|---:|
| LoCoMo, all | 983/1536 (64.0%) | 909/1536 (59.2%) | -4.8 pp (-6.4 to -3.5) | 30 / 104 |
| multi-hop | 45/282 | 31/282 | -5.0 pp (-8.5 to -1.8) | 4 / 18 |
| open-domain | 29/92 | 26/92 | -3.3 pp (-6.5 to +0.0) | 0 / 3 |
| single-hop | 657/841 | 620/841 | -4.4 pp (-6.4 to -2.4) | 21 / 58 |
| temporal | 252/321 | 232/321 | -6.2 pp (-9.3 to -3.1) | 5 / 25 |
| LoCoMo projected accuracy | 64.0% | 60.6% | -3.4 pp (-4.4 to -2.4) | |
| LongMemEval-S, all | 403/470 (85.7%) | 404/470 (86.0%) | +0.2 pp (-0.6 to +1.3) | 3 / 2 |
| knowledge-update | 67/72 | 67/72 | 0.0 | 0 / 0 |
| multi-session | 90/121 | 90/121 | 0.0 (-2.5 to +2.5) | 1 / 1 |
| single-session-assistant | 49/56 | 48/56 | -1.8 pp (-5.4 to +0.0) | 0 / 1 |
| single-session-preference | 26/30 | 26/30 | 0.0 | 0 / 0 |
| single-session-user | 62/64 | 62/64 | 0.0 | 0 / 0 |
| temporal-reasoning | 109/127 | 111/127 | +1.6 pp (+0.0 to +3.9) | 2 / 0 |
| LongMemEval-S projected accuracy | 86.0% | 86.1% | +0.1 pp (-0.4 to +0.8) | |

**Weighted with the current packing** (`scoring.fusion="weighted"`, reader format, score order):

| Benchmark / category | Without | With | Change (95% interval) | Wins / losses |
|---|---:|---:|---|---:|
| LoCoMo, all | 1202/1536 (78.3%) | 1133/1536 (73.8%) | -4.5 pp (-5.7 to -3.3) | 13 / 82 |
| multi-hop | 103/282 | 75/282 | -9.9 pp (-13.8 to -6.0) | 3 / 31 |
| open-domain | 48/92 | 42/92 | -6.5 pp (-13.0 to +0.0) | 2 / 8 |
| single-hop | 756/841 | 741/841 | -1.8 pp (-3.0 to -0.6) | 6 / 21 |
| temporal | 295/321 | 275/321 | -6.2 pp (-9.0 to -3.4) | 2 / 22 |
| LoCoMo projected accuracy | 73.7% | 70.7% | -3.0 pp (-3.8 to -2.2) | |
| LongMemEval-S, all | 396/470 (84.3%) | 393/470 (83.6%) | -0.6 pp (-1.9 to +0.6) | 3 / 6 |
| knowledge-update | 65/72 | 63/72 | -2.8 pp (-8.3 to +2.8) | 1 / 3 |
| multi-session | 87/121 | 85/121 | -1.7 pp (-4.1 to +0.0) | 0 / 2 |
| single-session-assistant | 54/56 | 53/56 | -1.8 pp (-5.4 to +0.0) | 0 / 1 |
| single-session-preference | 24/30 | 24/30 | 0.0 | 0 / 0 |
| single-session-user | 63/64 | 63/64 | 0.0 | 0 / 0 |
| temporal-reasoning | 103/127 | 105/127 | +1.6 pp (+0.0 to +3.9) | 2 / 0 |
| LongMemEval-S projected accuracy | 85.1% | 84.7% | -0.4 pp (-1.2 to +0.4) | |

Against the prediction stated before the run: LoCoMo multi-hop, temporal and single-hop lost more than
1 point, as stated. Categories that lost more than 1 point without being stated: LoCoMo open-domain
(both runs), LongMemEval-S single-session-assistant (one question, both runs) and, with the reader
format, knowledge-update (predicted to gain) and multi-session (stated). Temporal-reasoning gained
instead of losing. Decision (the issue's own rule): event-time recency does not help outside
current-state questions, so recency stays out of their default score, which rank fusion (the
default since #177) already does. The setting stays opt-in and off; it is not a default candidate,
and a DeepSeek pair would compare it against rank fusion rather than isolate it (break scenario 4).

## Simulations
`python -m scripts.run_simulations` on `e0216e7c` (the reviewed code gave the same results):

| Settings | Result |
|---|---|
| Current defaults | 74/74 |
| Previous defaults (`weighted`, `auditable`, `balanced`) | 74/74 |
| Previous defaults + `PRME_SCORING__RECENCY_TIME=event_time` | 74/74 |
| `weighted` with the current packing + `PRME_SCORING__RECENCY_TIME=event_time` | 74/74 |

## Tests
- `uv run pytest tests/ -q` on `e0216e7c`: 4385 passed, 875 skipped (PostgreSQL variants skip
  without `PRME_TEST_DATABASE_URL`).
- `uv run ruff check src/ tests/` and the strict mypy check of `tests/typing/public_api.py`: clean.
  Plain mypy on `scoring.py` reports one error at `fusion.score(weights.rrf_k)`, which is on `main`
  too.
- New `tests/test_event_time_recency.py` covers the three acceptance criteria that tests can check:
  imported history (past event time, ingestion today, past reference time) with and without the
  setting, through `score_and_rank` and through `engine.retrieve`; records without an event time
  stored and retrieved at wall-clock time (identical traces in scoring, same order and recency within
  1e-9 through the engine); and the receipt, configuration and gate rules.

## Resolution Status
| Finding | Status |
|---|---|
| Should Fix 1 to 6 | Resolved |
| Consider items | Resolved or recorded above |
| Break scenario 2 | Fixed now (plan changed) |
| Break scenario 4 | Fixed now (PR text and gate method) |
| Break scenarios 1, 3, 6 | Follow-ups raised |
| Break scenarios 5, 7 | Accepted, documented |

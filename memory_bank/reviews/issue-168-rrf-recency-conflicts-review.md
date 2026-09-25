# Code Review: issue-168-rrf-recency-conflicts

## Files Changed
- `src/prme/retrieval/config.py`: `ScoringWeights.rrf_recency_boost` (`[HYPOTHESIS]`, above 0 and at
  most 4) and `ScoringWeights.rrf_tie_break` (`"event_time"`), both unset by default and left out of
  serialized settings when unset. `RANK_FUSION_OPT_INS` and `RANK_FUSION_ONLY_SETTINGS` name them once;
  the before-validator drops any of them under weighted fusion with a warning; `version_id` appends
  them only when set; `rank_fusion_opt_ins` returns the ones that are set.
- `src/prme/retrieval/scoring.py`: the recency boost as a pool-relative factor on current-state
  questions, the event-time tie-break, and helpers (`_memory_time`, `_stated_time`,
  `_update_recency_multiplier`, `_recency`, `_current_state_recency`, `_event_time_tie_breaks`). The
  weighted formula's recency is computed by the same `_recency`, float for float as before.
  `validate_rank_fusion_request` rejects weighted settings that still carry the terms (only a
  `model_copy` can produce them).
- `src/prme/retrieval/models.py`: `RankFusion.recency_boost_factor` and `RankFusion.tie_break`, left
  out when unset; `RankFusion.score` applies them; `ScoreProvenance` ties them to the weights.
- `src/prme/models/relevance.py`: receipt schema version 19.
- `src/prme/config_audit.py`: activation gate for the boost.
- `benchmarks/diagnostics/product_packing.py`: the evidence gate refuses every rank-fusion-only setting
  without rank fusion.
- `simulations/harness.py`: `SimCheckpoint.context_keywords` and `CheckpointResult.context_missing`.
- `simulations/scenarios/{consolidation,remention,surprise_gating}.py`: the four checks named in the
  owner's decision move from ranking to context checks; descriptions reworded to match.
- `.github/workflows/ci.yml`: a second simulations step with the reader-rrf-sd06 settings plus a 0.25
  boost and the tie-break.
- Tests: new `tests/test_rank_fusion_recency.py`, new pinned fixture
  `tests/fixtures/relevance/receipt-v18-rrf.json` (written by `main` at `fc253310`),
  `tests/test_config_audit.py`, `tests/test_product_packing_diagnostic.py`,
  `tests/test_simulation_timeline.py`.
- Docs: RFC-0005 Section 7.2, RFC-0006, RFC-0017, `docs/PACKING.md`, `docs/HTTP-API.md`,
  `docs/INTEGRATION.md`, `documentation/configuration.md`, `AGENTS.md`, `simulations/README.md`,
  `CHANGELOG.md`.

## Approach Summary
Root cause: `_rank_fused_scores` scores a candidate from its semantic and lexical ranks and three
pool-relative factors (epistemic, node type, temporal). Recency is not an input, and ties go to path
count and then the random node ID. With the reader-rrf-sd06 settings, `run_simulations` passed 66 of
74 on `main`. The owner's decision on the issue split the eight failures into three kinds: three
recency conflicts (`changing_facts` MySQL/PostgreSQL, the CEO check, the sprint velocity check), one
tie (response time), and four checks of the weighted formula's order, which become context checks.

Recency: a multiplier, not an extra ranked list. Both fixed the three conflict checks in the
prototype on the issue. The multiplier was chosen because:
- it uses the weighted formula's own current-state recency (an exponential in the time gap, doubled
  for update wording), where a ranked list would treat a day-old and a year-old memory as one step
  apart;
- it is at most 1.0, so first place on both channels is still 1.0 and a one-channel memory still
  scores at most 0.5, the scale the 0.6 session decay was measured on; a third list would add a
  term to every candidate (all have a time) and lift one-channel memories above 0.5;
- it is the same mechanism as the temporal factor, recorded and replayed the same way.
The weighted formula applies no other supersedence or epistemic demotion that fusion lacks: the
epistemic factor and the current-update multiplier already apply under rank fusion.

Tie-break: event time, not lexical score. Ties under rank fusion are mostly mirror ranks ((a, b) and
(b, a)) and single-channel ranks at the same place. A lexical tie-break would always favor the
lexical-only memory over the semantic-only one; event time is channel-neutral and matches the
issue's premise that the newer memory should win. The tie-break is part of the fused score (a
positive score loses its time place times 1e-11) rather than a new sort key: fused scores are
rounded to ten decimals, so only equal ones change order, and the fourteen sorts after scoring
(session, episode and evidence context, reranker, packing, `replay_ranking`, `learning._order`) and
receipt replay honor it without any change.

Boost value: 0.25. The three conflict checks need more than about 0.07; 0.25 passes them with
margin, and on the evidence gate it changed no benchmark significantly, while 1.0 lost 0.6 points of
LoCoMo evidence (interval excluding zero).

Plan change during review (step 16a, break scenario 1): recency and the tie-break first used the
weighted formula's reference time, `event_time or updated_at or created_at`. An organizer promotion
resets `updated_at` after 7 days by default, so for memories stored without an event time a promoted
older memory looked newest and the boost put it first. Both now use the stated time, `event_time or
created_at`, which never moves and is how the context formatter dates memories. The weighted formula
is unchanged.

## Must Fix
None.

## Should Fix (all resolved)
- The "exactly the weighted formula's recency" claim is false with a custom `w_recency` of 0.25 or
  more, where the weighted formula does not raise lambda (Agents 3, 4, 5, 1). Resolved: the field
  description, RFC-0005, the changelog and the helper docstring now say it follows the weighted
  formula with its default weights and always raises lambda, and the update-wording multiplier is one
  shared helper for both formulas.
- The rank-fusion-only setting names were repeated in five places (Agents 3, 4, 5). Resolved:
  `RANK_FUSION_OPT_INS` and `RANK_FUSION_ONLY_SETTINGS` in `config.py`, and the
  `ScoringWeights.rank_fusion_opt_ins` property, used by the validator, `version_id` checks, receipt
  rules and the evidence gate.
- `RankFusion.recency_factor` collided with `ScoreTrace.recency_factor`, which holds the raw recency
  (Agents 3, 4). Resolved: renamed `recency_boost_factor` before any receipt carries it.
- The `surprise_gating` docstring and scenario description still claimed the removed ranking check
  (Agents 1, 3, 4, 5). Resolved: reworded to what the checks test; no check changed.
- The context checks cannot fail while a small scenario's whole store fits the budget (Agent 3).
  Resolved as documentation: the harness comment and the simulations README say so. This is the
  relaxation the owner asked for; the PR lists each memory's rank.
- The engine test's precondition rested on a comment and reused one store for both runs (Agents 3,
  6). Resolved: separate users, and the test asserts from the provenance that the older memory ranks
  first on both channels.
- A receipt branch (settings only in provenance, version below 19) was untested (Agent 3). Resolved
  by simplification: the provenance-match rule applies to every version, so the branch is gone.
- HTTP-API.md described version 19 before version 18 (Agents 3, 6). Resolved.
- CI never ran the simulations with the new settings (Agent 6). Resolved: a second step in the
  simulations job.
- Review notes file missing (Agent 4). This file.

## Consider
- Tampered receipts could record a recency boost factor on some candidates only (Agent 2). Done:
  version 19 requires the factor on every rank-fused score or on none. A lower bound on the factor was
  not added, because a pool with no ranked candidate legitimately records 0.
- Naive datetimes and `timestamp()` (Agents 1, 2, 3): times without a zone are read as UTC, as storage
  records them. Tested.
- Later adjustments with other coefficients can reorder scores within 1e-11, and inherited context
  scores compare the trigger's place (Agent 1): documented in the field description and RFC-0005.
- `rrf_tie_break` description said "instead of node ID order" (Agents 1, 7): now "path count and node
  ID", and it says memories stated at the same time still fall back to them.
- Weighted `model_copy` carrying the terms (Agent 4): rejected early by `validate_rank_fusion_request`.
  Tested, as is the feedback tuner keeping the terms.
- Config audit condition text (Agent 6): "(current-state questions only)".
- Changelog: the earlier rank fusion entry now points to the opt-in, the new entry names the tested
  value, and the simulation change is under Changed (Agents 3, 4, 6).
- Test gaps (Agent 3): added tests for a zero score with a tie-break, an implicit current-state
  question without an update, the boost with the current-update multiplier, and naive times. The
  simulation harness test was renamed to what it shows.
- Not done: a single warning per stray setting instead of the joined message (Agent 5), merging the
  test helpers with those in `test_rank_fusion.py` and `test_rank_fusion_vector_failure.py` (Agent 5),
  and folding the `rrf_k` gate test into the new parametrized one (Agent 5). They would touch
  unrelated tests for no behavior change.
- Kept: the after-validator backstops, which mirror the existing `rrf_k` check (Agents 3, 5), and the
  single-value `Literal["event_time"]`, which keeps receipts and environment variables
  self-describing (Agent 5).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or personal data | PASS | The warning names settings, not values; receipts add the user's own recency and time place |
| Ownership and isolation | PASS | Factors are pool-relative within one user's scoped pool; cross-scope hints are scored in their own call and are not in receipts |
| Input validation | PASS | NaN, infinity, 0, negatives and values above 4 rejected from code, JSON and the environment; HTTP and MCP cannot set scoring |
| `model_copy` bypass | PASS | `validate_rank_fusion_request` rejects weighted settings carrying the terms; provenance checks finiteness |
| Tampered receipts | PASS | Versions below 19 cannot carry the settings; version 19 needs one; provenance must match; scores and ranking must replay; the boost factor is all or none |
| Denial of service | PASS | One sort (n log n) and linear passes |
| Credentials in fixtures | PASS | Synthetic IDs and text |

## Pattern Consistency Assessment
Follows #82 (rank fusion), #111 (receipt version 17) and #150 (version 18): omitted-when-unset fields,
warning-and-drop under weighted fusion, `version_id` appended only when set, config audit gate,
evidence gate refusal, receipt rules (`< N` cannot, `== N` requires, provenance must match),
`make_receipt` selection, a pinned earlier-version fixture by checksum, environment and HTTP
round-trip tests, and the same documentation set. The deviation is deliberate: the tie-break lives in
the score rather than a sort key, for the reasons above.

## Redundancy Check
Two settings rather than one: the tie-break applies to every question and the boost only to
current-state ones, and the gate measured them separately (the tie-break alone changed 143 contexts
and no evidence). `_memory_time` replaces the old `_ref_time` closure; `_newest_first` became
`_event_time_tie_breaks` and reuses `_competition_ranks`; the recency factor reuses `_pool_relative`.

## Wiring Findings
Reachable from `PRME_SCORING__RRF_RECENCY_BOOST` and `PRME_SCORING__RRF_TIE_BREAK` (tested),
`retrieve(weights=...)`, the evidence gate `--set` and `gpt54_baselines prepare --set`, whose variant
identity picks them up from the dump (unset fields are omitted, so existing identities are unchanged).
`WeightTuner` keeps them (tested); `feedback_apply`, ranking profiles and learning already skip rank
fusion. Receipts round-trip through the engine and `GET /v1/retrievals/{id}` (tested on DuckDB; the
PostgreSQL variant runs in CI). Only `relevance.py` enumerates receipt versions. CI picks up the new
test file and fixture, and the simulations job now runs both configurations.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Opt-in rank fusion recency boost served last week's database:
organizer promotion reset `updated_at` on memories stored without event time, so the stale fact ranked
first."

| # | Scenario | Trigger | Likelihood | Impact | Label | Verdict | Reasoning |
|---|---|---|---|---|---|---|---|
| 1 | A promoted older memory looks newest to the boost and the tie-break | Memories stored without `event_time` (HTTP, MCP, LangChain), promoted by the default 7-day organizer before the update | Medium | The stale fact ranks first on the question the feature is for | Newly introduced (for rank fusion) | Fix now | Contained: rank fusion's terms use the stated time `event_time or created_at`; weighted unchanged. Tested |
| 2 | The boost reaches few questions: "What X does Y..." needs an update-worded memory even with "currently"; two updates within about two weeks both cap at 1.0; 68 of 78 LongMemEval-S knowledge-update questions are not current-state by the regex | Natural phrasing and repeated updates | High | The boost does little outside the simulation's wording | Pre-existing gate, newly relied on | Follow-up (#169) | Changing the current-state gate or the doubling is a product decision and would change the weighted path's twin |
| 3 | One memory dated in the future becomes the anchor and flattens every other memory's recency | Extraction resolves "next month", or a caller passes a planned time | Low to Medium | Planned facts answer current-state questions | Pre-existing in the weighted formula, newly reachable under rank fusion | Follow-up (#170) | The fix (cap at the request time) belongs in both formulas |
| 4 | Memories that share a timestamp still fall back to node ID | Session turns with one session time, same-day simulation messages | High | Low: ties within one session | Newly introduced limitation | Accept | A tie on both score and time has no meaningful order left; documented in the description, RFC-0005 and PACKING.md |
| 5 | Rolling back strands version 19 receipts | Settings enabled, then an older release | Low | Loud read error | Pre-existing pattern | Accept | Every receipt version bump has this; the settings are off by default |

## Follow-ups Raised
- #169: break scenario 2.
- #170: break scenario 3.
- #171: found while verifying determinism, not by Agent 7. On `main`,
  `python -m simulations.run --deterministic consolidation` and `remention` fail under the default
  settings (maximum score deltas of about 2e-3, all on consolidated excerpt nodes). Pre-existing; the
  determinism tests in `tests/test_determinism_rebuild.py` pass.

## Evidence gate (offline)
Share of questions with all annotated evidence packed, by category. "reader-rrf-sd06" is `scoring.fusion=rrf`, `packing.context_format=reader`, `packing.multipath_ordering=score` and `packing.session_context_rank_fusion_score_decay=0.6`. Final runs are on this branch's commit; the single-setting and 1.0 rows ran on the same code before review renames, and their contexts are unaffected by those changes (the 0.25 run on the final commit reproduced its earlier run's 2,040 contexts exactly).

**LoCoMo**

| Run | All evidence packed | multi-hop | open-domain | single-hop | temporal | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|
| reader-rrf-sd06 on `main` | 1308/1536 (85.2%) | 147/282 | 54/92 | 806/841 | 301/321 | 78.4% |
| reader-rrf-sd06, new settings unset | 1308/1536 (85.2%) | 147/282 | 54/92 | 806/841 | 301/321 | 78.4% |
| + boost 0.25 and tie-break | 1305/1536 (85.0%) | 145/282 | 54/92 | 805/841 | 301/321 | 78.3% |
| + boost 0.25 only | 1305/1536 (85.0%) | 145/282 | 54/92 | 805/841 | 301/321 | 78.3% |
| + tie-break only | 1308/1536 (85.2%) | 147/282 | 54/92 | 806/841 | 301/321 | 78.4% |
| + boost 1.0 and tie-break | 1299/1536 (84.6%) | 140/282 | 54/92 | 804/841 | 301/321 | 78.1% |

**LongMemEval-S**

| Run | All evidence packed | knowledge-update | multi-session | single-session-assistant | single-session-preference | single-session-user | temporal-reasoning | Projected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| reader-rrf-sd06 on `main` | 408/470 (86.8%) | 72/72 | 88/121 | 53/56 | 24/30 | 63/64 | 108/127 | 86.6% |
| reader-rrf-sd06, new settings unset | 408/470 (86.8%) | 72/72 | 88/121 | 53/56 | 24/30 | 63/64 | 108/127 | 86.6% |
| + boost 0.25 and tie-break | 409/470 (87.0%) | 72/72 | 88/121 | 53/56 | 24/30 | 63/64 | 109/127 | 86.8% |
| + boost 0.25 only | 409/470 (87.0%) | 72/72 | 88/121 | 53/56 | 24/30 | 63/64 | 109/127 | 86.8% |
| + tie-break only | 408/470 (86.8%) | 72/72 | 88/121 | 53/56 | 24/30 | 63/64 | 108/127 | 86.6% |
| + boost 1.0 and tie-break | 408/470 (86.8%) | 72/72 | 88/121 | 53/56 | 24/30 | 63/64 | 108/127 | 86.6% |

Paired comparison (`gate-compare`, settings unset against boost 0.25 and tie-break, 95% intervals resampling questions):

| Benchmark | All evidence packed | Wins / losses | Evidence recall | Projected accuracy |
|---|---|---|---|---|
| LoCoMo | -0.2 pp (-0.5 to +0.0) | 0 / 3 | -0.1 pp (-0.3 to +0.1) | -0.1 pp (-0.3 to +0.0) |
| LongMemEval-S | +0.2 pp (+0.0 to +0.6) | 1 / 0 | +0.2 pp (+0.0 to +0.5) | +0.1 pp (+0.0 to +0.4) |

Boost 1.0 with the tie-break: LoCoMo -0.6 pp (-1.0 to -0.3; multi-hop -2.5 pp, -4.6 to -0.7), LongMemEval-S +0.0 pp.

- With the settings unset, all 2,040 contexts are identical to `main`'s under reader-rrf-sd06, and under the current defaults the gate reproduces all 2,040 saved contexts (LoCoMo 983/1536, LongMemEval-S 403/470).
- The tie-break alone changes the order in 143 contexts (114 LoCoMo, 29 LongMemEval-S) and no evidence coverage. Boost 0.25 with the tie-break changes 343 contexts.
- The gate cannot see which of two conflicting memories comes first, only whether both reach the context, so it cannot show the effect this issue is about; the simulations do.

## Simulations
`python -m scripts.run_simulations`:

| Settings | Result |
|---|---|
| Current defaults, `main` | 74/74 |
| Current defaults, this branch | 74/74 |
| reader-rrf-sd06, `main` | 66/74 |
| reader-rrf-sd06, this branch, new settings unset | 70 or 71/74 (the response time check is a random tie) |
| reader-rrf-sd06 + `rrf_recency_boost=0.25` + `rrf_tie_break=event_time` | 74/74 in four runs (70/74 before the four context checks) |
| reader-rrf-sd06 + boost 0.25 only, before the context checks | 70/74 (the response time tie passed by chance) |
| reader-rrf-sd06 + tie-break only, before the context checks | 67/74 |

Ranks of the four relaxed checks' memories (first matching result):

| Check | Weighted defaults | reader-rrf-sd06 + new settings | In context |
|---|---:|---:|---|
| `consolidation`, "What infrastructure does the team use?" (Kubernetes) | 1 | 8 | yes, both |
| `remention`, "What tools does the team rely on?" (Docker; Snowflake) | 1; 7 | 3; 1 | yes, both |
| `surprise_gating`, "What database does the team use?" (original "primary relational database"; restatement "main database system") | 2; 3 | 4; 2 | yes, both |
| `surprise_gating`, "What observability tools are in use?" (OpenTelemetry) | 5 | 6 | yes, both |

## Tests
`uv run pytest -q`: 4377 passed, 873 skipped (PostgreSQL variants skip without
`PRME_TEST_DATABASE_URL`). `uv run ruff check src/ tests/` and the strict mypy public API check pass;
mypy on the changed modules reports only the pre-existing optional `rrf_k` at the `fusion.score` call.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Recency "exactly" claim, duplicated multiplier | Should Fix | Resolved |
| Setting names repeated | Should Fix | Resolved |
| `recency_factor` name collision | Should Fix | Resolved (renamed) |
| `surprise_gating` descriptions | Should Fix | Resolved |
| Context checks cannot fail while the store fits | Should Fix | Resolved (documented) |
| Engine test fragility and shared store | Should Fix | Resolved |
| Untested receipt branch | Should Fix | Resolved (removed) |
| HTTP-API.md order | Should Fix | Resolved |
| CI simulations with the new settings | Should Fix | Resolved |
| Review notes | Should Fix | Resolved |
| All-or-none boost factor | Consider | Resolved |
| Naive datetimes | Consider | Resolved |
| Reordering within 1e-11 after adjustments; inherited ties | Consider | Documented |
| Tie-break description | Consider | Resolved |
| Weighted `model_copy` carrying the terms; tuner | Consider | Resolved, tested |
| Audit condition text, changelog | Consider | Resolved |
| Test gaps | Consider | Resolved |
| Warning format, test helper merging, gate test merging | Consider | Not done (no behavior change) |
| Break 1: promoted older memory looks newest | Fix now | Resolved, tested |
| Break 2: boost reach | Follow-up | #169 |
| Break 3: future-dated anchor | Follow-up | #170 |
| Break 4: same-time ties | Accept | Documented |
| Break 5: rollback strands version 19 receipts | Accept | Pre-existing pattern |
| Deterministic simulation check on `main` | Pre-existing | #171 |

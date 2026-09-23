# Code Review: issue-82-rrf-fusion-drop-constant-signals

## Files Changed
- `src/prme/retrieval/config.py`: `ScoringWeights.fusion` (`"weighted"` default, `"rrf"` opt-in) and
  `rrf_k` (default 60, only with `"rrf"`). A weighted configuration omits both fields when
  serialized and keeps its `version_id`; a rank-fusion configuration adds them to both.
- `src/prme/retrieval/models.py`: `RankFusion` (both competition ranks and the three
  pool-relative factors, plus the shared `score(k)` expression). `ScoreProvenance.formula_version`
  accepts 2, which must match `weights.fusion` and carry `rank_fusion`; version 1 omits the field
  when serialized. `replay_base_score` replays version 2 from `rank_fusion` alone.
- `src/prme/retrieval/scoring.py`: `_competition_ranks`, `_pool_relative`, `_rank_fused_scores`,
  the rank-fusion branch of `score_and_rank`, and `_epistemic_weight` extracted from
  `compute_composite_score` so both formulas share it. `compute_composite_score` rejects rank-fusion
  weights.
- `src/prme/models/relevance.py`: receipt schema version 15 for rank fusion. Versions 1 to 14
  cannot claim it, version 15 must state `fusion` and `rrf_k`, and version 15 is only for rank
  fusion. `make_receipt` selects 15 and requires an execution descriptor.
- `src/prme/quality/tuner.py`: the `feedback_apply` tuner keeps every setting it does not tune.
- `src/prme/retrieval/learning.py`: learning rejects formula version 2 provenance.
- Tests: new `tests/test_rank_fusion.py`.
- Docs: `docs/RFC-0005-Hybrid-Retrieval-Pipeline.md` (Section 7.2), `documentation/configuration.md`,
  `docs/HTTP-API.md`, `docs/INTEGRATION.md`, `AGENTS.md`, `CHANGELOG.md`.

## Approach Summary
Rank fusion is a second scoring formula selected by `ScoringWeights.fusion`, so it flows through the
existing per-request `weights=`, `PRMEConfig.scoring` (`PRME_SCORING__FUSION=rrf`), the receipt's
`scoring` field and every provenance's `weights`. Under `"rrf"`, `score_and_rank` ranks the pool on
the semantic channel (VECTOR path or a semantic score) and the lexical channel (LEXICAL path or a
lexical score) with competition ranks. It scores `sum(1/(k + rank)) * (k + 1) / 2`, so first place on
both is 1.0. The result is multiplied by the epistemic weight, the node-type boost and
`1 + temporal_boost * affinity`, each divided by the pool's largest value. Graph proximity, recency,
salience and confidence are not used, and neither are the query-specific weight shifts. Session,
episode and evidence inheritance, reranking and packing are unchanged and operate on the fused
score. The default is unchanged.

Alternatives considered:
- Rank fields on `ScoreTrace`: rejected. `ReceiptCandidate.trace` embeds it in every receipt and the
  receipt serializer never strips trace fields (`relevance.py`, `serialize_version`), so every old
  receipt's canonical bytes would change.
- Reweighting the weighted sum (for example 0.5 semantic, 0.5 lexical): rejected. It keeps mixing a
  raw cosine with a per-query min-max BM25 score (`candidates.py:38-64`), which is the scale problem
  the issue measures, and the issue's replay measured rank fusion, not reweighting.
- RRF in the benchmark adapter: rejected. Improvements belong in `src/prme`, and the issue asks
  for the product fusion.
- Reusing `benchmarks/evidence.py:reciprocal_rank_fusion`: rejected. `src` cannot import
  `benchmarks`, and it returns an order, not per-candidate replay inputs.
- Ordinal ranks broken by node ID (as the audit script did): rejected in favor of competition ranks,
  which do not depend on IDs or arrival order. The gate measures the product choice directly.
- Adding the fusion fields to the receipt serializer's per-version pop list: unnecessary. A weighted
  `ScoringWeights` omits them itself, which also protects ranking profiles and full-retrieval
  evaluations that hash `base_scoring.model_dump(mode="json")` (`models/learning.py:409`,
  `full_learning.py:234`).

## Must Fix (all resolved)
- **One labeled rank-fusion receipt permanently broke `evaluate_learning`** (Agents 1, 2, 3, 4, 7).
  `proposed_score` raised for formula 2 and the baseline replay ran on every labeled receipt; receipts
  and labels are append-only. Resolved: `evaluate_learning` counts them under
  `rank_fusion_receipt_records`; `proposed_score` keeps the raise as a guard. Tested with a mixed
  weighted and rank-fused snapshot.
- **`scoring.rrf_k` missing from the hypothesis audit; 3 config-audit tests failed** (Agents 1, 4, 6).
  Resolved: activation gate on `scoring.fusion == 'rrf'`, expected set updated, dormant by default and
  effective under rank fusion (tested).
- **Wrap serializers slowed the default path** (Agent 3): receipt dump plus checksum went from about 6.1
  to 10.8 ms for 555 candidates. Resolved: `exclude_if` on `fusion`, `rrf_k` (now `None` unless
  fusion is `rrf`) and `rank_fusion`; `validate_components` reads weights by attribute instead of
  dumping them. Remeasured at 6.1 to 6.7 ms on both main and the branch.

## Should Fix (all resolved)
- **Request multipliers under rank fusion returned HTTP 500 after candidate generation** (Agents 1 to
  4, 7). Resolved: `validate_rank_fusion_request` runs in `engine.retrieve` before any work, the HTTP
  route pre-checks it and returns 422 (a blanket ValueError catch was tried and dropped because it
  would echo internal errors), and MCP returns an error payload. Tested on all three.
- **A crafted or stale profile with rank-fusion base scoring could be activated and break retrieval**
  (Agent 2). Resolved: `_ranking_profile_inapplicability` returns `rank_fusion_scoring` whenever the
  current base scoring is rank fusion, which also blocks activation. Tested.
- **Version 15 rules would block every future receipt version** (Agents 3, 4). Resolved: "rank fusion
  only" applies to version 15 exactly; from 15 on, only a rank-fused setting must state `rrf_k`.
- **Pool maximum included candidates on neither channel** (Agent 1), which lowered every ranked
  candidate's factors. Resolved: maxima come from ranked candidates only, capped at 1.0. Tested.
- **Negative node-type boosts or epistemic weights failed deep inside pydantic** (Agents 1, 2, 3).
  Resolved: a clear `ValueError` before scoring. Tested.
- **`rrf_k` unbounded; a stray `rrf_k` stopped startup on rollback** (Agents 2, 3, 7). Resolved:
  `1 <= rrf_k <= 10,000`; weighted scoring drops a supplied `rrf_k` with a warning; the evidence
  gate refuses `scoring.rrf_k` without rank fusion because the run would change nothing (Agent 6).
- **`feedback_apply` changed unused weights and reported success** (Agents 3, 4, 7). Resolved: under
  rank fusion it returns `not_applicable` and keeps its signals. Tested.
- **CHANGELOG "Fixed" entry described a bug that never shipped** (Agents 1, 3, 6): main's tuner
  passed all 13 fields. Removed; the tuner now carries every setting forward, which matters only for
  the new ones.
- **Docs**: RFC-0005 Section 12 contradicted 7.2 (now exempts formula 2); `rrf_k` default tagged
  `[HYPOTHESIS]`; channel membership, pool maxima, cross-scope hints and trace fields documented;
  PACKING.md, RFC-0006, RFC-0017, LEARNING.md and HTTP-API.md updated (Agents 3, 4, 6).
- **Tests**: `rrf_k` other than 60, reader plus rank assignment on version 15, version 15 in the
  packing-defaults test, and the gate override (Agents 3, 4, 6).

## Consider
- Resolved: `_query_adjusted_weights` extracted so `score_and_rank` has one rank-fusion branch; the
  two field-by-field `ScoringWeights(...)` rebuilds now use `model_validate({**model_dump(), ...})`
  (Agents 3, 4, 5); `_temporal_affinity_applies` shared by both formulas (Agents 4, 5); neutral
  multiplier check compares with `RankingMultipliers()` (Agent 5); tuner leftovers removed (Agent 5);
  separate provenance error messages (Agent 3); docstrings (Agent 3).
- Not done: tying version 15 provenance weights to `receipt.scoring` and trace similarities to saved
  ranks (Agent 2). Receipts are server-written and checksummed; weighted receipts have no such check
  either.
- Not done: writing `fusion="weighted"` into every pre-15 receipt dict on load (Agent 4). A missing
  `fusion` must always mean weighted, which the field comment and RFC now state, and a default change
  must go through `PRMEConfig.scoring` instead. A receipt-only fill would not cover profiles and
  evaluations and would copy every provenance dict on every load.
- Pre-existing: `feature_identity` hashes `scoring.py`, `config.py` and `models.py`, so existing ranking
  profiles report `feature_identity_mismatch` after this release, as after every release that edits
  them (Agent 6).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs and responses | PASS | No new logging; new receipt fields are ranks, factors and `rrf_k`. |
| Receipt ownership | PASS | Owner checks unchanged; provenance carries no identity. |
| Tenant and scope isolation | PASS | Ranks and factors use only the already filtered pool; hints are ranked in their own filtered pool. |
| Checksum and version rules | PASS | Weighted bytes and version IDs unchanged; only version 15 can claim rank fusion. |
| Unsafe deserialization | PASS | Pydantic only; `fusion` is a Literal. |
| Input validation | PASS after fixes | `rrf_k` bounded; negative adjustments rejected clearly. |
| Error handling | PASS after fixes | 422 and MCP errors instead of 500. |
| Availability | PASS after fixes | Learning exclusion; profiles inapplicable under rank fusion. |

## Pattern Consistency Assessment
Follows the receipt-version precedents (v9, v13, v14): the after-validator rejects the feature below its
version, `make_receipt` selects the version and requires an execution descriptor, and the new setting
must be explicit from its version on. Unlike v9, the new settings are omitted by the model itself
(`exclude_if`, as `RankingProfileState.application` already does) rather than popped per version,
which also protects profiles and evaluations. Config field uses a `Literal` and `[HYPOTHESIS]` tag like
`PackingConfig`, and is gated in the hypothesis audit like the episode and evidence settings.

## Redundancy Check
No new dependency. `_competition_ranks`, `_pool_relative`, `_rank_fused_scores` and `RankFusion.score`
have no equivalent in `src/`; `benchmarks/evidence.py:reciprocal_rank_fusion` cannot be imported from
`src` and ranks ordinally without per-candidate inputs. Both serializers were replaced with
`exclude_if`, and the duplicated neutral-multiplier check was consolidated.

## Wiring Findings
Env (`PRME_SCORING__FUSION`, `PRME_SCORING__RRF_K`), `PRMEConfig.scoring`, SDK `weights=`, DuckDB and
PostgreSQL pipelines, receipt persistence and reload, the hypothesis audit and the gate's `--set` all
carry the new settings (tested or probed). HTTP and MCP have never taken per-request weights.
`RankFusion` is not exported, matching `ScoreProvenance`.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Ranking-profile learning returns 422 forever for every user who labeled
results during the RRF trial."

| # | Scenario | Introduced | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | One labeled rank-fusion receipt makes `evaluate_learning` fail for that owner and scope forever | New | Medium | High, permanent (append-only feedback) | Fix now | Contained; resolved with the `rank_fusion_receipt_records` exclusion and a mixed-snapshot test. |
| 2 | `min_score` stops meaning "relevant enough": an unrelated memory scores near 1.0, cross-scope hints are fused in their own pool | New | Medium-Low (opt-in, needs `min_score` callers) | Medium, silent | Follow-up #110 | Needs a product decision on which relevance value `min_score` gates; must be settled before rank fusion becomes the default. Documented in RFC-0005 Section 7.2. |
| 3 | Rolling back by removing only the fusion flag leaves `PRME_SCORING__RRF_K` set and startup fails | New | Medium-Low | High (outage during rollback) | Fix now | Contained; weighted scoring now drops a stray `rrf_k` with a warning. Tested. |
| 4 | Request multipliers under rank fusion return an opaque 500 | New trigger | Low-Medium | Medium | Fix now | Contained; early engine check, HTTP 422, MCP error payload. Tested. |
| 5 | `feedback_apply` under rank fusion reports success but changes nothing | New | Low | Low, silent | Fix now | One-liner; reports `not_applicable` and keeps the signals. Tested. |
| 6 | Downgrading to a release without version 15 support leaves rank-fusion receipts unreadable | Pre-existing pattern, newly reachable | Low | Medium (learning and receipt endpoints) | Accept | Alternatives re-checked: recording rank fusion in a version 14 receipt would make an older release replay it as weighted, which is a silent wrong answer instead of a loud failure. Versions 13 and 14 accepted the same property. Stated in the CHANGELOG. |
| G | Evidence gate: under rank fusion, session expansion's 0.85 decay lifts neighbors above most primary candidates and costs LongMemEval-S multi-session evidence (-8.3 pp at 4K, -5.8 pp at 8K, intervals exclude zero) | New, opt-in only | Certain when both are on | Medium (LongMemEval-S overall -1.1 pp and -0.6 pp) | Follow-up #111 | Fixing it means retuning session expansion (another subsystem, related to #86) against the gate. Defaults are unchanged, and rank fusion without session expansion improves both benchmarks at both budgets. |

Tally: Fix now 4, Follow-up 2 (#110, #111), Accept 1, Dismissed 0.

## Follow-ups Raised
- #110: Under rank fusion, an unrelated memory can score near 1.0, so `min_score` stops filtering
  irrelevant results.
- #111: Under rank fusion, session expansion crowds LongMemEval-S multi-session evidence out of the
  context.

## Evidence gate (offline, final tree)
Reader format with score ordering, current weighted score against rank fusion. All evidence packed,
paired 95% intervals from the gate comparison:

| Run | LoCoMo | LongMemEval-S |
|---|---|---|
| 4K weighted | 78.3% | 84.3% |
| 4K rank fusion, session expansion on | 84.6% (+6.3, +4.8 to +7.9) | 83.2% (-1.1, -3.8 to +1.5) |
| 4K rank fusion, session expansion off | 82.4% (+4.1, +2.5 to +5.7) | 86.8% (+2.6, +0.4 to +4.9) |
| 8K weighted | 87.0% | 89.1% |
| 8K rank fusion, session expansion on | 90.2% (+3.2, +2.1 to +4.3) | 88.5% (-0.6, -2.6 to +1.3) |
| 8K rank fusion, session expansion off | 88.4% (+1.4, +0.3 to +2.7) | 91.5% (+2.3, +0.6 to +4.3) |

Default JSON format at 4K: LoCoMo 64.0% to 72.4% (+8.4, +6.6 to +10.3), LongMemEval-S 85.7% to 88.1%
(+2.3, +0.0 to +4.7). At default settings the final tree reproduces 1540/1540 and 500/500 saved
contexts byte for byte.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Learning evaluation broken by rank-fused receipts | Must Fix | Resolved |
| `rrf_k` missing from the hypothesis audit | Must Fix | Resolved |
| Wrap serializers slowed the default path | Must Fix | Resolved |
| Request multipliers returned HTTP 500 | Should Fix | Resolved |
| Rank-fusion profiles could break retrieval | Should Fix | Resolved |
| Version 15 rules blocked later versions | Should Fix | Resolved |
| Pool maximum included unranked candidates | Should Fix | Resolved |
| Negative adjustments failed unclearly | Should Fix | Resolved |
| `rrf_k` unbounded; stray `rrf_k` broke startup; gate no-op override | Should Fix | Resolved |
| `feedback_apply` no-op reported success | Should Fix | Resolved |
| CHANGELOG entry for a bug that never shipped | Should Fix | Resolved |
| Docs out of date (RFC-0005 Section 12, PACKING, RFC-0006, RFC-0017, LEARNING, HTTP-API) | Should Fix | Resolved |
| Missing tests (`rrf_k`, version 15 combinations, gate override) | Should Fix | Resolved |
| Structure and duplication cleanups | Consider | Resolved |
| Version 15 provenance consistency checks | Consider | Not done (defense in depth only) |
| Pre-15 receipts fill `fusion` on load | Consider | Not done (field comment and RFC instead) |
| Feature identity invalidates existing profiles | Consider | Pre-existing, noted in the PR |

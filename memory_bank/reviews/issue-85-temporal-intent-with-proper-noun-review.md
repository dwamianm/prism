# Code Review: issue-85-temporal-intent-with-proper-noun

## Plan (written before implementation)

Root cause: `_classify_intent` (`src/prme/retrieval/query_analysis.py`) returns the first match and
checks the two entity patterns (a who / what is / what are prefix, and any capitalized word after the
first) before temporal wording and parsed dates. A temporal question that names a person, place or
organization is therefore ENTITY_LOOKUP. Only `QueryIntent.TEMPORAL` is read downstream, in three
places: `_temporal_affinity_applies` (`scoring.py`, both formulas), `_is_current_state_query`
(`scoring.py`, which keeps TEMPORAL questions without explicit current wording off the current-state
path) and `_detect_context_type` (`context_formatter.py`, the temporal guidance). ENTITY_LOOKUP is read
nowhere; entity names are extracted into `QueryAnalysis.entities` whatever the intent.

Approach: an opt-in `PRMEConfig.query_intent_order` (`PRME_QUERY_INTENT_ORDER`), default
`"entity_first"`. `"temporal_first"` checks temporal wording and dates before both entity checks, as the
issue proposes. Threaded through both engine constructors into `RetrievalPipeline` and `analyze_query`,
recorded in receipts only when set, so default receipts keep their bytes. The evidence gate records per
question whether temporal affinity varies and whether the current-state path applied, and `gate-compare`
reports both, which acceptance criterion 3 asks for. The default changes only after the epic #77 paired
DeepSeek run (criterion 4), so the PR is "Part of #85".

Alternatives considered:
- Change the default directly: ruled out by the epic #77 rule and criterion 4.
- A multi-intent `QueryAnalysis` field: no consumer reads ENTITY_LOOKUP (grep of `src/` and
  `benchmarks/`: the three reads above are the only ones), so it would add a model and receipt contract
  for no behavior difference.
- A `ScoringWeights` field: intent is Stage 1 query analysis, not scoring, and a new scoring field needs
  a new receipt schema version because `ScoringWeights` ignores unknown keys (#83 precedent).

Reading of criterion 2 ("no change for temporal questions without a proper noun"): questions that the
default already classifies as TEMPORAL do not change. Because the issue proposes checking temporal intent
before both entity checks, a temporal question that starts with who / what is / what are changes even
without a proper noun ("Who did I meet before the parade?", "What are my plans for tomorrow?"); the tests
say so explicitly.

## Files Changed
- `src/prme/config.py`: `query_intent_order` field with its own section comment.
- `src/prme/retrieval/query_analysis.py`: `QueryIntentOrder`, `QUERY_INTENT_ORDERS`, `temporal_first`
  in `_classify_intent`, `_is_name_signal`, and `intent_order` in `analyze_query`.
- `src/prme/retrieval/pipeline.py`: constructor argument and validation, execution feature
  `query_intent_classification` and parameter `query_intent_order` (both only when set), the order
  passed to the main and the alternate-query `analyze_query` calls.
- `src/prme/storage/engine.py`: both `RetrievalPipeline` constructions pass the setting.
- `benchmarks/diagnostics/product_packing.py`: `_query_scoring_observations` per PRME replay row,
  `_query_scoring` and `_query_scoring_markdown` in the comparison, `_listed` helper.
- Tests: new `tests/test_query_intent_order.py`; additions to `tests/test_query_analysis.py`,
  `tests/test_product_packing_diagnostic.py`, `tests/test_experimental_retrieval_policies.py`;
  `tests/test_reformulation_merge.py` stand-in gains the attribute.
- Docs: RFC-0005 Section 3, `docs/INTEGRATION.md`, `documentation/configuration.md`, `AGENTS.md`,
  `BENCHMARKS.md`, `CHANGELOG.md`, and this review.

## Approach Summary
See the plan. Production defaults are unchanged: with `entity_first` every query classifies as before,
the resolved windows are the same, and no receipt gains a key.

## Must Fix
None from any agent.

## Should Fix (all resolved)
1. Docs overstated what the default withholds (Agents 3 S1, 4 S3, 6 S1): the context formatter's keyword
   fallback already gives "when / before / after / last" questions the temporal guidance, and
   `_is_current_state_query` already excludes "when did", "how long", "before" and "after" questions.
   Reworded everywhere: a named temporal question loses temporal affinity; the current-state path
   changes only for present-tense questions; the guidance changes only where the wording does not
   already select it.
2. The gate comparison printed "0 entered / 0 left" when the receipts could not show the path
   (Agents 1 S1, 3 S2, 7 scenario 5a). It now counts `current_state_path_unknown` and says so.
3. Gate key names (Agents 3 S3, 4 S5): row key `query_scoring` with `temporal_affinity_varies` and
   `current_state_path`; comparison keys `temporal_affinity_varies_by_category`,
   `entered_current_state_path`, `left_current_state_path`, `current_state_path_unknown`.
4. No test showed a question leaving the current-state path or the guidance changing (Agent 3 S4).
   Added.
5. The alternate-query `analyze_query` call did not get the order (Agent 4 S1). It does now; it changes
   nothing today because candidate generation never reads intent.
6. The setting was only in `execution.features` (Agent 4 S2). Also recorded in
   `execution.parameters`, as RFC-0017 puts query-processing settings there; the feature entry stays for
   ranking-profile applicability.
7. `docs/INTEGRATION.md` not updated (Agent 4 S4). Added under Stage 1.
8. Allowed values spelled out five times (Agents 5 S1, 2, 3, 4 Consider). One `QueryIntentOrder` alias
   and `QUERY_INTENT_ORDERS` set in `query_analysis.py`, used by the pipeline. `PRMEConfig` keeps its
   inline `Literal`, as its analogues do, to avoid importing retrieval modules into `config.py`.
9. The ranking-profile test duplicated the parametrized analogue (Agent 5 S2). Moved into
   `test_policy_change_prevents_profile_activation` as an `intent` case; the invalid-value loop there
   covers the new field too.
10. Criterion 2 wording (Agent 1 S2): see the plan; the test is renamed and lists the prefix cases.

## Consider (resolved or recorded)
- Resolved: section comment for the field; error messages include the value; feature key renamed from
  `query_intent` (which RFC-0005 Section 10 uses for the classified intent) to
  `query_intent_classification`; "only when set" now says "only when it is temporal_first"; the
  changelog code span no longer breaks across lines; the AGENTS.md sentence has its own paragraph instead
  of sitting under the experimental policies pointer; `_query_scoring_observations` sits above its caller
  and guards `rank_fusion`; the Markdown table has a Questions column and one layout; the list
  truncation shares `_listed`; a comparison without observations says so; the tests give each order its
  own user and ignore any exported `PRME_QUERY_INTENT_ORDER` or `.env`; BENCHMARKS.md says the replay
  records the observations and qualifies "can change the order" with "under rank fusion".
- Not changed: `_classify_intent` keeps a boolean `temporal_first` rather than the order string (private,
  one caller); `analyze_query` keeps its own validation for direct callers.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets and personal data | PASS | Receipts gain a fixed enum value; gate reports hold flags, counts and question IDs |
| Authorization and receipt ownership | PASS | Receipts are still fetched per user; the setting changes no filter |
| Input validation | PASS | Invalid values fail at `PRMEConfig`, `RetrievalPipeline`, `analyze_query` and the gate's `--set` |
| Per-request control | N/A | Neither HTTP nor MCP can set the order; it is engine-wide |
| Ranking profiles | PASS | A profile built under one order reports `feature_identity_mismatch` under the other |

## Pattern Consistency Assessment
Follows `query_reformulation_merge_policy` and `reranker_policy`: a `Literal` field on `PRMEConfig`
passed through both engine constructors, pipeline validation, a versioned execution feature and an
execution parameter added only when non-default, tests in `tests/test_experimental_retrieval_policies.py`,
and a configuration row. Gate additions follow the existing row and comparison structure.

## Redundancy Check
No new dependencies and no dead code. The duplicated values and the duplicated profile test were
consolidated (Should Fix 8 and 9).

## Wiring Findings
Wired end to end (Agent 6): environment and `.env`, both engine constructors (every `MemoryEngine`,
`MemoryWorkspace`, HTTP, MCP and CLI path goes through them), the gate's `--set` isolated from the
environment, the DeepSeek harness's `variant_settings`, CI picks up the new test file on both backends,
and receipts written with the setting validate and replay.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Temporal-first passed the DeepSeek pair on LoCoMo's 'When did
Caroline...' questions and became the default. Two weeks later, 'Who is June dating?' returned last
June's boyfriend: neither benchmark contains a month-named person, and no receipt records which intent a
question got."

| # | Scenario | Label | Likelihood / impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1a | Month and weekday names ("Who is June dating?", "Sun Microsystems") become dates, flip the intent, invent a window and drop the current-state boost | newly introduced (opt-in) | Medium / high, silent | Fix now | Contained and only in the temporal_first branch: a single capitalized date match that is part of an extracted name is read as the name. Neither benchmark has such a name, so no later evidence would catch it |
| 1b | Product codes and amounts with digits ("80D", "6S", "$12") resolve to dates | pre-existing (already flips unnamed questions under the default) | Medium / low | Follow-up #193 | A signal-extraction fix that changes the default path too, so it needs its own gate run |
| 2 | Present-tense questions with recently / since leave the current-state path and lose the recency boost | newly introduced reach of an existing rule | Medium / medium, quiet | Follow-up #192 | Needs a product decision on what "recently" and "since" mean; the gate lists the affected questions and the flip must review them |
| 3 | The content date pattern matches "may", "march", "June", "2077" in any case | pre-existing, amplified | High / low-medium | Follow-up #193 | Changes existing temporal scoring, so it needs its own gate run |
| 4 | Query dates collapse to one instant and months without a year resolve into the future | pre-existing, amplified | Medium / low | Follow-up #193 | Same as 3 |
| 5a | The gate printed "0 entered / 0 left" when the receipts could not show the path | newly introduced | Low / low | Fix now | One count and one line |
| 5b | `temporal_affinity_varies` reads like "temporal scoring changed the order" | newly introduced (reporting) | Medium / low | Accept | It is the criterion 3 metric by definition; the table caption says what it measures, and the evidence metrics show the effect on packing |

Accept record: (5b) the count answers "how many temporal questions get a non-constant
temporal_affinity"; whether that changed the packed evidence is what the gate's evidence rows measure.

## Follow-ups Raised
- #192: Present-tense questions that say recently or since lose the current-state boost under
  temporal-first intent (scenario 2).
- #193: Temporal affinity rewards dates that are not dates, and a month or year in the question becomes
  a single day (scenarios 1b, 3 and 4).

## Evidence gate (offline)
Share of questions with all annotated evidence packed, at the default 4K budget, both runs on commit
`22007c01` (clean tree), current defaults against `--set 'query_intent_order="temporal_first"'`.
`gate-compare` intervals resample questions. The comparison is committed as
`benchmarks/results/research/2026-09-25/temporal-first-intent-gate-comparison.{json,md}`.

| Benchmark / category | Before | After | Change (95% interval) | Wins / losses |
|---|---:|---:|---|---:|
| LoCoMo, all | 84.0% | 84.3% | +0.3 pp (+0.1 to +0.7) | 5 / 0 |
| multi-hop | 49.6% | 50.0% | +0.4 pp (+0.0 to +1.1) | 1 / 0 |
| open-domain | 57.6% | 58.7% | +1.1 pp (+0.0 to +3.3) | 1 / 0 |
| single-hop | 95.2% | 95.2% | 0.0 | 0 / 0 |
| temporal | 92.2% | 93.1% | +0.9 pp (+0.0 to +2.2) | 3 / 0 |
| LoCoMo projected accuracy | 77.6% | 77.9% | +0.2 pp (+0.0 to +0.4) | |
| LongMemEval-S, all | 94.7% | 94.9% | +0.2 pp (+0.0 to +0.6) | 1 / 0 |
| temporal-reasoning | 93.7% | 94.5% | +0.8 pp (+0.0 to +2.4) | 1 / 0 |
| other LongMemEval-S categories | | | 0.0 | 0 / 0 |
| LongMemEval-S projected accuracy | 91.4% | 91.5% | +0.1 pp (+0.0 to +0.4) | |

Criterion 3 details:
- Intent changes: 569 LoCoMo questions (291 of 321 temporal, 242 single-hop, 25 multi-hop, 11
  open-domain) and 86 LongMemEval-S questions (41 temporal-reasoning, 19 multi-session, 13
  single-session-assistant, 12 knowledge-update, 1 single-session-user).
- Questions whose candidates do not all share one temporal affinity, before / after: LoCoMo temporal
  0 / 37 of 321, single-hop 0 / 152, multi-hop 0 / 13, open-domain 0 / 8; LongMemEval-S
  temporal-reasoning 37 / 78 of 133, multi-session 23 / 42, knowledge-update 11 / 23,
  single-session-assistant 3 / 16, single-session-user 7 / 8, single-session-preference 1 / 1. Most
  LoCoMo temporal questions stay constant because no date window is resolved and every stored turn
  begins with its session date, as the issue predicted.
- Current-state path: 18 LoCoMo questions leave it and none enter; no LongMemEval-S question moves.
  Thirteen of the 18 name a date or period; three use "when" to mean "whenever" (conv-43-q0127,
  conv-48-q0154, conv-50-q0107) and two say "recently" (conv-41-q0088, conv-42-q0163), recorded in #192.
- Contexts changed: 210 LoCoMo and 83 LongMemEval-S. Gained all evidence: LoCoMo conv-42-q0084,
  conv-44-q0008, conv-47-q0001, conv-47-q0032, conv-49-q0006; LongMemEval-S a3838d2b. Lost: none.
- A first run on the uncommitted tree before the name guard gave the same evidence numbers; the guard
  changed only the temporal affinity counts of two LongMemEval-S questions.

Decision: the default stays `entity_first`. Criterion 4 (the epic #77 paired DeepSeek answer run on both
benchmarks) is open, and #192 and #193 should be looked at before that run.

## Tests
- `uv run pytest -q` on `22007c01`: 4575 passed, 877 skipped (PostgreSQL variants skip without
  `PRME_TEST_DATABASE_URL`). `uv run pytest tests/ -q`, as CI runs it: 4460 passed, 877 skipped.
- `uv run ruff check src/ tests/` and the strict mypy check of `tests/typing/public_api.py`: clean.
- `python -m scripts.run_simulations`: 74/74 with the defaults, with the previous defaults
  (`weighted`, `auditable`, `balanced`) and with `PRME_QUERY_INTENT_ORDER=temporal_first`.
- Criterion 1: unit tests for temporal questions naming a person, a place and an organization, plus a
  date-only question and the who / what are prefix cases (`tests/test_query_analysis.py`), and an
  engine test on DuckDB and PostgreSQL where a named temporal question gets temporal affinity only with
  the setting (`tests/test_query_intent_order.py`).
- Criterion 2: questions already classified TEMPORAL and questions without temporal wording keep their
  intent under both orders.

## Resolution Status
| Finding | Status |
|---|---|
| Should Fix 1 to 10 | Resolved |
| Consider items | Resolved or recorded above |
| Break scenarios 1a, 5a | Fixed now |
| Break scenarios 1b, 2, 3, 4 | Follow-ups raised |
| Break scenario 5b | Accepted, recorded |

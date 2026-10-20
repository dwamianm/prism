# Review: issue-276-graph-seed-rank (prism-monorepo #276)

## Review Scope

- Stacked on the #275 branch. Reviewed only this branch's own diff: base `cdf8b354` (the #275 head at the time), head `24ee9888` (6 commits). Review snapshot `23c3bc27d3830b92ceac2d5ed90e0271a1536768c043ab356ee1ea1e9f22bfba` (housekeeping `review-plan --base cdf8b354 --risk DATA --risk PERSISTED_FIELD`).
- Tier L. Predicates: DATA (added by hand: new graph reads per question), PERSISTED_FIELD (added by hand: receipts and saved provenance record the new setting and graph ranks), NEW_SYMBOLS (detected).
- Roster limit: the user set a budget of two reviewers per issue. Core (Opus) carried checklists 1, 3, 4, 5 and the merged security (2) and data safety (7) checklists; adversarial (Opus) carried 8 with a cap of 8 scenarios. Infrastructure (6) was not applicable. No checklist was skipped.
- Reports: core and adversarial, both `Status: COMPLETE` on the snapshot above.
- After review the branch was rebased onto the final #275 head `ecb127d6` (on 0.16.0a5) and the fixes landed in `4e67ac36` and `b66335f5`. They were checked against each finding by reading the diff, by the targeted tests, and by the full suite on the final head `d8b10f88`.

## Files Changed

`src/prme/retrieval/candidates.py`, `config.py`, `models.py`, `pipeline.py`, `scoring.py`, `session_context.py`, `episode_context.py`, `evidence_context.py`, `src/prme/config_audit.py`, `src/prme/models/owner_reference.py`, `src/prme/ingestion/entity_merge.py`, `tests/test_graph_seed_count.py` (new), `tests/test_graph_fusion.py`, `tests/test_config_audit.py`, `tests/test_default_structure_options.py`, `tests/conftest.py`, `benchmarks/fixtures/fast-cycle/baseline-main.json`, `BENCHMARKS.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md`, `docs/PACKING.md`, `docs/RFC-0005-Hybrid-Retrieval-Pipeline.md`, `documentation/configuration.md`.

## Approach Summary

`scoring.rrf_graph_seed_count` adds a third rank fusion list that holds only a record that two or more question seeds reach within `graph_max_hops` without passing through the owner's entity, that one seed links directly, that the semantic search ranks in its top 10, and that is not an entity. It ranks more seeds first, then fewer total hops, with weight `rrf_graph_weight` (0.5 when unset). Context records inherit their trigger's score without the term. It is on by default in the alpha channel; `PRME_SCORING__RRF_GRAPH_SEED_COUNT=false` restores 0.16.0a5 scoring. The score formula stays version 2 (a saved `graph_rank` replays the same way under either rule); the rule is recorded in `ScoringWeights.version_id` (`:graph_rule=seed_count:top=10`) and in the receipts' scoring settings.

## Must Fix

None.

## Should Fix

| # | Finding | Source | Resolution |
|---|---|---|---|
| S1 | A 0.16.0a4 user who set `rrf_graph_weight` for the hop list silently gets the seed count list | core SF-1, adversarial 6 | Fixed: CHANGELOG upgrade note and the configuration row |
| S2 | AGENTS.md alpha-default list not updated | core SF-2 | Fixed: entries for both #275 and #276 |

## Consider

| # | Finding | Verdict |
|---|---|---|
| C1 | The walk read every edge of a non-seed owner hub without using them | Fixed: non-seed owners are left out of the read; reach unchanged (0 mismatches on 10 LoCoMo walks) |
| C2 | A failed reach read dropped the whole graph backend | Fixed: logged, empty reach, `graph_seed_reach: backend_error` in the response metadata, graph candidates kept; test |
| C3 | Episode and evidence augmentation context inherited the graph term | Fixed: one `inherited_score()` for session, episode and evidence augmentation context; evidence projection keeps the term, with the reason in code; tests |
| C4 | A dumped default config with only the switch turned off enables the hop list at 0.5 | Fixed: documented in the switch's description |
| C5 | Criterion 5 wording; the version label did not encode the rule's constant | Fixed: `:graph_rule=seed_count:top=10` with a comment that any rule change must change the label; the PR states how criterion 5 is met |
| C6 | Duplicate entity nodes of one person count as two seeds | Follow-up F5 (#337) |
| C7 | DRY and quality: duplicated dense ranks, import per node, literal "owner_reference", no receipt round-trip test, baseline timings taken under load | Fixed: `_dense_ranks` reused, import once per walk, shared `OWNER_REFERENCE_STATUS`, round-trip test, baseline retaken with no other test or benchmark process running |

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| Tenant isolation of the new read | PASS | Only edges whose endpoints are seeds or admitted graph results (user- and scope-filtered) are kept |
| Scope filters | PASS | The walk stays within scope- and time-filtered nodes |
| Cross-user owner lookup | PASS | Owners come from metadata of nodes already admitted |
| Unbounded input from the question | PASS | Seeds bounded by the existing seed search; the walk is now capped at 5,000 nodes |
| Logging of personal data | PASS | The new warning logs no content |
| PostgreSQL parity | PASS | Same `get_edges` contract |

## Pattern Consistency Assessment

The option matches `rrf_tie_break` and `rrf_graph_weight` on every configuration point (field with `exclude_if`, env var, PRMEConfig default, `RANK_FUSION_OPT_INS`, `version_id`, `config_audit`, configuration.md, CHANGELOG, CLAUDE.md and AGENTS.md alpha lists, docs/PACKING.md, RFC-0005). The conftest pin differs on purpose (documented in its comment). No receipt version bump, consistent with `rrf_graph_weight` in 0.16.0a4.

## Redundancy Check

`_seed_count_ranks` now reuses `_dense_ranks`; `OWNER_REFERENCE_STATUS` is shared with `entity_merge.py`; one `inherited_score()` replaces the session-only helper. No dead code.

## Wiring Findings

Not a separate checklist at this tier. The core reviewer confirmed the evidence gate's rank fusion guard covers the option automatically and every ScoringWeights builder gets the intended value.

## Data Safety and Read/Write Proof

| Persisted item | Writer | Reader |
|---|---|---|
| `ScoringWeights.rrf_graph_seed_count` in receipts and provenance | PRMEConfig defaults, provenance in scoring | config validators, scoring, pipeline, session context, receipt checks |
| `rrf_graph_weight` filled at 0.5 | config validator | rank fusion score, scoring, config audit |
| `RankFusion.graph_rank` under the new rule | scoring | score replay; stripped for inherited context records |
| `RetrievalMetadata.graph_seed_reach` | candidates, pipeline | response consumers; omitted when the walk ran |

Receipts saved before this change load with the switch off and replay their saved ranks. The off path keeps configuration dumps and version ids unchanged.

## Break Scenarios (adversarial)

Pre-mortem headline: "Alpha seed-count list lifts the newest near-name records instead of the old answer, adds seconds of locked DuckDB time on long-lived packs, and leans on an owner marker that no pre-0.16.0a3 pack has."

| # | Scenario | Introduced | Likelihood | Verdict | Reason |
|---|---|---|---|---|---|
| 1 | Only the kept graph candidates can join, so old answers on large packs never do; first-person questions have one seed | new | High | Follow-ups F3 and F5, limits documented now | Criterion 2 is met on its fixture; fixing eligibility changes default retrieval and needs the gates |
| 2 | Packs without the owner marker let walks pass through the owner hub | new dependency | High on upgraded installs | Follow-up F6 (#338) | Measured on the probes with the carry off: no answer lost; large unmarked packs not measured |
| 3 | The extra link read grows with the neighborhood: seconds on large packs under the DuckDB connection lock | new | Medium | Fix now: fixed | Owner hubs left out of the read, walk capped at 5,000 nodes and reported; docs corrected |
| 4 | A failed walk drops the whole graph backend | new | Low | Fixed (with C2) | Contained, about 6 lines |
| 5 | Opt-in context paths inherit the graph term | new | Low | Fixed (with C3) | Contained |
| 6 | Users of the 0.16.0a4 hop list silently switch rules | new | Low | Fixed (with S1) | Documentation |

## Follow-ups Raised

- F3: #335 (shared with prism-monorepo #275: the graph cut by confidence, and seed reach only on kept candidates)
- F5: #337 (seed identity: first person and duplicate nodes)
- F6: #338 (owner marker missing on older packs)

## Resolution Status

| Item | Status |
|---|---|
| S1, S2 | Resolved |
| C1 to C5, C7 | Resolved |
| C6 | Follow-up F5 (#337) |
| Adversarial 3 to 6 | Resolved |
| Adversarial 1, 2 | Follow-ups F3, F5, F6; limits documented |

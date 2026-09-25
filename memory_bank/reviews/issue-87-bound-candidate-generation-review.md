# Code Review: issue-87-bound-candidate-generation

## Plan (written before implementation)

Issue #87 asks to evaluate per-channel candidate limits (50, 100, 150) together with rank fusion, to
report candidate recall at k per channel on the offline evidence gate separately from context
retention, to report retrieval latency p50 and p95 before and after on both benchmarks, and to state
how the limits interact with the aggregation widening. The default changes only after the epic #77
paired DeepSeek run.

Approach: no product code changes. `vector_k` and `lexical_k` already exist as validated settings
(`src/prme/retrieval/config.py:474-479`, `PRME_PACKING__VECTOR_K` / `PRME_PACKING__LEXICAL_K`, and the
gate's `--set packing.vector_k=...`), and rank fusion is the default since #177, so the evaluation can
run now. The missing pieces are measurement, all in `benchmarks/diagnostics/product_packing.py`:

1. For each annotated question, where each resolved evidence turn ranks in the vector index's and the
   BM25 index's own full ranking of the stored turns (the ranking the limits cut), measured after the
   timed `retrieve()`.
2. Summaries: recall at fixed depths per channel and for their union, candidate recall of the actual
   pool (all evidence among the returned candidates), candidates per question and their share of the
   stored turns, retrieval p50 and p95, and the count and list questions with the paths at their limit.
3. `gate-compare`: paired candidate recall, latency before and after, candidates per question.
4. A warm-up search per engine before the first timed question, because the embedding model loads
   lazily and LongMemEval-S opens one engine per question.
5. Runs: defaults and 150 / 100 / 50 per channel, each at 4K and 8K.

Alternatives considered:
- Reading channel ranks from the receipts' rank fusion provenance (`semantic_rank`, `lexical_rank`):
  rejected. They rank within the merged pool, the lexical one mixes the main BM25 list with the entity
  and aggregation scans' separately normalized hits (`pipeline.py:545-611`), session expansion replaces
  a node's provenance with its trigger's (`session_context.py:160-203`), and they stop at the configured
  limit, so a run at 100 could not show recall at 150.
- Having `generate_candidates` return its per-channel lists: a product change for a measurement need,
  and the lists would still stop at the configured limit.
- A new configuration option: not needed, the limits are already settings.
- A score threshold on vector search: not what the issue proposes; `min_score` already exists (#110).

Shared state: the gate only reads scratch copies of the saved packs. The added index searches are
read-only; the warm-up adds one fixed text to the per-engine embedding cache. The DeepSeek harness
(`benchmarks/integrations/gpt54_baselines.py`) calls `run_gate` in `prepare`, so its prepared arms
get the new report fields and warm-up timing; their contexts do not change (checked below).

## Files Changed
- `benchmarks/diagnostics/product_packing.py`: channel ranks, stored turns, channel limits, count and
  list observations, warm-up, summaries, comparison fields and Markdown; `plain_ranking` refactored onto
  the shared `_stored_turns` and `_index_rankings`.
- `benchmarks/integrations/gpt54_baselines.py`: `_seconds_summary` reuses the gate's p50/p95 helper
  (identical output).
- `tests/test_product_packing_diagnostic.py`: replay, warm-up, non-turn pack, summary, Markdown and
  comparison tests.
- `documentation/configuration.md`: the candidate limit defaults were stale (250, 250, 2.5, 500).
- `docs/RFC-0005-Hybrid-Retrieval-Pipeline.md`: implementation status of the candidate limits.
- `BENCHMARKS.md`: the gate's new outputs and the candidate limit results.
- `CHANGELOG.md`: the configuration reference fix.
- `benchmarks/results/research/2026-09-25/candidate-limits-*`: the gate comparisons.

## Approach Summary
Measurement only; production defaults unchanged (500 / 500). See the plan above and the evidence gate
section below.

## Must Fix
None from any reviewer.

## Should Fix (all resolved)
1. Channel recall table said `vector_k` / `lexical_k` cut every question at k, but count and list
   questions are cut at `min(k * aggregation_k_multiplier, aggregation_k_max)` (Agents 1, 3, 7).
   Resolved: each row records its own channel limits (widened when the question is a count or list),
   the summary adds `channel_recall_at_run_limits`, the Markdown table has a "This run's limits" column,
   and the text says which columns apply the widening.
2. The warm-up test compared two warmed replays (Agents 1, 3). Resolved: the reference run uses a no-op
   warm-up, and the test compares both the context and the channel ranks.
3. PRME replays stopped on any pack that is not turn-only (Agents 3, 6, 7). Resolved: channel ranks and
   the stored-turn count are left out for such packs (`NotAllTurnsError`), retrieval is still measured,
   and plain baselines still refuse them. Stored turns load once per engine.
4. The aggregation line counted fixed limits (LEXICAL_AGG at 50 per term, PINNED at 500) as widened
   limits that "still filled" (Agents 3, 6, 7); the key reused the status name `candidate_limited`
   (Agent 4). Resolved: `filled_widened_limit` counts only GRAPH, VECTOR and LEXICAL, and
   `paths_at_limit` lists every path. The unread `status` is gone.
5. Markdown said "annotated evidence turns" for a pooled share of resolved turns (Agents 3, 4).
   Resolved: the wording says pooled and resolved, names what is not counted, and notes that the pool's
   "Evidence in top 25" counts only returned turns.
6. `_latency` duplicated `gpt54_baselines._seconds_summary` (Agents 4, 5). Resolved: `_seconds_summary`
   now builds on `gate._latency`; its output keys and values are unchanged, and `gpt54_baselines.py` is
   not one of the answer modules the DeepSeek pairing compares (`ANSWER_MODULES`).
7. The comparison's aggregation block had a different shape from the #85 and latency pattern and was
   silently dropped (Agent 4). Resolved: it is None unless both reports record it, and the Markdown says
   so; tested in both orders.
8. `candidates_generated` and `candidates_max` were never read, and the LEXICAL count included entity
   scan hits (Agents 5, 3). Resolved: removed.

## Consider (resolved or recorded)
- Plain-versus-PRME latency compared different work (Agents 3, 6): `_compared_latency` now returns None
  when the reports' plain methods differ.
- Unknown-channel check was unreachable and after the empty-user shortcut (Agents 3, 5): checked first,
  tested.
- `candidate_share_mean` was None for a whole benchmark if one user had no turns (Agents 1, 3): it now
  averages over users with stored turns.
- The reranker loads lazily and the timing label was unversioned (Agents 1, 7): the warm-up scores one
  fixed pair when `enable_reranker` is set, and the label is `after-warm-up-v1`.
- Lost and gained lists for candidate recall (Agent 4): added.
- Naming (Agents 3, 4): `stored_turns`, `_rank_in_channel`, `GATE_EITHER`, `GATE_RECALL_DEPTHS`.
- Recorded, not changed: BM25 hits are deduplicated by node before ranking (pre-existing in
  `plain_ranking`; the pipeline also deduplicates, after its cut, so a node indexed twice would rank one
  place better here than at the cut). `--set vector_exact_search=false` would make the channel ranks approximate; the docstring says the
  ranks assume exact search, the default. The comparison recomputes latency and aggregation from rows
  so earlier reports are read with the current definitions. Channel recall is not compared because it
  does not depend on the settings; the Markdown says so.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets and personal data | PASS | New fields hold ranks, counts, limits and backend names; no benchmark text |
| User scoping | PASS | Index searches (including the warm-up) filter by the question's user |
| Saved archive | PASS | Only scratch copies are opened; the searches are read-only |
| Paid APIs | PASS | Local embedding only; embedding and extraction settings stay fixed |
| Input validation | PASS | `--set` keys are checked as before; `gate-compare` parses JSON only |

## Pattern Consistency Assessment
The additions follow the #85 pattern for reports written before them (None per field, a Markdown note,
tests in both orders), reuse `_first_ranks`, `_all_within`, `_share` and `paired_statistics`, and use the
same linear-interpolation p95 as the GPT-5.4 study family (now one helper). Schema version stays 1: the
additions are optional and `gate-compare` refuses any other version.

## Redundancy Check
`plain_ranking`'s body moved into `_stored_turns` and `_index_rankings` and is shared, not copied. The
p50/p95 helper is shared with the DeepSeek harness. Unread fields were removed.

## Wiring Findings
`--set packing.vector_k` / `lexical_k` / `aggregation_k_*` are accepted and recorded in
`provenance.engine_config.packing`. New fields reach the gate and comparison JSON and Markdown for PRME
and plain runs. The DeepSeek `prepare` path gets the same reports; the harness tests pass (190). Docs
updated: BENCHMARKS.md, configuration.md (stale defaults), RFC-0005 implementation note, CHANGELOG.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "#87 published a latency win measured against a defaults run taken while
seven review agents loaded the machine, and a recall table that cut LongMemEval-S count questions at a
third of their real depth, while the entity and aggregation scans that bounding switched on stayed
invisible."

| # | Scenario | Label | Likelihood / impact | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | The defaults run that latency compares against was timed while seven review agents loaded the machine | newly introduced (measurement) | High / medium, silent | Fix now | Latency is published only from dedicated runs made one at a time on a quiet machine at the final commit, with the defaults run twice as a noise floor; reports now record the load average at start and end, and the comparison prints it |
| 2 | At bounded limits the entity and aggregation scans add hits that tie with the top BM25 hit at lexical rank 1, and the gate cannot attribute them | pre-existing mechanism, exposed by bounding | Medium / medium to high, silent | Follow-up (#198) | Changing how fusion scores scan hits is a ranking change that needs its own opt-in and gate run; grouped with the one-channel loss the runs found |
| 3 | The channel recall table cuts count and list questions at k although retrieval cuts them at 3k | newly introduced | Medium / medium, quiet | Fix now | Rows record their own limits; the table has a column at each question's own limits |
| 4 | The warm-up label does not cover the lazily loaded reranker and is unversioned | newly introduced | Low to medium / medium, quiet | Fix now | Contained: warm the reranker when enabled, label `after-warm-up-v1` (the adversarial pass suggested Follow-up; the fix was one function) |
| 5 | The PRME gate stops on packs that are not turn-only | newly introduced | Medium over the epic / low to medium, loud | Fix now | Contained: leave the channel ranks out for such packs; plain baselines still refuse them (suggested Follow-up; fixed here) |
| 6 | The aggregation line counts fixed limits as widened limits that still filled | newly introduced | Medium / low, quiet | Fix now | One count and one sentence |

Attacked and dismissed by Agent 7: the warm-up changing contexts (read-only; the defaults run
reproduced all 2,040 contexts of the earlier defaults run), existing reports misread by the new code
(fields are optional and latency is refused without the label), and `plain_ranking` compatibility.

## Follow-ups Raised
- #198

## Evidence gate (offline)
All runs are full offline gate runs (1,540 LoCoMo and 500 LongMemEval-S questions) with the
current defaults apart from the limits. The 4K runs were made one at a time at `7825ec20` (clean tree)
after the review, with the defaults first and last; the 8K runs at `a6dd65d8` (clean tree; the later
commit changes only measurement code). Every run at `7825ec20` reproduced all 2,040 contexts of the
matching `a6dd65d8` run, and the defaults reproduced all 2,040 contexts of the earlier defaults run at
`ea1f05f5`, so the warm-up and the added searches change nothing the reader sees. Comparisons:
`benchmarks/results/research/2026-09-25/candidate-limits-{150,100,50}-{4k,8k}.{json,md}` and
`candidate-limits-defaults-repeat-4k` (the two defaults runs: every question ties). After rebasing onto #197 (opt-in
session context packing), the defaults gate at the rebased head reproduced all 2,040 contexts of the
`7825ec20` defaults run; the rebase resolved additive conflicts in the gate module, BENCHMARKS.md and
the configuration reference by keeping both changes.

All evidence packed, change against 500 per channel (paired 95% interval over questions):

| Limit | LoCoMo 4K | LongMemEval-S 4K | LoCoMo 8K | LongMemEval-S 8K |
|---|---|---|---|---|
| 500 | 1,290/1,536 (84.0%) | 445/470 (94.7%) | 1,377/1,536 (89.6%) | 457/470 (97.2%) |
| 150 | +0.7 (-0.7 to +2.0) | -3.2 (-5.1 to -1.5) | +1.5 (+0.4 to +2.6) | -3.4 (-5.1 to -1.9) |
| 100 | +1.7 (+0.6 to +2.9) | -3.8 (-5.7 to -2.1) | +1.2 (+0.1 to +2.3) | -3.8 (-6.0 to -1.9) |
| 50 | +1.1 (-0.1 to +2.3) | -7.2 (-9.8 to -4.7) | +0.4 (-0.8 to +1.6) | -5.3 (-7.7 to -3.2) |

Acceptance criterion 1 (no category lower at 4K and 8K) fails for every limit on LongMemEval-S, so no
limit is chosen and the defaults stay at 500. At 150 and 100 all LongMemEval-S evidence is still among
the candidates; every question that loses it (16 and 19 at 4K, 16 and 21 at 8K) has an evidence turn
inside exactly one channel's cut (#198). Latency at 4K (p50 / p95): defaults 0.192 / 0.240 s LoCoMo
and 0.179 / 0.249 s LongMemEval-S (repeat 0.192 / 0.244 and 0.175 / 0.231); 150: 0.134 / 0.175 and
0.121 / 0.225; 100: 0.117 / 0.154 and 0.094 / 0.204; 50: 0.100 / 0.133 and 0.068 / 0.159.

## Tests
- `uv run pytest -q` after rebasing onto #197: 4689 passed, 895 skipped (PostgreSQL variants skip
  locally).
- `uv run pytest -q tests/test_product_packing_diagnostic.py tests/test_gpt54_baselines.py`: 246 passed.
- `uv run ruff check src/ tests/` and the gate module: clean.
- New tests: channel ranks against each index's own ranking and retrieval's cut at `vector_k=1`, widened
  limits for a count question, non-turn packs, the warm-up (against a no-op warm-up) and the reranker
  warm-up, summaries (channel recall at fixed depths and at each question's limits, candidate recall,
  latency, aggregation), gate and comparison Markdown, latency refused for earlier and plain reports,
  aggregation missing on either side.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Channel recall ignores the aggregation widening | Should Fix | Resolved |
| Warm-up test compared two warm runs | Should Fix | Resolved |
| PRME replay stops on non-turn packs | Should Fix | Resolved |
| Aggregation count mixes fixed and widened limits; key name | Should Fix | Resolved |
| Markdown wording (resolved, pooled) | Should Fix | Resolved |
| Duplicate p50/p95 helper | Should Fix | Resolved |
| Aggregation comparison shape and note | Should Fix | Resolved |
| Unread `candidates_generated`, `candidates_max` | Should Fix | Resolved |
| Docs (BENCHMARKS, configuration, RFC-0005, CHANGELOG, gate docstring and help) | Should Fix | Resolved |
| Plain-versus-PRME latency | Consider | Resolved |
| Unknown-channel check order | Consider | Resolved |
| Candidate share with zero-turn users | Consider | Resolved |
| Reranker warm-up and versioned label | Consider / adversarial 4 | Resolved |
| Lost and gained candidate lists | Consider | Resolved |
| BM25 deduplication, approximate vector search | Consider | Recorded |
| Adversarial 1 (latency under load) | Fix now | Resolved |
| Adversarial 2 (scan hits at lexical rank 1) | Follow-up | #198 |
| Adversarial 3, 5, 6 | Fix now | Resolved |

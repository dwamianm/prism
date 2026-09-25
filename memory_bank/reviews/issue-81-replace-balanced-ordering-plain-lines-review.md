# Code Review: issue-81-replace-balanced-ordering-plain-lines

## Files Changed
- `benchmarks/results/research/2026-09-24/READER-PACKING-ORDER-GATE-V1.md` (new): the decision
  record. Evidence gate results for `balanced` against score order under the reader format, at 4K
  and 8K, under three rankings (weighted; rank fusion with session decay 0.6; the same plus #168's
  recency settings), overall and by category, with the categories that lose more than 1 point of
  all-evidence share explained, the acceptance criteria of #81, consequences, limits and
  reproduction.
- `benchmarks/results/research/2026-09-24/reader-packing-order-gate-v1-results.json` (new): the 13
  gate runs (overrides, commit, clean-tree flag, report SHA-256, summaries by benchmark and
  category) and the six `gate-compare` outputs as written.
- `docs/PACKING.md`: one dated paragraph in the reader-format section on the ordering trade-off.
- `docs/RFC-0006-Retrieval-Cost-and-Context-Efficiency.md`: a dated evidence update after the
  2026-09-13 balanced entry.
- `BENCHMARKS.md`: one sentence pointing to the record after the plain-baselines comparison.

No code, configuration, default or test changes.

## Approach Summary
#81 proposes making score order the default `multipath_ordering` for plain-line records, on the
audit's projection that `balanced` packs short filler turns. The gate confirms that for LoCoMo
under the weighted score (+5.9 projected points at 4K), but score order loses LongMemEval-S under
every ranking (-1.9 weighted, -4.6 to -4.8 under rank fusion at 4K), and under rank fusion its
LoCoMo gain shrinks to +0.7. The cause is LongMemEval-S's layout: its evidence sits in short user
turns (median 50 tokens) next to long assistant replies (median 484), and `balanced`'s quarter-power
length penalty keeps the user turns. Rank fusion's steeper score fall-off leaves the penalty less
room to reorder LoCoMo's similar-length lines.

The approach is therefore to record the evidence and keep the default. The PR is `Part of #81`:
criterion 1 is met, criterion 2 is answered (not met under any ranking, read per benchmark), and
the default change stays open.

Alternatives considered:
- Flip the default to score now, as the issue proposes. Rejected: the gate contradicts it under
  rank fusion (LongMemEval-S -4.8 projected at 4K, interval -6.3 to -3.3), and the epic's rule
  requires a DeepSeek paired run before any default change.
- Make the ordering default depend on the context format (score for reader). Rejected: the same
  evidence, and variant identity in `benchmarks/integrations/gpt54_baselines.py` (`variant_settings`,
  around line 1912) compares settings with the defaults, so a derived default would change what an
  existing variant means without changing its settings.
- Keep the numbers only in an issue comment. Rejected: the default-flip pull request needs a
  tracked, citable record, and research records live under `benchmarks/results/research/`.

Plan change during review: `main` moved to `7201503b` while the review ran, with #168 merged and a
`reader-rrf-sd06-rec` variant already prepared for the DeepSeek track with score order. Break
scenario 3 showed that a re-run of the record's commands after #168 would reproduce the same bytes,
because #168's settings are opt-in. I added the recency ranking (four more gate runs at
`7201503b`) and an identity check that the rank fusion runs still describe `main`. The conclusion
did not change.

## Must Fix (resolved)
- Criterion 2's verdict contradicted the record's tables: it said score order led "only under the
  weighted score", then quoted a positive LoCoMo gain under rank fusion (Agents 1 and 3). Resolved:
  the verdict now says score order leads on LoCoMo and trails on LongMemEval-S under all three
  rankings, reads "overall" per benchmark as the default-change rule does, and gives the pooled
  figures (+4.0 and +2.2 weighted, -0.6 under rank fusion).

## Should Fix (all resolved)
- Decision paragraph hard to parse, with no numbers and an undefined variant name (Agent 3 S1).
  Rewritten with the headline projected changes and the variant's settings in the Protocol.
- Terms used before defined; audit not linked (Agent 3 S2, Agent 4 C4, Agent 5 F9). Metrics defined
  and linked in the Protocol; audit linked in the Question.
- Confusing record-count sentence (Agent 3 S3); loss table metric unnamed (Agent 3 S4). Both
  reworded.
- "As the audit expected" credited the audit with the LongMemEval-S loss (Agent 1 S1). Now credits
  the LoCoMo side to the audit and the LongMemEval-S loss to #109.
- Supporting figures not reproducible (Agent 3 S5, Agent 4 S2). The record now states inputs and
  method for each. Score ratios now use all candidates at the true 10th and 50th places, which
  also removes the dependence on score traces that session expansion clears (Agent 1 C2, C3;
  Agent 7 scenario 6).
- Score ratios stated as general but LoCoMo only (Agent 1 S3). Both benchmarks now given.
- LongMemEval-S line lengths came only from packed records (Agent 1 S4). Replaced with oracle user
  and assistant turn medians; LoCoMo line lengths kept with their population stated.
- PACKING.md wording: "depends on the scoring formula" (Agent 1 S2), undefined terms and settings
  (Agent 3 S6, Agent 4 S4, Agent 6 C1), numbers that go stale (Agent 3 S7, Agent 5 F1). Rewritten:
  the trade-off is stated per benchmark, dated, links the gate, names
  `ScoringWeights.fusion="rrf"` and `PackingConfig.session_context_rank_fusion_score_decay`, and says
  a default change needs a paired run.
- No machine-readable results or digests (Agent 4 S1). Added the results file with report digests
  and all comparisons.
- RFC-0006's dated ordering log not updated (Agent 4 S3, Agent 6 C3). Added the 2026-09-24 entry.
- LongMemEval-S "range" was a percentile range (Agent 4 S5). Replaced.
- Review record missing (Agent 6 S1). This file.

## Consider
- Resolved: "four at a time" against a sequential loop (Agents 1, 2, 3, 5, 6); no checkout step
  (Agents 1, 3); "-0.0" and "+0.0" interval rendering (Agents 1, 3); intervals "narrow" rather than
  understated (Agents 1, 3); 322 of 323 multi-session evidence turns once abstention questions are
  left out (Agent 1 C1); ceiling effect under rank fusion (Agent 1 C8); reserved head and tier
  scope in the explanation (Agent 1 C12, Agent 3 C6); header label and section names (Agent 4 C1,
  C2); decay tuned under score order (Agent 4 C6); "mostly" for user-turn evidence (Agent 3 C11,
  Agent 4 C9); per-category projected columns dropped and wins/losses added (Agent 3 C1, C5,
  Agent 5 F3); BENCHMARKS.md sentence moved after the comparison paragraph and names the decay
  (Agent 3 C10, Agent 4 C7, Agent 1 C11); PACKING.md states the condition for a default change
  (Agent 4 C3); #109 cited directly (Agent 1 C9, Agent 3 C8).
- Kept: a balanced row in the BENCHMARKS.md plain-baselines table (Agent 4 C8). That table compares
  PRME with plain RAG baselines; the record carries the ordering comparison.
- Kept: no INTEGRATION.md pointer (Agent 4 C10). Its ordering paragraph describes the options and the
  default, which have not changed; PACKING.md and RFC-0006 now carry the evidence.
- Kept: the rank fusion tables without recency live only in the results file (Agent 5 F2, F4); they
  are within 3 questions per category of the recency tables.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Benchmark text in published files | PASS | Aggregates, question IDs and hashes only; no conversation, question or answer text. The Reproduce block does not pass `--capture-dir`. |
| Secrets, PII, credentials | PASS | None. The results file carries the archive path already published in BENCHMARKS.md. |
| Paid or model calls | PASS | The gate refuses model-backed settings and runs the embedding model offline. |
| Unsafe commands | PASS | The Reproduce block writes only to `/tmp` and deletes nothing. |
| Code, config or permission changes | N/A | None in the diff. |
| External links | N/A | All links are relative and resolve. |

## Pattern Consistency Assessment
The record follows the research-record pattern of `benchmarks/results/research/2026-09-18/*` and
`benchmarks/results/packing/2026-09-12/CONFIRMATION.md`: decision first, date, revision, question,
protocol, result, interpretation, consequence, limits, and a results JSON next to the Markdown.
Settings in user docs use the class-attribute form used elsewhere in PACKING.md. RFC-0006 gets a
dated evidence update like its 2026-09-12 and 2026-09-13 entries.

## Redundancy Check
The comparison exists nowhere else in the repository: #109 and #112 reported score order and
`balanced` only at 4K under the weighted score, overall, in pull request bodies. Four of the eight
original runs repeat numbers already tracked (BENCHMARKS.md and the #79, #82 and #111 reviews); the
record uses them as cross-checks. PACKING.md and RFC-0006 carry only the 4K headline and link the
record.

## Wiring Findings
- All relative links and anchors resolve (`BENCHMARKS.md#offline-evidence-gate-the-first-gate-for-retrieval-changes`,
  `BENCHMARKS.md#inputs`, the audit, the results file).
- Nothing enumerates `benchmarks/results/research/2026-09-24/`: the DeepSeek harness and its tests
  read exact file names only. `tests/test_gpt54_baselines.py` and `tests/test_product_packing_diagnostic.py`
  pass (225 tests). The full suite on the branch rebased onto `7201503b` gives 4455 passed and
  873 skipped, and `uv run ruff check src/ tests/` passes.
- CI runs no Markdown lint, link or spelling check. No CHANGELOG entry: research and docs changes
  in this epic have not added one.

## Break Scenarios (adversarial)

Pre-mortem headline (Agent 7): "The gate was right and the hand-off was wrong: the record deferred
the ordering to nobody, its '#168 re-run' replayed identical bytes because #168 is opt-in, and
PACKING.md said `balanced` stays the default on the day the flip made score the default."

| # | Scenario | Likelihood | Impact | Label | Verdict | Resolution |
|---|---|---|---|---|---|---|
| 1 | PACKING.md's "`balanced` stays the default" and "default weighted score" become false when the planned flip lands, and a rebase merges without conflict. | Medium-High | Medium | New | Fix now | The paragraph is dated ("measured on 2026-09-24 while `balanced` and the weighted score were the defaults") and says a default change needs a paired run. The record's Consequence says a flip to score must revise the paragraphs added here, and the PR says so. |
| 2 | "Needs its own decision" invites a flip that drops score order from the set while citing the score-order verdict. | Low-Medium | High | Wording new; missing check pre-existing | Fix now (wording) and Follow-up (check) | Decision and Consequence say the `balanced` set is a new variant that none of the existing verdicts covers and that needs its own first pair and confirmation. The mechanical check that a flip's defaults match the cited verdict's settings is already tracked in #165, so no new issue. |
| 3 | The requested re-run "on top of #168" replays identical bytes because #168's settings are opt-in. | Medium | Medium | New | Fix now | #168 merged during the review, so I ran the recency ranking at `7201503b` with `rrf_recency_boost=0.25` and `rrf_tie_break="event_time"`, added it to every table, and put the settings in the Reproduce block. |
| 4 | The deferred ordering decision has no owner, and the next variant is already prepared with score order. | High | Low-Medium | New | Fix now | #81 stays open (`Part of #81`) with the ordering decision as its remaining criterion, and the issue comment puts the question to the owner. The record names the three prepared variants that use score order. |
| 5 | Nothing anchors the numbers to the shipped defaults, so "-4.8" reads as the flip regressing LongMemEval-S. | Medium | Medium-Low | New | Fix now | Added the current defaults' numbers and the gain of each ordering over them (recency set at 4K: score +14.3 / +0.8, `balanced` +13.7 / +5.4 projected). |
| 6 | The record cannot be checked once the scratchpad is gone; the score-ratio population drops session-promoted candidates. | Medium | Low | New | Fix now | Committed the results file with report digests and all comparisons, described every supporting figure's inputs and method, and recomputed the ratios over all candidates. |
| 7 | Under `balanced`, decay 0.6 no longer does what #111 tuned it to do. | Low | Medium | Pre-existing interaction | Fix now | The Consequence section says to sweep the decay again if `balanced` is chosen for a rank fusion set, with the worked example. No code depends on it until someone proposes that set. |

Attacks that failed (Agent 7): per-category LongMemEval-S rates would favor `balanced` more, not
less; the DeepSeek answers for the score-order arms match the gate's projections within about 1.5
points; and the reserved head is the same record score order puts first, so the quarter-power
penalty does all the work.

## Follow-ups Raised
None. Scenario 2's mechanical check is already tracked in #165.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Criterion 2 verdict contradicted the tables | Must Fix | Resolved |
| Decision paragraph, terms, audit link, sentence clarity, loss table metric | Should Fix | Resolved |
| Supporting figures: method, population, benchmark scope, LongMemEval-S line lengths | Should Fix | Resolved |
| PACKING.md wording, setting names, staleness | Should Fix | Resolved |
| Results file and digests | Should Fix | Resolved |
| RFC-0006 evidence log | Should Fix | Resolved |
| Review record | Should Fix | Resolved |
| Consider items | Consider | Resolved or kept with reasons above |
| Break scenarios 1 to 7 | Adversarial | Fixed in this change; scenario 2's check tracked in #165 |

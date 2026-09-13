# Common readers for the normalized PRME–Hindsight development capture

Registered before dataset capture completion or inspection of its quality
outcomes. This is a separate answer-generation study following the normalized
raw-memory capture at `f5a2a69`, not a change to its source-recall protocol.
All 119 development cases remain included. No new independent holdout is claimed.

## Frozen comparison

Use only the exact 4,096-token contexts verified by the completed capture
analyzer: PRME's default-density product bundle and the disclosed Hindsight
adapter rendering of exact returned units. Do not replace returned chunks with
source documents, use the shared whole-turn evaluator reconstruction, add gold
snippets, or optimize either context after observing answers. Include a fresh
empty-memory control for every query. All arms share the same question and
reference date, common generation instructions and reader settings.

Run both installed readers: `qwen3.5:4b` and `gemma4:26b`. Their model digests,
Ollama version, prompt, options and code hashes are frozen in the accompanying
reader declaration. Use temperature 0, seed 42, no thinking, a 65,536-token
context request and 1,024-token answer limit. The existing generation prompt
asks for evidence-based answers, preserves temporal qualifications and unresolved
contradictions, and requires abstention when support is absent. It is unchanged
for this comparison. Conservative byte headroom prevents knowingly submitting
oversized requests; returned native token counts are also checked. This is not
proof of the server's tokenizer or universal deterministic inference.

Generation inputs contain opaque case IDs, questions, supplied reference dates
and rendered context strings only. They contain no answer, category, evidence
labels, original abstention-suffixed IDs or product labels. Internal arms
`memory_a`, `memory_b` and `empty` map to PRME, Hindsight and no memory; these
names are never inserted in the reader prompt. Native context formats may still
reveal product characteristics. This is label isolation, not perfect blinding.

Per-case arm order is fixed by a hash of opaque case ID and arm. Identical full
requests reuse their saved response and report both logical predictions and
unique generations. The two readers have separate state/output files. No valid
answer can be replaced by rerunning. A truncated, invalid or failed response is
retained and stops that study; it requires a recorded amendment before any new
study. An interrupted process can resume immutable successful responses only
with identical input, code, model and options. Unrecorded in-flight responses
cannot be recovered, and their costs may be unknown.

## Completion and judging

Require both capture workers to finish all 119 cases with native exit zero and
the analyzer to verify every context before preparing dataset reader inputs.
Freeze each prepared input hash and reader plan before its first generation.
Require all 357 logical predictions and independently observed native exit zero
for each reader before exporting predictions for judging. No correctness-based
selection, retries, exclusions or partial-study winner claims are allowed.

Use the existing separate `gemma4:31b` judge with the pinned custom category
rubric and authored calibration controls. Freeze its declaration now and require
its complete calibration to pass before dataset judging. Judge question,
reference and prediction without the product/reader identity or memory context;
retain raw responses, prompt hashes, category rules, token counts and verdicts.
The answer labels are used only in this distinct judging process. A failed
calibration leaves predictions intact and prevents this judge from producing an
accepted quality report. The study is not the official GPT-4o benchmark protocol.

Primary descriptive outcome is paired judged correctness at 4K between PRME
and Hindsight, separately for each reader. Also report each against its own
fresh empty-memory control, every category, all abstention cases, wins/losses/
ties, query-bootstrap uncertainty and the same base-question/identical-history
cluster sensitivity as the source study. Partially overlapping histories remain
dependent. These are exploratory comparisons with no superiority or production
default-promotion gate. Do not pool reader families into a single headline score
or ignore category regressions. Retain ambiguous labels and judge disagreement
in later manual error analysis without silently editing original scores.

The judge shares a model family with the Gemma reader; authored calibration and
the Qwen reader do not remove all judge bias. Raw profiles disable extraction,
consolidation and full graph capabilities. Development questions have already
informed PRME changes. No result establishes overall market leadership. Report
saved inference counts/times alongside ingestion/retrieval records without
claiming a fair speed or dollar-cost comparison under concurrent local load.

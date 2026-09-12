# Product packing development evaluation — 2026-09-12

The initial [three-question capture/replay check](smoke-afc1539.json) uses frozen
source `afc1539`, LongMemEval S cleaned histories and the existing PRME development
split (`prme-evidence-v1`). It ingests raw NOTE turns and replays every public
retrieval candidate through the production packer. Every original saved context
and token count reproduced exactly. Capture and replay both exited zero; the
capture completed in 113.86 seconds with zero errors. The source run had a clean
worktree. The subsequent 119-question development capture was stopped after a
reproduced admission failure; see the retained failure below. This smoke report
is not a complete development result.

The diagnostic changes only the comparator inside the multi-path tier, from
score/token density to composite score. Pins, instructions, active tasks, all
other priorities, source labels, representation fallbacks and budgets remain
fixed. The product tokenizer is cl100k_base, with 100 reserved overhead tokens.
Only complete source text inside content-bearing representations earns evidence
credit; UUID-only pointers do not. Source snapshots remain outside the repository;
the report retains their hashes, selected IDs and representation-level inclusion.

On these three questions, density packing retained none of the labeled support
at 2,048 or 4,096 tokens. Relevance ordering retained all labeled support at both
budgets. At 8,192 tokens, density retained support for one question and relevance
for all three. These are **three selected development questions**, not an accuracy
score, held-out result or competitive comparison. The reported bootstrap intervals
are mechanically computed sample summaries and do not make this tiny observation
generalizable. Production density ordering remains unchanged pending broader
measurement.

The harness tests passed **39 checks**, including real public-engine capture and
replay after closing the pack, evaluator-label independence, reference-only credit,
unchanged candidates, source hashes, control reproduction and rejection of stale
or abnormally exited worker reports.

## Retained failure and exact-question recovery

The [stopped capture](stopped-afc1539.json) records 36 attempted questions,
35 successes and one failure out of 119 selected questions (29.41% successful
coverage). The worker was deliberately interrupted after `gpt4_7fce9456` exposed
four empty source turns. `Event` left their content hashes empty, which violated
the durable direct-store record's SHA-256 binding. Its actual worker exit was
-2, and the report remains incomplete. No partial quality result is credited.

`376178a` hashes empty source strings without discarding or altering them.
The [exact-question reproduction](empty-source-reproduction-1f5375a.json) used
the same dataset hash and development split with frozen source `1f5375a`. It
completed with zero errors in 51.34 seconds and an actual zero process exit.
The offline product context and token count reproduced exactly. The diagnostic
now accounts for blank text without giving it positive evidence credit
(`61d87a4`, 13 tests passed). On this single regression question, relevance
ordering lost support at 2K, tied at 4K and gained at 8K. This is a regression
check, not evidence for changing the production default.

A fresh full 119-question capture at `1f5375a` is running independently. Its
results will not be assembled from the stopped run and this targeted retry.

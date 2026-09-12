# Product packing development evaluation — 2026-09-12

The initial [three-question capture/replay check](smoke-afc1539.json) uses frozen
source `afc1539`, LongMemEval S cleaned histories and the existing PRME development
split (`prme-evidence-v1`). It ingests raw NOTE turns and replays every public
retrieval candidate through the production packer. Every original saved context
and token count reproduced exactly. Capture and replay both exited zero; the
capture completed in 113.86 seconds with zero errors. The source run had a clean
worktree. The complete 119-question development capture is running separately;
this smoke report is not its result.

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

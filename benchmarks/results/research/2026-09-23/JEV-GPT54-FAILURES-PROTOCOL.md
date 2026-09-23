# GPT-5.4 temporal resolver + Jev: LongMemEval failure diagnostic

Registered 2026-09-23 UTC in an isolated worktree from main `2e105851`.
Implementation commit: `765cb871`. The user explicitly selected the existing
opt-in temporal-reasoning feature with OpenAI GPT-5.4 replacing Ollama.

Reproduce from branch `research/jev-gpt54-failures-2026-09-23`. The new OpenAI
adapter is isolated on that branch; transferring the findings to the main
project does not merge the experimental implementation.

## Frozen comparison

All 70 incorrect answers from the completed 500-question GPT-5.4 LongMemEval-S
baseline are selected, in their original order. The exact IDs, artifact trees,
source hashes, dependency versions and full configuration are in
[`jev-gpt54-failures-v1-registration.json`](jev-gpt54-failures-v1-registration.json).
The registration checksum is
`1038e33f709259ee1aef18418db09a6d263a0b9c2610e14380813499fc498980`.

Each arm uses a separate clone of the same immutable source pack. The fresh
control must reproduce the original benchmark context byte for byte. All
ranking, routing, source evidence, packing settings and the 3,996-token effective
context ceiling remain fixed. Only the candidate enables temporal relations and
selects the new OpenAI resolver. The original feature's query rules route 16/70
questions; benchmark category labels do not affect routing.

The resolver uses `gpt-5.4-2026-03-05`, medium reasoning, JSON output, Flex,
8,192 maximum output tokens including reasoning, and `store=False`. Its system
prompt and one-schema-repair limit are unchanged. TypeSafe `jev-1.13.0` retains
its exact operand question and threshold 0.85. Local quote/date validation,
deterministic arithmetic and same-budget repacking are unchanged. This is a
new provider variant and must report `confirmation_protocol_aligned=false`.
The previous Ollama confirmation is not evidence for this variant.

Only the question, question time and exact packed source representations are
sent to the resolver. Jev receives the question and proposed operand alignments.
Neither receives reference answers, evidence labels, historical generated
answers or judge feedback. `propose_product_alignment()` is excluded.

## Answer evaluation and interpretation

Both arms receive fresh independent GPT-5.4 reader/judge calls for every one of
the 70 questions, including questions with identical contexts. The prior zero
correct selected cohort is a selection criterion, not a fresh causal control.
Reader and official judge prompts, model snapshot, medium reasoning, token
limits and exact yes/no scoring match the completed baseline. Alternating arm
submission order is fixed by cohort index; provider concurrency is four.

Report overall and category correct counts; paired wins/losses/ties and 95%
question/full-history-cluster bootstrap intervals (10,000 draws, seed20260922);
routed, accepted, changed and unchanged-context subsets; retrieval p50/p95;
context tokens; annotation omissions and conflict flags; provider attempts,
failures, token use and cost. Unchanged-context differences estimate rerun
variability and cannot be attributed to Jev. Existing ingestion is reused,
with historical ingestion time reported separately from zero new ingestion.
Jev usage is recorded without inventing a dollar price.

All 140 arm evaluations must complete. Any missing answer, provider failure,
invalid verdict, source/configuration drift, context mismatch, backend error or
token violation fails the run closed. Failed artifacts are retained and never
silently replaced. The stage directories are created exclusively.

This is examined, failure-selected development data. Do not extrapolate a new
500-question headline by adding recovered answers to 430, or claim that the
430 previously correct answers cannot regress. Default promotion requires a
separate full-cohort regression and untouched confirmation. Production defaults
remain unchanged.

## Reproduction and retained evidence

Runner: `benchmarks/diagnostics/longmemeval_jev_gpt54_failures.py`.
Verifier: `benchmarks/diagnostics/analyze_jev_gpt54_failures.py`.
The pinned environment is `/tmp/prme-v0.12.0-release-venv/bin/python`, with
`PYTHONPATH=src`. Stages are `register`, `preflight`, `capture`, then `run`.
These commands are intentionally non-resumable and refuse existing outputs.

Raw artifacts: `data/jev-gpt54-failures-v1/` in the research worktree. Provider
request/response bodies are saved without credential headers. The ledger
accounts for actual OpenAI usage and retains reservations for ambiguous charges.
The authored live preflight uses two synthetic frame events, independent of the
benchmark, and passed before dataset provider calls. Its result and local test
summary are separately checksummed. No production feature flags are deployed.

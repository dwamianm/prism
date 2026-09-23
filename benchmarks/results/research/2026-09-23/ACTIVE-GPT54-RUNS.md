# Completed GPT-5.4 comparison handoff

**Both complete cohorts are finished and authenticated. No benchmark process
remains active. Do not rerun or replace these executions.**

- LongMemEval-S: **430/500 (86.0%)**.
- LoCoMo categories 1–4: **985/1,540 (63.96%; 64.0% rounded)**.
- All 4,080 benchmark reader/judge calls returned HTTP 200 on their first attempt.
- Total observed API usage including controls and access probes: **$16.45756725**.
- No unresolved charge reservations or terminal benchmark failures.
- Source preparation completed all ten LoCoMo conversations and 5,882 turns.

The worktree is `/Users/dwamianm/Sites/prism-locomo-baseline-2026-09-22`, branch
`research/locomo-release-baseline-2026-09-22`, based on released v0.12.0
`aaa2e4e6320d9a75d47362e555e659c72baf38a8`. Production source, defaults and
pins are unchanged. At benchmark completion, no automatic merge, push or new
release was performed.

## Main project integration

The user subsequently requested that all learning move into the main project.
The complete research history is now integrated into `main`, including the
tools, tests, registrations, retained failures, complete results and diagnostics.
README, BENCHMARKS.md, the research agenda, roadmap, project goals and AGENTS.md
carry the current findings and the next experiment's acceptance gates.

The main checkout retains a byte-identical raw-run archive at
`data/gpt54-comparison-v1/`, plus the 500 historical LongMemEval source captures,
their execution/verification records and pinned official judge source under
`data/gpt54-comparison-dependencies/`. The
[transfer record](gpt54-main-project-transfer.json) binds the complete local
checksum manifest. These raw archives stay outside Git. Original path strings
and benchmark bytes were preserved; the local manifest maps archived historical
dependencies. Original worktrees and full historical source packs are retained.
This integration changes no production code, defaults or dependency pins and
does not create a new release.

## Authoritative artifacts

- [Complete comparison](GPT54-DEFAULT-BENCHMARK-COMPARISON.md)
- [Registration](gpt54-comparison-v1-registration.json)
- [Final verification](gpt54-comparison-v1-verification.json)
- [Post-hoc evidence diagnostics](GPT54-EVIDENCE-DIAGNOSTICS.md)
- [LongMemEval result](gpt54-longmemeval-v1-result.json)
- [LoCoMo result](gpt54-locomo-v1-result.json)
- [Vendor reference facts](zep-reference-20260923.json)

Private source packs, contexts, every provider request/attempt/response, and the
shared usage ledger remain under `data/gpt54-comparison-v1/`. Credentials came
from the main checkout's project `.env`; no credential is in the public records.
The shell credential was stale and was not used for these paid runs.

Use `PYTHONPATH=src` with `/tmp/prme-v0.12.0-release-venv/bin/python` to inspect
the frozen harness. Do not change its dependencies or registered modules merely
to clean up a harmless unused import in the source scheduler. The original
calibration import failure and cost remain recorded. Its registered loader
amendment loads the exact official prompt function AST without the unrelated
CLI dependency. The source scheduling amendment partitioned independent,
untouched conversations; the original coordinator's expected FileExistsError
was a pre-ingestion ownership handoff, not a failed source case. Every child,
the final source coordinator, and the answer queue completed successfully.

The final verifier reauthenticated the full protocol/amendment/source/dependency
identities, every cohort ID, request, response, context, result and closed pack;
it recomputed scores, category counts and confidence intervals. The post-hoc
retention audit ran only after both complete cohorts passed verification.

## Interpretation and next work

Zep reports 90.2% LongMemEval and 94.7% LoCoMo. These are external reference
values, not matched live arms: its exact prompts, cohort checksum and execution
artifacts are unavailable, context budgets differ, and its LoCoMo category
counts do not reconcile. Our LoCoMo score is semantic yes/no accuracy, not
upstream token-F1. The prior DeepSeek 87.4% LongMemEval result remains separate.

Source selection is the principal measured opportunity: LongMemEval lost 94
returned annotation instances at packing; LoCoMo lost 989. Retention is not a
causal guarantee of answer quality. Preserve the earlier negative evidence for
unconditional episode routing and broad session penalties. Register an
answer-blind complementary-evidence candidate, then require a complete positive
paired trial, backend/regression checks and untouched new histories
before any default proposal. No new default is recommended from baseline
measurements alone. README and the research agenda now contain both positive
and negative findings.

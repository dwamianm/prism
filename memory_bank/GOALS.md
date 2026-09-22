# PRME goals and production handoff

**Status:** active handoff, 2026-09-22  
**Production baseline:** `main` at `6446f06` (the current branch is an
ancestor; the merged mainline also contains the later typed-value, profile,
and temporal-parser work).  
**Research record:** [research agenda](../docs/RESEARCH-AGENDA.md)

## Product goal

Build a trustworthy local-first memory package whose ordinary APIs preserve user
data and tenant isolation, and whose quality advantages survive reproducible,
held-out comparisons. We should claim leadership only for named tasks,
versions, readers, judges, budgets, and cost constraints where PRME actually
leads.

## What is already in production

Mainline already contains the completed work from the memory-reliability
branch and subsequent production merges. The safe promoted surface includes:

- durable raw ingestion and restart/retry handling;
- source-preserving extraction, scoped retrieval, contradiction handling, and
  atomic replacement/publication paths;
- balanced context packing as the default, with density and score policies
  still available explicitly;
- tenant-bound HTTP/MCP access, workspace/lease isolation, PostgreSQL parity,
  deterministic rebuild and recovery paths;
- durable profile staging/recovery, shared temporal-parser locking, and typed
  canonical values where the API exposes them;
- opt-in evidence-bound temporal relations and exact quantity aggregation.

These are production code paths, not a promise that every feature wins every
answer benchmark. The current mainline is the promotion target; no additional
branch merge is required for the above set.

Verification performed against a detached checkout of `main` at
`7b52290`: `3667 passed, 800 skipped` with `uv run pytest -q`. The focused
core checks were `64 passed, 18 skipped`; HTTP/MCP checks with the declared
extras were `53 passed, 10 skipped`. The skipped cases are optional or require
live external services/backends; no test failures were observed.

## Deliberately not promoted as defaults

The following remain experimental or rejected and must not be enabled merely
because they resemble competitor techniques:

- generic compact/grouped/metadata-factored renderers;
- episode routing and evidence augmentation as unconditional policies;
- answerability/verifier providers, learned ranking profiles, or rerankers;
- result-guidance prompts and free-form value-fidelity repairs;
- store-time supersedence, QA pairing, surprise gating, and query
  reformulation;
- automatic bulk alias/entity merges. Jev/product proposals remain inert and
  require caller selection and explicit review.

## Evidence we can currently defend

- balanced packing: 83/119 development and 250/381 confirmation answers versus
  67/119 and 185/381 for density, using the registered local reader/judge;
- LongMemEval-V2 web-small: 80/149 with memory versus 10/149 without memory;
- AgentMemBench judged retrieval: 979/1000 recall@5;
- operational isolation/lifecycle checks: no cross-user leakage in the
  registered operational run, 200/200 archive retirements, and 200/200 writes
  at each tested worker count;
- temporal-relation confirmation: 60/104 versus 50/104 on its frozen disjoint
  partition, with the feature disabled by default.

These are bounded results, not universal superiority claims. Important negative
evidence remains: MemoryArena travel failed non-inferiority, EventQA trailed
BM25, BEAM remains 13/20, and claim-verification candidates have not passed
the required precision/recall gates.

## Next goals and gates

1. **Production safety:** keep full installed, DuckDB, PostgreSQL, HTTP/MCP,
   restart, and workspace isolation regressions green before release changes.
2. **Matched competitive evaluation:** run a current LongMemEval-S comparison
   against Zep or another live alternative with identical history, reader/judge,
   context budget, ingestion accounting, latency accounting, and an untouched
   holdout. See the [Zep methodology review](../benchmarks/results/research/2026-09-17/ZEP-BENCHMARK-METHODOLOGY-REVIEW.md).
3. **Interactive quality:** repair conflict resolution, temporal/value
   rendering, and source-cited episodic reconstruction on fresh cohorts; do
   not promote prompt-only fixes from inspected development splits.
4. **Knowledge verification:** test typed argument/qualifier/temporal
   alignment on a new development source, then require an untouched external
   cohort before enabling any provider verifier.
5. **Release discipline:** a change is promotable only when its measured gain
   beats the current production baseline, its failure modes are recorded, and
   targeted plus relevant backend tests pass. Otherwise keep it opt-in or
   archive it as a rejected experiment.

## Resume instructions

Start with this file and `docs/RESEARCH-AGENDA.md`. Treat the archived
`.planning/milestones/v1.0-phases/` and `.planning/archive/v1.0-legacy/` as
history only. Treat `main` as production; do not merge a research branch
wholesale just to recover benchmark artifacts. Check current branch ancestry
and run the relevant installed-package tests before any release or default
change.

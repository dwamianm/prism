# PRME goals and production handoff

**Status:** active handoff, 2026-09-23

**Production baseline:** released v0.12.0 at `aaa2e4e`; the subsequent GPT-5.4
benchmark evidence is integrated into `main` without product-code or default
changes.

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
- the reader context format, score ordering, rank fusion scoring with a
  0.25 current-state recency boost and an event-time tie-break, and a 0.6
  rank fusion session decay as the retrieval defaults (since 2026-09-25,
  adopted on the DeepSeek answer track), with the auditable and compact
  formats, balanced and density ordering and the weighted formula still
  available explicitly;
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

- complete GPT-5.4 benchmarks of the retrieval defaults before 2026-09-25:
  **LongMemEval-S 430/500 (86.0%)** and **LoCoMo 985/1,540 (64.0%)**, with a
  3,996-token effective context ceiling, medium reader/judge reasoning and zero
  terminal failures;
- on the separate DeepSeek answer track at the same 3,996-token ceiling, the
  current defaults against the previous ones in two interleaved pairs: LoCoMo
  65.7% to 80.9% and 65.8% to 81.6%, LongMemEval-S 85.8% to 86.8% and 86.4% to
  86.6% (`BENCHMARKS.md`; not comparable with the GPT-5.4 numbers);
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

## Current benchmark learning

The [audited GPT-5.4 report](../benchmarks/results/research/2026-09-23/GPT54-DEFAULT-BENCHMARK-COMPARISON.md)
and [post-hoc evidence audit](../benchmarks/results/research/2026-09-23/GPT54-EVIDENCE-DIAGNOSTICS.md)
are the current baseline for this reader and raw-turn storage protocol. All
2,040 questions completed; 4,080 benchmark provider calls succeeded on their
first attempt. Total observed API cost including controls was $16.4576.
LongMemEval reuses the frozen production-control contexts; LoCoMo freshly stores
5,882 source turns. The older DeepSeek 437/500 (87.4%) run stays separate.

PRME trails Zep's published 90.2% and 94.7% references, but this is not a live
matched comparison: source preparation, prompts and context budgets differ.
LoCoMo uses registered semantic yes/no accuracy, not official token-F1.
Historical audit numbers do not supersede these scoped current results.

**Prioritize context selection.** LongMemEval returned all 886 annotated
evidence instances but omitted 94 during packing. LoCoMo omitted 989 returned
instances; 443 of its 555 incorrect answers lacked some resolvable annotated
evidence. Multi-hop scored only 80/282 (28.37%). Annotation retention is a
diagnostic, not proof that an answer will improve. Separately audit errors with
retained evidence for temporal interpretation, updates and conflict handling.

Test answer-blind complementary evidence selection while retaining the strongest
anchor. Preserve the negative results for unconditional episode routing, broad
session penalties and existing reranking. No new default is supported by these
baseline measurements. Both canonical cohorts are examined development data;
confirmation must use independently prepared untouched histories/questions.

## Next goals and gates

1. **Production safety:** keep full installed, DuckDB, PostgreSQL, HTTP/MCP,
   restart, and workspace isolation regressions green before release changes.
2. **Matched competitive evaluation:** run a current LongMemEval-S comparison
   against Zep or another live alternative with identical history, reader/judge,
   context budget, ingestion accounting, latency accounting, and an untouched
   holdout. See the [Zep methodology review](../benchmarks/results/research/2026-09-17/ZEP-BENCHMARK-METHODOLOGY-REVIEW.md).
   The completed GPT-5.4 reference comparison supplies PRME baseline numbers;
   it does not close the matched-live-competitor gate.
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

### Jev with a GPT-5.4 temporal resolver (2026-09-23)

The [70-failure diagnostic](../benchmarks/results/research/2026-09-23/JEV-GPT54-FAILURES-REPORT.md)
completed with zero provider failures: fresh control 6/70, candidate 7/70.
All score differences occurred on unchanged contexts; the only added temporal
relation did not repair its answer. No Jev answer gain is demonstrated. The
variant remains research-only and the default baseline stays 430/500 (86.0%).
Prioritize source coverage and separately test temporal precision/operand
limitations; retain the flagged possible reference inconsistency without
changing official scores. Do not infer overall accuracy from selected failures.

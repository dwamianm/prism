# PRME roadmap

Updated 2026-09-23. Current package: v0.12.0. Product priority: **reliable, measurable AI memory with an excellent developer experience**.

## Direction

Find the right evidence, preserve changing facts, and fit useful context into an application's token budget. Evaluate retrieval separately from the model generating the final answer. See [BENCHMARKS.md](BENCHMARKS.md) for the measurement contract.

The foundation already includes local DuckDB/USearch/Tantivy storage, optional PostgreSQL, hybrid and temporal retrieval, ingestion and organizer pipelines, context packing, index rebuilds, MCP/REST, MemoryClient, and LangChain/LlamaIndex adapters. Additional frameworks and federation are deferred while retrieval quality is established.

## Current retrieval-quality priority

The completed [GPT-5.4 default benchmarks](benchmarks/results/research/2026-09-23/GPT54-DEFAULT-BENCHMARK-COMPARISON.md)
score **86.0% LongMemEval-S** and **64.0% LoCoMo**. The
[evidence audit](benchmarks/results/research/2026-09-23/GPT54-EVIDENCE-DIAGNOSTICS.md)
identifies context packing as the main measured opportunity: 94 and 989 returned
annotation instances, respectively, were omitted from packed context. LoCoMo
multi-hop accuracy is 28.37%; annotation omissions accompany 191 of its 202
incorrect multi-hop answers.

Prioritize an answer-blind complementary-evidence packing candidate that
preserves the strongest anchor under the same token budget. Require complete
paired answer results as well as source retention, then relevant regression and
backend checks and a new untouched confirmation cohort. Separately investigate
errors with retained evidence, especially updates, conflict handling and temporal
reasoning. Do not infer a deployable gain from annotation coverage alone, revive
failed blanket episode routing, or change defaults based on these baseline runs.
See [project goals](memory_bank/GOALS.md) and the [research agenda](docs/RESEARCH-AGENDA.md)
for current evidence and promotion gates.

## Earlier evidence-led delivery

Historical milestones below provide context, not current acceptance criteria. This
work on `feat/memory-reliability-quality` has delivered durable fast ingestion and
processing status; replayable retrieval time; faithful token-bounded context;
coverage-checked consolidation; source-cited extraction; named replacement rules;
atomic replacement commits; and working project `.env` configuration. A fresh
Python 3.13 wheel passed restart recovery and scoped retrieval. The latest
completed frozen full suite (`5ff7234`) passed 1,868 tests, with 42 skips
and live PostgreSQL, including research and example checks.
Failed startup now releases acquired resources on both backends and restores
encryption after a local pack was decrypted; wrong-key failures preserve the pack.
Vector payloads now survive abrupt exits before the USearch snapshot is saved;
startup reconciles missing inserts and stale deletions without model calls.
Grounded LLM output is now journaled before graph writes and reused on indexing
retries. Scoped inspection works through the engine, sync client, HTTP and MCP.
A real local-model fault-injection workflow and installed Python 3.13 ingestion
both passed; subsequent work added atomic graph replay and durable extraction jobs.
Independent indexing preserves a healthy search path during an outage. Scoped
entity matching now covers older entities beyond the former 100-node window.
Retrieval distinguishes backend failures from empty results and detected model
mismatches, with sanitized diagnostics through HTTP/MCP. A newer Python 3.13
installed wheel passed source, recovery, selection and authenticated API checks.
Cancelled materialization now finishes tracking committed writes and cleans
partial artifacts, while preserving a final replacement that already committed.
Normal ingestion now prepares fixed graph and index inputs before publication,
journals the first complete plan, and commits the graph and receipt atomically.
The prepared-plan journal and commit path pass concurrency, independent-reader,
rollback and abrupt-process-exit checks. Startup
also preserves explicitly assigned epistemic types instead of reclassifying
modern nodes with a legacy heuristic. Idempotent index staging now survives
retries and abrupt process exits without inference; compaction preserves
unpublished staging claims. Crash testing also repaired vector-key reuse after
DuckDB recovery. Explicit retry after reopening uses saved plans without new
inference and skips already committed work. Durable extraction jobs now survive
restart with scoped status, explicit processing and retry across Python, HTTP,
MCP and CLI. Lease generations fence stale workers inside graph transactions;
completion commits with its receipt. Explicit stale-plan recovery now preserves
old plans while queuing a new revision from saved extraction. Abandoned-stage
collection and model-output revisions remain open. A real local-model trial also
exposed inconsistent entity/subject names that leave facts unlinked. Built-in
provider responses now validate closed, type-qualified references; custom facts
retain explicit missing/ambiguous link status. Semantic aliases and same-name
identity resolution remain extraction-quality gaps. Live outputs also exposed
coarse/wrong relationship labels and missing relationship epistemic qualifiers.
New derivations now represent relationships as source-cited, epistemically
filtered claims with subject/object association links. Model labels no longer
become direct semantic graph edges; their semantic accuracy remains unproven.
Two real-model diagnostic trials still misclassified hypothetical usage as a
preference; one failed namesake extraction. An extra relationship FACT masked
the wrong preference in the second diagnostic, so the evaluator now checks all
claim kinds and preserves the invalid pass as failure evidence. A subsequent
12-case classification probe found persistent kind/condition errors. A clearer
prompt/schema candidate did not establish a gain and was reverted after testing;
[raw control/candidate results](benchmarks/results/extraction/2026-09-12/README.md)
remain available. Namesake evaluation now checks actual graph roles instead of
requiring one exact spelling of an otherwise valid entity mention. A fixed-input
review experiment reduced passing cases from 9/12 to 7/12 when it rejected claims;
classification alone reached 11/12 while preserving all source-grounded claims.
It remains experimental: one error persists and each message needs another model
call. Recovery diagnostics also now preserve timeout/provider/schema categories
instead of reporting a provider deadline as caller cancellation. Installed ONNX Runtime telemetry caused a native
shutdown abort; local embeddings now disable that optional uploader by default
and diagnostics require a clean child-process exit before reporting success. Processing is explicit; retrieval does not run an LLM recovery job.

Four full-history development evaluations exposed and repaired a recency
heuristic regression. The final development profile reaches 91.96% support recall
at 2,048 tokens, versus 90.79% initially; its paired confidence interval still
touches zero. That frozen profile completed all 381 held-out questions without errors. It
reaches 85.27% support recall at 2,048 tokens; vector/RRF do better at larger
budgets, and the paired differences against them include zero. The original
version also completed all 381 questions with zero errors. Its 2,048-token recall
was 85.12%; the updated profile's +0.15 percentage-point change has a 95% interval
of −1.28 to +1.74. The query clock also changed, so this is not an isolated
algorithm comparison. Held-out retrieval improvement remains unproven.
Complete reports and comparisons are linked from BENCHMARKS.md. Causal simulation
repairs removed future-message leakage and event timestamp rewriting; 71 of 74
checks pass, with three retrieval-quality failures still open. No current-release superiority
claim is supported yet. Next gates remain complete reproducible held-out evidence,
semantic extraction quality, durable LLM derivations/replay, complete aggregation,
database-enforced isolation, and actual agent/developer outcomes.

Local branch implementation now also includes per-user HTTP/MCP identities,
scoped request maintenance, atomic contradiction operations, original event
reads and precise derivation lookup. Explicit score/count selection works across
the engine and clients; HTTP filter/mode/limit parameters are no longer ignored.
PrecisionMemBench exposed excessive irrelevant candidates (11/77 single-turn
assertions and 0/12 session assertions passed); application-calibrated selection
is available, while a reliable default acceptance policy remains a quality gap.

## First delivery

- Repair vector candidate starvation under user, scope, lifecycle, and temporal filters; count distinct nodes rather than vector entries.
- Remove dataset observations, copied answer examples, and harness-only query expansion from benchmark evaluation.
- Replace stale accuracy claims with explicit measurement limits.
- **Merged in PR #69:** organizer isolation, shared DuckDB connection locking, and restored CI coverage for main.
- **Merged in PR #70:** filtered vector recall, benchmark evidence boundaries, and corrected performance documentation (#63).
- **Merged in PR #72:** lexical index shutdown reliability (#71).
- **Merged in PR #73:** explicit benchmark error accounting, measured coverage, and failure status for incomplete runs (#64 remains open for the full baseline).

Adaptive vector search repairs recall but does not implement RFC-0004 index-level namespace partitioning. Highly selective searches may scan the full index; measure latency before scaling this approach.

## Prioritized work

The retrieval-quality priority above is current. The issue table below records
the September 12 backlog review; it is not a fresh issue-status audit.

Effort: S = 1–2 focused days, M = 3–5 days, L = more than a week. Estimates are provisional; research experiments may stop after a negative result.

| Priority | Work | Effort | Acceptance criterion |
|---|---|---|---|
| P0 | [#64 Measurement](https://github.com/dwamianm/prism/issues/64), [#63 Documentation](https://github.com/dwamianm/prism/issues/63) | M | Held-out baseline; evidence recall and packing quality separate from answer scores; full run provenance and repeated-run spread |
| P0 for shared deployments | [#35 Server-bound identity](https://github.com/dwamianm/prism/issues/35) | M | REST/MCP callers cannot choose another user's identity; all read/write surfaces covered |
| P1 | [#29 Complete aggregation](https://github.com/dwamianm/prism/issues/29) | L | Scoped enumeration with explicit completeness/truncation; correct counts beyond top-k and context limits |
| P1 | [#30 Temporal entity state](https://github.com/dwamianm/prism/issues/30) | L | Current and historical answers respect event time, knowledge time, provenance, and unresolved contradictions |
| P1 | [#65 Public API gaps](https://github.com/dwamianm/prism/issues/65) | M | Reliable event-to-node resolution under concurrent writes; retrieval modes work through candidate generation and public APIs |
| P2 | [#27 Entity state](https://github.com/dwamianm/prism/issues/27), [#31 Fact-first packing](https://github.com/dwamianm/prism/issues/31) | M | Improve support recall at fixed token budgets without losing qualifiers, negations, or provenance |
| P2 | [#32 Near-miss disambiguation](https://github.com/dwamianm/prism/issues/32) | M | Held-out near-miss and abstention improvement justifies added ingestion cost |
| P2 for PostgreSQL deployments | [#67 Database isolation](https://github.com/dwamianm/prism/issues/67) | L | Restricted-role RLS tests, including pooled connection reuse; depends on server identity and PostgreSQL CI |

Measurement comes first because unreliable scores cannot guide optimization. Aggregation and temporal state address identifiable retrieval limitations. Entity cards, compression, and contrastive encoding remain experiments until they demonstrate gains against that baseline.

## Backlog decisions

Reviewed all 16 open issues and added scope/dependency guidance to the 11 retained issues above. Closed:

| Issue | Decision | Reason |
|---|---|---|
| [#60](https://github.com/dwamianm/prism/issues/60) | Completed | Late-stage filtering fixed in bc64244; regression tests pass |
| [#61](https://github.com/dwamianm/prism/issues/61) | Completed | Language pin, cue gate, and threaded parsing in ddeb9cf; tests pass |
| [#62](https://github.com/dwamianm/prism/issues/62) | Completed | Scheduled maintenance and shutdown draining in 5fc73c6; tests pass |
| [#28](https://github.com/dwamianm/prism/issues/28) | Not planned | Umbrella reconstruction redesign overlaps measurable work in #27, #29, and #30 |
| [#33](https://github.com/dwamianm/prism/issues/33) | Not planned | Arbitrary 98% target conflates retrieval and answer-model quality without a trustworthy baseline; replaced by #64 |

A subsequent CI run exposed an intermittent post-close lexical directory cleanup failure, tracked separately in [#71](https://github.com/dwamianm/prism/issues/71).

PR #69 subsequently merged and closed #66 after all eight CI checks passed, including live PostgreSQL on Python 3.11–3.13. Its first CI attempt hit the intermittent cleanup race recorded in #71; the affected job passed on retry.

Issue #35 remains open on GitHub; identity binding is implemented and tested on the current local branch, pending delivery. Issue #67's original claim of no PostgreSQL CI job is stale: the job exists, and its branch triggers and test dependency setup were repaired in #69.

## Release gates

- Core tests and relevant simulations pass on supported Python versions.
- PostgreSQL changes pass against a live database using the intended application role.
- Quality improvements preserve scope, temporal, lifecycle, and provenance rules.
- Benchmark reports identify dataset subsets, models, configuration, and costs.
- API changes preserve compatibility or supply a documented migration path.

A stable release follows demonstrated behavior and maintainable APIs. No date or accuracy percentage is promised before the corresponding evidence exists.

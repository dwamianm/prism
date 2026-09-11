# PRME roadmap

Updated 2026-09-11. Current package: v0.10.0. Product priority: **best possible retrieval quality**.

## Direction

Find the right evidence, preserve changing facts, and fit useful context into an application's token budget. Evaluate retrieval separately from the model generating the final answer. See [BENCHMARKS.md](BENCHMARKS.md) for the measurement contract.

The foundation already includes local DuckDB/USearch/Tantivy storage, optional PostgreSQL, hybrid and temporal retrieval, ingestion and organizer pipelines, context packing, index rebuilds, MCP/REST, MemoryClient, and LangChain/LlamaIndex adapters. Additional frameworks and federation are deferred while retrieval quality is established.

## First delivery

- Repair vector candidate starvation under user, scope, lifecycle, and temporal filters; count distinct nodes rather than vector entries.
- Remove dataset observations, copied answer examples, and harness-only query expansion from benchmark evaluation.
- Replace stale accuracy claims with explicit measurement limits.
- **Merged in PR #69:** organizer isolation, shared DuckDB connection locking, and restored CI coverage for main.
- Retrieval and benchmark changes are available in draft PR #70.

Adaptive vector search repairs recall but does not implement RFC-0004 index-level namespace partitioning. Highly selective searches may scan the full index; measure latency before scaling this approach.

## Prioritized work

Effort: S = 1–2 focused days, M = 3–5 days, L = more than a week. Estimates are provisional; research experiments may stop after a negative result.

| Priority | Work | Effort | Acceptance criterion |
|---|---|---|---|
| P0 | [#64 Measurement](https://github.com/dwamianm/prism/issues/64), [#63 Documentation](https://github.com/dwamianm/prism/issues/63) | M | Held-out baseline; evidence recall and packing quality separate from answer scores; full run provenance and repeated-run spread |
| P0 for shared deployments | [#35 Server-bound identity](https://github.com/dwamianm/prism/issues/35) | M | REST/MCP callers cannot choose another user's identity; all read/write surfaces covered |
| P1 | [#29 Complete aggregation](https://github.com/dwamianm/prism/issues/29) | L | Scoped enumeration with explicit completeness/truncation; correct counts beyond top-k and context limits |
| P1 | [#30 Temporal entity state](https://github.com/dwamianm/prism/issues/30) | L | Current and historical answers respect event time, knowledge time, provenance, and unresolved contradictions |
| P1 | [#71 Shutdown reliability](https://github.com/dwamianm/prism/issues/71) | M | Immediate pack cleanup/move/reopen after close cannot race background index activity |
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

Issue #35 remains open: engine ownership checks landed, but identity binding did not. Issue #67's original claim of no PostgreSQL CI job is stale: the job exists, and its branch triggers and test dependency setup were repaired in #69.

## Release gates

- Core tests and relevant simulations pass on supported Python versions.
- PostgreSQL changes pass against a live database using the intended application role.
- Quality improvements preserve scope, temporal, lifecycle, and provenance rules.
- Benchmark reports identify dataset subsets, models, configuration, and costs.
- API changes preserve compatibility or supply a documented migration path.

A stable release follows demonstrated behavior and maintainable APIs. No date or accuracy percentage is promised before the corresponding evidence exists.

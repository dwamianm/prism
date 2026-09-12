# PRME memory leadership goal

Authorized 2026-09-11: work autonomously to close the assessment gaps and
deliver an excellent developer experience. Leadership requires comparative
evidence; completed features alone do not satisfy this goal.

Baseline: a26338e, v0.11.0; 1,187 local tests passed, 25 skipped.

User clarification: historical GSD goals are not authoritative. Test existing
behavior, supersede outdated approaches where evidence supports it, and select
techniques against the current goal. This delivery order is provisional and
must adapt to measured failures and comparative experiments.

## Delivery order and gates

1. Durable ingestion: atomic event/work persistence, original-event provenance,
   restart recovery, idempotent retries, scoped processing, visible failures.
2. Measurement: explicit dataset variants/splits, provenance, evidence metrics,
   actual context budgets, raw-ingestion evaluation, comparable baselines.
3. Knowledge quality: contextual extraction, grounded assertions, temporal
   updates with cardinality/conditions, complete scoped enumeration.
4. Context and maintenance: faithful rendering, bounded budgets, evidence-safe
   consolidation/forgetting, access to supporting episodes.
5. Recovery and trust: complete versioned derivation log/replay, identity-bound
   shared APIs, deliberate retention/deletion semantics, backend parity.
6. Learning and developer experience: persistent scoped outcome feedback,
   stable simple APIs, actionable errors, documented local workflows.
7. Comparative validation: held-out long-history evaluations, cost/latency and
   quality comparisons, longitudinal agent tasks and real developer feedback.

Each delivery needs targeted regression tests, appropriate backend checks,
updated documentation, and a reviewable commit. Keep historical planning
artifacts as history; the root ROADMAP.md and this file describe current work.

## Current work (verified 2026-09-12)

Historical GSD milestones are not acceptance criteria. This work is driven by
actual failures, source-preserving invariants, current primary-source research,
and reproducible measurements. The active branch is feat/memory-reliability-quality.

Delivered and tested:

- Durable raw ingestion work survives restart/process exit, partial indexing,
  bounded queues and scoped drains. Async/sync processing status and retries
  expose durable attempts/errors. This currently covers ingest_fast() only.
- Retrieval has an explicit replayable reference time. Query-date interpretation
  is separated from caller validity filters; episode recency and relevance floors
  are consistent. Historical, aggregation, and duration questions preserve older
  evidence rather than receiving current-state recency weighting.
- Whole rendered context uses real tokenization, respects reserved overhead, and
  preserves qualifiers. Packer preflight avoids expensive whole-context recounts
  for entries that cannot fit; packing runs off the async event loop.
- Consolidation retains omitted sources, pins and namespace boundaries, verifies
  unchanged source coverage, and cannot retire sources on an unsupported summary.
- LLM extraction stays in the caller namespace, requires exact source citations
  in the built-in provider, validates subject/object mentions, and retains full
  supporting paragraphs. Different values coexist; automatic retirement requires
  a named, source-supported replacement in an observed/asserted update.
- Replacement state/pointers/edges commit atomically after materialization, on
  DuckDB and PostgreSQL. Index/batch failures preserve prior facts. Cross-user,
  cross-scope, self, and retired-node replacements are rejected.
- Extraction retries are bounded, respect configured timeouts and shutdown, and
  waiting callers receive typed failures. Project .env settings/selected provider
  credentials now load with documented precedence, without global env mutation.
- Normal local startup no longer attempts an unused community extension install.
- Complete scoped node enumeration now uses bounded stable-ID pages in both
  clients/backends. It counts stored assertions, not distinct real-world events,
  and does not claim a transaction snapshot across concurrent page reads.
- The wheel carries py.typed; client responses, events, nodes, and organizer
  results have concrete return types. CI checks a static consumer contract.
- Hierarchical summaries preserve full excerpts and source labels, retain user/
  scope boundaries, group by UTC episode time, and remain INFERRED. New summaries
  are indexed; failures remove partial artifacts and preserve original sources.

Validation: frozen 68e443d passed 1,459 tests / 12 skips with live PostgreSQL.
The subsequent causal simulation timeline regression passed; contradiction
transaction tests passed 42 checks / 1 backend-specific skip. A built wheel
installed cleanly on Python 3.13.3 and passed real local embedding, restart
recovery, processing status, scoped retrieval and close. Installed-wheel positive
and negative static consumer checks passed with mypy 1.19.1.

Measurement: full LongMemEval S histories, fixed 119-question development split,
neutral source IDs, evidence recall/MRR/nDCG, actual whole-turn token budgets,
and BM25/vector/RRF/recency/empty baselines. Runs come from frozen source checkouts
and retain configuration, dependencies, dataset checksum and coverage. Complete
matched-run comparisons include paired uncertainty estimates and all failures.
No default ranking replacement is justified by a five-question smoke test.

Four complete 119-question development runs expose and repair the initial
current-state heuristic regression. The frozen duration correction at 6fc6b67
reached 91.96% support recall at 2,048 tokens versus baseline 90.79%; the paired
change is +1.17 points, with a 95% interval of 0.00 to +3.22, two wins, no losses,
and 112 ties among 114 labeled questions. This small development result does not
establish a general improvement. The untouched 381-question test split is now
running against that frozen profile; no partial held-out answers are being used
for tuning. Complete development reports and comparisons are retained in
benchmarks/results/evidence/2026-09-12/.
These are evidence metrics, not answer accuracy or superiority over competitors.

The simulation harness previously exposed future messages to earlier checkpoints
and rewrote immutable event timestamps to simulate aging. It now ingests messages
at their causal arrival time, retrieves with an explicit clock, scopes organizer
runs, and isolates/cleans temporary packs. The corrected suite passes 71/74 checks;
remaining failures concern API-decision relevance, current database state, and
CEO replacement ordering. The script's legacy 80% exit threshold is not a claim
that these scenarios all pass.

Remote extraction: updated project credentials now authenticate, but the provider
returns credit_balance_exhausted / insufficient_quota. Local Ollama qwen3.5:4b
was used for synthetic extraction diagnostics. It exposed false preference
classification, omitted conditions and unsupported object paraphrases. Citation
validation recovered source-supported values in the condition example; semantic
classification and relationship entailment remain fallible. No live-model accuracy
claim follows from these diagnostics.

Still open: crash-resumable LLM derivation jobs, complete event/operation replay,
contextual extraction and semantic entailment, complete semantic aggregation,
bounded faithful semantic compression, persistent outcome feedback, identity-bound
shared APIs, held-out comparisons and longitudinal agent outcomes. Atomic graph
replacement is not a complete ingestion transaction or durable derivation log.

## Limits on claims

No current-release accuracy or superiority claim until reproducible comparative
runs support it. External-user feedback and production outcomes must be actual
observations, never simulated claims. Missing services/credentials are recorded
as validation gaps while independent work continues.

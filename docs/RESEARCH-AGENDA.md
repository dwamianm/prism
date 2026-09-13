# Research agenda: demonstrated memory quality and developer experience

Updated 2026-09-12. This agenda supersedes the [v0.6 proposal](archive/RESEARCH-AGENDA-v0.6.md).
Its historical scores and projected “98%+” target do not establish today's
performance. GSD completion states and RFC proposals are not acceptance evidence.
The aim is a leading memory package whose advantages survive reproducible
comparisons and whose ordinary APIs preserve user data and isolation.

## What the evidence currently supports

| Area | Verified result | Boundary |
|---|---|---|
| Product context packing | Two fixed local readers improved by 20 judged-correct answers each on the same 119 development questions when multi-path candidates used score ordering. | Custom local rubric, shared histories, one judge; second reader shares its model family. Not an independent test set or competitor result. |
| Profile fidelity and publication | Complete qualified source excerpts, explicit inference/provenance, exact token budgets, atomic publication and complete scoped source scans. | Profiles are source collections; this does not prove semantic synthesis, exhaustive facts or automatic freshness. |
| Storage and developer workflow | [Latest PostgreSQL workspace validation](../benchmarks/results/recovery/2026-09-12/PG-WORKSPACES.md) at `b7521bc`: full regression passed 2,877 tests with 81 skips, including live PostgreSQL, research and examples. The installed selection passed 143 tests with 13 skips. The real-BGE 100-project concurrent retrieval and native backup/restore workflow completed; a separate verifier checked all source and merged-entity evidence in both retained dumps. | Separate overlapping invocations at recorded commits. Authored recovery, eligibility and namespace contracts do not establish answer quality, hosted project grants or all deployment environments. Client resource samples exclude database-server memory. |
| Identity and maintenance | Unresolved personal references stay event-local; merging preserves type/provenance/validity, including copied relationships. Default maintenance no longer consumes anonymous feedback to change global weights. New merges atomically journal evidence, relationships and supersedence. | These are reproduced storage contracts. Coreference, equal-name disambiguation, full historical organizer replay and scoped learning activation remain incomplete. |
| Deferred raw-source throughput | Local processing now shares a durable lexical commit before acknowledging sources. A frozen 32-source real-model workflow reduced 32 commits to one, with identical candidates and contexts across serial/batch trials. | Small authored histories with warmed embeddings on one host under concurrent load; no competitive speed claim. Direct `store()` still indexes immediately. |
| Local embedding consistency | Cache residency and text grouping no longer change tested BGE vectors after `e297d08`; installed real-model and focused regression checks passed. | Individual inference costs throughput on short-text batches. No cross-hardware bitwise guarantee. |
| Matched raw retrieval | All 119 development questions completed against pinned Mem0 OSS. At 4K shared whole-turn packing, PRME source recall was 96.49% versus 93.27%; Mem0 led preferences. | Raw mode, frozen older PRME reference, shared evaluator packer and no answer generation. The 4K interval touches zero; no end-to-end leadership claim. |
| Matched raw-context answers | [Complete PRME–Hindsight comparison](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-READER-RESULTS.md): Qwen 53/119 versus 52/119, Gemma 71/119 versus 56/119. All 714 logical judgments and native artifact chains verified. | Qwen's paired group interval includes zero. Both readers answer only 1/9 assistant questions correctly with PRME. Development cohort, custom local judge, raw profiles; no consistent overall leadership or default-promotion claim. |
| PersonaMem-v2 pilot | All 96 questions completed and independently verified. Alpha .25 packing answered 42 correctly, density 36, score 41 and no memory 33. | Primary cluster interval includes zero; losses on other-person and health questions. Custom persona-hidden variant, one local reader, no default promotion. |

See the [reader study](../benchmarks/results/packing/2026-09-12/READER-STUDY.md),
[recovery evidence](../benchmarks/results/recovery/2026-09-12/README.md),
[identity and maintenance report](../benchmarks/results/recovery/2026-09-12/IDENTITY-AND-MAINTENANCE.md),
[entity profile guide](ENTITY-PROFILES.md), and
[comparative protocol audit](../benchmarks/results/research/2026-09-12/COMPARATIVE-EVALUATION.md).
These records preserve commit identities, raw-output hashes, failures and limits.

## Immediate decisions and their gates

1. **Honor the failed packing confirmation gate.** All 381 frozen questions and
   controls completed. At 4K, labelled-source recall rose 65.04% → 85.77%, but
   preference recall fell 78.26% → 68.12%, violating the preregistered category
   guard. Density remains the default; score is opt-in. Diagnose preference
   coverage and test any new selection policy as a new exploratory hypothesis;
   do not rewrite the gate or call a tuned rerun independent confirmation. The
   [complete record](../benchmarks/results/packing/2026-09-12/CONFIRMATION.md)
   preserves both gains and regressions. This is source retention, not test-set
   answer accuracy.
2. **Extend the completed external baseline carefully.** The
   [registered Mem0 comparison](../benchmarks/results/research/2026-09-12/mem0-raw-dev-completion-b384095.json)
   completed all 119 questions without errors. Keep its raw-turn, shared-packer
   result distinct from future extraction and actual product-context arms. The
   next protocol must match reader, total rendered tokens and ingestion costs,
   and explicitly support each product's temporal API. Hindsight and Graphiti
   have been source-audited. Hindsight's subsequent authored public-API preflight
   passed retain/reopen/recall, bank isolation and seven matched embedding inputs;
   the first strict dataset capture stopped when native ingress removed control
   characters. A [fresh normalized comparison](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-NORMALIZED-DEV-PROTOCOL.md)
   began all 119 development questions against installed PRME `b7521bc`.
   Both use FastEmbed 0.8.0 and identical numerical dependencies/model assets.
   Complete source readback and returned-text validation precede analysis; actual
   returned contexts remain separate from shared whole-turn reconstruction.
   Its strict metadata-preservation gate rejected missing optional custom metadata
   in two cases. The full 119-case operational audit completed with native exit 1;
   all seven affected units retained native role and timestamp fields. PRME was
   stopped after nine cases. The [native-provenance preflight](../benchmarks/results/research/2026-09-12/native-provenance-preflight-verification.json)
   passed both products and all 24 context reproductions without quality labels.
   A [fresh complete comparison](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-NATIVE-DEV-PROTOCOL.md)
   completed both 119-case captures with zero errors and native exit zero; failed
   captures are excluded. The [source comparison](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-NATIVE-SOURCE-RESULTS.md)
   verified all 714 contexts. At 4K shared whole-turn packing, PRME retained
   96.35% of labelled sources versus 52.27%; actual document-hit recall was
   74.85% versus 45.91%. PRME lost all nine assistant evidence sources from its
   actual bundle, while Hindsight retained seven. These are source metrics only.
   The [common-reader protocol](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-READER-PROTOCOL.md)
   is frozen for Qwen/Gemma plus fresh empty controls. Its authored live reader
   check passed; the separate judge passed 41/42 calibration controls with zero
   false accepts. Final scoring verifies native capture, reader and judge chains,
   keeps reader families separate and reports category losses. Dataset reader
   input and plans are frozen at `f1a0b64`; Qwen completed all 357 predictions
   with native exit zero and offline reproduction of every saved response.
   Gemma also completed all 357 predictions with native exit zero. The
   [complete judged results](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-READER-RESULTS.md)
   verify all 714 logical judgments: PRME versus Hindsight is 53/52 correct with
   Qwen and 71/56 with Gemma. Assistant memory is a consistent loss, 1/9 for
   PRME versus 8/9 and 7/9. A stronger source-recall total has not solved packing.
   Timing under concurrent load cannot support a speed ratio.
3. **Explain remaining failures before adding techniques.** Use completed results
   to separate missing sources, lost qualifiers, insufficient context, incorrect
   temporal or episode associations, arithmetic and task-completion errors.
   Preserve ambiguous annotations and reader/judge disagreements. Improvements
   must work beyond the examples used to discover them.
   The [completed lexical ablation](../benchmarks/results/research/2026-09-12/LEXICAL-QUERY-STUDY.md)
   improved preference evidence at 4K but lost other evidence and reduced the
   overall 2K mean. PostgreSQL's all-term query semantics
   also need deliberate evaluation; its duplicate-before-limit defect is fixed. The
   [PostgreSQL vector study](../benchmarks/results/recovery/2026-09-12/PG-VECTOR-SEARCH.md)
   separately reproduced filtered HNSW starvation. Both backends now honor the
   exact-search default; PostgreSQL materializes eligible rows before ordering.
   Broad-query cost is higher in the synthetic probe, and approximate search
   remains an explicit recall/cost tradeoff. Named PostgreSQL projects now use
   identity-checked schemas and a shared pool; hosted grants remain separate.
   The [completed full-hybrid development study](../benchmarks/results/research/2026-09-12/HYBRID-LEXICAL-STUDY.md)
   ran both lexical policies through actual retrieval, both packing orders and
   three budgets on all 119 development questions, with zero errors. All 1,428
   saved contexts reproduced exactly. Stopword removal improved default-density
   4K recall by 11.11 points, but reduced score-packing recall by 2.56 points,
   including multi-session and assistant regressions. It remains experimental.
   Density still retained none of the nine assistant evidence sources at 4K.
   The [completed length-penalty diagnostic](../benchmarks/results/research/2026-09-12/PACKING-LENGTH-STUDY.md)
   evaluated all 30 registered arms and 3,570 contexts. Native-parser alpha .25
   improved 4K recall to 95.03%, but retained less assistant evidence than score
   ordering. Carry that single-comparator hypothesis into broader evaluation;
   production defaults and the failed confirmation gate remain unchanged.
   The [completed PersonaMem pilot](../benchmarks/results/research/2026-09-12/PERSONAMEM-PACKING-STUDY.md)
   is inconclusive: alpha .25 improves by six correct answers, with a cluster
   interval that includes zero and category regressions. Post-hoc annotated
   snippet coverage exposes substantial packing loss, but errors remain even
   when annotated snippets are complete. Test an explicitly annotation-selected
   reader control before attributing those errors to retrieval or adding more
   scoring heuristics. The subsequently [completed annotation-selected control](../benchmarks/results/research/2026-09-12/PERSONAMEM-ANNOTATED-READER.md)
   answered 66/96 versus a fresh no-memory control's 34/96, with a positive
   persona-cluster interval. It still answered 0/10 other-person questions
   correctly; two inspected cases assign a colleague's preferences/condition to
   the user. Audit subject attribution and merge semantics alongside evidence
   selection. This control is not product retrieval or an independent holdout.

The [fixed one-head packing experiment](../benchmarks/results/research/2026-09-12/PACKING-HEAD-STUDY.md)
retains the top-scored ordinary multi-path candidate before density packing. On
the examined 119-question development cohort, 4K whole-source recall rose from
74.85% to 81.51%, recovering seven assistant sources with no per-question source
loss at 4K. One multi-session question regressed at 2K. All 714 contexts passed
independent measurement checks. Answer-reader validation and broader regression
evidence remain necessary; this does not change the product default.

## Capability work still required

The [current provenance research review](../benchmarks/results/research/2026-09-12/PROVENANCE-RESEARCH-UPDATE.md)
adds MemIR, Agent Zero Memory and AttriMem as hypotheses/comparison candidates.
It distinguishes claim support, source opening and credit assignment from
reproduced product improvements; no published total replaces the checks here.

The [complete authored extraction probe](../benchmarks/results/research/2026-09-12/AUTHORED-EXTRACTION-RESULTS.md)
retains Qwen's 10/12 and Gemma's 11/12 checks, including ambiguous kind labels
and one incorrectly asserted conditional preference. The isolated prompt-only
follow-up produced Qwen 8/12 and Gemma 11/12: Qwen's target error persisted with
two additional failed checks; Gemma fixed conditional usage but timed out on
namesake extraction. The unproven prompt change was reverted. All failures,
identity checks and the rollback are retained in the linked report.

| Gap | Next implementation/evaluation requirement |
|---|---|
| Named projects and domains | `MemoryWorkspace` manages identity-checked local packs and PostgreSQL schemas with shared embeddings and a bounded lease cache. PostgreSQL projects share a bounded connection pool; the installed 100-project backup/restore workflow passed. The [workspace guide](WORKSPACES.md) states cancellation, background-extraction and operator boundaries. Next: hosted credential-to-project grants, larger realistic partition benchmarks, explicit legacy import and lifecycle operations. A metadata filter alone is insufficient, and the full grant hierarchy remains unimplemented. |
| Organizer replay coverage | New duplicate/alias merges atomically journal evidence, relationship copies and supersedence, with backend fault, process-exit, concurrent-writer and retry checks. Other organizer/manual mutations and historical data still lack complete replay inputs; extend coverage without inventing past events. External index cleanup remains a separate repairable step. |
| Reliable compact semantic memory | Compare grounded extraction and source-backed summaries with raw-turn baselines under the same reader and token/cost conditions. Retain qualifiers, temporal boundaries, contradictions and provenance; compression ratio alone is insufficient. |
| Profile maintenance recovery | Durable prepared inputs, owner-scoped Python recovery, journal reconstruction and fenced replacement are implemented. Explicit abandonment and fenced collection of unpublished staging are implemented. Next: evaluated scheduled maintenance policy. No automatic profile scheduler is present. |
| Temporal and aggregate questions | Prove coverage of the queried set and distinguish episodes and reference clocks. Counting retrieved top-k items is not an exhaustive count; newest mention alone is not truth. Evaluate multi-session, updates and abstention together. |
| Adaptive retrieval | Complete scoped profile activation and rollback only after replay evaluation from immutable feedback receipts. Global weight changes must not silently use one tenant's feedback for another. See RFC-0017. |
| Developer experience | Keep installed-wheel sync/async examples runnable, ownership and scope explicit, failures actionable and retries bounded. Test first write, restart, retrieval, recovery and migration without repository-only imports. |
| Interactive task completion | The [MemoryArena adapter](../benchmarks/results/research/2026-09-12/AGENTIC-MEMORY-INTEGRATION.md) passed the unmodified upstream client with installed PRME, real embeddings, task isolation and restart provenance. The authored travel scorer audit exposes omitted-failure denominator changes and prefix false accepts. Require exact registered cohort coverage and validate task scoring before interpreting any travel success rate. No interactive dataset task has been scored. |

The existing [namespace RFC](RFC-0004-Namespace-and-Scope-Isolation.md),
[derivation RFC](RFC-0016-Durable-Derivation-Commits.md), and
[learning RFC](RFC-0017-Scoped-Retrieval-Learning.md) describe constraints and
proposals. Their draft requirements need implementation evidence; they do not
replace the observed gaps above.

## Evidence needed for a leadership claim

A credible claim must name the versions, tasks and resource constraints where
PRME leads. Require independently reproducible runs against current, correctly
configured alternatives, fixed ingestion/reader/judge conditions, more than one
reader family, measured ingestion and retrieval costs, and held-out task data.
Include updates, abstention, conflicting claims, multi-session reasoning and
interactive task completion rather than relying on one static QA total.

Publish failure coverage and category regressions beside aggregate changes.
Pin datasets, model assets, code/configuration and token accounting. Keep raw
source and extraction costs visible, distinguish public product behavior from
adapter-added behavior, and make comparisons runnable from an installed package.
The current local studies advance that evidence; they do not establish that PRME
is the best memory system available.

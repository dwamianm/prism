# Research agenda: demonstrated memory quality and developer experience

Updated 2026-09-14. This agenda supersedes the [v0.6 proposal](archive/RESEARCH-AGENDA-v0.6.md).
Its historical scores and projected “98%+” target do not establish today's
performance. GSD completion states and RFC proposals are not acceptance evidence.
The aim is a leading memory package whose advantages survive reproducible
comparisons and whose ordinary APIs preserve user data and isolation.

## What the evidence currently supports

| Area | Verified result | Boundary |
|---|---|---|
| Product context packing | The default balanced policy improved a fixed 35B reader from 67/119 to 83/119 on the development cohort and from 185/381 to 250/381 on a separately registered answer confirmation. It improved every category or tied. | Both source partitions had already been inspected; one local reader and one custom calibrated local judge. This is not an independent test set or competitor result. |
| Profile fidelity and publication | Complete qualified source excerpts, explicit inference/provenance, exact token budgets, atomic publication and complete scoped source scans. | Profiles are source collections; this does not prove semantic synthesis, exhaustive facts or automatic freshness. |
| Storage and developer workflow | [Latest PostgreSQL workspace validation](../benchmarks/results/recovery/2026-09-12/PG-WORKSPACES.md) at `b7521bc`: full regression passed 2,877 tests with 81 skips, including live PostgreSQL, research and examples. The installed selection passed 143 tests with 13 skips. The real-BGE 100-project concurrent retrieval and native backup/restore workflow completed; a separate verifier checked all source and merged-entity evidence in both retained dumps. | Separate overlapping invocations at recorded commits. Authored recovery, eligibility and namespace contracts do not establish answer quality, hosted project grants or all deployment environments. Client resource samples exclude database-server memory. |
| Identity and maintenance | Unresolved personal references stay event-local; merging preserves type/provenance/validity, including copied relationships. Default maintenance no longer consumes anonymous feedback to change global weights. New merges atomically journal evidence, relationships and supersedence. | These are reproduced storage contracts. General coreference, equal-name disambiguation and full historical organizer replay remain incomplete. |
| Hierarchical summary maintenance | Daily, weekly and monthly source excerpts now use durable prepared publications with deterministic period lineages and complete stable-ID source paging. Unchanged, concurrent and restarted runs converge on one active identity; changed selected inputs and legacy excerpts are replaced atomically. | Authored consistency, recovery, 501-source scoped pagination and explicit operator-scan tests. This does not establish that extractive summaries improve answer quality or provide exhaustive coverage. |
| Claim qualifiers | Built-in extraction preserves typed polarity and exact explicit conditions, rejects common modality collapses, and excludes unresolved conditions from default retrieval. Explicit condition evaluation is atomic, evidence-aware, idempotent across restarts, and available on Python, HTTP, and MCP; confirmed conditions receive asserted retrieval weight. | Authored local probes, not held-out accuracy. Qwen still exhausted strict citation retries on one mixed claim; PRME records caller evaluations but does not automatically decide condition truth. |
| Retrieval semantics | Present-state ranking recognizes ordinary “what does” and “who is” queries without applying inferred recency when no explicit update exists. Decisions and instructions share the full semantic-node boost. Two consecutive full simulation runs passed 74/74; the complete live-PostgreSQL suite passed 3,268 tests. | Authored causal scenarios and regression tests. They do not establish held-out answer quality or optimal default weights. |
| Deferred raw-source throughput | Local processing now shares a durable lexical commit before acknowledging sources. A frozen 32-source real-model workflow reduced 32 commits to one, with identical candidates and contexts across serial/batch trials. | Small authored histories with warmed embeddings on one host under concurrent load; no competitive speed claim. Direct `store()` still indexes immediately. |
| Local embedding consistency | Cache residency and text grouping no longer change tested BGE vectors after `e297d08`; installed real-model and focused regression checks passed. | Individual inference costs throughput on short-text batches. No cross-hardware bitwise guarantee. |
| Matched raw retrieval | All 119 development questions completed against pinned Mem0 OSS. At 4K shared whole-turn packing, PRME source recall was 96.49% versus 93.27%; Mem0 led preferences. | Raw mode, frozen older PRME reference, shared evaluator packer and no answer generation. The 4K interval touches zero; no end-to-end leadership claim. |
| Matched raw-context answers | [Complete PRME–Hindsight comparison](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-READER-RESULTS.md): Qwen 53/119 versus 52/119, Gemma 71/119 versus 56/119. All 714 logical judgments and native artifact chains verified. | Qwen's paired group interval includes zero. Both readers answer only 1/9 assistant questions correctly with PRME. Development cohort, custom local judge, raw profiles; no consistent overall leadership or default-promotion claim. |
| Held-out agent-trajectory answers | [Registered LongMemEval-V2 web-small comparison](../benchmarks/results/research/2026-09-14/LONGMEMEVAL-V2-WEB-UNSEEN-DETERMINISTIC-V1.md): PRME scored 80/149 (53.69%) versus 10/149 (6.71%) without memory, with 75 paired wins, 5 losses and a +37.58 to +55.70 point question-bootstrap interval. All rows, official deterministic scores, inputs, reader settings, pack/config bindings and zero-memory baseline behavior passed the fail-closed comparator. | One local Qwen 9B reader and one saved PRME artifact versus no memory. The filtered web-small cohort excludes judge-dependent categories, enterprise and eight previously inspected questions. Mean PRME context was 43,195 tokens. This establishes memory utility on the named cohort, not competitive leadership. |
| Agent-trajectory context efficiency | [Registered compact 4K development study](../benchmarks/results/research/2026-09-14/LONGMEMEVAL-V2-WEB-COMPACT4K-DEVELOPMENT.md): PRME scored 49/149 versus 10/149 without memory, with 45 paired wins and 6 losses, while mean reader memory context fell 83.05% from the 32K reference. | The same 149 questions and earlier outcomes were known. Compact 4K lost 31 correct answers versus the earlier arm, and the study changes both format and budget. Keep compact explicit and measure an intermediate budget curve before changing defaults. |
| PersonaMem-v2 pilot | All 96 questions completed and independently verified. Alpha .25 packing answered 42 correctly, density 36, score 41 and no memory 33. | Primary cluster interval includes zero; losses on other-person and health questions. Custom persona-hidden variant, one local reader, no default promotion. |
| Scoped retrieval learning | Explicit labels feed a deterministic observed-candidate proposal gate; fresh paired receipts with complete gold identities feed a separate full-retrieval gate. Immutable owner/exact-scope profiles persist and apply across restart with inspected activation, deactivation and rollback on both backends. | Authored functional and concurrency validation proves the gate and lifecycle contracts. No learned profile has yet passed a representative task holdout or established answer-quality improvement. |
| External lifecycle and scale workflows | The registered MELT run completed five seeds and all 20 lifecycle checkpoints at recall@12 and NDCG@12 of 1.000. The registered BEAM raw workflow ingested all 94 public 100K chunks and returned all 50 requested memories for 20 questions across ten abilities. | MELT uses four held-out lifecycle cases and a deterministic embedding profile. BEAM is predict-only and does not measure relevance or answer correctness. |

See the [reader study](../benchmarks/results/packing/2026-09-12/READER-STUDY.md),
[recovery evidence](../benchmarks/results/recovery/2026-09-12/README.md),
[identity and maintenance report](../benchmarks/results/recovery/2026-09-12/IDENTITY-AND-MAINTENANCE.md),
[entity profile guide](ENTITY-PROFILES.md), and
[comparative protocol audit](../benchmarks/results/research/2026-09-12/COMPARATIVE-EVALUATION.md).
These records preserve commit identities, raw-output hashes, failures and limits.

## Immediate decisions and their gates

1. **Preserve the failed score-only gate and use the separately tested balanced
   policy.** All 381 frozen score-ordering questions and controls completed. At
   4K, labelled-source recall rose 65.04% → 85.77%, but preference recall fell
   78.26% → 68.12%, violating the preregistered category guard. Score remains
   opt-in. The later balanced policy is a distinct algorithm with its own complete
   source and answer evidence and is now the default; it does not rewrite the
   failed score-only gate. The
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

The [first one-head answer trial](../benchmarks/results/research/2026-09-12/PACKING-HEAD-READER-INCOMPLETE.md)
stopped before judging when Gemma timed out after logging 245 of 357 predictions.
Qwen completed all 357. Saved responses and native exits are verified, but this
incomplete trial has no comparative answer score. A follow-up must register a
fresh complete trial; failed outcomes cannot be selectively replaced.

The [fresh 600-second-timeout follow-up](../benchmarks/results/research/2026-09-12/PACKING-HEAD-READER-TIMEOUT-FOLLOWUP.md)
also stopped, during Qwen at 315/357 logged predictions. All 275 saved unique
responses passed structural/checksum checks, but Gemma and judging never
started. Neither incomplete trial supports comparative answer scores.
Time-correlated local server logs include a model-loading timeout and a closed
client connection. The service responds and all three model digests remain
available; this does not establish a hardware or model-capacity cause. Diagnose
execution before another complete reader run or a larger model download.

The [fixed packing composition study](../benchmarks/results/research/2026-09-12/PACKING-COMPOSITION-STUDY.md)
completed all 1,428 contexts with independent verification and exact reproduction
of 714 prior controls. Combining one reserved head with the quarter-length rule
retains 95.91% of labelled sources at 4K, versus 95.03% for quarter-length and
74.85% for density. Its primary gain over quarter-length is one question, with
a cluster interval touching zero; at 2K it also loses one question against that
simpler rule. This reused development cohort supports further testing, not a
default change. Source recall remains distinct from answer correctness.

The [larger fixed packing regression](../benchmarks/results/research/2026-09-12/PACKING-REGRESSION-STUDY.md)
then verified all 4,572 contexts on the already examined 381-question partition,
including exact reproduction of 2,286 original controls. At 4K, quarter-length
retains 89.37% of labelled evidence and the combined policy 90.55%, versus 65.04%
for density. Both improve every category mean, including preferences, but each
loses on two questions. The combination improves assistant retention while
losing on two multi-session questions versus quarter-length. The public
`balanced` policy now reproduces all 4,500 saved density/score/balanced contexts
over 500 questions and three budgets. Installed receipt compatibility and the
guide workflow pass. See
the [implementation verification](../benchmarks/results/recovery/2026-09-12/BALANCED-PACKING.md).
The original failed score-only policy gate remains unchanged.

The [complete balanced answer trial](../benchmarks/results/research/2026-09-13/BALANCED-QWEN35B-ALL-V2.md)
then retained all 119 development questions across density, balanced and empty
contexts. A fixed 35B Qwen reader and separately calibrated 31B Gemma judge
scored balanced at 83/119 versus density at 67/119, with 26 paired wins, 10
losses and no lower category total. The first registered run failed at a
512-token generation ceiling and remains recorded; a separately registered
short-answer replacement completed all 714 reader and judge outcomes without a
provider failure. This answer evidence, together with the two source-retention
studies, promotes balanced to the default while preserving explicit density and
score options. The examined cohort and custom local judge still require an
independent answer holdout before any competitive leadership claim.

The [381-question answer confirmation](../benchmarks/results/research/2026-09-13/BALANCED-QWEN35B-REGRESSION.md)
then used different questions with the same registered reader and judge controls.
Balanced scored 250/381 versus density at 185/381, with 88 paired wins, 23 losses,
and no lower category total. All 762 reader calls and 762 judged outcomes
completed without a failed call. The source partition had already been examined,
so this is an out-of-development answer confirmation rather than an independent
benchmark holdout. Auditing all losses found one clear source omission, six clear judge
inconsistencies, and a concentration of remaining errors in reader temporal
arithmetic and conflict interpretation. Future packing changes should use new
questions rather than tune to these outcomes.

The [simulation clock correction](../benchmarks/results/recovery/2026-09-12/SIMULATION-CLOCK.md)
fixed real-time leakage into maintenance age checks and native mutation
timestamps, but its full run still passed only 70/74. Consolidation publication
is now checksummed, atomic and restart-safe; unchanged and concurrent attempts
reuse one active summary. Current-state and actionable-memory scoring then
reproduced the remaining four failures before correction and passed the complete
gate twice at 74/74. See the
[current scoring evidence](../benchmarks/results/recovery/2026-09-13/SCORING-SEMANTICS.md).
The separate owned-service head1 answer trial failed before judging after a
logged GPU out-of-memory error; no partial answer scores were inspected.
All original prompts fit the existing conservative headroom check at 32K,
suggesting a fresh bounded-memory probe before another registered trial, with
the existing model assets. See the
[failure record](../benchmarks/results/research/2026-09-12/packing-head-reader-owned-incomplete.json)
and [context bounds](../benchmarks/results/research/2026-09-12/ollama-owned-context-headroom.json).

## Capability work still required

The [current provenance research review](../benchmarks/results/research/2026-09-12/PROVENANCE-RESEARCH-UPDATE.md)
adds MemIR, Agent Zero Memory and AttriMem as hypotheses/comparison candidates.
It distinguishes claim support, source opening and credit assignment from
reproduced product improvements; no published total replaces the checks here.

The [current local extraction diagnostics](../benchmarks/results/extraction/2026-09-13/README.md)
supersede the earlier prompt-only candidate. They preserve typed polarity and
exact conditions, use configurable temperature-zero extraction, and reject
uncertain or contingent actions mislabeled as completed decisions. Equal-context
Qwen 9B and 35B-A3B profiles each passed 12/12; the 35B-A3B profile repeated the
result with byte-identical structured outputs and lower observed latency. These
authored probes still require held-out and provider-diverse validation.

| Gap | Next implementation/evaluation requirement |
|---|---|
| Named projects and domains | `MemoryWorkspace` manages identity-checked local packs and PostgreSQL schemas with shared embeddings and a bounded lease cache. PostgreSQL projects share a bounded connection pool; the installed 100-project backup/restore workflow passed. The [workspace guide](WORKSPACES.md) states cancellation, background-extraction and operator boundaries. Next: hosted credential-to-project grants, larger realistic partition benchmarks, explicit legacy import and lifecycle operations. A metadata filter alone is insufficient, and the full grant hierarchy remains unimplemented. |
| Organizer replay coverage | Duplicate/alias merges, explicit reinforcement, single-node lifecycle transitions, explicit supersedence and consolidation retirement now atomically retain complete before/after records. Confirmation request IDs and deterministic correction identities survive retries; pairwise transitions reject unavailable owner/scope evidence. Legacy non-finite metadata has an explicit lossless snapshot encoding. Other organizer/manual mutations and historical data still lack complete replay inputs; extend coverage without inventing past events. External index cleanup remains a separate repairable step. |
| Consolidation quality | [Publication is now durable, atomic and idempotent](../benchmarks/results/recovery/2026-09-13/CONSOLIDATION-PUBLICATION.md), with deterministic request generations, restart recovery and cross-engine concurrency checks on both backends. Next: compare source-backed summaries with raw episodes under matched reader, token and ingestion-cost conditions. Atomic publication proves consistency, not summary usefulness. |
| Reliable compact semantic memory | Hierarchical source excerpts now publish atomically, recover without repeated embedding work, and refresh when selected period inputs change. Next: compare grounded extraction and source-backed summaries with raw-turn baselines under the same reader and token/cost conditions. Retain qualifiers, temporal boundaries, contradictions and provenance; consistency and compression ratio alone are insufficient. |
| Profile maintenance recovery | Durable prepared inputs, owner-scoped Python recovery, journal reconstruction and fenced replacement are implemented. Explicit abandonment and fenced collection of unpublished staging are implemented. Next: evaluated scheduled maintenance policy. No automatic profile scheduler is present. |
| Temporal and aggregate questions | Present-state and dated-history routing pass the causal simulation gate. Semantic counts/lists now expose non-exhaustive coverage, every observed cap/failure/selection limit, and a token-counted model warning; stored-record enumeration remains explicit. Next: evaluate semantic qualification, real-item deduplication, multi-session updates and abstention together. |
| Adaptive retrieval and memory credit | Immutable owner-scoped receipts, explicit relevance labels and answer-time citations preserve returned, packed and cited memory identities across restart. Exact packed-context ablation removes one cited entry without mutation or fill-in and assigns citation-checked signed presence credit under a named fixed evaluation protocol. The registered source-anchored development diagnostic produced 23 correct-to-wrong flips in 37 singly annotated contexts; its six redundant non-flips and one reference inconsistency show why missing flips cannot become negative labels. Scoped ranking profiles now require positive observed-candidate and fresh full-retrieval gates, bind exact owner/scope and runtime feature identity, and support append-only activation and rollback. Next: reproduce [Hindsight Memory-PRM](https://arxiv.org/abs/2608.29605) bank deletion and re-retrieval on fixed readers and held-out tasks, persist accepted interventions, and establish task-level gains before consuming intervention credit. A context ablation is narrower than a retrieval-invariant bank deletion. Global weights and retention must not silently consume one tenant's observations. See RFC-0017 and [the learning guide](LEARNING.md). |
| Developer experience | Installed-wheel sync/async workflows cover confirmation and lifecycle retries and [explicit corrections](MEMORY-CORRECTIONS.md), including retained sources, scoped evidence, full audit records, HTTP/MCP parity and exact retry safety. [Metadata admission](METADATA.md) rejects nonportable values and ambiguous JSON key collisions consistently, and preserves legacy values in journals. Keep testing first write, restart, retrieval, recovery and migration without repository-only imports; these checks do not establish end-to-end memory quality. |
| Interactive task completion | The [MemoryArena adapter](../benchmarks/results/research/2026-09-12/AGENTIC-MEMORY-INTEGRATION.md) passed the unmodified upstream client with installed PRME, real embeddings, task isolation and restart provenance. The authored travel scorer audit exposes omitted-failure denominator changes and prefix false accepts. Require exact registered cohort coverage and validate task scoring before interpreting any travel success rate. No interactive dataset task has been scored. |

The first registered held-out LongMemEval-V2 answer comparison is complete. Its
large gain over no memory clears the memory-utility gate for this cohort, while
its 43K-token mean context makes context compression an immediate product and
evaluation priority. A registered same-cohort compact 4K development arm scored
49/149 versus 10/149 without memory and reduced mean memory context from 43,195
to 7,321 tokens, but lost 31 correct answers against the 32K arm. Measure
intermediate budgets and selection policies before changing either retrieval
default. Add matched current memory baselines, a second reader family and the
excluded judge-dependent categories before generalizing answer quality.

A pinned [MemoryAgentBench integration](../benchmarks/integrations/MEMORYAGENTBENCH.md)
now supports the benchmark's four incremental competency families using the 4K
product packer, complete source manifests, stable query clocks, and exact context
capture. The fail-closed workflow preregisters every prepared source, query,
reference and context assignment, then verifies complete result metrics against
bounded recounted contexts, durable receipts and the exact source manifests.
Final 20-question paired registrations for all four families bind identical
source chunks, questions, reader prompts and model settings across PRME and BM25,
as well as exact preprocessing dependencies. The BM25 verifier independently
reconstructs every ranking and captured source list. Contract tests, pinned-tree
installation, a real-data 500-query EventQA registration dry run, and one-context
real-data ingress smoke across all four families pass, but no task score has been
promoted. Run and verify this registered common-reader matrix next. Treat its
test-time-learning arm as retrieved in-context demonstrations and its conflict
arm as numbered-source resolution; separate experiments are still required for
scoped ranking-profile learning and transactional graph supersedence.

The existing [namespace RFC](RFC-0004-Namespace-and-Scope-Isolation.md),
[derivation RFC](RFC-0016-Durable-Derivation-Commits.md), and
[learning RFC](RFC-0017-Scoped-Retrieval-Learning.md) describe their respective
constraints and implementation boundaries. Their status does not replace the
observed gaps or task-level evidence above.

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

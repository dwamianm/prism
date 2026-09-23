# PRME opt-in interactions — registered design, 2026-09-22

Production source: `main` at `a66ee854325890c6bc28f6b515efeb6ed7df4deb`.
Research branch: `research/opt-in-interactions-2026-09-22`.
No production defaults, environment-wide feature settings, deployment or merge
are authorized by this study. All writes use private benchmark packs.

## Provider amendment, before dataset execution

The user explicitly replaced the unavailable GPT-5.4 reader/judge with Ollama
`deepseek-v4.1-flash:cloud`. Both original credential failures remain recorded:
the shell credential returned 401; the archived baseline's project credential
returned 429 `credit_balance_exhausted`. No dataset inference used either.

Reader and judge now use the local Ollama endpoint, tag digest
`e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`,
temperature 0, seed 42, `num_ctx=65536`, `think=false`, no streaming, and the
original 1024/64 output-token limits respectively. Keep the original
`prme_longmemeval_s_reader_v1` prompt and official category-specific judge
prompts, including the official case-insensitive `yes` substring scoring rule.
Changing the provider is a disclosed new study, not a matched GPT-5.4 score.
The hosted model behind the tag remains opaque despite checking the local digest.

Query reformulation also uses this explicitly selected Ollama model, through
the unmodified production implementation and its production prompt/retry
settings. That implementation does not expose the reader's temperature/seed
controls: do not claim the reformulation calls use them. Record that distinction
and all returned alternatives/failures. No reference answer or evidence label
may enter retrieval, reformulation, temporal resolution, or ingestion.

Temporal relations keep their documented resolver model/digest, 65,536/1600
context/output options, one schema repair, Jev `jev-1.13.0`, and 0.85 gate.
Ordinary queries make no temporal provider call. Any `provider_error` invalidates
the arm even if production safely returns its unchanged control bundle.
Semantic rejection or `unsupported` is a legitimate recorded outcome.

## Matrix

Every row starts from current production settings, including balanced packing,
auditable context, temporal guidance, exact vector search and a 4096-token
budget with the existing 100-token caller reserve. The JSON registration
contains all settings, not only these differences.

| Arm | Difference from baseline | Ingestion |
|---|---|---|
| baseline | None | Frozen registered raw-turn pack, current production retrieval |
| store_supersedence | `enable_store_supersedence=true` | Fresh per question |
| qa_pairing | `enable_qa_pairing=true` | Fresh per question |
| surprise_gating | `enable_surprise_gating=true` | Fresh per question |
| reranker | `enable_reranker=true` | Reuse frozen baseline input |
| query_reformulation | `enable_query_reformulation=true` | Reuse frozen baseline input |
| temporal_relations | `temporal_relation.enabled=true` | Reuse frozen baseline input |
| episode_routing | `packing.episode_context_top_k=2` | Reuse frozen baseline input |
| evidence_augmentation | `packing.evidence_augmentation_top_k=10` | Reuse frozen baseline input |
| episode_augmentation | Episode 2 + augmentation 10 | Reuse frozen baseline input |
| episode_projection | Episode 2 + projection 50 | Reuse frozen baseline input |
| reranker_reformulation | Both named flags | Reuse frozen baseline input |
| temporal_episode | Temporal relation + episode 2 | Reuse frozen baseline input |
| supersedence_balanced | Exact alias of store_supersedence | Same observation, not a replication |
| qa_balanced | Exact alias of qa_pairing | Same observation, not a replication |
| surprise_balanced | Exact alias of surprise_gating | Same observation, not a replication |
| best_individuals_combined | Rule below, frozen before confirmation | Fresh if any ingestion flag is selected |
| full_feature_exploratory | All eight individual settings | Fresh with all three ingestion flags |

Projection and augmentation are mutually exclusive. The full-feature arm uses
augmentation, leaves projection zero, and is explicitly exploratory. Augmentation
retains its default `all` anchor policy. Do not silently substitute `non_entity`.
Projection uses the documented 50 groups, one source and 1.0 decay; episode uses
local-k 8 and .95 decay; augmentation uses one source and .99 decay.

The three balanced aliases cannot identify a statistical interaction because
balanced has no variation in this matrix. Factorial interaction estimates require
additional registered density controls; none are implied here. The other named
combinations are comparisons against baseline; estimate synergy only where both
individual component arms exist. Episode+projection has no projection-only
control and cannot establish a projection interaction term.

## Artifacts and ingestion

All 500 questions of the checksum-pinned cleaned LongMemEval-S file are the
first development stage. This entire cohort has been examined previously. It
cannot supply an untouched confirmation, even after randomly splitting it.

Retrieval arms may reuse the existing registered 500 portable packs after
checking each capture's source identity, capture checksum and entire pack tree.
These are historical ingestion artifacts, not newly measured ingestion under
current main. Report their recorded 73,944.443 summed question-wall seconds
separately, never as current ingestion latency. Every retrieval opens a private
copy, because receipts and maintenance can mutate the database. Hash the input
before and after cloning and retain the final private pack and its receipts.
Never open a master pack for writes. Run current production retrieval for the
control; historical contexts or answers are not the new baseline outputs.

Store supersedence, QA pairing, surprise gating and full-feature each get fresh
ingestion from the complete source histories, through the baseline's sequential
`MemoryEngine.store` FACT API. Preserve session-position-qualified IDs, roles,
timestamps and source metadata. Omit exactly the 12 empty unlabeled source turns
already excluded in the baseline protocol. Do not introduce extraction or feed
questions, labels or answers into store(). Do not copy baseline state and then
toggle an ingestion flag. Store content completeness, lifecycle changes, extra
QA nodes, novelty metadata and failures are recorded independently.

Fresh packs have new creation clocks and UUIDs. These differ from the historical
control artifacts and can affect tie-breaking/packing. Any apparent ingestion
gain therefore requires a fresh no-flag ingestion control with matched clocks
and the untouched confirmation before attributing the difference to the flag.
No ingestion arm is eligible for promotion on the historical-pack comparison.

Reuse requires identical source history, ingestion configuration and dependency
identity. A materialization backlog, missing receipt, backend failure, swallowed
feature failure or checksum mismatch invalidates execution. Preserve failures
and partial data; never recreate a failed pack at the same run identity.

## Pins and applicability

The upstream LongMemEval revision remains
`9e0b455f4ef0e2ab8f2e582289761153549043fc`; its evaluator/generator hashes are
checked against the original registration. The baseline packs report
`fastembed-0.7.4`, BGE-small-en-v1.5, dimension 384, matching the available local
environment. Freeze every installed distribution used now and model asset
checksums before running. The old registration does not include a full transitive
lock, so complete historical environment equality is unknown; never claim it.
Run all new comparisons under the same frozen current environment.

Check treatment activation, not just configuration: counts of superseded nodes,
QA pairs, novelty adjustments, neural score operations, reformulations, routed
temporal calls/accepted guidance, episode operations and source additions or
replacements. Raw direct-turn packs do not reproduce an extracted claim graph.
An inactive projection/augmentation arm supplies no evidence about its utility
on extracted memories. The existing MAB adapter uses `ingest_fast_many` and
hard-codes these flags off; a flag-only edit is not a valid ingestion experiment.
A separate, common direct-store stratum must be registered for all MAB arms if
testing these hooks there, alongside the unchanged native-adapter baseline.

## Execution, completeness and analysis

Freeze every selected ID/config/prompt/model/artifact before the first dataset
call. Five memory workers and four reader/judge workers match the registered
baseline's concurrency. Measure cold retrieval after reopen (excluding open)
and immediate warm retrieval separately. Retain both outcomes; the first is the
answer context. Model-backed cold/warm differences are reported as stochastic
variation, not silently repaired or chosen by score. Deterministic arms require
identical contexts and rankings across the repeats.

Record monotonic ingestion/open/retrieval/reader/judge times, p50/p95, exact
cl100k rendered tokens, provider input/output tokens, request/response hashes,
receipt and artifact hashes, conflicts, returned/packed labeled source coverage,
all question-level losses and all provider attempts. Do not infer dollars from
token counts; monetary cost is unknown unless actual billing is available.
Warm/cold runs with concurrent ingestion are workload timings, not serving SLAs.

Every registered case and arm must complete and authenticate before a matrix
answer comparison or winner selection. Logical provider calls have the baseline
four-attempt transient-HTTP retry ceiling; save each failed attempt. A final
provider failure, truncation, malformed response, duplicate/missing result, or
artifact mismatch stops the run fail-closed. A later repair needs a separately
registered study and must retain this failed run. Do not replace individual
failures, average successful subsets, or score incomplete data as wrong answers.

Primary endpoint: paired all-question answer accuracy difference versus fresh
current-production retrieval. Report category and abstention totals, wins,
losses and ties. Use 10,000 deterministic paired bootstrap draws with seed
20260922 for question intervals and a primary source-history-cluster interval
(shared history belongs to one cluster). Report 95% intervals for both.
Use Holm correction across the non-alias, non-exploratory comparisons for
confirmatory significance. Also report paired token/latency deltas. Do not call
bootstrap intervals universal generalization evidence on this inspected cohort.

Choose at most two individual arms, using only the complete development run:
eligible arms need a positive mean paired difference, a strictly positive lower
cluster interval, no category/abstention accuracy loss, no new complete-evidence
loss, and zero failures. Sort by mean gain descending, then retrieval p95
ascending, then arm ID. If fewer than two qualify, the combined arm is a declared
alias of the one selected arm or baseline, not an invented winning combination.
Freeze selected flags before any confirmation question/output is inspected.

## Later stages and promotion

After the earlier run completes, continue Banking, EventQA, Conflict, Detective
in that order. Preserve their original upstream revision, dataset revision,
question preparation and scoring rules (`exact_match` for Banking/Detective,
`substring_exact_match` for EventQA/Conflict), prompts and output contracts.
Their archived local-Qwen reader remains the matched task control unless a
provider amendment is explicitly recorded. They are not a pooled common score
with LongMemEval. Bind the upstream preprocessing versions/resources from their
verification artifacts (datasets 5.0.1, NLTK 3.9.4, tiktoken .12.0).

Before later inference, register exact prepared task manifests and a disjoint
confirmation cohort after excluding every previously examined question and
shared source-history family. If an adequate untouched cohort cannot be shown,
report confirmation unavailable; never rebrand a development slice as untouched.
BEAM and MemoryArena may start only after all earlier stages complete. Their
task-specific prompts, scoring and budgets must be bound separately. This design
does not pretend those as-yet-unbound cohorts are already fully registered.

Safe-to-promote requires a complete development win, an untouched confirmation
with positive adjusted evidence and no protected-category/evidence regression,
and relevant installed DuckDB/PostgreSQL, API/MCP, restart and isolation checks.
QA pairing additionally needs the documented provenance/atomicity/recovery gaps
resolved in separate production work. Full-feature stays exploratory regardless
of its score. Negative complete evidence rejects promotion; missing evidence
means unassessed/opt-in only, not rejection or equivalence. No automatic default
change or merge follows any result.

Jev product alignment is a separately registered explicit pair and review
workflow. Its authored operational assay exercises inert publication, explicit
accept/reject, retry identity, owner isolation and restart without retiring
either source. It is not another matrix flag, automatic pair selection, bulk
publishing, or held-out product-matching accuracy.

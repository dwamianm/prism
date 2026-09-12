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
| Storage and developer workflow | Full repository regression at `107f535`: 2,400 passed, 57 skipped. Installed Python 3.13 profile workflow also passed. | Tests establish their covered contracts, not answer quality or every deployment environment. |
| Local embedding consistency | Cache residency and text grouping no longer change tested BGE vectors after `e297d08`; installed real-model and focused regression checks passed. | Individual inference costs throughput on short-text batches. No cross-hardware bitwise guarantee. |
| Competitor preparation | Pinned Mem0 source, matched BGE assets, recommended NLP and BM25 support, raw storage and metadata checks pass. | Compatibility preflight is not comparative accuracy. |

See the [reader study](../benchmarks/results/packing/2026-09-12/READER-STUDY.md),
[recovery evidence](../benchmarks/results/recovery/2026-09-12/README.md),
[entity profile guide](ENTITY-PROFILES.md), and
[comparative protocol audit](../benchmarks/results/research/2026-09-12/COMPARATIVE-EVALUATION.md).
These records preserve commit identities, raw-output hashes, failures and limits.

## Immediate decisions and their gates

1. **Complete the frozen packing confirmation.** The
   [registered 381-question protocol](../benchmarks/results/packing/2026-09-12/confirmation-plan.json)
   compares the selected ordering at 2K, 4K and 8K tokens. Require complete native
   process exit, exact control reproduction, positive 4K evidence-recall change
   with a positive lower confidence bound, and the declared budget/category
   guards. Do not adjust the hypothesis after reading test results. Keep density
   as the production default until the gate has been evaluated. A passing source
   gate supports a configurable packing change; it does not by itself establish
   answer-accuracy gains on the test partition.
2. **Measure a matched external baseline.** The
   [registered Mem0 development comparison](../benchmarks/results/research/2026-09-12/mem0-raw-dev-plan.json)
   uses identical raw turns, BGE model assets, 100-result output limits and a
   shared whole-turn token packer. All 119 development questions and failures
   remain visible. This isolates raw retrieval; neither system extracts facts.
   Preserve that distinction when adding later extraction and product-context
   arms. Timing from separately run workloads cannot support a speed ratio.
3. **Explain remaining failures before adding techniques.** Use completed results
   to separate missing sources, lost qualifiers, insufficient context, incorrect
   temporal or episode associations, arithmetic and task-completion errors.
   Preserve ambiguous annotations and reader/judge disagreements. Improvements
   must work beyond the examples used to discover them.

## Capability work still required

| Gap | Next implementation/evaluation requirement |
|---|---|
| Named projects and domains | Enforce an explicit namespace through reads, writes, graph/index candidates, derivation, maintenance, receipts and migration on both backends. A metadata filter alone cannot prevent cross-project merges. Separate packs remain the supported workaround. Full grants and hierarchy require their own tests. |
| Reliable compact semantic memory | Compare grounded extraction and source-backed summaries with raw-turn baselines under the same reader and token/cost conditions. Retain qualifiers, temporal boundaries, contradictions and provenance; compression ratio alone is insufficient. |
| Profile maintenance recovery | Extend atomic publication with durable preparation/retry and cleanup of unpublished staged indexes. Do not describe an atomic commit as a durable organizer queue. |
| Temporal and aggregate questions | Prove coverage of the queried set and distinguish episodes and reference clocks. Counting retrieved top-k items is not an exhaustive count; newest mention alone is not truth. Evaluate multi-session, updates and abstention together. |
| Adaptive retrieval | Complete scoped profile activation and rollback only after replay evaluation from immutable feedback receipts. Global weight changes must not silently use one tenant's feedback for another. See RFC-0017. |
| Developer experience | Keep installed-wheel sync/async examples runnable, ownership and scope explicit, failures actionable and retries bounded. Test first write, restart, retrieval, recovery and migration without repository-only imports. |

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

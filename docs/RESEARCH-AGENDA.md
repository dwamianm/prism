# Research agenda: demonstrated memory quality and developer experience

Updated 2026-09-18. This agenda supersedes the [v0.6 proposal](archive/RESEARCH-AGENDA-v0.6.md).
Its historical scores and projected “98%+” target do not establish today's
performance. GSD completion states and RFC proposals are not acceptance evidence.
The aim is a leading memory package whose advantages survive reproducible
comparisons and whose ordinary APIs preserve user data and isolation.

## What the evidence currently supports

| Area | Verified result | Boundary |
|---|---|---|
| Product context packing | The default balanced policy improved a fixed 35B reader from 67/119 to 83/119 on the development cohort and from 185/381 to 250/381 on a separately registered answer confirmation. It improved every category or tied. A later [500-question episode-composition grid](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-EPISODE-COMPOSITION-V1.md) rejected all five candidates because promoted episodes displaced evidence. The subsequent [session-marginal grid](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-SESSION-MARGINAL-V1.md) found one lossless mild arm with a single turn-recall win, but its [31-changed-context answer diagnostic](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-SESSION-MARGINAL-ANSWER-V1.md) tied 25/31 with one registered win and one registered loss. Manual review found the loss was a judge inconsistency, while the sole source win did not improve its answer. | Both source partitions and the full LongMemEval-S composition cohort had already been inspected. One local reader and one custom calibrated local judge support balanced; the later composition work is development-only and its measured effect is too small for product surface area. Do not expose session-marginal packing or change the balanced default. Keep episode routing opt-in. |
| Profile fidelity and publication | Complete qualified source excerpts, explicit inference/provenance, exact token budgets, atomic publication and complete scoped source scans. | Profiles are source collections; this does not prove semantic synthesis, exhaustive facts or automatic freshness. |
| Storage and developer workflow | [Latest PostgreSQL workspace validation](../benchmarks/results/recovery/2026-09-12/PG-WORKSPACES.md) at `b7521bc`: full regression passed 2,877 tests with 81 skips, including live PostgreSQL, research and examples. The installed selection passed 143 tests with 13 skips. The real-BGE 100-project concurrent retrieval and native backup/restore workflow completed; a separate verifier checked all source and merged-entity evidence in both retained dumps. | Separate overlapping invocations at recorded commits. Authored recovery, eligibility and namespace contracts do not establish answer quality, hosted project grants or all deployment environments. Client resource samples exclude database-server memory. |
| Identity and maintenance | Unresolved personal references stay event-local; merging preserves type/provenance/validity, including copied relationships. Default maintenance no longer consumes anonymous feedback to change global weights. New merges and unverified alias proposals atomically journal complete inputs and outputs with deterministic retry identities. | These are reproduced storage contracts. General coreference, equal-name disambiguation and full historical organizer replay remain incomplete. |
| Jev decision support | A pinned TypeSafe Jev product protocol improved held-out proposal recall from 2.50% to 78.00% and accuracy from 51.00% to 87.50% versus normalized exact names, at 96.30% precision. PRME exposes it as an opt-in advisor and can atomically publish positive results as inert, audited alias proposals. The owner-scoped review inbox records one accepted or rejected decision; acceptance adds a separately verified relationship while retaining both entities. A deterministic Walmart-Amazon candidate trial routed 193/193 positives while reducing the catalog cross product by 99.8644%. Earlier held-out beer alignment improved recall from 14.29% to 64.29%, but its 94.74% precision missed the 98% automatic-merge gate. | Jev is validated for caller-selected product-pair advice and the candidate ranker is validated for discovery. They are not validated as an automatic bulk pipeline: the original composition reached 73.76% precision/77.20% recall, and a development-calibrated rule fell to 88.17%/42.49% on untouched Jev outputs; both were rejected. A retrieval reranker and general claim verification were also rejected. Keep pair selection application-directed, proposals inert until explicit review, and automatic merging forbidden. |
| Hierarchical summary maintenance | Daily, weekly and monthly source excerpts now use durable prepared publications with deterministic period lineages and complete stable-ID source paging. Unchanged, concurrent and restarted runs converge on one active identity; changed selected inputs and legacy excerpts are replaced atomically. | Authored consistency, recovery, 501-source scoped pagination and explicit operator-scan tests. This does not establish that extractive summaries improve answer quality or provide exhaustive coverage. |
| Claim qualifiers | Built-in extraction preserves typed polarity, exact explicit conditions, and literal first-person attempts or intentions; it excludes unresolved conditions from default retrieval. Explicit condition evaluation is atomic, evidence-aware, idempotent across restarts, and available on Python, HTTP, and MCP; confirmed conditions receive asserted retrieval weight. Separately registered [v12 cross-model development runs](../benchmarks/results/extraction/2026-09-16/SPEECH-ACT-V12-CROSS-MODEL.md) each passed 14/14 cases and all 15 safety/utility targets with zero unsafe claims on hosted DeepSeek and local Qwen 35B-A3B artifacts. | Authored probes through one Ollama interface, not held-out accuracy or provider diversity. V8–v11 failures and the initial Qwen whole-object scoring gap are retained. V12's narrow exact-source/entity recovery has causal local tests, but the passing DeepSeek run did not activate it. The validator is not general entailment and does not cover every speech act. |
| Evidence-bound temporal relations | The frozen disjoint [104-question confirmation](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-TEMPORAL-RELATION-CONFIRMATION-V2.md) improved answer accuracy from 50/104 to 60/104, with 10 paired wins, zero losses and all 5 abstention controls preserved. The accepted subset improved from 11/22 to 21/22. The current [product-path replay](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-TEMPORAL-RELATION-PRODUCT-PARITY-V3.md) matched all 104 frozen contexts and receipts, reproduced 22/22 accepted changes, and reduced resolver routing from 100 to 82 questions without missing a changed case. A [500-pack regression](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-TEMPORAL-RELATION-FULL-REGRESSION-V1.md) matched every context and receipt, reproduced all 31 frozen changes, and reduced the full call surface from 195 to 145. The preregistered [12-case live-provider preflight](../benchmarks/results/research/2026-09-18/LONGMEMEVAL-S-TEMPORAL-RELATION-LIVE-PREFLIGHT-V1.md) passed every automatic gate with zero provider errors, exact accepted-control contexts, safe rejection/fallback, and observed provider tokens and latency. | One held-out partition of the same LongMemEval-S source, one hosted resolver/reader alias, a custom judge and an external gate. The live preflight was a small, short stability assay; it is not an availability study or answer benchmark. Thirty-eight routed non-temporal-category questions were inert-only in the full regression, and five of six sampled live shapes safely produced no change. This supports the exact composition, not universal accuracy or a competitor claim. Keep it disabled by default while broader mixed-category evidence is gathered. |
| Local claim verification | Early registered assays exposed unsafe model-only refutation, speech-act collapse, missed conflicts and evidence-order sensitivity; each failed result is retained. Pinned MiniCheck trials culminated in a preregistered [atomic follow-up](../benchmarks/results/research/2026-09-16/WICE-MINICHECK-FLAN-ATOMIC-V1.md): human decomposition reconstructed 357/358 parents, while the model reached 76.04% precision and 66.36% recall. A fresh [SummEdits proof trial](../benchmarks/results/research/2026-09-16/SUMMEDITS-PROOF-VERIFICATION-V1.md) rejected free-form quotations. The causal [segment-ID v2](../benchmarks/results/research/2026-09-16/SUMMEDITS-SEGMENT-VERIFICATION-V2.md) reached 99.00% reference integrity, 72.00% recall and 76.00% balanced accuracy, but only 78.26% precision. An [independent critic](../benchmarks/results/research/2026-09-16/SUMMEDITS-INDEPENDENT-CRITIC-V3.md) reduced false support to 8.00% while collapsing recall to 30.00%. A balanced 1,100-claim [LLM-AggreFact FactCG trial](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-FACTCG-V1.md) reached only 79.44% precision at 61.82% recall. Its preregistered [evidence-local Mistral cascade](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-EVIDENCE-CASCADE-V2.md) improved recall but reached only 85.12% precision at 63.45% recall; at 90% precision recall fell to 36.55%. A pinned 8B [Bespoke sentence-fusion trial](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-BESPOKE-SENTENCE-FUSION-V3C.md) reached 83.54% precision at 60.91% recall; at 90% precision recall fell to 38.00%. It also exposed two MPS generation failures before a direct no-cache forward completed development. A document-grouped [structured FactCG stack](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-STRUCTURED-STACK-V1B.md) added lexical alignment, numeric/name anchors and negation agreement, but no inner cross-fit found the required threshold; post hoc outer scores reached only 81.13% precision at 60.18% recall. No candidate passed calibration, and the external test cohort remained sealed. | WiCE assumes perfect retrieval and SummEdits uses controlled edits over 40 repeated source families. Keep verification opt-in. General-model unanimity, direct task-model classification, shallow structural stacking and evidence-local ranker/verifier intersection are rejected. Next investigate explicit typed argument, qualifier and temporal alignment on a new development source; do not add provider verification until an untouched external test cohort passes. |
| Retrieval semantics | Present-state ranking recognizes ordinary “what does” and “who is” queries without applying inferred recency when no explicit update exists. Decisions and instructions share the full semantic-node boost. Two consecutive full simulation runs passed 74/74; the complete live-PostgreSQL suite passed 3,268 tests. | Authored causal scenarios and regression tests. They do not establish held-out answer quality or optimal default weights. |
| Deferred raw-source throughput | Local processing now shares a durable lexical commit before acknowledging sources. A frozen 32-source real-model workflow reduced 32 commits to one, with identical candidates and contexts across serial/batch trials. | Small authored histories with warmed embeddings on one host under concurrent load; no competitive speed claim. Direct `store()` still indexes immediately. |
| Local embedding consistency | Cache residency and text grouping no longer change tested BGE vectors after `e297d08`; installed real-model and focused regression checks passed. | Individual inference costs throughput on short-text batches. No cross-hardware bitwise guarantee. |
| Matched raw retrieval | All 119 development questions completed against pinned Mem0 OSS. At 4K shared whole-turn packing, PRME source recall was 96.49% versus 93.27%; Mem0 led preferences. | Raw mode, frozen older PRME reference, shared evaluator packer and no answer generation. The 4K interval touches zero; no end-to-end leadership claim. |
| Matched raw-context answers | [Complete PRME–Hindsight comparison](../benchmarks/results/research/2026-09-12/HINDSIGHT-PRME-READER-RESULTS.md): Qwen 53/119 versus 52/119, Gemma 71/119 versus 56/119. All 714 logical judgments and native artifact chains verified. | Qwen's paired group interval includes zero. Both readers answer only 1/9 assistant questions correctly with PRME. Development cohort, custom local judge, raw profiles; no consistent overall leadership or default-promotion claim. |
| Held-out agent-trajectory answers | [Registered LongMemEval-V2 web-small comparison](../benchmarks/results/research/2026-09-14/LONGMEMEVAL-V2-WEB-UNSEEN-DETERMINISTIC-V1.md): PRME scored 80/149 (53.69%) versus 10/149 (6.71%) without memory, with 75 paired wins, 5 losses and a +37.58 to +55.70 point question-bootstrap interval. All rows, official deterministic scores, inputs, reader settings, pack/config bindings and zero-memory baseline behavior passed the fail-closed comparator. | One local Qwen 9B reader and one saved PRME artifact versus no memory. The filtered web-small cohort excludes judge-dependent categories, enterprise and eight previously inspected questions. Mean PRME context was 43,195 tokens. This establishes memory utility on the named cohort, not competitive leadership. |
| Agent-trajectory context efficiency | A strict local-Qwen [4K/8K/16K/32K curve](../benchmarks/results/research/2026-09-17/LONGMEMEVAL-V2-QWEN-BUDGET-CURVE.md) scored 50/58/71/76 of 149. DeepSeek's web development [32K/40K/48K curve](../benchmarks/results/research/2026-09-17/LONGMEMEVAL-V2-DEEPSEEK-UPPER-BUDGET-CURVE.md) favored 48K at 89/149 versus 80/149, but the fresh enterprise [32K/48K confirmation](../benchmarks/results/research/2026-09-17/LONGMEMEVAL-V2-DEEPSEEK-ENTERPRISE-HOLDOUT.md) scored 55/140 versus 54/140. The latter passed complete artifact, source, reader and question-paired verification. | The web cohorts were already observed. The enterprise confirmation uses one hosted reader alias whose remote weights are not immutable and is not a competitor result. Reject universal 48K promotion: it used 44.39% more reader tokens and lost five net static answers. Keep 32K as the quality preset; 16K remains the more efficient local-reader option. |
| PersonaMem-v2 pilot | All 96 questions completed and independently verified. Alpha .25 packing answered 42 correctly, density 36, score 41 and no memory 33. | Primary cluster interval includes zero; losses on other-person and health questions. Custom persona-hidden variant, one local reader, no default promotion. |
| Scoped retrieval learning | Explicit labels feed a deterministic observed-candidate proposal gate; fresh paired receipts with complete gold identities feed a separate full-retrieval gate. Immutable owner/exact-scope profiles persist and apply across restart with inspected activation, deactivation and rollback on both backends. | Authored functional and concurrency validation proves the gate and lifecycle contracts. No learned profile has yet passed a representative task holdout or established answer-quality improvement. |
| External lifecycle and scale workflows | The registered MELT run completed five seeds and all 20 lifecycle checkpoints at recall@12 and NDCG@12 of 1.000. The corrected [BEAM extracted-memory run](../benchmarks/results/research/2026-09-15/BEAM-100K-EXTRACTED-SCORED.md) scored 13/20 with a 0.56750 mean rubric score after schema-5 validation confirmed 188/188 raw materializations and extractions. A tuned [dual-representation ablation](../benchmarks/results/research/2026-09-15/BEAM-EVIDENCE-AUGMENTATION-ABLATION.md) scored 14/20 versus 13/20, but its [untouched confirmation](../benchmarks/results/research/2026-09-15/BEAM-EVIDENCE-AUGMENTATION-CONFIRMATION.md) scored 14/20 versus a fresh 15/20 baseline. A [non-entity diagnostic](../benchmarks/results/research/2026-09-15/BEAM-NON-ENTITY-AUGMENTATION-DIAGNOSTIC.md) fixed that contradiction loss but produced one different loss and a -0.03000 combined mean delta. A [frozen-context stability trial](../benchmarks/results/research/2026-09-16/BEAM-Q19-ANSWER-STABILITY.md) rejected a stable interpretation of the new loss: baseline passed 3/5, candidate 4/5, and both averaged 0.40. The [v1 answerability trial](../benchmarks/results/research/2026-09-16/BEAM-ANSWERABILITY-DEV-V1.md) failed every gate; the [speech-act-aware v2 rerun](../benchmarks/results/research/2026-09-16/BEAM-ANSWERABILITY-DEV-V2.md) still produced 2/12 unsafe full answers, 62/108 ordinary full answers, 5 citation errors, and stable actions on 26/40 questions. The [frozen-draft trial](../benchmarks/results/research/2026-09-16/BEAM-ANSWERABILITY-DRAFT-DEV-V1.md) then fully accepted 5/36 samples from incorrect drafts and only 13/84 from correct drafts, with 7 citation errors and stable actions on 22/40 questions. | MELT uses four held-out lifecycle cases and a deterministic embedding profile. BEAM evidence covers two 100K conversations with mutable hosted model aliases. Both augmentation policies and all three answerability trials are rejected as defaults. The answerability trials flattened typed metadata. The draft result also shows that cited entailment cannot resolve a benchmark's temporal target or prove exhaustive counts. An exploratory exact-source v12 probe abstained 5/5, isolating distractor-sensitive evidence-set reasoning as a remaining problem. No cross-product leadership is established. |
| AgentMemBench operational behavior | The preregistered official-size run returned the new fact first for 250/250 explicit updates, leaked no facts across 100 users, retired 200/200 archived memories from retrieval, and materialized 200/200 writes with zero errors at each of 1, 4, 8 and 16 workers. Recall@3 was 100% at both 100 and 1,000 records. | Synthetic operational workloads with local timings. Archive means retrieval retirement backed by immutable events, not physical erasure. The run exposed and led to a fixed USearch 2.23 native deletion stall. |
| AgentMemBench judged retrieval | The preregistered official-size run scored 979/1,000 recall@5 with a 97.0% to 98.7% bootstrap interval, including 98.8% personal-fact and 97.0% task-request recall. All 1,000 writes materialized. The fail-closed verifier bound the clean PRME/upstream revisions, dataset, operation journal, invocation, judge controls and exact local model digest. | One synthetic dataset and one local Qwen 35B A3B judge. Published system results use another judge setup, so their headline totals are not controlled comparisons. A post hoc audit found all 21 missed sources in the top ten; three rank-one sources did not literally state the reference answer and 18 ranked sixth through tenth. |
| MemoryAgentBench test-time learning | [Matched Banking77 development comparison](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-BANKING-STRICT-DEV20.md): PRME scored 20/20 versus BM25 at 17/20 under the official strict metric, while using 90.52% fewer retrieved-context tokens. The [episode regression](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-EPISODE-REGRESSIONS-DEV20.md) preserved PRME at 20/20 with all 220 routed promotions packed. | Twenty previously inspected development questions, one reader and one lexical control. The paired interval includes zero. The 5,897-record episode pack took 463.112 seconds to construct, so bulk typed ingestion remains a material cost. |
| MemoryAgentBench accurate retrieval | The initial [matched EventQA development comparison](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-EVENTQA-SESSION-DEV20.md) scored PRME at 16/20 versus BM25 at 20/20. The subsequent [two-stage episode-routing trial](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-EVENTQA-EPISODE-DEV20.md) scored 19/20 versus 20/20 while retaining the 90.48% context reduction. It changed three prior PRME failures to successes with no losses, and every durable artifact chain verified. | Twenty previously inspected development questions, one reader and one lexical control. The new paired interval versus BM25 is -15 to 0 points; the before/after interval is 0 to +30. The remaining miss had routed evidence but no verbatim answer. Cross-task development regressions passed; keep episode routing opt-in pending a larger cohort and another reader. |
| MemoryAgentBench conflict resolution | [Matched FactConsolidation development comparison](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-CONFLICT-ANSWERONLY-DEV20.md): PRME scored 1/20 versus BM25 at 0/20 under the shared answer-only contract. Reference-answer text appeared in 19 PRME contexts and all 20 BM25 contexts. A separately registered Qwen 35B A3B check produced the same scores; all artifact chains verified. | Twenty previously inspected development questions, two related local readers and one lexical control. Near-zero accuracy in both arms makes this a reader-interface failure, not a retrieval or conflict-resolution quality result. Audit numbered-source reasoning and evaluate PRME's actual correction lifecycle directly. |
| MemoryAgentBench long-range understanding | [Matched DetectiveQA development comparison](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-DETECTIVE-CHOICE-DEV20.md): PRME and BM25 each scored 13/20 under the official exact metric. The [episode regression](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-EPISODE-REGRESSIONS-DEV20.md) scored 15/20, with three gains and one loss against flat PRME while mean context stayed below 4K; every receipt verified. | Twenty previously inspected development questions, one local reader and separate revisions/query clocks. The episode before/after interval is -10 to +30 points. This supports broader testing of episodic routing, not a default or leadership claim. |

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
| Organizer replay coverage | Duplicate/alias merges, unverified alias proposals, TTL expiration, explicit reinforcement, single-node lifecycle transitions, explicit supersedence and consolidation retirement now atomically retain complete records on both backends. Deterministic pair/correction/retention identities survive retries; pairwise transitions reject unavailable owner/scope evidence. Legacy non-finite metadata has an explicit lossless snapshot encoding. Preexisting alias links are reused without invented history. Other organizer/manual mutations and historical data still lack complete replay inputs; extend coverage without inventing past events. External index cleanup remains a separate repairable step. |
| Consolidation quality | [Publication is now durable, atomic and idempotent](../benchmarks/results/recovery/2026-09-13/CONSOLIDATION-PUBLICATION.md), with deterministic request generations, restart recovery and cross-engine concurrency checks on both backends. Next: compare source-backed summaries with raw episodes under matched reader, token and ingestion-cost conditions. Atomic publication proves consistency, not summary usefulness. |
| Reliable compact semantic memory | Hierarchical source excerpts now publish atomically, recover without repeated embedding work, and refresh when selected period inputs change. Next: compare grounded extraction and source-backed summaries with raw-turn baselines under the same reader and token/cost conditions. Retain qualifiers, temporal boundaries, contradictions and provenance; consistency and compression ratio alone are insufficient. |
| Profile maintenance recovery | Durable prepared inputs, owner-scoped Python recovery, journal reconstruction and fenced replacement are implemented. Explicit abandonment and fenced collection of unpublished staging are implemented. Next: evaluated scheduled maintenance policy. No automatic profile scheduler is present. |
| Temporal and aggregate questions | Present-state and dated-history routing pass the causal simulation gate. The opt-in evidence-bound temporal relation path passed its disjoint answer confirmation, reproduced all 104 confirmation contexts through the product API, passed a 500-pack routing/context/receipt regression, and passed its preregistered 12-case live-provider preflight. Semantic counts/lists expose non-exhaustive coverage, every observed cap/failure/selection limit, and a token-counted model warning; stored-record enumeration remains explicit. Next: evaluate semantic qualification, real-item deduplication, multi-session updates and abstention together under a complete-item protocol. |
| Adaptive retrieval and memory credit | Immutable owner-scoped receipts, explicit relevance labels and answer-time citations preserve returned, packed and cited memory identities across restart. Exact packed-context ablation removes one cited entry without mutation or fill-in and assigns citation-checked signed presence credit under a named fixed evaluation protocol. The registered source-anchored development diagnostic produced 23 correct-to-wrong flips in 37 singly annotated contexts; its six redundant non-flips and one reference inconsistency show why missing flips cannot become negative labels. Scoped ranking profiles now require positive observed-candidate and fresh full-retrieval gates, bind exact owner/scope and runtime feature identity, and support append-only activation and rollback. Next: reproduce [Hindsight Memory-PRM](https://arxiv.org/abs/2608.29605) bank deletion and re-retrieval on fixed readers and held-out tasks, persist accepted interventions, and establish task-level gains before consuming intervention credit. A context ablation is narrower than a retrieval-invariant bank deletion. Global weights and retention must not silently consume one tenant's observations. See RFC-0017 and [the learning guide](LEARNING.md). |
| Developer experience | Installed-wheel sync/async workflows cover confirmation and lifecycle retries and [explicit corrections](MEMORY-CORRECTIONS.md), including retained sources, scoped evidence, full audit records, HTTP/MCP parity and exact retry safety. [Metadata admission](METADATA.md) rejects nonportable values and ambiguous JSON key collisions consistently, and preserves legacy values in journals. Built-in embedding configuration now infers registered model dimensions and rejects unknown dimensions before index startup. The unreplayed, unevaluated QA-pair heuristic is now explicit opt-in. Keep testing first write, restart, retrieval, recovery and migration without repository-only imports; these checks do not establish end-to-end memory quality. |
| Interactive task completion | The [MemoryArena adapter](../benchmarks/results/research/2026-09-12/AGENTIC-MEMORY-INTEGRATION.md) passed the unmodified upstream client with installed PRME, real embeddings, task isolation and restart provenance. The corrected travel trial then completed a fresh 12-group [confirmation](../benchmarks/results/research/2026-09-17/MEMORYARENA-TRAVEL-DEEPSEEK-CONFIRM12-V1.md): PRME used 19.76% fewer input tokens but scored 0.00 strict PS and 65.12 SPS versus native history at 7.69 and 80.24, failing both -5-point non-inferiority gates. Post-hoc localization found one exact-value rendering failure contributed 40 of the net 56 lost slots even though the full base plan was retrieved. Two prompt-only repairs passed their broad development gates but failed targeted inspection: [V12](../benchmarks/results/research/2026-09-17/MEMORYARENA-TRAVEL-DEEPSEEK-DEV12-V12.md) preserved 235/318 qualified values and caused 161 qualified tool calls; [V13](../benchmarks/results/research/2026-09-17/MEMORYARENA-TRAVEL-DEEPSEEK-DEV12-V13.md) preserved 236/318 and caused 75, versus pre-change V11 at 239/318 and 55. | Reject V11 as a generally non-inferior replacement and reject prompt-only value-fidelity repair. Represent canonical presentation values and tool lookup values as separate typed data, or enforce the distinction at the tool boundary, and require a targeted development gain before selecting a new confirmation cohort. The mutable hosted model alias, one generation per arm and exact-string scorer prevent broader claims. |

The first registered held-out LongMemEval-V2 answer comparison is complete. Its
large gain over no memory clears the memory-utility gate for this cohort, while
its 43K-token mean context makes context compression an immediate product and
evaluation priority. A subsequent strict local-Qwen curve scored 50, 58, 71 and
76 of 149 at 4K, 8K, 16K and 32K. DeepSeek improved from 80/149 at 32K to 89/149
at 48K on the observed web cohort, but a preregistered enterprise confirmation
scored 55/140 at 32K and 54/140 at 48K while the larger budget used 44.39% more
reader tokens. Keep 32K as the quality preset, retain 16K as the more efficient
local-reader choice, and reject universal 48K promotion. Add matched current
memory baselines, another reader family and the excluded judge-dependent
categories before generalizing answer quality.

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
real-data ingress smoke across all four families pass. The first corrected
[Banking77 result](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-BANKING-STRICT-DEV20.md)
scored PRME at 20/20 versus BM25 at 17/20 while reducing mean retrieved context
from 41,925 to 3,975 tokens. Its three paired wins and zero losses are encouraging,
but its interval includes zero and the 468-second PRME pack build exposes a bulk
ingestion gap. The subsequent matched
[EventQA result](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-EVENTQA-SESSION-DEV20.md)
scored PRME at 16/20 versus BM25 at 20/20 despite a 90.48% retrieved-context
reduction. Its missing packed evidence and failed larger-budget diagnostic make
source-cited episodic reconstruction the next retrieval-quality experiment. The
matched [Conflict Resolution result](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-CONFLICT-ANSWERONLY-DEV20.md)
scored 1/20 versus 0/20 despite reference-answer text appearing in 19/20 and
20/20 contexts, respectively. The installed Qwen 35B A3B reader produced the
same scores in a separately registered check, ruling out capacity alone. Audit
the numbered-source interface and evaluate PRME's actual correction lifecycle.
The matched [DetectiveQA result](../benchmarks/results/research/2026-09-14/MEMORYAGENTBENCH-DETECTIVE-CHOICE-DEV20.md)
completed the first four-family development pass: both arms scored 13/20 while
PRME reduced retrieved context by 90.52%. Expand the preregistered cohorts and
reader families, add evidence-aware retrieval diagnostics, and improve typed
bulk ingestion before broadening any claim. Treat the test-time-learning arm as
retrieved in-context demonstrations and its conflict arm as numbered-source
resolution; separate experiments are still required for scoped ranking-profile
learning and transactional graph supersedence.

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

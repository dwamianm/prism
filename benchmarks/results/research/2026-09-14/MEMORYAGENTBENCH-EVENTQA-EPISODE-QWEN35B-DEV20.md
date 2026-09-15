# MemoryAgentBench EventQA episode-routing 35B development comparison

**Completed:** 2026-09-14  
**Systems:** PRME with two-stage episode routing versus the pinned MemoryAgentBench BM25 control  
**Reader:** local Ollama `prme-qwen3.5:35b-a3b-8k`, temperature 0, seed 42, thinking disabled  
**Questions:** the same first 20 registered EventQA development questions used by the 9B trial  
**Scoring:** the benchmark's official `substring_exact_match`

## Result

PRME answered 19 of 20 questions correctly and the official ten-document BM25
control answered 14. PRME had six paired wins, one loss and 13 ties, an observed
+25-point difference. The question-bootstrap 95% interval is 0 to +50 points and
the exact two-sided McNemar p-value is 0.125. This small, previously inspected
development slice does not establish a population-level difference.

| System | Exact | Retrieved context / question | Reader input / question |
|---|---:|---:|---:|
| PRME episode route | 19/20 (95%) | 3,972.30 tokens | 4,549.80 tokens |
| BM25, 10 documents | 14/20 (70%) | 41,738.05 tokens | 4,098.00 tokens |
| BM25, 1 document follow-up | 17/20 (85%) | 4,159.65 tokens | 4,452.50 tokens |

PRME used 90.48% fewer retrieved-context tokens than the official BM25 control.
The BM25 control supplied about 41.7K verified retrieval tokens to an 8K-profile
reader, while the provider reported only about 4.1K input tokens. Its 70% score
therefore measures the end-to-end behavior of an oversized retrieval result at
this bounded reader; it is not an isolated comparison of intrinsic BM25 recall.

After observing that boundary, a separately registered post hoc BM25 arm used
one document and about the same context as PRME. It scored 17/20. Against this
budget-matched diagnostic, PRME had three wins, one loss and 16 ties, an observed
+10-point difference. The bootstrap interval is -10 to +30 points and McNemar
p is 0.625. PRME used 4.50% fewer retrieval tokens. The diagnostic was selected
after the official-control result and is not a preregistered confirmation.

## Protocol and verification

The PRME arm set `episode_context_top_k=2`, `episode_context_local_k=8`, and
`episode_context_score_decay=0.95`. It reconstructed 381 ordered records from 17
source chunks, used deterministic two-stage BM25 episode routing, and packed the
selected evidence under a 4,096-token budget without another model call.

Every arm was registered before its own reader inference against PRME
`0e5eeb203494878d47e7f6545e005f7de74bedb2`, upstream MemoryAgentBench
`fe1735de8cf8b9908e1e3d3b5612afc815698062`, and dataset revision
`7ea066982b140a19337e17e60d45d4076e042faf`. The registrations bind source,
question, answer, retrieval-query, preprocessing, configuration, adapter and
harness identities. The PRME verifier authenticated all 20 durable retrieval
receipts, ranking replays, source maps, scopes, candidate identities, episode
settings, token counts and rendered contexts. The BM25 verifier independently
rebuilt all lexical selections. Only aggregate verification and comparison
artifacts are published; questions, answers, contexts and model outputs remain
outside the repository.

The serving model's observed digest was
`45870b70b6fa65ab09355aeb7901897763ae40745c6cdd11d28bc33a6b818ffc`.
The registrations bind the model tag rather than the provider digest.

## Reader checks and exclusions

The earlier 9B trial on the same questions scored PRME at 19/20 and BM25 at
20/20. PRME therefore retained 95% observed accuracy across two Qwen model
sizes, while the control changed under the reader's context boundary. This is a
second reader configuration within one model family, not an independent reader
family confirmation.

A registered `gpt-4o-mini` run was attempted first, but the service returned
HTTP 429 `credit_balance_exhausted` on its first generation call. No scored
OpenAI arm exists. The first local 35B attempt mistakenly left thinking enabled;
both arms exhausted the 40-token answer allowance on reasoning and scored zero.
That protocol-invalid trial was discarded, and the corrected arms used new run
identities with `reader_reasoning_effort: none`.

## Claim boundary

This comparison covers one inspected development task, 20 questions, one local
model family, and lexical controls. The official control exceeded the reader's
context window, while the post hoc control matched the context budget only
approximately. The result supports episode routing and compact evidence
selection on this cohort. It does not establish general memory-system
leadership or justify enabling episode routing by default without a larger,
held-out, cross-family confirmation.

Artifacts: [official-control comparison](memoryagentbench-eventqa-episode-qwen35b-dev20-comparison.json),
[budget-matched diagnostic](memoryagentbench-eventqa-episode-qwen35b-budgeted-bm25-dev20-comparison.json),
[PRME verification](memoryagentbench-eventqa-episode-qwen35b-dev20-prme-verification.json),
[official BM25 verification](memoryagentbench-eventqa-episode-qwen35b-dev20-bm25-verification.json),
and [budgeted BM25 verification](memoryagentbench-eventqa-episode-qwen35b-budgeted-bm25-dev20-verification.json).

# MemoryAgentBench Banking77 matched development comparison

**Completed:** 2026-09-14  
**Systems:** PRME versus the pinned MemoryAgentBench BM25 control  
**Reader:** local Ollama `qwen3.5:9b`, temperature 0, seed 42, thinking disabled  
**Questions:** first 20 preregistered Banking77 test-time-learning questions  
**Scoring:** the benchmark's official strict `exact_match`

## Result

PRME answered all 20 questions correctly. BM25 answered 17. PRME had three
paired wins, no losses and 17 ties, a 15-point observed difference. The
question-bootstrap 95% interval is 0 to 30 points and the exact two-sided
McNemar p-value is 0.25. This small development slice therefore does not
establish a population-level accuracy advantage.

| System | Exact | Retrieved context / question | Reader input / question |
|---|---:|---:|---:|
| PRME | 20/20 (100%) | 3,975.15 tokens | 5,078.90 tokens |
| BM25 | 17/20 (85%) | 41,924.90 tokens | 44,133.15 tokens |

PRME used 90.52% fewer retrieved-context tokens and 88.49% fewer total reader
input tokens on these questions. Its memory context remained below the declared
4,096-token budget on every query. PRME's mean end-to-end query time was 4.80
seconds and BM25's was 64.02 seconds, but the arms ran sequentially on one local
host, so these timings are descriptive rather than a controlled latency claim.

## Protocol and verification

Both arms were registered before inference against PRME
`e9fde2d53f56584d5b357a6ea22c7fb9bd50c2d2`, upstream MemoryAgentBench
`fe1735de8cf8b9908e1e3d3b5612afc815698062` and dataset revision
`7ea066982b140a19337e17e60d45d4076e042faf`. Registrations bind the same 26
source chunks, 20 question and answer identities, derived retrieval questions,
preprocessing versions, reader controls and `numeric-label-v1` output contract.
That contract appends the same digits-only response instruction to both arms to
resolve the pinned harness's conflict between its prompt format and strict
digits-only scorer; saved model outputs were not rewritten.

PRME reconstructed the source stream as 5,897 single-label records, retrieved a
mean 62.25 records per question and persisted a replayable receipt for every
query. Its verifier reopened the finished pack read-only and authenticated all
receipt checksums, ranking replays, scopes, candidate identities and rendered
contexts. The BM25 verifier independently rebuilt every registered ranking and
matched all 200 retrieved documents. The paired comparator additionally rejects
cross-arm output-contract and derived-query drift. The adjacent verification
and comparison artifacts contain exact code, configuration, result and capture
hashes without publishing benchmark answers.

The serving model's local inventory digest observed after completion was
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
It was not included in the preregistration, so the registered model identity is
the `qwen3.5:9b` tag. Future launches should bind the provider model digest before
generation.

## Cost and claim boundary

PRME spent 467.549 seconds constructing the durable 5,897-record pack before
answering. BM25 prepared 26 coarse documents. Those representations are the
systems under test, but the difference makes bulk typed-memory ingestion an
immediate performance target. Faster query-time reading does not erase PRME's
larger ingestion cost.

This is a development result on one classification task, one local reader and
one lexical control. The questions and earlier outcomes had been inspected
during adapter diagnosis. BM25 is not a feature-equivalent memory product, and
the paired interval includes zero. The result demonstrates that PRME can retain
perfect answer accuracy on this named slice while using about one tenth of the
retrieved context. It does not establish market leadership.

Artifacts: [paired comparison](memoryagentbench-banking-strict-dev20-comparison.json),
[PRME verification](memoryagentbench-banking-strict-dev20-prme-verification.json),
and [BM25 verification](memoryagentbench-banking-strict-dev20-bm25-verification.json).

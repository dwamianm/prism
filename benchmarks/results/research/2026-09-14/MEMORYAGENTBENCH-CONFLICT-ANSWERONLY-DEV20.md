# MemoryAgentBench Conflict Resolution matched development comparison

**Completed:** 2026-09-14  
**Systems:** PRME versus the pinned MemoryAgentBench BM25 control  
**Reader:** local Ollama `qwen3.5:9b`, temperature 0, seed 42, thinking disabled  
**Questions:** first 20 preregistered FactConsolidation 6K conflict-resolution questions  
**Scoring:** the benchmark's official `substring_exact_match`

## Result

PRME answered 1 of 20 questions correctly and BM25 answered none. PRME had one
paired win, no losses and 19 ties, a positive 5-point observed difference. The
question-bootstrap 95% interval is 0 to 15 points and the exact two-sided
McNemar p-value is 1.0. Near-zero accuracy in both arms makes this a failed
reader-task trial, not evidence of a useful system advantage.

| System | Exact | Retrieved context / question | Reader input / question |
|---|---:|---:|---:|
| PRME | 1/20 (5%) | 3,966.40 tokens | 5,261.40 tokens |
| BM25 | 0/20 (0%) | 6,642.00 tokens | 7,658.25 tokens |

PRME used 40.28% fewer retrieved-context tokens and 31.30% fewer total reader
input tokens. Its memory context remained below the declared 4,096-token budget
on every query. PRME's mean end-to-end query time was 4.83 seconds and BM25's
was 2.62 seconds, but the arms ran sequentially on one local host, so these
timings are descriptive rather than a controlled latency claim. PRME ingested
the two source chunks as 454 records in 35.134 seconds.

## Protocol and verification

Both arms were registered before inference against PRME
`4487d955687ad38cd0e783b111f460ac9292bdf3`, upstream MemoryAgentBench
`fe1735de8cf8b9908e1e3d3b5612afc815698062` and dataset revision
`7ea066982b140a19337e17e60d45d4076e042faf`. Registrations bind the same two
source chunks, 20 question and answer identities, derived retrieval questions,
preprocessing versions, reader prompts and generation controls. Both arms used
the registered `answer-only-v1` contract, which adds one shared concise-answer
instruction and leaves saved model output unchanged.

PRME persisted a replayable receipt for every query. Its verifier reopened the
finished pack read-only and authenticated all receipt checksums, ranking
replays, scopes, candidate identities and rendered contexts. The BM25 verifier
independently rebuilt every registered ranking and matched all 40 retrieved
documents. The paired comparator rejects reader, output-contract and
derived-query drift. The adjacent artifacts contain exact code, configuration,
result and capture hashes without publishing benchmark answers.

The serving model's local inventory digest observed after completion was
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
It was not included in the preregistration, so the registered model identity is
the `qwen3.5:9b` tag. Future launches should bind the provider model digest
before generation.

## Failure analysis and next technique

A post hoc normalized substring check found reference-answer text in 19 of 20
PRME contexts and all 20 BM25 contexts. The reader answered only one of those 39
answer-bearing cases correctly. This diagnostic used references after scoring,
so it explains the failure and is not an additional benchmark metric or a
selectable retrieval policy.

The result isolates reader reasoning and task interpretation as the dominant
failure for this configuration. The next trial should use the already-installed
Qwen3.5 35B A3B model with the identical sources, output contract and matched
arms. Retrieval changes are not justified by a task where the reader fails even
when the reference text is present. Separately, conflict-resolution evaluation
must distinguish this benchmark's numbered-source multi-hop QA from PRME's
transactional correction and supersedence APIs.

## Claim boundary

This is one development task, one local reader and one lexical control. BM25 is
not a feature-equivalent memory product. The result demonstrates that the Qwen
9B configuration is inadequate for this task despite high answer-text coverage.
It does not establish conflict-resolution quality, a statistically reliable
advantage, or competitive leadership.

Artifacts: [paired comparison](memoryagentbench-conflict-answeronly-dev20-comparison.json),
[PRME verification](memoryagentbench-conflict-answeronly-dev20-prme-verification.json),
and [BM25 verification](memoryagentbench-conflict-answeronly-dev20-bm25-verification.json).

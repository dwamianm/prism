# MemoryAgentBench DetectiveQA matched development comparison

**Completed:** 2026-09-14  
**Systems:** PRME versus the pinned MemoryAgentBench BM25 control  
**Reader:** local Ollama `qwen3.5:9b`, temperature 0, seed 42, thinking disabled  
**Questions:** first 20 preregistered DetectiveQA long-range-understanding questions  
**Scoring:** the benchmark's official `exact_match`

## Result

PRME and BM25 each answered 13 of 20 questions correctly. PRME had one paired
win, one loss and 18 ties, for no observed accuracy difference. The
question-bootstrap 95% interval for PRME minus BM25 is -15 to +15 points and the
exact two-sided McNemar p-value is 1.0. This is accuracy parity on the selected
development slice, not evidence that the systems are equivalent in general.

| System | Exact | Retrieved context / question | Reader input / question |
|---|---:|---:|---:|
| PRME | 13/20 (65%) | 3,979.70 tokens | 4,835.95 tokens |
| BM25 | 13/20 (65%) | 41,974.40 tokens | 45,055.85 tokens |

PRME used 90.52% fewer retrieved-context tokens and 89.27% fewer total reader
input tokens. Its memory context remained below the declared 4,096-token budget
on every query. PRME's mean query time was 5.41 seconds and BM25's was 70.89
seconds, but the arms ran sequentially on one local host, so these timings are
descriptive rather than a controlled latency claim. PRME indexed the three
source contexts as 1,620 records in 143.371 seconds total.

## Protocol and verification

Both arms were registered before inference against PRME
`3dd5da227f89d5b8f61ad9c09ba04c685e9a2d80`, upstream MemoryAgentBench
`fe1735de8cf8b9908e1e3d3b5612afc815698062` and dataset revision
`7ea066982b140a19337e17e60d45d4076e042faf`. Registrations bind the same 75
source chunks, 20 question and answer identities, derived retrieval questions,
preprocessing versions, reader prompts and generation controls. Both arms used
the registered `choice-only-v1` contract, which requests exactly one
`A. choice text` line and leaves saved model output untouched.

PRME persisted a replayable receipt for every query. Its verifier reopened the
finished pack read-only and authenticated all receipt checksums, ranking
replays, scopes, candidate identities and rendered contexts. The BM25 verifier
independently rebuilt every registered ranking and matched all 200 retrieved
documents. The paired comparator rejects reader, output-contract and
derived-query drift. The adjacent artifacts contain exact code, configuration,
result and capture hashes without publishing benchmark answers.

The serving model's local inventory digest observed after completion was
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
It was not included in the preregistration, so the registered model identity is
the `qwen3.5:9b` tag. Future launches should bind the provider model digest
before generation.

## Failure analysis and next technique

The secondary substring score was 15/20 for PRME and 16/20 for BM25. Two PRME
misses and three BM25 misses therefore contained a reference answer but violated
the exact output contract by adding text or structure. They remain wrong under
the official metric; no parser or post-processing rewrote the responses.

A post hoc normalized substring check found the reference-answer text in 5 of
20 PRME contexts and 6 of 20 BM25 contexts. DetectiveQA requires deductions
from narrative evidence, so this is not a recall measure: an answer can be
supported without appearing verbatim, and a mentioned option can still be
wrong. Isolating retrieval quality requires evidence-labelled questions or a
controlled reader comparison, rather than treating answer-string presence as
ground truth.

The compact PRME context preserved the matched BM25 answer score while using
about one tenth of its retrieved tokens. The next evidence should come from a
larger preregistered cohort, a second reader family and an evidence-aware
retrieval diagnostic. The EventQA result separately shows that episodic
reconstruction remains a measured gap, so this tie does not justify changing
retrieval defaults.

## Claim boundary

This is one previously inspected development task, one local reader and one
lexical control. BM25 is not a feature-equivalent memory product, and the paired
interval is wide. The result supports compact-context efficiency on this named
slice. It does not establish statistical equivalence, general long-range
reasoning quality or competitive leadership.

Artifacts: [paired comparison](memoryagentbench-detective-choice-dev20-comparison.json),
[PRME verification](memoryagentbench-detective-choice-dev20-prme-verification.json),
and [BM25 verification](memoryagentbench-detective-choice-dev20-bm25-verification.json).

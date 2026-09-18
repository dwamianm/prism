# MemoryAgentBench EventQA matched development comparison

**Completed:** 2026-09-14  
**Systems:** PRME versus the pinned MemoryAgentBench BM25 control  
**Reader:** local Ollama `qwen3.5:9b`, temperature 0, seed 42, thinking disabled  
**Questions:** first 20 preregistered EventQA accurate-retrieval questions  
**Scoring:** the benchmark's official `substring_exact_match`

## Result

PRME answered 16 of 20 questions correctly. BM25 answered all 20. PRME had no
paired wins, four losses and 16 ties, a negative 20-point observed difference.
The question-bootstrap 95% interval is -40 to -5 points and the exact two-sided
McNemar p-value is 0.125. The observed loss is material, but this small,
previously inspected development slice does not establish a population-level
accuracy difference.

| System | Exact | Retrieved context / question | Reader input / question |
|---|---:|---:|---:|
| PRME | 16/20 (80%) | 3,972.50 tokens | 4,551.50 tokens |
| BM25 | 20/20 (100%) | 41,738.05 tokens | 41,968.00 tokens |

PRME used 90.48% fewer retrieved-context tokens and 89.15% fewer total reader
input tokens. Its memory context remained below the declared 4,096-token budget
on every query. PRME's mean end-to-end query time was 4.52 seconds and BM25's
was 62.36 seconds, but the arms ran sequentially on one local host, so these
timings are descriptive rather than a controlled latency claim.

## Protocol and verification

Both arms were registered before inference against PRME
`555aad70a47c1f728219397c4ad0bc6bdd49d75b`, upstream MemoryAgentBench
`fe1735de8cf8b9908e1e3d3b5612afc815698062` and dataset revision
`7ea066982b140a19337e17e60d45d4076e042faf`. Registrations bind the same 17
source chunks, 20 question and answer identities, derived retrieval questions,
preprocessing versions, reader prompts and generation controls. The run used
the upstream reader contract without response rewriting.

PRME reconstructed the source stream as 381 ordered records, used the fixed
session-neighborhood implementation, and persisted a replayable receipt for
every query. Its verifier reopened the finished pack read-only and authenticated
all receipt checksums, ranking replays, scopes, candidate identities and rendered
contexts. The BM25 verifier independently rebuilt every registered ranking and
matched all 200 retrieved documents. The paired comparator rejects reader,
output-contract and derived-query drift. The adjacent artifacts contain exact
code, configuration, result and capture hashes without publishing benchmark
answers.

The serving model's local inventory digest observed after completion was
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
It was not included in the preregistration, so the registered model identity is
the `qwen3.5:9b` tag. Future launches should bind the provider model digest before
generation.

## Failure analysis and next technique

The exact reference-answer text was absent from each of PRME's four failed
packed contexts. A post hoc inspection of all 381 scored records found the
strongest answer-token-overlap record at ranks 14, 69, 138 and 248. Testing the
four misses at 4K, 8K and 16K showed that a larger flat token budget alone still
failed to include answer-bearing records consistently. These diagnostics use
the reference answers after scoring and therefore explain failures; they are not
a selectable retrieval policy or additional benchmark score.

The result exposes a specific gap: flat fragment ranking and greedy packing can
discard the surrounding episode even when the durable store contains it. The
next evaluated design should retain raw episodic blocks, route with summary,
semantic and lexical signals, then extract source-cited evidence locally before
the final pack. This follows the general episodic-context reconstruction pattern
described by [E-mem](https://arxiv.org/abs/2601.21714), while PRME must preserve
its owner/scope isolation, deterministic stored artifacts and auditable
provenance. No default should change until a registered matched-reader trial
shows answer gains without category regressions.

## Claim boundary

This is one development task, one local reader and one lexical control. BM25 is
not a feature-equivalent memory product and used about ten times the retrieved
context. The result demonstrates that PRME's compact context loses important
episodic evidence on this named slice. It does not support a competitive quality
leadership claim, and the context reduction does not compensate for the four
wrong answers.

Artifacts: [paired comparison](memoryagentbench-eventqa-session-dev20-comparison.json),
[PRME verification](memoryagentbench-eventqa-session-dev20-prme-verification.json),
and [BM25 verification](memoryagentbench-eventqa-session-dev20-bm25-verification.json).

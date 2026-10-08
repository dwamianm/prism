# Jev owner-memory admission: first short development assay

On 2026-10-08 the frozen 40-case authored assay passed its proposed development
screen with `jev-1.13.0`: zero false admissions among 20 negative cases, 18/20
valid claims retained, and 20/20 valid claim kinds classified correctly.
This warrants a fresh bounded confirmation, not automatic production adoption.

| Arm | Unsafe admitted / 20 | Valid retained / 20 | Meaning |
| --- | ---: | ---: | --- |
| Local source-support validator + user role | 12 | 20 | Deliberately supplied claim proposals, not full extraction |
| Jev support + attribution >= 0.9, retaining local checks | 0 | 18 | Frozen development screen |
| Same cache, threshold 0.8 | 0 | 19 | Post-hoc threshold exploration |

The local validator comparison measures one source-support function on supplied
proposals. It is not PRME's overall unsafe-memory rate: the full extraction
provider, reference validation, recovery, durable plans and retrieval filters are
not evaluated here. Role and verbatim-evidence failures are rejected by code.

All 40 responses were valid with no provider failures. There was one bounded
collection, with at most four concurrent requests and no retries. Median request
latency was **0.143 seconds**, p95 **0.339 seconds**. The run used 26,161 input
tokens and 3,298 output tokens. These are request timings, not an end-to-end
ingestion or retrieval latency measurement. The offline integrity tests passed
11/11 in 0.11 seconds.

At the frozen 0.9 threshold Jev declined:

- “Call me Sam” -> “I prefer to be called Sam”: support 0.73, attribution 0.90.
- “Please call me Sam, and keep answers short” -> “I prefer short answers”:
  support 0.86, attribution 0.95.

Lowering the threshold to 0.8 recovered the second case using the same saved
probabilities, without provider calls. It still lost the first. This is examined
development evidence; thresholds must be frozen before new confirmation cases.

Every case is authored synthetic development text. Twenty negative examples
without a false admission still permit a roughly 14% one-sided 95% binomial upper
bound. The experiment does not establish semantic reliability, answer gains or a
safe universal threshold. Classification was measured on the supplied claim,
with a speculative support premise; storage consumes no category yet.

The next test should use new, source-labeled conversations with a frozen policy,
then compare required and unwanted claims in actual packed retrieval context at
matched budgets. Preserve the two imperative failures as development cases.
Keep all production defaults and the confirmed product-advisor rule unchanged.

Artifacts: [registration](jev-memory-admission-dev-v1-registration.json),
[complete cached judgments](jev-memory-admission-dev-v1-cache.json),
[frozen result](jev-memory-admission-dev-v1-result.json), and
[exploratory 0.8 replay](jev-memory-admission-dev-v1-threshold08.json).
The registration names implementation commit `c3365104`, complete source inputs,
questions, thresholds and hashes. Cached judgments bind each exact request.

Reproduce offline from the repository root:

```sh
python -m benchmarks.diagnostics.jev_memory_admission score \
  --registration benchmarks/results/research/2026-10-08/jev-memory-admission-dev-v1-registration.json \
  --cache benchmarks/results/research/2026-10-08/jev-memory-admission-dev-v1-cache.json \
  --output /tmp/admission-replay.json
```

See the [assay protocol](../../../../docs/MEMORY-ADMISSION-ASSAY.md).

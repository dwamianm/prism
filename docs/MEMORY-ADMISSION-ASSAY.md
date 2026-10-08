# Fast memory-admission experiments

Use a small, source-labeled assay to reject a weak theory before paying the time
cost of full extraction and answer benchmarks. The first theory is an optional
Jev gate over an existing proposed claim. This experiment is separate from the
confirmed product-alignment advisor and never changes its protocol or thresholds.

The gate retains PRME's local source-support validator and requires a user role.
Jev then judges complete claim support and attribution to the author, and chooses
fact, preference, decision or other as an independent classification. A supported
intention is different from a claim that the intended action has happened. A
question is different from a medical diagnosis. A pasted first-person letter is
different from the owner's own statement. These distinctions are the target of
this assay, rather than general world knowledge.

Code checks literal evidence and existing grounding rules. Jev sees the complete
source, proposed claim and evidence quote, without fixture IDs or expected labels.
All independent judgments run in one request. The model is pinned to
`jev-1.13.0`; source, questions, implementation, registration and cached requests
have identities. The assay makes no memory writes and does not produce extraction
strings. It only tests a decision over supplied candidates, so it cannot measure
claims the extractor never proposed or fix a missing candidate.

## Run the short development assay

The committed corpus has 40 authored contrasts, with 20 admissible claims and 20
inadmissible claims. It includes the Kio reports and valid controls, questions,
conditions, third-party text, assistant/system roles, failed attempts, negation,
mixed messages and missing evidence. These cases are examined development data,
not held-out accuracy or a population error-rate estimate.

```sh
python -m benchmarks.diagnostics.jev_memory_admission register \
  --registration /tmp/admission-registration.json

# Explicit provider step: at most 40 requests, four concurrent, one attempt each.
# Uses JEV_API_KEY or TYPESAFE_API_KEY from the environment or current .env.
python -m benchmarks.diagnostics.jev_memory_admission collect \
  --registration /tmp/admission-registration.json \
  --cache /tmp/admission-cache.json --output /tmp/admission-result.json

# No provider calls: reuse the exact raw probabilities under another threshold.
python -m benchmarks.diagnostics.jev_memory_admission score \
  --registration /tmp/admission-registration.json \
  --cache /tmp/admission-cache.json --output /tmp/admission-threshold-08.json \
  --threshold 0.8
```

The frozen screening threshold is 0.9 for both support and attribution, with zero
false admissions, at least 90% valid retention and at least 90% claim-kind accuracy
on supported cases required. This is a proposed
development screen, not an established reliability guarantee. Non-frozen threshold
results are marked exploratory. A service error or missing result blocks passing
and counts a positive case as lost. Completed results, including errors, are
cached; a resumed collection does not silently retry them. Use a separate cache
for a deliberately repeated run and retain every result.

Reports separate false admissions, valid claims lost, abstentions, category
accuracy on supported cases, family-level errors, token usage and median/p95
request latency. The comparison is with the local support validator under a user
role restriction, not the full extraction/retrieval pipeline. It does not include
entity-reference closure, durable plan publication, claim recovery or provider
proposal accuracy. Malformed evidence and foreign source roles remain rejected
by code even if Jev is confident.

## Move a surviving theory through progressively larger tests

1. Run deterministic fixture and cache-integrity tests locally; these check the
   harness, not model performance.
2. Run the 40-case model assay and inspect errors. Replay cached probabilities for
   prompt-independent threshold exploration. Changing evidence, model or question
   meanings requires new inference and registration.
3. Freeze a candidate policy, then test new source-labeled conversations without
   choosing thresholds on their results. Split by conversation/family so near
   duplicates cannot leak from development into confirmation. Include both claims
   to keep and claims to reject. A small zero-error sample cannot establish a low
   production error rate; with 20 negative cases, zero false admissions still has
   a roughly 14% one-sided 95% binomial upper bound.
4. Run a bounded, matched end-to-end trial on those conversations: actual
   extraction, storage and retrieval under identical budgets. Check both unwanted
   facts in packed context and required evidence lost. Preserve exact outputs and
   receipts; a better classifier can still make retrieval worse.
5. Run the full answer track only for candidates that survive these screens.
   Production retrieval defaults retain the existing promotion requirements.

The first two stages should provide a short iteration loop; actual elapsed time
must be measured. The full benchmark is a final promotion test rather than the
first way to discover an unsupported theory. Latency claims in vendor materials
are not timings of this application.

TypeSafe's typed output constrains the interface; it does not prove semantic
correctness. Choice confidence summarizes distribution concentration, and Noul
returns the probability of yes. Keep raw probabilities for offline decisions,
and validate thresholds on the intended task. See the
[introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev),
[confidence documentation](https://docs.typesafe.ai/confidence), and
[citation-check example](https://docs.typesafe.ai/cookbooks/citation_check).

PRME's earlier 400-case document-support Jev development trial failed its safety
gates ([record](../benchmarks/results/research/2026-09-17/jev-claim-verification-development-v1-result.json)).
That result remains evidence against those rules on that distribution. A narrow
memory-admission trial asks a different question and must earn its own evidence.

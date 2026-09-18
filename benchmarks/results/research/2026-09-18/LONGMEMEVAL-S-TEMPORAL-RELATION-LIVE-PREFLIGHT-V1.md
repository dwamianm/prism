# LongMemEval-S temporal relation live preflight v1

**Status:** Passed  
**Date:** 2026-09-18  
**Registered implementation revision:** `52d91f2764b0b1fc6a99407e422a6f32c3ea8bbe`  
**Execution revision:** `041bad716ca263e4e9c7b0369d45b2e574cd2331`

## Question

Does the opt-in temporal relation composition remain stable, fail safely, and
produce auditable cost and latency observations when the current product path
uses the live pinned Ollama DeepSeek resolver and TypeSafe Jev gate?

## Protocol

The sample and gates were committed before any live provider call. The 12 fixed
frozen LongMemEval-S packs comprised two previously accepted relations, two
known rejection controls, one temporal-category no-call control, six routed
shapes that had only received an inert resolver in the 500-pack regression, and
one ordinary preference no-call control.

The runner reopened each pack through `MemoryEngine.retrieve()`. It required the
frozen candidate ranking, original control-context hash, exact persisted
receipt metadata, registered provider configuration, pinned Ollama digest, and
one durable outcome per case. Known accepted controls had to retain their
operation, locally computed value, and evidence IDs. Known rejected controls
could not change context. No-call controls had to remain provider-free and byte
identical. Any newly accepted relation required a separate evidence review.
Credentials and raw provider messages or responses were excluded from state and
result artifacts.

The preregistration is
[longmemeval-s-temporal-relation-live-preflight-v1-registration.json](longmemeval-s-temporal-relation-live-preflight-v1-registration.json).

## Result

| Check | Result |
| --- | ---: |
| Cases completed | 12 / 12 |
| Automatic case gates passed | 12 / 12 |
| Unhandled failures | 0 |
| Provider errors | 0 |
| Routed resolver calls | 10 |
| Provider-free no-call controls | 2 / 2 |
| Known accepted relations reproduced | 2 / 2 |
| Known rejection controls preserved | 2 / 2 |
| Novel relations accepted | 0 |
| Manual reviews required | 0 |

The final statuses were two `accepted`, two `gate_rejected`, four
`validation_rejected`, two `unsupported`, and two `not_routed`. Both accepted
controls reproduced the prior result context byte for byte, including the
28-day chandelier relation and 17-day Super Bowl relation. The museum ambiguity
remained `gate_rejected`; the MoMA/Metropolitan Museum case remained
`validation_rejected`. Jev rejected the one valid novel proposal, a 10-minute
remedy duration, at `0.66`. The other five novel routes failed local validation
or returned `unsupported`, so none altered the packed context.

All ten resolver calls used `deepseek-v4.1-flash:cloud` through Ollama `0.34.2`
with digest
`e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`.
All calls completed in one transport attempt with zero schema repairs. All four
Jev calls used `jev-1.13.0` and completed in one attempt.

## Observed usage and latency

| Observation | Result |
| --- | ---: |
| Resolver input tokens | 28,205 |
| Resolver output tokens | 1,067 |
| Jev input tokens | 1,772 |
| Jev output tokens | 88 |
| Resolver median / p95 | 679 ms / 1,160 ms |
| Jev median / p95 | 382 ms / 442 ms |
| Full retrieval median / p95 | 0.939 s / 1.884 s |

These are provider-reported tokens and wall-clock observations from one short
run on this host. They are not prices, sustained-load measurements, or an SLA.

The self-checking result identity is
`8f03f47619b740b4b4b9d30dfaf7198a2eb8a2071c374a874af2b5e321a5963a`.
The complete case artifact is
[longmemeval-s-temporal-relation-live-preflight-v1-results.json](longmemeval-s-temporal-relation-live-preflight-v1-results.json).
The external durable state, launcher log, and completion marker remain under
`data/benchmarks/longmemeval-s-temporal-relation-live-preflight-v1/`.

## Boundary and next gate

This closes the bounded live-provider stability, audit, usage, latency, and
safe-fallback gate for the registered composition. It does not expand the
held-out answer result beyond its original 104 questions, establish provider
availability over time, validate every non-temporal routed shape, or compare
PRME with another memory product. The feature remains opt-in.

The next temporal/aggregation experiment should evaluate semantic
qualification, real-item deduplication, multi-session updates, and abstention
under one fixed complete-item protocol. Competitive claims still require a
matched current-system comparison with shared readers, judges, ingestion
boundaries, and cost accounting.

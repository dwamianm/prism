# Grounded quantity extraction cross-model confirmation v3

**Decision:** Accept `speech_act_v10` as registered product evidence for the
bounded quantity extraction behavior tested here.

**Date:** 2026-09-18

**Registration SHA-256:**
`97315bc95b734c98a39278925d765af1091c0aa46a7524ca0be15c9660e49f19`

## Question

Does the v10 candidate that passed DeepSeek also satisfy the same
zero-tolerance assay with a distinct local Qwen 35B-A3B artifact, including the
three currency cases that rejected the earlier cross-model candidate?

## Protocol

The preregistered confirmation reused the same 20 authored sources, twelve
expected quantities and all-or-nothing gates. It used the local
`prme-qwen3.5:35b-a3b-8k` artifact through Ollama, with its digest and the v10
implementation frozen before calls. Every source used a fresh engine and state
was checkpointed after each case. The matching DeepSeek v5 run had already
passed 20/20 under the same implementation.

## Result

The candidate passed every registered gate on the distinct model.

| Metric | Result |
| --- | ---: |
| Completed cases | 20 / 20 |
| Cases passing | 20 / 20 |
| Expected quantities retained | 12 / 12 |
| Missing expected quantities | 0 |
| Unexpected quantities | 0 |
| Field mismatches | 0 |
| Policy errors | 0 |
| Failed provider attempts | 0 |

The positive, negated and conditional `$500` cases that failed Qwen v1 each
materialized exactly one quantity. The first two used bounded recovery from the
already grounded fact object; the dropped conditional claim used the
source-bound conditional action path. The dimensionless score and JSON-decimal
repairs also passed. Approximation, range, version, date, identifier, time,
model-name, address and ordinal controls remained quantity-free.

Together, the DeepSeek v5 and Qwen v3 runs provide two-model confirmation for
the exact behavior: 40/40 completed cases, 24/24 expected quantity instances,
zero unexpected quantities and zero provider failures. Earlier v1-v4 and Qwen
v1 failures remain preserved and explain each accepted repair.

## Verification and artifacts

An independent pass verified the result self-hash, registration and state
hashes, all checkpoints, the successful exit, all metrics and pass flags,
twelve total quantities, the v10 policy and exactly one quantity fact in each
of the three formerly failing Qwen cases. The result identity is
`ba1a5dad434fe7d63ea494647ecdd6447033caafa58ef65a375458773e5b1416`.
The complete summary is
[quantity-extraction-qwen-confirm-v3-results.json](quantity-extraction-qwen-confirm-v3-results.json).
Raw state and logs remain under
`data/benchmarks/quantity-extraction-qwen-confirm-v3/`.

This is authored development/confirmation evidence through one Ollama-compatible
interface. It validates the named exactness and safety cases, not held-out
extraction accuracy, exhaustive unit coverage, answer quality, complete
real-world coverage or competitive leadership.

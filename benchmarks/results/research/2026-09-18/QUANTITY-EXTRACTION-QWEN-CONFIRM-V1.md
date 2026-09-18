# Grounded quantity extraction cross-model confirmation v1

**Decision:** Reject the v8 candidate as cross-model confirmed. Keep the
deterministic safety repairs, but do not claim provider-independent quantity
coverage.

**Date:** 2026-09-18

**Registration SHA-256:**
`6ed93013dd1e2fa946f32cb75976220bd9b5368237ebaec10a22d6c5fb4b6525`

## Question

Does the DeepSeek-passing v8 quantity extraction candidate also satisfy the
same zero-tolerance assay with a distinct local Qwen 35B-A3B model artifact?

## Protocol

The preregistered confirmation reused the same 20 authored sources, twelve
expected quantities and all-or-nothing gates. It used the local
`prme-qwen3.5:35b-a3b-8k` artifact through Ollama, with its digest frozen before
calls. Every source used a fresh engine and state was checkpointed after every
case. No hosted API quota was used.

## Result

All 20 calls completed with zero provider failures. Seventeen cases passed.
Three expected currency quantities were missing. There were no unexpected
quantities, field mismatches or policy errors, but the registered gate failed.

| Metric | Result |
| --- | ---: |
| Completed cases | 20 / 20 |
| Cases passing | 17 / 20 |
| Missing expected quantities | 3 |
| Unexpected quantities | 0 |
| Field mismatches | 0 |
| Policy errors | 0 |
| Failed provider attempts | 0 |

The positive `$500` and negated `$500` claims survived as grounded facts with
correct objects and polarity, but Qwen's quantity fields failed strict
validation and were removed. The conditional `$500` output failed claim-schema
validation entirely, leaving only the target entity. The deterministic
dimensionless recovery succeeded, as did the source-derived decimal repair.
All unsupported numeric controls remained quantity-free.

This localizes the next gap: exact quantity metadata still depends too heavily
on model-authored unit/source fields even when a grounded fact object already
contains one unambiguous supported measure. A provider-independent repair would
derive only a supported verbatim quantity phrase from the grounded fact object
and evidence, while retaining the existing exclusions. Conditional quantified
actions need a separate narrow source-bound path when the provider drops the
whole claim.

## Verification and artifacts

An independent pass verified the result self-hash, registration and state
hashes, all 20 checkpoints, zero failed calls, the recorded failing exit, the
three exact failed cases and all metric totals. The result identity is
`480292087e191f6253df7f4bcab8e49072beff70d365efa793889dcd5e89c9ae`.
The complete summary is
[quantity-extraction-qwen-confirm-v1-results.json](quantity-extraction-qwen-confirm-v1-results.json).
Raw state and logs remain under
`data/benchmarks/quantity-extraction-qwen-confirm-v1/`.

This is an authored confirmation assay, not held-out accuracy, answer quality,
complete real-world coverage or competitive evidence.

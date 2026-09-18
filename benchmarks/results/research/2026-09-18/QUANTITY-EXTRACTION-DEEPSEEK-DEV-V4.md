# Grounded quantity extraction development v4

**Decision:** Reject v9. It recovered every expected quantity, but emitted one
duplicate conditional quantity and failed the zero-extra gate.

**Date:** 2026-09-18

**Registration SHA-256:**
`c421fab242cb49cb222057a3e04112290a3cbd984704f027a2a253057bfda916`

## Question

Does deriving a bounded exact quantity from grounded fact objects and recovering
a dropped conditional quantified action close the Qwen failures without
changing the previously passing DeepSeek result?

## Protocol

The preregistered v9 assay reused the same 20 authored cases and zero-tolerance
gates. DeepSeek 4.1 Flash ran through Ollama with a frozen alias and digest. Each
source used a fresh engine and checkpointed after completion. The candidate
derived only one source-verbatim currency or unit from an already grounded fact
object and added a separate user-only recovery for a complete first-person
conditional quantified action.

## Result

All expected quantities were present, with zero provider failures, field
mismatches or policy errors. Nineteen cases passed. The conditional case
contained the same `$500` fact twice, so the candidate failed.

| Metric | Result |
| --- | ---: |
| Completed cases | 20 / 20 |
| Cases passing | 19 / 20 |
| Missing expected quantities | 0 |
| Unexpected quantities | 1 |
| Field mismatches | 0 |
| Policy errors | 0 |
| Failed provider attempts | 0 |

DeepSeek returned a valid conditional fact whose condition was `the campaign
succeeds`. The fallback represented the same source claim with condition `If
the campaign succeeds`. Exact condition-string comparison did not recognize
them as the same claim, so both materialized with identical subject, predicate,
object, quantity, polarity, evidence and epistemic type.

The next repair must suppress fallback when an existing fact has the same
source evidence and quantity identity, even if its condition span is a
different valid substring of that evidence. This is duplicate prevention; it
must not relax condition validation or merge graph claims after publication.

## Verification and artifacts

An independent pass verified the result self-hash, registration and state
hashes, all 20 checkpoints, zero failed calls, the recorded failing exit, the
single failed case and all metric totals. The result identity is
`6a7037a273433b95a7b37eb4a4c6e63db42eacf9be37b3cd84af725ed438a960`.
The complete summary is
[quantity-extraction-deepseek-dev-v4-results.json](quantity-extraction-deepseek-dev-v4-results.json).
Raw state and logs remain under
`data/benchmarks/quantity-extraction-deepseek-dev-v4/`.

This authored development assay is not held-out accuracy, answer quality,
complete real-world coverage or competitive evidence.

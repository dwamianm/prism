# LongMemEval-S temporal relation product parity v2

**Status:** Passed  
**Date:** 2026-09-18  
**Product revision:** `066aa2b632ce43aab7d7fc29cb4ee9d9fea1aac9`

## Question

Does PRME's shipped opt-in retrieval path reproduce the frozen 104-question
temporal-relation confirmation contexts and durable receipts, including every
accepted improvement, without making new resolver, Jev, reader, or judge calls?

## Protocol

The replay reused the immutable resolver decisions, Jev decisions, source-case
checksums, and baseline packs from the completed confirmation. For each frozen
question it cloned the corresponding pack, opened it through `MemoryEngine`,
and invoked the public `retrieve()` pipeline twice:

1. with temporal enrichment removed to reproduce the frozen control; and
2. with replay providers installed to exercise product routing, validation,
   deterministic arithmetic, gate handling, repacking, response metadata, and
   receipt persistence.

Both rendered context bytes and token counts had to equal the registered
confirmation arms. Every material accepted case had to invoke the resolver and
gate. A rejected case could avoid provider calls only when its frozen candidate
was byte-identical to control. The replay read no reference answers and made no
model calls.

The first attempt stopped after 11 questions because its verifier incorrectly
required every frozen question to invoke the temporal stage. That stop exposed
two accepted explicit-duration questions that the product router also missed.
The v2 source separated relation routing from base context formatting, added
the missing duration constructions, and retained four unchanged rejected cases
as deliberate no-call outcomes. The terminal v1 state is preserved in the
[aborted record](longmemeval-s-temporal-relation-product-parity-v1-aborted.json).

## Result

| Check | Result |
| --- | ---: |
| Questions completed | 104 / 104 |
| Control/candidate contexts matched | 104 / 104 |
| Failures | 0 |
| Routed to resolver | 100 |
| Deliberate no-call outcomes | 4 |
| Frozen accepted decisions reproduced | 22 / 22 |
| Contexts changed | 22 |
| Accepted decision mismatches | 0 |
| Unrouted changed contexts | 0 |
| Confirmation protocol aligned on routed cases | yes |

The routed status distribution was 22 `accepted`, 12 `gate_rejected`, 17
`unsupported`, and 49 `validation_rejected`. The remaining four cases were
`not_routed`, made no replay-provider calls, and retained the control context.

The self-checking result identity is
`19ff2a48f9f4645ce368bb2388aa9d498648fdfba6db936e830d0a17224a9b8c`.
The complete per-question artifact is
[longmemeval-s-temporal-relation-product-parity-v2-results.json](longmemeval-s-temporal-relation-product-parity-v2-results.json).

## Interpretation

This closes the gap between the frozen experiment and the public Python
retrieval path for the tested cohort. It proves byte-exact context reproduction,
accepted-case routing, typed metadata, and durable receipt capture under the
frozen decisions.

It is not a fresh answer-quality result, a live-provider availability or
latency measurement, a full-dataset regression, or a competitor comparison.
The feature remains disabled by default. The next gate is a full 500-question
product regression that measures no-call behavior outside the target cohort,
followed by a bounded live-provider preflight and cost/latency capture.

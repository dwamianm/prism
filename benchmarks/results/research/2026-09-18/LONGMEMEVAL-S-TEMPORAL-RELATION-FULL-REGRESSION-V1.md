# LongMemEval-S temporal relation full regression v1

**Status:** Passed  
**Date:** 2026-09-18  
**Product revision:** `31d45e1beb537cf93b1f4b9f62172ed44481230d`

## Question

Does the opt-in temporal relation path preserve current ranking, context, and
receipt behavior across every frozen LongMemEval-S pack, reproduce all prior
temporal decisions, and expose a bounded provider-call surface?

## Protocol

The answer-blind runner reopened all 500 frozen baseline packs through the
public `MemoryEngine.retrieve()` path. The 133 `temporal-reasoning` questions
reused every frozen resolver and Jev decision from the 29-question development
and 104-question confirmation cohorts. The other 367 questions used an inert
resolver that returned `unsupported`, allowing routing, payload, fallback, and
receipt behavior to be measured without an external call or a model-generated
change.

Every run had to reproduce the frozen candidate ranking. Frozen temporal cases
had to match their registered candidate context byte for byte. Other cases had
to match the baseline context byte for byte. Every retrieval receipt had to
persist the same final context hash. The runner read no reference answers and
made no resolver, Jev, reader, or judge calls.

## Result

| Check | Result |
| --- | ---: |
| Questions completed | 500 / 500 |
| Contexts matched | 500 / 500 |
| Receipts persisted | 500 / 500 |
| Failures | 0 |
| Frozen accepted changes reproduced | 31 / 31 |
| Contexts changed | 31 |
| Resolver routes | 145 |
| No-call queries | 355 |
| Jev routes from frozen decisions | 47 |
| Broad-router call surface | 195 |
| Calls avoided by operation-aware routing | 50 |

The new router reduced the call surface by 25.64% while retaining all 31
accepted development and confirmation changes. Statuses were 31 `accepted`,
16 `gate_rejected`, 56 `validation_rejected`, 42 `unsupported`, and 355
`not_routed`.

| Dataset category | Questions | Resolver routes | Accepted changes |
| --- | ---: | ---: | ---: |
| temporal-reasoning | 133 | 107 | 31 |
| multi-session | 133 | 18 | 0 |
| knowledge-update | 78 | 8 | 0 |
| single-session-user | 70 | 11 | 0 |
| single-session-assistant | 56 | 1 | 0 |
| single-session-preference | 30 | 0 | 0 |

## Cost surface

The 145 exact production resolver request bodies totaled 1,480,973 JSON bytes.
Their median was 10,094 bytes and p95 was 11,845 bytes. The concatenated prompt
messages measured 405,624 tokens under PRME's `cl100k_base` estimator, with a
median of 2,780 and p95 of 3,043 tokens per routed query. These are reproducible
payload and tokenizer measurements, not provider-billed tokens for the opaque
DeepSeek tokenizer.

The 47 replayed Jev request bodies totaled 59,821 bytes. Local product retrieval
including instant replay providers had a 0.274-second median and 0.311-second
p95 on this host. Those timings exclude network and live model latency.

The result identity is
`07696f904109833c2a393c159370913485c604bd5e55a772ed6ff7accc0b38f3`.
The complete per-question artifact is
[longmemeval-s-temporal-relation-full-regression-v1-results.json](longmemeval-s-temporal-relation-full-regression-v1-results.json).

## Boundary and next gate

This proves complete product-path compatibility, receipt persistence, frozen
temporal parity, inert fallback, and the exact routing/payload surface for the
500 stored packs. It does not establish live provider availability or latency.
The 38 routed questions outside the benchmark's temporal category received the
inert resolver, so this run also does not establish whether live proposals for
those questions would improve or harm answers.

Keep the feature opt-in. The next gate is a bounded live-provider preflight that
checks pinned identities, observed tokens, latency, safe fallback, and proposal
quality on representative accepted, rejected, and newly routed questions.

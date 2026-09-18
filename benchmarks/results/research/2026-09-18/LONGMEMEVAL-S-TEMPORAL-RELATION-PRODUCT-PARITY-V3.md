# LongMemEval-S temporal relation product parity v3

**Status:** Passed  
**Date:** 2026-09-18  
**Product revision:** `7083ca5d572bac3489c25bbb0db5d68f25f04bbc`

V3 repeated the complete answer-blind product replay after replacing broad
temporal-format routing with explicit interval, duration, and chronological
order triggers. The objective was to retain every confirmed context change
while avoiding resolver calls for date lookups and other temporal wording that
does not request a supported relation.

## Result

| Check | Result |
| --- | ---: |
| Questions completed | 104 / 104 |
| Frozen control/candidate contexts matched | 104 / 104 |
| Frozen accepted decisions reproduced | 22 / 22 |
| Contexts changed | 22 |
| Resolver calls | 82 |
| Deliberate no-call outcomes | 22 |
| Accepted decision mismatches | 0 |
| Unrouted changed contexts | 0 |
| Failures | 0 |

The routed statuses were 22 `accepted`, 11 `gate_rejected`, 4 `unsupported`,
and 45 `validation_rejected`. Twenty-two unchanged rejected cases were not
routed. Compared with v2, the same 22 accepted contexts were reproduced with
18 fewer resolver calls.

The self-checking result identity is
`456f64ee90a69e1a1e73ebe3aea1968d84c368058b3b5e5c5c5d96e0eec5195d`.
The complete per-question artifact is
[longmemeval-s-temporal-relation-product-parity-v3-results.json](longmemeval-s-temporal-relation-product-parity-v3-results.json).

This supersedes v2 for the current router. The v2 result remains valid for its
bound source revision and preserves the discovery of the original routing gap.
Both replays used frozen provider outputs and made no model calls. V3 proves
product parity and reduced call surface on this cohort; it does not measure
live provider latency, availability, or cost.

# Broad claim verification evidence-order confirmation

The registered same-cohort confirmation passed **all eight targeted gates** and
matched **44/48** authored labels. It produced zero unsafe support decisions,
recovered all six two-passage chains, retained all 12 expected-insufficient
decisions and four conflicts, and refused all four exhaustive claims without a
model call.

The [registration](claim-verification-broad-order-v3-registration.json) was
fixed before this execution. It binds PRME revision `b8ad38d`, exact runner and
implementation hashes, the 48 unchanged broad cases, the immutable
`cross-encoder/nli-deberta-v3-base` revision, policies, thresholds, group bounds,
caller evidence order and all machine gates. The
[machine-readable result](claim-verification-broad-order-v3-results.json)
retains every group, score, typed identity, limitation, basis, digest and gate
verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe `supported` decisions | 0 | 0 | Pass |
| Expected-insufficient cases called `refuted` | 0 | 0 | Pass |
| Correct supported cases | >=18 | 18 | Pass |
| Correct two-passage minimal groups | >=6 | 6 | Pass |
| Correct explicit refutations | >=6 | 6 | Pass |
| Correct insufficient cases | >=12 | 12 | Pass |
| Correct contested cases | >=4 | 4 | Pass |
| Incomplete cases with no model call | >=4 | 4 | Pass |

Observed statuses were 18 `supported`, six `refuted`, 16 `insufficient`, four
`contested`, and four `incomplete`.

## Causal result

Exactly two statuses changed from the safety-v2 result:

- The Orion relation chain changed from `insufficient` to `supported`; its pair
  entailment is 0.9700 in caller order and 0.7428 when reversed.
- The Cedar relation chain changed from guarded `insufficient` to `supported`;
  its pair entailment is 0.9569 in caller order, while the reversed premise is
  assigned 0.9692 contradiction.

The implementation had ranked individual passages to select a bounded subset
and accidentally kept that score order when creating combinations. Ranking now
selects candidates only; combinations restore the exact caller sequence already
bound by the evidence digest. The other 46 status decisions did not change.

This is a caller-order contract, not an order-invariant NLI claim. A caller that
supplies the same facts in reverse order can still receive a different model
score. Accepting the maximum over both permutations would hide a severe
support-versus-contradiction disagreement on Cedar, so the implementation does
not use permutation maximization.

## Remaining misses

The four remaining mismatches are the deliberately included implicit
contradictions (`enabled`/`disabled`, blue/green, PostgreSQL/SQLite and
Paris/Berlin). They remain `insufficient` because the passages contain no
explicit correction or typed exclusivity contract. Their raw model
contradiction scores remain visible. This avoids silently assuming that every
lexical alternative is mutually exclusive.

## Evidence boundary

Both earlier broad results and a two-order diagnostic were known before this
registration. Passing is causal same-input evidence for restoring caller order,
not independent confirmation or general compositional reasoning. The short
authored claims use one pinned local NLI model and do not measure retrieval,
long contexts, calibration, domain transfer, answer quality or comparative
product leadership.

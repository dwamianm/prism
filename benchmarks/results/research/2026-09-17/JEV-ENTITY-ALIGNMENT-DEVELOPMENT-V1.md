# Jev entity-alignment development trial v1

**Result: passed development; product integration remains blocked on confirmation.** Pinned `jev-1.13.0` increased true entity matches from 8/40 to 21/40 while producing zero false merges across 228 nonmatching candidate pairs.

## Bound protocol

- Dataset: DeepMatcher Structured BeerAdvocate-RateBeer training split
- Cohort: 268 labeled candidate pairs, including 40 matches and 228 nonmatches
- Provider: TypeSafe AI, pinned model `jev-1.13.0`
- State: the two structured entity records with name, brewery, style, and ABV
- Questions: the published three-level relation score plus independent name, brewery, and style Nouls
- Frozen selected decision: `link_state.score >= 1.5`
- Gates: at least 98% merge precision, 50% merge recall, at most one false merge, and at least 25 points of recall gain over current conservative matching
- No graph mutation occurred; the runner emits no entity text or credential

## Results

| Method | True matches | False merges | Merge precision | Merge recall |
|---|---:|---:|---:|---:|
| Current PRME conservative name match | 8/40 | 0/228 | 100.00% | 20.00% |
| Jev score route | 21/40 | 0/228 | 100.00% | 52.50% |

Jev improved recall by 32.5% without an observed precision loss. All 268 responses were valid and resolved to the pinned model. The run completed every request without retry, used 137,430 input tokens and 19,028 output tokens, and had 0.178-second median request latency.

The stricter confidence and field-agreement arms remained safe but lost too much recall. The plain three-level score route is therefore the only frozen candidate for confirmation.

## Decision

Register a held-out confirmation with the exact selected route. Do not integrate Jev into organizer behavior yet. A passing confirmation would support an optional semantic alignment advisor; it still could not bypass PRME owner, scope, type, provenance, lifecycle, or atomic-publication checks, and semantic similarity alone would remain insufficient to mutate graph identity.

## Artifact integrity

- Registration SHA-256: `1ca5abd8a6fdcb63c40d1e642cbe1c39ed0161ca558e77a11e46ed7643572266`
- Result file SHA-256: `80f1e32b04b63a88f95d8c6016670fc90aecc6cc2647e1adee7136e0aa7974e3`
- Canonical result SHA-256: `b008ad7e376abb3cbcb8b209e81e8c8f187e4c76fa6f6e7c32388b9e2f064339`

The result contains pair identifiers, labels, typed scores, decisions, token use, and latency. It contains no entity field text or credential.

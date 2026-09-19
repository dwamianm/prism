# MemoryArena value-binding coverage V1

## Outcome

The corrected source-backed city-binding extractor covered every qualified
current-city value in the pinned MemoryArena reference corpus. This audit made
no model calls. It used the independent pinned city/state catalog as the oracle
and checked every base and answer plan in all 270 test groups.

| Measure | Result |
| --- | ---: |
| Reference plans | 2,139 |
| Current-city fields | 10,623 |
| Fields containing qualified values | 8,415 |
| Compound route fields containing qualified values | 4,909 |
| Qualified value occurrences | 10,518 |
| Expected record-scoped distinct bindings | 3,506 |
| Emitted record-scoped distinct bindings | 3,506 |
| Missing bindings | **0** |
| Unexpected bindings | **0** |

The previous extractor accepted an entire `Current City` field only when it was
one value, such as `San Antonio(Texas)`. It now decomposes the dataset's route
grammar, so `from Dallas(Texas) to Houston(Texas)` emits independent exact
bindings for Dallas and Houston. Duplicate values within one plan still produce
one stable binding.

## Fail-closed execution

The MemoryArena tool adapter now recursively checks resolved JSON arguments. If
any complete parenthesized presentation value remains, the adapter records the
blocked JSON pointers, skips the underlying tool, and returns a retryable error
to the model. Audit records distinguish blocked arguments from arguments that
were actually executed. This closes the safety failure observed in confirmation
V2 even when a generated tool call uses a value outside the audited reference
format.

The generic PRME resolver remains format-neutral: it replaces only exact
caller-registered values and does not guess whether an unmatched string is
invalid for a particular tool. Applications define their own fail-closed policy
from the tool schema and retained binding-use evidence.

## Decision

Accept compound-field extraction and fail-closed execution for the MemoryArena
adapter. This repairs the identified coverage defect and provides deterministic
evidence before another paid model experiment.

This result does not revive call-local free-form result guidance, which the
fresh confirmation rejected. The next presentation-fidelity candidate should
restore source-backed forms only in declared structured output slots and should
be evaluated against a matched exact-resolution control.

## Evidence identity

- Coverage artifact SHA-256:
  `0bf8bb15ad42ad3a7fa673662af5dd38fd5edce55da1557b8c39557f88bb96e5`
- Coverage audit source SHA-256:
  `e2fd388c7d00ac4921627e13bb3187593a6473252a2c1c020da292e6f8b42e28`
- Adapter source SHA-256:
  `3b436de154a711206fc5d9114481f8726076af68584c8cc5a580cbc6a94e9a1e`
- Dataset content SHA-256:
  `3a1c6b924c5c2816aeda42c57a253ae52e2de0734fcd989726f417a70a4728c3`
- City/state catalog SHA-256:
  `a3d18b5c692857cd561cbb4ad8d221ac3eb6c47af7c787a3489e3ffa184ca4d6`
- Pinned MemoryArena revision:
  `6cd9de14b71915e39ac742a20dc33785e14b6aab`

## Limits

The corpus audit covers reference-plan `Current City` fields under the pinned
MemoryArena grammar. Generated text may use other formats; the runtime block is
the guard for those cases. Catalog agreement does not prove that a lookup form
is accepted by every external tool, and it does not measure answer quality.

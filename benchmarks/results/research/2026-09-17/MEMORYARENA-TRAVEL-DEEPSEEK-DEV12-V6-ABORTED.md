# MemoryArena projected-trace development ablation V6 — aborted

Date: 2026-09-17

## Decision

This registered development run was interrupted during the first PRME group and
has no score. It must not be compared with the completed V5 baseline.

The native arm completed all eight travelers in group 140. The PRME arm completed
the first traveler and began the second, leaving nine durable traveler
checkpoints and one completed group-arm checkpoint. No group pair, cohort, or
scoring artifact was complete.

## Observed defect

`traveler_final_plan_v1` projected both base and subsequent trajectory records to
the traveler name plus final plan. The first PRME traveler retrieved the correct
base plan, but the base plan did not carry the original dated trip request. The
current traveler query also omitted those dates because MemoryArena expects them
to come from shared history. The model consequently searched for flights around
the 2026 execution date instead of the March 2022 task dates.

This is a deterministic information-loss bug in the projection policy, not a
transport failure. The corrective policy keeps the base traveler's original task
beside its final plan while subsequent travelers remain name-plus-final-plan
projections. That preserves shared dates, budget, route and group constraints
without reintroducing scratchpads or raw tool payloads.

## Artifacts and exposure

- Registration: `memoryarena-travel-deepseek-dev12-v6-registration.json`
- Registration SHA-256: `9ec26c26541e737cab2968f1b54eae2de7c3de9c78b7a28d58eb024f689ec31c`
- Registered PRME revision: `b0002106c7b99c127aceec3941ad77ca6995090a`
- Pinned upstream revision: `6cd9de14b71915e39ac742a20dc33785e14b6aab`
- Exposed output: all native group-140 travelers, the first PRME traveler, and a
  partial second PRME request

The underlying 12 groups were already examined by V5, so this interruption does
not consume a fresh confirmation cohort. A new registration is still required
because the projection policy and adapter source changed.

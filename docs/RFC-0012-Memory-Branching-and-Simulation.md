# RFC-0012: RMS Memory Branching and Simulation

**Status:** Draft
**Tier:** 4 — Advanced Capabilities
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000 through RFC-0009

---

## 1. Abstract

This RFC specifies memory branching: the ability to create isolated copies of the memory state for the purpose of hypothetical reasoning, scenario simulation, planning, and safe experimentation — without corrupting the canonical memory.

Without branching, an LLM that explores a hypothetical scenario contaminates its memory with simulated events. "What if we adopted microservices?" becomes a real memory object indistinguishable from "We adopted microservices." Branching separates the two.

This RFC also specifies the hardest problem: merge semantics. How do we safely bring useful conclusions from a simulation branch back into canonical memory?

---

## 2. What a Branch Is

A Branch is a logically isolated memory context that:

- Derives from a specific snapshot of the canonical (or parent) branch at a defined point.
- Has its own event log (appended after the snapshot point).
- Has its own derived state (memory objects, graph).
- Does NOT affect the canonical branch unless explicitly merged.
- Is auditable: all operations within a branch are traceable to the branch's creation event.

A Branch is NOT:

- A copy of the entire database. It is a snapshot reference plus a delta log.
- A namespace. Namespaces partition memory horizontally by scope; branches partition it vertically by time and epistemic status.
- A version control branch in the VCS sense. Branches are not for collaborative development; they are for isolated reasoning.

---

## 3. Branch Types

| BranchType | Description | Auto-expires? | Merge permitted? |
|---|---|---|---|
| `CANONICAL` | The primary, authoritative memory. One per deployment. | No | N/A |
| `SIMULATION` | Hypothetical exploration. "What if X were true?" | Yes (configurable) | Selective merge only |
| `COUNTERFACTUAL` | Retrospective analysis. "What if Y had happened differently?" | Yes | No merge to canonical |
| `PLANNING` | Forward planning sandbox. Contains goals, hypothetical decisions. | Yes | Selective merge |
| `SANDBOX` | Testing and development. Completely isolated. | Yes | No merge to canonical |
| `TEST` | Automated testing. Never contains user data. | Yes | No merge to canonical |

**`COUNTERFACTUAL` branches MUST NOT be merged into the canonical branch.** Their purpose is analysis, not action. Any conclusions drawn from a counterfactual analysis that should be recorded must be recorded as new assertions in the canonical branch by a human actor.

---

## 4. Branch Lifecycle

```
Branch creation (BRANCH_CREATE operation)
    │
    ▼
Branch event log accumulates (isolated from canonical)
    │
    ├──► Branch expires (auto-delete on expiration)
    │
    ├──► Branch is explicitly closed (BRANCH_CLOSE operation)
    │         ├──► Selective merge into canonical or parent (BRANCH_MERGE operation)
    │         └──► Discard (no merge, branch archived)
    │
    └──► Branch is abandoned (no close, expires via TTL)
```

Every branch MUST have an expiration policy. Unbounded branches are a resource leak.

```
BranchExpiry {
  max_age_days:       Integer?    -- Maximum lifetime from creation. Null = no age limit (requires explicit override).
  max_events:         Integer?    -- Maximum events before forced close.
  max_storage_mb:     Integer?    -- Maximum storage before forced close.
  on_expiry:          ExpiryAction  -- ARCHIVE_BRANCH | DISCARD_BRANCH
}
```

**Default expiration for SIMULATION branches:** 7 days. For PLANNING branches: 30 days. These defaults MUST be configurable per namespace.

---

## 5. Branch Creation

Creating a branch requires:

1. A snapshot of the canonical branch at a specific event log offset.
2. A policy version snapshot (all RFC policy versions at branch creation time).
3. Namespace visibility: the branch inherits namespace visibility from its parent at the snapshot point and MUST NOT see namespace changes made after the snapshot unless explicitly synced.

```
BRANCH_CREATE operation payload:
{
  "branch_id": "<uuid>",
  "parent_branch_id": "CANONICAL",
  "base_event_offset": 84721,
  "base_snapshot_id": "<snapshot_uuid>",
  "branch_type": "SIMULATION",
  "created_by": "<actor_id>",
  "created_at": "<ISO8601>",
  "purpose": "Exploring impact of switching to microservices architecture",
  "policy_snapshot": {
    "decay_version": "decay_v1",
    "confidence_version": "confidence_v1",
    "extraction_version": "extraction_v3"
  },
  "expiry": {
    "max_age_days": 7,
    "on_expiry": "DISCARD_BRANCH"
  }
}
```

---

## 6. Isolation Requirements

Events created within a branch:

- MUST reference the `branch_id` in their metadata.
- MUST NOT be written to the canonical event log.
- MAY reference canonical objects by ID (read-only references).
- MUST be stored in a branch-specific partition of the event store.

Retrieval within a branch context:

- Returns branch-local objects first.
- Falls back to canonical objects at the snapshot offset (not the current canonical state).
- MUST NOT surface canonical objects added after the snapshot unless the branch is explicitly synced.

**The canonical snapshot at the base offset is used, not the live canonical state.** This is critical for consistency: a branch that sees live canonical changes is not isolated — it is a real-time fork, which is a different and much more complex construct.

---

## 7. Resource Limits

Branches are not free. Each branch maintains a separate event log partition and derived state.

**Resource controls that MUST be implemented:**

```
max_concurrent_branches_per_user:   10    (configurable)
max_concurrent_branches_per_system: 100   (configurable)
branch_storage_quota_per_user_mb:   500   (configurable)
```

When a branch creation request would exceed any of these limits, it MUST be rejected with a clear error. The error MUST suggest closing or discarding existing branches.

**Garbage collection:** The organiser runs a branch garbage collection pass daily. Expired branches are processed according to their `on_expiry` policy. Branches pending expiry for more than 7 days past their `max_age` MUST be force-closed by the organiser regardless of activity.

---

## 8. Merge Semantics

Merging a branch into canonical is the hardest operation in this RFC. It is specified in full here because the original suite deferred it to "the memory semantic layer" — which is not a specification.

**Merge is selective by default.** Full branch merge (merging every object from a branch into canonical) is NOT permitted. Every merge MUST specify which objects are included.

```
BRANCH_MERGE operation payload:
{
  "branch_id": "<branch_id>",
  "target_branch_id": "CANONICAL",
  "merge_type": "SELECTIVE",
  "included_object_ids": ["<id_1>", "<id_2>"],
  "conflict_resolution_policy": "<policy_ref>",
  "merged_by": "<actor_id>",
  "merged_at": "<ISO8601>"
}
```

**Merge conflict resolution — the full specification:**

For each included object, the system checks whether a canonical object exists that:
- Has the same entity + attribute combination (for FACT objects).
- Has the same task/intent identity (for TASK/INTENT objects).
- Was created after the branch snapshot offset (indicating the canonical branch has also evolved).

If no conflict: the branch object is added to canonical as a new assertion with `source_type: IMPORTED` and its original `asserted_by`, `epistemic_type`, and `evidence_ids` preserved.

If conflict exists, three strategies are available (set in `conflict_resolution_policy`):

| Strategy | Behaviour |
|---|---|
| `PRESERVE_BOTH` | Add the branch object as a new conflicting assertion. Mark both with `CONTRADICTS` edge. No automatic resolution. Default. |
| `PREFER_CANONICAL` | Do not import the branch object. Log that it was evaluated and rejected. |
| `PREFER_BRANCH` | Import the branch object, deprecate the canonical object, log the replacement. Requires ADMIN permission. |

`PREFER_BRANCH` MUST require explicit ADMIN permission and MUST generate a `MERGE_OVERRIDE` alert. It is provided for cases where a simulation has conclusively determined that a canonical belief is wrong, but it is an elevated action, not a default.

**Objects marked HYPOTHETICAL in the branch MUST have their epistemic type reviewed before merge.** They cannot be merged as HYPOTHETICAL — they must be explicitly reclassified by the merging actor to ASSERTED or another non-hypothetical type.

---

## 9. Branch Divergence Tracking

The organiser tracks branch divergence: how much the branch state has changed relative to canonical.

```
BranchDivergence {
  branch_id:            String
  measured_at:          Timestamp
  events_since_fork:    Integer
  objects_created:      Integer
  objects_modified:     Integer
  canonical_events_since_fork: Integer  -- How much canonical has changed
  divergence_score:     Float           -- Normalised measure of how different branch and canonical are
}
```

High divergence scores indicate that merging is risky — canonical has changed significantly since the branch was created, increasing the likelihood of conflicts.

Implementations SHOULD warn when `divergence_score > 0.70` before proceeding with a merge. This is a warning, not a block — the operator decides whether to proceed.

---

## 10. Retrieval in Branch Context

Retrieval within a branch context follows the same pipeline as RFC-0005 with these modifications:

- Query analysis is unchanged.
- Candidate generation uses: (1) branch-local objects, (2) canonical objects at snapshot offset.
- Scoring uses branch-local confidence and salience for branch-local objects; canonical values for inherited objects.
- Epistemic filtering applies the branch type's epistemic weight overrides. HYPOTHETICAL objects that are `ACTIVE` within a SIMULATION branch are included in that branch's DEFAULT retrieval (because the simulation context assumes the hypothesis is being explored). They are still marked as HYPOTHETICAL.

---

## 11. Conformance Requirements

`[REQUIRED FOR TIER 4 — Branching]`

- All five branch types (excluding CANONICAL which always exists) MUST be supported.
- Every branch MUST have an expiration policy; unbounded branches are NOT permitted.
- Branches MUST NOT exceed the resource limits in Section 7.
- Retrieval in branch context MUST use the canonical snapshot at fork offset, not live canonical.
- Full branch merge (all objects) is NOT permitted; merge MUST be selective.
- COUNTERFACTUAL branches MUST NOT be mergeable into canonical.
- Conflict resolution MUST use one of the three strategies in Section 8.
- HYPOTHETICAL branch objects MUST be reviewed and reclassified before merge.
- The organiser MUST run branch garbage collection daily.

---

## 12. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Isolation test: verify that canonical state is unchanged by 1000 branch events across 10 concurrent SIMULATION branches.
- Merge correctness test: for each conflict resolution strategy, verify correct outcomes on 50 conflicting object pairs.
- Resource limit enforcement: verify that creating a branch beyond `max_concurrent_branches` is correctly rejected.
- Garbage collection test: verify that expired branches are correctly cleaned up within 24 hours.

---

*End of RFC-0012*

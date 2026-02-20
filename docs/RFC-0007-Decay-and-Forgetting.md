# RFC-0007: RMS Decay and Forgetting Model

**Status:** Draft
**Tier:** 3 — Lifecycle
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0002, RFC-0003

---

## 1. Abstract

This RFC specifies the decay and forgetting model: the mechanism by which memory objects lose salience and confidence over time when not reinforced, and how they are eventually archived or expired.

Forgetting is not a failure mode. It is a feature. A memory system that retains everything with equal weight indefinitely will surface increasingly irrelevant, outdated, and noisy information. The decay model ensures that the most useful memories remain prominent while stale ones recede — mirroring the cognitive property that makes biological memory adaptive rather than merely accumulative.

The model is grounded in the Ebbinghaus forgetting curve (1885) and spacing effect research. It is parameterisable to allow tuning per memory type, namespace, and deployment context.

---

## 2. What Decays and What Does Not

**Decays:** Salience and confidence of ACTIVE memory objects over time, governed by decay profiles.

**Does NOT decay:** The event log. Events are immutable. Decay operates only on derived state (memory objects). A decayed memory object is not deleted; its scores decrease and it may be archived, but it remains in the event log and is always rebuildable.

**Does NOT decay:** Objects with `lifecycle_state == ARCHIVED` or `DEPRECATED`. Decay is only applied to ACTIVE objects.

**Does NOT decay by default:** Memory objects explicitly pinned by the user (`salience == 1.0`). Pinned objects are exempt from automated decay. The organiser MUST check for the pin flag before applying decay.

---

## 3. Decay Function

The base decay function is exponential:

```
salience(t) = salience_base × exp(-λ × t)
```

Where:
- `salience_base` is the current baseline salience (updated by reinforcement events).
- `λ` (lambda) is the decay rate coefficient, determined by the object's decay profile.
- `t` is time since the last reinforcement event, in days.

**Half-life:** The half-life of a memory object is `ln(2) / λ` days. This is the intuitive parameter — how many days until salience halves without reinforcement.

| Decay Profile | λ | Half-life | Typical objects |
|---|---|---|---|
| `PERMANENT` | 0.000 | ∞ | Pinned objects, user-stated preferences explicitly marked as stable. |
| `SLOW` | 0.005 | ~139 days | Long-term OBSERVED facts, stable preferences. |
| `MEDIUM` | 0.020 | ~35 days | ASSERTED facts, decisions, active project context. |
| `FAST` | 0.070 | ~10 days | INFERRED facts, recent context that may become outdated. |
| `RAPID` | 0.200 | ~3.5 days | HYPOTHETICAL and UNVERIFIED claims, short-lived task context. |

Decay profiles are assigned per memory object at creation based on its epistemic type and the namespace's decay policy. The mapping MUST be configurable.

**Recommended default mapping `[HYPOTHESIS — requires tuning per deployment]`:**

| Epistemic Type | Default Decay Profile |
|---|---|
| OBSERVED | SLOW |
| ASSERTED | MEDIUM |
| INFERRED | FAST |
| HYPOTHETICAL | RAPID |
| CONDITIONAL | MEDIUM (or RAPID if condition_state is UNKNOWN) |
| UNVERIFIED | RAPID |

---

## 4. Confidence Decay

Confidence decays separately from salience. The confidence decay function is:

```
confidence(t) = confidence_current × exp(-μ × t)
```

Where `μ` (mu) is the confidence decay rate. By default:

```
μ = λ × 0.5
```

That is, confidence decays at half the rate of salience. This reflects the intuition that a memory becoming less prominent (salience decay) does not immediately mean it is becoming less accurate (confidence decay). The two are decoupled.

Confidence decay is NOT applied to OBSERVED objects unless they have not been accessed in more than 180 days. OBSERVED objects represent primary source information; their accuracy does not degrade with time in the same way inferences do.

---

## 5. Reinforcement Decay

Reinforcement events temporarily boost salience. But the boost itself decays if not sustained.

This prevents the "once hot, forever hot" failure mode: a memory object that was heavily used during one project should not remain high-salience indefinitely after the project ends.

```
salience_effective(t) = salience_base + reinforcement_boost × exp(-ρ × t_since_last_reinforce)
```

Where:
- `reinforcement_boost` is the cumulative boost from all reinforcement events (capped at a maximum per RFC-0008).
- `ρ` (rho) is the reinforcement decay rate. Default: 0.10 (half-life ~7 days).
- `t_since_last_reinforce` is days since the most recent reinforcement event.

When a new reinforcement event occurs, `t_since_last_reinforce` resets to 0 and the `reinforcement_boost` is updated.

---

## 6. Decay Tiers and Lifecycle Transitions

As `salience_effective` falls below defined thresholds, the organiser MUST execute lifecycle transitions:

| Threshold | Action |
|---|---|
| `salience_effective < 0.30` | Log a DECAY_WARNING operation. No state change yet. |
| `salience_effective < 0.10` | If `confidence < 0.40`: transition to DEPRECATED. Otherwise: transition to ARCHIVED. |
| `salience_effective < 0.05` | Regardless of confidence: transition to ARCHIVED (if not already DEPRECATED). |
| `confidence < 0.15` (any salience) | Transition to DEPRECATED. Object is no longer believed with sufficient confidence to be useful. |

Transitions MUST be logged as `DECAY_APPLIED` operations including the thresholds that triggered them.

**Transition to DEPRECATED is irreversible.** A DEPRECATED object cannot become ACTIVE again. A new object MUST be asserted to replace it. This prevents confusion between "we forgot this" and "we corrected this."

**Transition to ARCHIVED is reversible.** An ARCHIVED object can be unarchived (transitioned back to ACTIVE) by an explicit user or system operation. This is useful when a project pauses and later resumes.

---

## 7. Suppression (Distinct from Decay)

Suppression is a namespace- or policy-level mechanism that excludes a memory object from retrieval without decaying its scores. It is NOT decay.

Use suppression when:
- A memory object is temporarily not relevant (e.g., out-of-office period, project on hold).
- A namespace policy excludes certain object types from retrieval.
- An actor has been revoked access to a namespace (RFC-0011).

Suppression does NOT modify `salience` or `confidence`. It is a retrieval filter, not a scoring change. Suppressed objects continue to decay normally unless explicitly frozen.

```
Suppression {
  object_id:    ObjectID
  reason:       String
  suppressed_by: ActorID
  suppressed_at: Timestamp
  expires_at:   Timestamp?   -- Null means indefinite suppression.
  policy_ref:   String
}
```

---

## 8. Decay Scheduling and the Organiser

The organiser is a background process that applies decay on a scheduled basis. The organiser is NOT triggered by retrieval; it runs on its own schedule.

**Organiser schedule:**

| Task | Default frequency |
|---|---|
| Apply RAPID and FAST decay | Every 6 hours |
| Apply MEDIUM decay | Daily |
| Apply SLOW decay | Weekly |
| Evaluate lifecycle transitions | Daily |
| Recalculate salience for graph-central objects | Weekly |
| Entity deduplication and alias resolution | Weekly |
| Summarisation pass | Daily (for eligible event windows) |

The organiser MUST record all decay applications as `DECAY_APPLIED` operations. These operations include:
- The object ID.
- The decay profile applied.
- The policy version used.
- The delta (change in salience and confidence).
- The new values.

This allows any decay state to be audited and, if a policy error is discovered, corrected by replaying from the event log with the corrected policy.

---

## 9. Tombstones

A tombstone is the operation record created when an event or memory object is logically deleted for retention policy compliance. Tombstones allow the system to honour retention windows without destroying the integrity chain.

```
TOMBSTONE operation payload:
{
  "target_id": "<event_id_or_object_id>",
  "target_type": "event | memory_object",
  "reason": "retention_policy_expiry | user_request | compliance",
  "policy_ref": "<retention_policy_version>",
  "content_hash_of_deleted": "<sha256>",
  "tombstone_ts": "<ISO8601>"
}
```

**Tombstone format MUST be standardised.** Implementations that use different tombstone formats will be unable to interoperate portability artifacts (RFC-0002, Section 9).

The `content_hash_of_deleted` field allows integrity verification that the tombstoned content matches what was expected to be deleted, without retaining the content itself.

---

## 10. Decay Policy Versioning

Decay parameters (λ, μ, ρ, tier thresholds) are governed by a named, versioned policy:

```
DecayPolicy {
  id:       String     -- e.g., "default_v1"
  version:  String     -- Semantic version string.
  params: {
    lambda_by_profile:    { SLOW: 0.005, MEDIUM: 0.020, ... }
    mu_multiplier:        0.5
    rho:                  0.10
    tier_thresholds:      { warning: 0.30, archive: 0.10, force_archive: 0.05, deprecate_by_confidence: 0.15 }
  }
}
```

Every `DECAY_APPLIED` operation MUST reference a specific policy version. This ensures that if the policy is later changed, the historical decay record is still interpretable.

---

## 11. Graph Centrality Salience Boost

Objects that are referenced by many other objects (high in-degree in the entity-object graph) SHOULD receive a periodic salience boost from the organiser.

```
centrality_boost = graph_centrality_score × centrality_weight
salience_base_adjusted = salience_base + centrality_boost
```

Where `graph_centrality_score` is the normalised in-degree of the object in the local subgraph. `centrality_weight` default: 0.05.

This is `[HYPOTHESIS]`. The rationale is that objects referenced by many other objects are likely foundational facts that should remain retrievable even without recent direct usage. This requires empirical validation.

---

## 12. Conformance Requirements

`[REQUIRED FOR TIER 3]`

- All five decay profiles MUST be supported.
- Decay MUST be applied to ACTIVE objects only.
- Pinned objects (salience == 1.0) MUST be exempt from automated decay.
- Lifecycle transitions MUST trigger at the thresholds in Section 6.
- Transition to DEPRECATED MUST be irreversible without a new assertion.
- Tombstones MUST use the standardised format in Section 9.
- All decay operations MUST reference a versioned decay policy.
- The organiser MUST run on the schedule in Section 8.
- Confidence decay MUST be decoupled from salience decay.

---

## 13. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Decay correctness test: for each decay profile, verify that `salience(t)` at t = half-life equals 50% of initial salience within floating-point tolerance.
- Lifecycle transition test: confirm that all four transition thresholds trigger correctly across 10,000 simulated decay steps.
- Organiser performance: time to apply one decay cycle to N objects (N = 10k, 100k, 1M).
- Tombstone round-trip: export artifact, load, verify that tombstoned content is absent and tombstone operation is present.
- Long-horizon relevance test: `[HYPOTHESIS]` — compare retrieval precision@5 at 30, 90, and 180 days against a no-decay baseline. The hypothesis is that decay-enabled systems maintain higher precision at long horizons.

---

*End of RFC-0007*

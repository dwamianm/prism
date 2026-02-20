# RFC-0008: RMS Confidence Evolution and Reinforcement

**Status:** Draft
**Tier:** 3 — Lifecycle
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0002, RFC-0003, RFC-0007

---

## 1. Abstract

This RFC specifies how the confidence score of a memory object evolves over time through reinforcement and penalty signals. Confidence represents the system's current degree of belief in the accuracy of a memory object. It is not static — it increases when evidence accumulates and decreases when contradictions are encountered.

The confidence model is designed to:
- Increase confidence asymptotically toward 1.0 (not to reach it, because certainty is rarely warranted).
- Decrease confidence multiplicatively on contradictions (not to zero, because a single correction rarely invalidates a well-established belief entirely).
- Prevent runaway reinforcement from a single actor or correlated sources.
- Be fully reproducible from the operation log.

---

## 2. Reinforcement Update Rule

When a positive signal is received for a memory object:

```
confidence_new = confidence_old + γ × (1.0 - confidence_old)
```

Where `γ` (gamma) is the signal weight: a value in (0.0, 1.0) that reflects how strong this reinforcement signal is.

**Properties of this update rule:**
- Asymptotic: confidence always increases but never reaches 1.0 from finite reinforcement.
- Diminishing returns: a high-confidence object benefits less from additional reinforcement than a low-confidence one.
- Bounded: the result is always in (0, 1) for valid inputs.

Simultaneously, the salience baseline is updated:

```
salience_base_new = min(1.0, salience_base_old + γ × 0.5 × (1.0 - salience_base_old))
```

The salience boost is half the confidence boost to reflect that salience and confidence serve different purposes.

---

## 3. Penalty Update Rule

When a negative signal is received:

```
confidence_new = confidence_old × (1.0 - δ)
```

Where `δ` (delta) is the penalty weight: a value in (0.0, 1.0).

**Properties:**
- Multiplicative: a single contradiction does not reduce confidence to zero unless `δ == 1.0`.
- Reversible: confidence can recover through subsequent reinforcement.

Salience may also be reduced on penalty:

```
salience_base_new = salience_base_old × (1.0 - δ × 0.3)
```

The salience penalty is 30% of the confidence penalty, reflecting that a correction should reduce how prominently the memory is surfaced, but less aggressively than it reduces belief in its accuracy.

---

## 4. Signal Types and Weights

The following signals are defined. `γ` and `δ` weights are `[HYPOTHESIS — require empirical calibration]`.

**Positive signals:**

| Signal | γ (default) | Trigger |
|---|---|---|
| `REFERENCED` | 0.08 | The LLM explicitly cited or used this memory in its response. |
| `CONFIRMED` | 0.15 | The user explicitly confirmed this memory is correct. |
| `TASK_COMPLETED_WITH` | 0.12 | A task was completed and this memory was in the context bundle. |
| `CORROBORATED_BY_EXTERNAL` | 0.10 | An external source independently supports this claim. |

**Negative signals:**

| Signal | δ (default) | Trigger |
|---|---|---|
| `CORRECTED` | 0.25 | The user explicitly corrected a claim the model made using this memory. |
| `CONTRADICTED_BY_OBSERVATION` | 0.20 | A new direct observation conflicts with this memory. |
| `TASK_FAILED_WITH` | 0.10 | A task failed and this memory was the primary contributing context. |
| `IGNORED_REPEATEDLY` | 0.05 | The memory was injected multiple times but never referenced (via RFC-0009). |

**Neutral signals:**

| Signal | Effect |
|---|---|
| `UNUSED` | No confidence change. `reinforcement_count` does not increment. Decay proceeds normally. |

---

## 5. The Agent Independence Problem

A critical failure mode in multi-source reinforcement is treating correlated evidence as independent.

**Example:** Agent A reads document D and infers "Alice's preferred stack is Python." Agent B reads the same document D and independently asserts the same claim. Naive cross-agent reinforcement would apply two `γ` updates, treating these as two independent confirmations. But they derive from the same source and are not independent.

**Resolution rule:** Before applying cross-agent reinforcement, implementations MUST check whether the reinforcing agents share a common evidence source:

```
if any(evidence_id in reinforcing_agent.evidence_ids for evidence_id in object.evidence_ids):
    # Correlated evidence — reduce gamma
    γ_effective = γ × correlation_discount_factor  # Default: 0.4
else:
    # Independent evidence — full gamma
    γ_effective = γ
```

Independent corroboration from genuinely independent sources is strong evidence. Correlated "confirmation" from agents that share a source is not.

This check is `[BEST-EFFORT]` in cases where agent evidence provenance is not available. In such cases, implementations SHOULD apply the correlation discount by default and log the uncertainty.

---

## 6. Saturation Controls

Without saturation controls, a high-frequency feedback loop could drive confidence to near-1.0 within a single session, making the object nearly impossible to correct.

**Controls that MUST be implemented:**

1. **Per-session reinforcement cap:** Maximum total `γ` applicable to a single object within one session = 0.40. Any reinforcement signals that would push the session total above this cap are logged but their `γ` is reduced to the remaining cap.

2. **Diminishing returns for repeated identical signals:** The nth identical signal from the same actor applies `γ × (1 / n)`. A user confirming the same fact 10 times in a row should not produce 10× the confidence boost.

3. **Minimum confidence floor for CORRECTED signal:** After receiving a `CORRECTED` signal, `confidence` MUST NOT drop below `max(0.05, confidence_old × (1.0 - δ))`. This prevents a single spurious correction from destroying a well-established belief.

4. **Maximum confidence ceiling:** `confidence` MUST NOT exceed 0.97 through automated reinforcement. Values above 0.97 are reserved for explicitly pinned OBSERVED facts from highly trusted sources.

---

## 7. Epistemic Type Constraints on Reinforcement

Epistemic type constrains what reinforcement can do:

| Epistemic Type | Reinforcement permitted? | Can confidence exceed? |
|---|---|---|
| OBSERVED | Yes | 0.97 |
| ASSERTED | Yes | 0.92 |
| INFERRED | Yes | 0.85 |
| HYPOTHETICAL | Yes (but see note) | 0.65 |
| CONDITIONAL | Only when condition_state == TRUE | 0.85 |
| UNVERIFIED | Yes, until threshold triggers promotion | 0.50 |
| DEPRECATED | NO | — |

**Note on HYPOTHETICAL reinforcement:** Reinforcing a HYPOTHETICAL object does NOT automatically promote it to ASSERTED. Promotion requires an explicit `EPISTEMIC_TRANSITION` operation (RFC-0003, Section 6). The confidence ceiling prevents HYPOTHETICAL objects from accumulating high confidence without explicit promotion.

---

## 8. All Updates Must Be Logged

Every confidence or salience update MUST generate an operation log entry. There are no silent updates.

```json
{
  "op_type": "REINFORCE",
  "target_id": "<object_id>",
  "payload": {
    "signal_type": "CONFIRMED",
    "gamma": 0.15,
    "gamma_effective": 0.09,
    "correlation_discount_applied": true,
    "confidence_before": 0.72,
    "confidence_after": 0.781,
    "salience_before": 0.65,
    "salience_after": 0.682,
    "session_id": "<session_id>",
    "session_gamma_total": 0.21,
    "signal_source": "<actor_id>",
    "policy_version": "confidence_v1"
  }
}
```

```json
{
  "op_type": "PENALTY",
  "target_id": "<object_id>",
  "payload": {
    "signal_type": "CORRECTED",
    "delta": 0.25,
    "confidence_before": 0.72,
    "confidence_after": 0.54,
    "salience_before": 0.65,
    "salience_after": 0.601,
    "signal_source": "<actor_id>",
    "policy_version": "confidence_v1"
  }
}
```

---

## 9. Interaction with Decay (RFC-0007)

Confidence and salience are maintained in two separate update tracks:

- **Decay track (RFC-0007):** Reduces scores over time without interaction.
- **Reinforcement track (this RFC):** Updates `salience_base` and `confidence` directly.

After any reinforcement event, the decay clock (`t` in the decay formula) resets for that object. This is the mechanism by which reinforced objects resist decay.

The effective salience at retrieval time is:

```
salience_effective = salience_base × exp(-λ × t_since_last_reinforce) + reinforcement_boost × exp(-ρ × t_since_last_reinforce)
```

(From RFC-0007, Section 5.) The `salience_base` updated by reinforcement feeds directly into the decay formula.

---

## 10. Conformance Requirements

`[REQUIRED FOR TIER 3]`

- The asymptotic reinforcement formula in Section 2 MUST be used.
- The multiplicative penalty formula in Section 3 MUST be used.
- All signal types in Section 4 MUST be supported.
- The agent independence check in Section 5 MUST be performed for cross-agent reinforcement.
- All four saturation controls in Section 6 MUST be implemented.
- Epistemic type confidence ceilings in Section 7 MUST be enforced.
- Every confidence and salience update MUST generate an operation log entry.
- Confidence MUST NOT exceed 0.97 through automated reinforcement.
- DEPRECATED objects MUST NOT receive reinforcement.

---

## 11. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Asymptotic behaviour test: apply 1000 `CONFIRMED` signals to a single object and verify confidence never exceeds 0.97.
- Saturation cap test: verify that the per-session reinforcement cap is enforced correctly.
- Independence discount test: compare confidence evolution with and without the correlation discount for a known correlated agent pair.
- Penalty floor test: verify that a single `CORRECTED` signal does not reduce confidence below the floor.
- Replay test: given the operation log, reproduce the final confidence state of 100 objects identically.

---

*End of RFC-0008*

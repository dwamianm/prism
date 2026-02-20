# RFC-0011: RMS Multi-Agent Memory Semantics

**Status:** Draft
**Tier:** 4 — Advanced Capabilities
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000 through RFC-0009

---

## 1. Abstract

This RFC specifies how RMS handles memory in environments where multiple agents — human users, AI agents, external services, and automated processes — share access to the same memory substrate. It defines agent identity, trust weighting, conflict resolution, and security requirements for multi-agent deployments.

The core challenge in multi-agent memory is this: different agents have different credibilities, different perspectives, and potentially conflicting goals. A memory system that treats all agents identically will be manipulated by less trustworthy agents and will fail to capitalise on the credibility of authoritative ones. A memory system that doesn't constrain agents can be poisoned.

---

## 2. The Trust Model: Context-Dependent, Not Scalar

**Critical revision from common naive designs:** Trust is context-dependent. An agent may be highly authoritative about financial data and completely unreliable about medical information. A scalar trust score ([0.0, 1.0]) applied to all domains equally is wrong.

This RFC defines a **domain-scoped trust model.**

```
AgentTrustProfile {
  agent_id:       String        -- Stable, verified agent identifier.
  agent_type:     ActorType     -- From RFC-0001, Section 5.
  default_trust:  Float         -- Fallback trust score for unspecified domains. Range: [0.0, 1.0].
  domain_trust:   { domain: Float }  -- Per-domain trust overrides.
  trust_version:  String        -- Policy version.
  verified_by:    ActorID       -- Who established this trust profile.
  verified_at:    Timestamp
}
```

**Domain examples:** `financial`, `medical`, `legal`, `technical`, `personal`, `creative`, `external_data`.

Memory objects are tagged with a `domain` field (new field in the extended data model for multi-agent deployments). Trust lookups use the object's domain to find the applicable trust score.

If no domain-specific trust is configured, `default_trust` is used. Implementations MUST support a minimum of 10 distinct domains per agent profile.

---

## 3. Agent Identity Verification

Agent identity MUST be verified before any write or assert operation is accepted. The verification method MUST be documented in the agent's trust profile.

**Supported verification methods (in order of strength):**

| Method | Strength | Description |
|---|---|---|
| `CRYPTOGRAPHIC_SIGNATURE` | High | Each operation is signed with the agent's private key. |
| `SHARED_SECRET_HMAC` | Medium | Operations include an HMAC using a pre-shared secret. |
| `NAMESPACE_TOKEN` | Medium | Agent presents a namespace-scoped bearer token with expiry. |
| `SYSTEM_INTERNAL` | High | Agent is a known system process (organiser, etc.) verified by the runtime. |

`NONE` (no verification) is NOT a valid verification method for write operations. Read operations may use `NAMESPACE_TOKEN` or `CRYPTOGRAPHIC_SIGNATURE`.

Cryptographic signatures SHOULD be applied per operation (not per session). The signing algorithm MUST be ECDSA (P-256 minimum) or Ed25519. RSA is not recommended for new implementations.

---

## 4. Trust-Weighted Reinforcement

When multiple agents independently reinforce the same memory object, the reinforcement magnitude is weighted by the contributing agent's domain trust score:

```
γ_effective = γ_base × agent_trust_score(agent_id, object.domain)
```

This applies to all reinforcement operations. A high-trust agent's confirmation produces a larger confidence boost than a low-trust agent's.

**The independence check from RFC-0008 still applies.** If two agents share a common evidence source, the correlation discount is applied regardless of their trust scores. High trust does not make correlated evidence independent.

---

## 5. Cross-Agent Conflict Resolution

When agents assert contradictory claims about the same entity and attribute:

1. Both claims are preserved (RFC-0003 contradiction modeling).
2. Confidence evolution for each claim is weighted by the asserting agent's trust score.
3. Conflict status is noted with a `CONTRADICTION_NOTED` operation.
4. At retrieval, the higher-confidence claim is surfaced first, with the conflict flagged.

**Resolution escalation:** If a conflict remains unresolved for longer than the namespace's conflict_resolution_ttl (default: 30 days), the organiser MUST:

1. Generate a SUMMARY object noting the unresolved conflict.
2. Boost the SUMMARY's salience to 0.70 to ensure it is surfaced.
3. NOT automatically resolve the conflict. Automatic resolution of unverified disputes is not permitted.

Conflict resolution is always a human or explicitly authorised arbitration agent action.

---

## 6. Memory Poisoning Defences

Memory poisoning — deliberately injecting false information to corrupt the memory system — is a realistic threat in multi-agent deployments.

**Mandatory defences:**

**Rate limiting:** Each agent MUST be rate-limited on write and assert operations per time window. Default limits:

```
max_assertions_per_hour:    100
max_assertions_per_day:     500
max_confidence_gained_per_session:  0.40  (as per RFC-0008 session cap, per agent)
```

Limits are configurable per namespace and per agent type. Exceeding limits generates a `RATE_LIMIT_VIOLATION` event and the excess operations are rejected, not silently dropped.

**New-agent quarantine:** When a new agent is added to a namespace, it operates in quarantine mode for a configurable period (default: 24 hours). In quarantine:
- All assertions by the quarantined agent have their confidence capped at 0.35 regardless of epistemic type.
- All assertions are flagged with `quarantined: true` in their metadata.
- Quarantine can be lifted by a HUMAN_USER or SYSTEM_PROCESS actor with ADMIN permission.

**High-volume assertion detection:** If any agent asserts more than 10 objects about the same entity within 1 hour, a `HIGH_VOLUME_ASSERTION_ALERT` operation MUST be logged. The system does NOT automatically reject these assertions, but they are flagged for review. A false-positive rate for this alert is expected — batch importing legitimate data will trigger it. The alert is a signal for human review, not an automatic block.

**Retroactive trust adjustment:** If an agent's trust_score is reduced (e.g., it is discovered to be malicious), the organiser MUST re-apply confidence calculations for all objects it has contributed to, using the updated trust score. This is a `policy_version` change and the full replay semantics of RFC-0002 apply.

---

## 7. Namespace Permission Precedence

Trust score MUST NOT override namespace permissions. An agent with `trust_score = 1.0` cannot read from a namespace where it has no READ permission. Trust affects the magnitude of reinforcement operations; it does not affect access control.

The precedence order is:

```
1. Namespace access policy (RFC-0004) — Can the agent access this namespace at all?
2. Agent identity verification (Section 3) — Is the agent who it claims to be?
3. Domain trust (Section 2) — How strongly are the agent's assertions weighted?
```

Any operation that fails at step 1 or 2 is rejected entirely. Step 3 only applies once the previous steps pass.

---

## 8. Multi-Agent Retrieval

In a multi-agent retrieval context, the memory bundle includes authorship metadata so the LLM can reason about source credibility:

```json
{
  "object_id": "<id>",
  "value": "The deployment target is Kubernetes.",
  "epistemic_type": "ASSERTED",
  "asserted_by": "agent:devops-agent-v2",
  "agent_trust_domain": "technical",
  "agent_trust_score": 0.85,
  "conflicts_with": ["<other_object_id>"]
}
```

This authorship information MUST be included in the bundle's `STRUCTURED` or higher representation formats. It allows the LLM to appropriately discount claims from low-trust sources.

---

## 9. Agent Revocation

If an agent is revoked:

1. Future operations from that agent_id are rejected immediately.
2. Past operations remain in the event log unchanged.
3. A `TRUST_SCORE_UPDATE` operation is written with the new trust score (which may be 0.0).
4. The organiser schedules a re-evaluation pass for all objects the agent contributed to, applying the updated trust weight.
5. Objects where the revoked agent was the sole asserting actor SHOULD be transitioned to UNVERIFIED epistemic type pending review.

Revocation does NOT delete memory. It flags it for review and reduces its confidence weight.

---

## 10. Federated Memory Merging

When merging memory from a federated node (a separate RMS instance):

- Agent identities from the federated node MUST be disambiguated using a namespace prefix: `federated:<node_id>:<original_agent_id>`.
- Trust profiles from the federated node MUST NOT be automatically adopted. A local trust policy MUST explicitly assign trust to federated agents.
- Conflicting agent IDs (same ID, different systems) MUST be assigned distinct identities before merge. Silent identity collapse is NOT permitted.
- Merge operations use union semantics on the event log: federated events are appended with their original timestamps and the merge timestamp noted in the operation.

---

## 11. Security Threat Model

This section enumerates the threats this RFC is designed to mitigate. Threats not addressed by this RFC are explicitly noted.

| Threat | Mitigated by | Severity if unmitigated |
|---|---|---|
| Reinforcement flooding | Rate limiting (Section 6) | High |
| Memory poisoning via false assertions | Quarantine + rate limits + trust weighting | High |
| Agent identity spoofing | Cryptographic signature requirement | High |
| Cross-namespace privilege escalation | Namespace permission precedence (Section 7) | High |
| Retroactive trust manipulation | Retroactive re-evaluation requires policy version change + audit | Medium |
| Byzantine agent coalition | Correlation discount (RFC-0008, Section 5) limits coordinated reinforcement | Medium |
| Replay attacks on signed operations | Operation `id` (UUID) + timestamp uniqueness check | Medium |
| Side-channel namespace inference | Addressed in RFC-0004, Section 6 | Medium |
| Adversarial agent coordinating with human user | NOT mitigated — requires human oversight layer | High |

**Unmitigated threat:** If a human user is in collusion with a malicious agent, the system cannot detect this. The security model assumes human users are trusted within their namespace permissions. Deployments with adversarial user models require additional controls outside the scope of this RFC.

---

## 12. Conformance Requirements

`[REQUIRED FOR TIER 4 — Multi-Agent]`

- Domain-scoped trust profiles MUST be supported with at least 10 domain slots per agent.
- At least one of the cryptographic verification methods in Section 3 MUST be implemented.
- `NONE` is NOT a valid verification method for write operations.
- Rate limiting MUST be enforced with the defaults in Section 6.
- New-agent quarantine MUST be enforced for the configurable quarantine period.
- Trust score MUST NOT override namespace access permissions.
- Agent revocation MUST trigger a re-evaluation pass.
- Federated agent IDs MUST be namespaced to prevent identity collision.

---

## 13. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Trust-weighted reinforcement test: verify that a `trust_score = 0.8` agent produces 0.8× the confidence boost of a `trust_score = 1.0` agent on identical signals.
- Poisoning resistance test: simulate a malicious agent asserting 500 false claims at maximum rate and verify that no false claim achieves confidence > 0.50 within the quarantine period.
- Revocation correctness: verify that a revoked agent's contributions are correctly re-weighted across 100 affected objects.
- Federated merge test: merge two RMS instances with overlapping agent IDs and verify correct disambiguation.

---

*End of RFC-0011*

# RFC-0014: RMS Portability, Sync, and Federation

**Status:** Draft
**Tier:** 4 — Advanced Capabilities
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000 through RFC-0009

---

## 1. Abstract

This RFC specifies how RMS memory can be moved between devices, synchronised across instances, and federated across organisational boundaries. It defines the sync protocol, conflict resolution semantics, and the trust model for federated memory.

This RFC supersedes and significantly extends the original RFC-0002 (Git Sync Profile) from the previous suite, which correctly identified Git as a viable transport substrate but did not adequately specify merge conflict resolution or partial sync handling.

---

## 2. Design Goals

**G1 — Local first.** The primary copy of memory lives on the user's device or in their controlled environment. Cloud sync is a replication path, not the authoritative store.

**G2 — Async sync only.** This RFC does not address real-time synchronisation. It targets background sync with eventual consistency. Real-time sync introduces distributed consensus requirements that are out of scope.

**G3 — Append-only transport.** Sync transports only the event and operation logs. Derived state is never transported; it is always rebuilt at the destination.

**G4 — No silent merges.** Every conflict that cannot be automatically resolved by the merge policy MUST produce an explicit conflict record. Users MUST be able to understand and resolve conflicts.

**G5 — Transparent identity.** When memory is received from another instance, its origin is always traceable. Memory does not appear to be locally-produced when it was received from elsewhere.

---

## 3. The Sync Unit

The fundamental unit of synchronisation is the **ops bundle**: a batch of event and operation log entries produced since the last sync point.

```
OpsBundle {
  bundle_id:        UUID
  produced_by:      InstanceID      -- The RMS instance that created this bundle.
  produced_at:      Timestamp
  instance_version: String          -- RMS version of the producing instance.
  policy_snapshot:  PolicySnapshot  -- Policy versions at time of export.
  sync_from_offset: Integer         -- Event log offset at start of this bundle.
  sync_to_offset:   Integer         -- Event log offset at end of this bundle.
  namespace_ids:    [String]        -- Namespaces included in this bundle.
  event_count:      Integer
  operation_count:  Integer
  checksum:         String          -- SHA-256 of the serialised events + operations.

  -- Content
  events:      [Event]             -- From RFC-0001 / RFC-0002.
  operations:  [Operation]         -- From RFC-0002.
}
```

Ops bundles are signed by the producing instance before transmission. The receiving instance MUST verify the signature and checksum before processing.

---

## 4. Transport Options

This RFC specifies the ops bundle format and sync protocol. It does NOT mandate a specific transport. The following transports are referenced as examples:

| Transport | Use case | Latency | Complexity |
|---|---|---|---|
| Git (append JSONL to ops file) | File-based, offline-capable sync | High (minutes to hours) | Low |
| HTTPS REST (PUT /sync/bundle) | Cloud-hosted sync endpoint | Medium (seconds) | Medium |
| Direct peer-to-peer (WebRTC, LAN) | Local network sync | Low (subseconds) | High |
| Email attachment | Truly air-gapped environments | Very high | Very low |

**Transport selection criteria:**
- All transports MUST use TLS 1.3 or equivalent for encryption in transit.
- All transports MUST support ops bundle checksum verification.
- No transport is required. Implementations may support zero, one, or multiple transports.

**Git transport specifics:** The original RFC-0002's Git approach is retained but constrained. When using Git as a transport:
- The ops file MUST be append-only JSONL. No in-place modification.
- The Git repository MUST NOT be used as a database; it is only a transport layer.
- Binary blobs (embedding vectors) MUST be stored via Git LFS.
- If an LFS object is missing from the receiving instance, the corresponding embedding is treated as unavailable. The memory object remains valid; its vector search capability is degraded until the LFS object syncs.

---

## 5. Sync Protocol

**Full sync** (for a new instance or recovery):

```
1. Requester sends: { instance_id, namespace_ids, starting_offset: 0 }
2. Provider sends: ops bundles in sequential order from offset 0 to current.
3. Requester applies bundles to empty local event log.
4. Requester rebuilds derived state.
5. Requester confirms final offset matches provider's reported current offset.
```

**Incremental sync** (for an existing instance):

```
1. Requester sends: { instance_id, namespace_ids, starting_offset: last_synced_offset }
2. Provider sends: ops bundles from last_synced_offset to current.
3. Requester applies bundles, deduplicating by event.id.
4. Requester reports new offset to provider.
```

**Deduplication:** Events are deduplicated by `id` (UUID). An event that already exists in the local log is skipped silently. Operations are deduplicated by `id`. This is the correct behaviour — idempotent application of the ops log means sync can be safely retried.

---

## 6. Merge Conflict Resolution

The original RFC-0002 deferred this problem. This RFC resolves it.

**Case 1: No conflicts.** Events from the sync bundle reference different entities and attributes than local events since the last sync. Apply all events. No conflict.

**Case 2: Same entity, same attribute, different values.** Two instances have independently asserted different values for the same attribute of the same entity since the last common sync point.

This is a genuine conflict. Resolution:

```
conflict_record = {
  local_object_id:    ObjectID
  remote_object_id:   ObjectID
  entity_id:          EntityID
  attribute:          String
  local_value:        String
  remote_value:       String
  local_confidence:   Float
  remote_confidence:  Float
  remote_instance_id: InstanceID
  detected_at:        Timestamp
  resolution_status:  PENDING
}
```

Automatic resolution is applied using the following policy (configurable per namespace):

| Auto-resolution condition | Resolution |
|---|---|
| Remote `confidence` > local `confidence` by more than 0.20 | Accept remote, deprecate local. |
| Local `confidence` > remote `confidence` by more than 0.20 | Keep local, note remote as CONTRADICTS. |
| Both confidences within 0.20 of each other | Create explicit CONTRADICTION, set resolution_status: PENDING. |
| Remote object is DEPRECATED | Skip remote object. |

Conflicts with `resolution_status: PENDING` MUST be surfaced to the user (or to a designated arbitration agent). Auto-resolution MUST NOT be applied to PENDING conflicts without explicit user action.

**Case 3: Structural conflicts** (entity merged locally but not remotely, namespace created on one side but not the other). These require human intervention and MUST generate `STRUCTURAL_CONFLICT_ALERT` operations. They are never auto-resolved.

---

## 7. Partial Sync and Missing Dependencies

Partial sync occurs when a bundle contains objects that depend on events or objects not present in the receiving instance's log.

**Missing evidence_ids:** If a received operation references an `evidence_id` that is not present locally, the operation MUST be accepted but flagged with `evidence_pending: true`. The object's `epistemic_type` is downgraded to `UNVERIFIED` until the missing event is received and verified.

**Missing parent objects:** If a received operation references a `supersedes` or `depends_on` object not present locally, the operation is accepted and the reference is stored as a forward-reference. The organiser resolves forward-references during its regular pass once the referenced objects arrive.

Partial sync MUST NOT cause data loss or silent corruption. Every piece of received data is either applied (possibly in a degraded state) or explicitly rejected with a logged reason.

---

## 8. Federation

Federation extends sync to allow different organisations to share memory across trust boundaries.

**Key differences from single-organisation sync:**

1. **Trust is negotiated, not inherited.** A federated instance does not automatically trust the assertions of another instance's agents. A local trust policy MUST explicitly assign trust to federated agents (RFC-0011, Section 2).

2. **Namespace scoping is mandatory.** All objects from a federated source arrive in a federated namespace (type: ORGANISATION or a dedicated FEDERATED type). They do not enter the receiving instance's PERSONAL or PROJECT namespaces without explicit promotion.

3. **Selective federation.** Instances MAY choose which namespaces to share with which federated partners. No namespace is shared by default. Federation is opt-in per namespace per partner.

**Federated agent identity:** Per RFC-0011, Section 10, federated agent IDs are prefixed: `federated:<source_instance_id>:<original_agent_id>`.

**Trust establishment:** Before accepting any federated ops bundle:

```
federation_agreement = {
  local_instance_id:     InstanceID
  remote_instance_id:    InstanceID
  shared_namespace_ids:  [NamespaceID]
  trusted_agent_domains: { agent_id: [domain] }
  default_trust_score:   Float    -- Applied to all federated agents without explicit domain mapping.
  established_by:        ActorID
  established_at:        Timestamp
  valid_until:           Timestamp?
}
```

A federation agreement is a local policy artifact. It is NOT transmitted to the remote instance in the ops bundle format.

---

## 9. Conflict with Policy Version Changes

A sync scenario that the original suite did not address: what happens when two instances have different policy versions?

**Example:** Instance A decays objects using `decay_v1`. Instance B has upgraded to `decay_v2`. They sync. Instance B now has events that were decayed under `decay_v1` policies — policies it no longer uses.

**Resolution:** Policy version is always preserved in operation records (RFC-0002, Section 4). When Instance B receives `DECAY_APPLIED` operations tagged with `decay_v1`, it MUST:
- Accept the operations as recorded (respecting the policy version at the time they were applied).
- NOT re-apply decay under its own `decay_v2` policy to these operations.
- Log a `POLICY_VERSION_MISMATCH` event noting the discrepancy.

Future decay passes on these objects will use `decay_v2`. The historical decay record under `decay_v1` remains in the log unchanged.

---

## 10. Security Requirements

All sync operations MUST:
- Transmit ops bundles over encrypted channels (TLS 1.3 or equivalent).
- Verify bundle checksums before application.
- Verify bundle signatures if the producing instance provides them.
- Reject bundles from unrecognised instances (not in the local federation agreement).
- Rate-limit incoming sync requests to prevent DoS via malicious bundle flooding.

Implementations MUST NOT apply a bundle that fails checksum verification, even if individual events pass validation. A corrupt bundle may contain carefully crafted valid events designed to pass per-event checks while corrupting the overall log.

---

## 11. Conformance Requirements

`[REQUIRED FOR TIER 4 — Sync and Federation]`

- The ops bundle format in Section 3 MUST be supported.
- Deduplication by event `id` MUST be idempotent.
- Missing evidence_ids MUST result in `UNVERIFIED` downgrade, not rejection.
- Conflict resolution in Section 6 MUST be implemented with the three auto-resolution conditions.
- PENDING conflicts MUST NOT be auto-resolved.
- Federation agreements MUST be established before accepting federated bundles.
- Federated agent IDs MUST use the namespaced format.
- Policy version mismatches MUST be logged and NOT silently overridden.

---

## 12. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Sync correctness: full sync from empty instance on a 100k-event log produces identical derived state to the source instance.
- Incremental sync idempotency: applying the same ops bundle twice produces identical state as applying it once.
- Conflict detection rate: on a synthetic workload where 5% of events conflict, the system correctly identifies and flags ≥95% of conflicts.
- Policy version mismatch handling: verify that cross-policy-version syncs produce correct `POLICY_VERSION_MISMATCH` logs without corrupting derived state.

---

*End of RFC-0014*

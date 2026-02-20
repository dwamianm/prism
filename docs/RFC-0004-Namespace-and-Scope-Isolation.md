# RFC-0004: RMS Namespace and Scope Isolation

**Status:** Draft
**Tier:** 1 — Storage and Integrity
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0002

---

## 1. Abstract

This RFC specifies the namespace model: the mechanism by which memory is partitioned into isolated, policy-governed scopes. Namespaces determine what memory is visible to whom, under what conditions, and with what access rights.

Namespaces are not a convenience feature. They are the boundary between a user's personal memory and a shared project memory, between a trusted agent's view and an untrusted connector's view, between what is retained forever and what expires in 30 days. Without namespaces, every piece of memory is accessible from everywhere, which is both a privacy failure and a retrieval quality failure.

---

## 2. Namespace Definition

A namespace is a named, isolated scope that:

- Contains a set of memory objects, events, and entities.
- Has its own access control policy.
- Has its own retention policy.
- Has its own decay policy defaults.
- Is identified by a stable, globally unique ID.

```
Namespace {
  id:               String        -- [REQUIRED] Globally unique. Typically a URN or UUID.
  name:             String        -- [REQUIRED] Human-readable display name.
  type:             NamespaceType -- [REQUIRED] See Section 3.
  parent_id:        String?       -- [OPTIONAL] Parent namespace. Null for root namespaces.
  created_at:       Timestamp     -- [REQUIRED]
  created_by:       ActorID       -- [REQUIRED]
  access_policy:    AccessPolicy  -- [REQUIRED] See Section 5.
  retention_policy: RetentionPolicy -- [REQUIRED] See Section 6.
  decay_policy_ref: String        -- [REQUIRED] Reference to a decay policy (RFC-0007).
  is_active:        Boolean       -- [REQUIRED]
  metadata:         JSON?
}
```

---

## 3. Namespace Types

| NamespaceType | Description | Typical scope |
|---|---|---|
| `PERSONAL` | Belongs to a single human user. Highest trust. | Per user |
| `PROJECT` | Shared across a defined set of actors working on a common goal. | Per project |
| `ORGANISATION` | Shared across an organisation. May contain cross-project facts. | Per org |
| `AGENT` | Private to a specific AI agent's working memory. | Per agent instance |
| `SYSTEM` | Reserved for system-generated content (summaries, organiser output). | System-wide |
| `SANDBOX` | Temporary, isolated scope for testing or simulation. Does not affect other namespaces. | Per session or task |

Namespace type influences default trust weighting in RFC-0011 (Multi-Agent Memory). PERSONAL namespaces receive the highest default trust for user-asserted content.

---

## 4. Namespace Hierarchy

Namespaces MAY have a single parent namespace. The hierarchy is a tree, not a DAG — cycles are not permitted.

**Hierarchy rules:**

- A namespace MUST NOT be its own ancestor.
- Permissions do NOT automatically inherit from parent to child.
- A child namespace may be granted explicit read access to its parent's content via the parent's access policy.
- A parent namespace MUST NOT be able to read a child namespace's content unless the child's access policy explicitly grants this.

This design prevents the common failure mode where a project-level namespace accidentally exposes its members' personal memories to all project participants.

---

## 5. Access Policy

Every namespace has an access policy that governs which actors can perform which operations on objects within that namespace.

```
AccessPolicy {
  namespace_id:   String
  version:        String        -- Policy version. Recorded on all operations.
  grants:         [AccessGrant]
  default_deny:   Boolean       -- If true, unlisted actors have no access. Default: true.
}

AccessGrant {
  actor_id:       String        -- Specific actor ID, or '*' for all authenticated actors.
  actor_type:     ActorType?    -- Optional filter by actor type.
  permissions:    [Permission]
}

Permission: READ | WRITE | ASSERT | DEPRECATE | ADMIN
```

**Permission semantics:**

| Permission | Allows |
|---|---|
| `READ` | Retrieve memory objects from this namespace. |
| `WRITE` | Write events to this namespace. |
| `ASSERT` | Create new memory objects in this namespace. |
| `DEPRECATE` | Deprecate or supersede objects in this namespace. |
| `ADMIN` | Modify the namespace's own access and retention policies. |

Permissions are NOT hierarchical. Having `ADMIN` does not imply `READ`. Implementations MUST check each permission independently.

---

## 6. Retrieval Isolation

**This section contains the most critical requirement in this RFC.**

Retrieval queries MUST be namespace-scoped. An actor without READ permission on a namespace MUST NOT receive any information about objects in that namespace, including:

- Object content.
- Object metadata.
- Entity names or IDs.
- The fact that a namespace exists.
- The fact that a query returned zero results due to namespace filtering (as opposed to no matching memory existing at all).

**The vector search isolation requirement:** Similarity search (HNSW) MUST apply namespace filters before returning candidates, not after. Post-hoc filtering of similarity search results is insufficient because the search result set itself may reveal the existence of namespace-filtered objects (through rank position changes and score distribution shifts).

Compliant implementations MUST implement one of:

1. **Namespace-partitioned HNSW indices** — separate indices per namespace, queried only when the caller has READ permission.
2. **Pre-filtered search** — namespace IDs embedded in the search space with access control enforced at the index level.
3. **Blind query execution** — search is executed on the full index, but results are filtered at the row-access-policy level of the storage engine, with the access check occurring inside the database engine, not in application code.

Option 3 is acceptable only if the storage engine provides proven row-level security that cannot be bypassed by application-layer queries.

**Side-channel mitigation:** To prevent inference of namespace membership through result count or latency differences, implementations SHOULD pad response times and result counts to fixed values when returning empty namespace-filtered results. This is a SHOULD, not a MUST, due to the performance cost involved.

---

## 7. Retention Policy

Every namespace defines a retention policy that governs how long memory is kept.

```
RetentionPolicy {
  namespace_id:           String
  version:                String
  default_ttl_days:       Integer?    -- Null means retain indefinitely.
  max_event_count:        Integer?    -- Null means no limit.
  min_retention_days:     Integer     -- Default: 90. Events within this window are never compacted.
  sensitive_data_ttl_days: Integer?   -- Override TTL for objects tagged as sensitive.
  on_expiry:              ExpiryAction  -- ARCHIVE | TOMBSTONE | HARD_DELETE
}
```

**ExpiryAction:**

| ExpiryAction | Behaviour |
|---|---|
| `ARCHIVE` | Move to ARCHIVED lifecycle state. Excluded from default retrieval. Log entry preserved. |
| `TOMBSTONE` | Write a TOMBSTONE operation. Log entry replaced with tombstone. Content not recoverable. |
| `HARD_DELETE` | Remove from all storage layers. No log entry preserved. Only permitted for SANDBOX namespaces. |

`HARD_DELETE` is permitted ONLY for SANDBOX namespaces. For all other namespace types, `HARD_DELETE` MUST be rejected. This is a strict requirement; PERSONAL and PROJECT memory must always maintain an auditable record even when content is expired.

---

## 8. Cross-Namespace References

Memory objects in one namespace MAY reference events or entities in another namespace, subject to:

- The referencing namespace's actor has READ permission on the target namespace.
- The cross-namespace reference is explicitly logged as a `CROSS_NS_REFERENCE` operation.
- The referenced object's namespace ID is preserved in the reference (no implicit de-referencing).

Cross-namespace reference MUST NOT cause the referenced object to appear in the referencing namespace's retrieval results. The reference is a pointer, not a copy.

---

## 9. Namespace Configuration — Common Patterns

The following patterns cover the most common deployment scenarios. Implementations SHOULD document which patterns they support.

**Pattern A: Single-user personal assistant**

```
namespace: rms:user:<user_id>:personal  (PERSONAL)
  └── namespace: rms:user:<user_id>:projects:<project_id>  (PROJECT)
```

**Pattern B: Team assistant with personal lanes**

```
namespace: rms:org:<org_id>:shared  (ORGANISATION)
  ├── namespace: rms:org:<org_id>:project:<project_id>  (PROJECT)
  │     ├── namespace: rms:user:<user_id>:project:<project_id>  (PERSONAL, within project)
  │     └── namespace: rms:agent:<agent_id>:work  (AGENT)
  └── namespace: rms:org:<org_id>:system  (SYSTEM)
```

**Pattern C: Simulation sandbox**

```
namespace: rms:user:<user_id>:personal  (PERSONAL, canonical)
  └── namespace: rms:sandbox:<session_id>  (SANDBOX, expires with session)
```

---

## 10. Namespace Mutation and Versioning

Namespace policy changes (access grants, retention policy updates) MUST be recorded as `NAMESPACE_POLICY_UPDATE` operations in the operation log, including the previous and new policy states.

Policy changes are NOT retroactive. Operations executed under a prior policy version remain valid under that version. This is enforced by the `policy_version` field on each operation.

---

## 11. Conformance Requirements

`[REQUIRED FOR TIER 1]`

- All six namespace types MUST be supported.
- Retrieval MUST enforce namespace isolation at the vector index level, not only at the application layer.
- `HARD_DELETE` MUST be rejected for non-SANDBOX namespaces.
- Cross-namespace references MUST be logged as operations.
- Namespace policy changes MUST be logged with before/after state.
- Access policy evaluation MUST check each permission independently.
- Namespace hierarchy MUST be a tree with no cycles.
- Child namespaces MUST NOT inherit parent permissions automatically.

---

## 12. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Namespace isolation test: confirm that zero objects from a restricted namespace appear in retrieval results for an actor without READ permission across 10,000 sampled queries on a corpus with at least 5 namespaces.
- Permission enforcement test: confirm that all five permission types are independently enforced.
- Cross-namespace side-channel test: confirm that response time and result count for a query returning namespace-filtered results is indistinguishable from a query returning empty results due to no matching memory.

---

*End of RFC-0004*

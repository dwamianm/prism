# RFC-0001: RMS Core Data Model and Terminology

**Status:** Draft
**Tier:** 0 — Foundation
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000

---

## 1. Abstract

This RFC defines the core data model for the Relational Memory Substrate (RMS): the canonical set of object types, their required fields, valid relationships between them, and the object lifecycle. All other RFCs in the suite reference this model.

The model is deliberately minimal at this tier. Fields required by higher-tier RFCs are introduced in those documents.

---

## 2. Design Constraints

The data model is governed by four constraints:

**C1 — Derivability.** Every memory object MUST be derivable from the event log. No memory object may exist in the system that cannot be fully reconstructed by replaying events from the beginning of the log.

**C2 — Typed epistemic status.** Every memory object MUST carry an epistemic type at creation. The type is never optional and never defaults to "unknown" without explicit declaration.

**C3 — Stable identity.** Memory object identities are UUIDs assigned at creation and never reused, even after supersession or deletion.

**C4 — Explicit lifecycle.** Every state a memory object can be in is explicitly named. There are no implicit states.

---

## 3. The Event

The event is the atomic, immutable unit from which all memory is derived.

```
Event {
  id:           UUID          -- [REQUIRED] Globally unique. Assigned at creation. Never reused.
  ts:           Timestamp     -- [REQUIRED] UTC, microsecond precision.
  stream:       String        -- [REQUIRED] Logical grouping identifier (e.g., session ID, agent ID).
  actor_id:     String        -- [REQUIRED] Identity of the entity that produced this event.
  actor_type:   ActorType     -- [REQUIRED] See Section 5.
  role:         Role          -- [REQUIRED] See Section 6.
  content:      String        -- [REQUIRED] The raw content of the event (e.g., message text, tool output).
  content_hash: String        -- [REQUIRED] SHA-256 of content. Used for deduplication and integrity.
  namespace_id: String        -- [REQUIRED] The namespace this event belongs to. See RFC-0004.
  metadata:     JSON          -- [OPTIONAL] Arbitrary structured metadata. Not used in retrieval scoring.
}
```

Events are stored in the Event Store (RFC-0002). They are never modified or deleted. Logical deletion is represented by a TOMBSTONE operation referencing the original event.

---

## 4. Memory Object Types

Memory objects are derived from one or more events by extraction pipelines. The following object types are supported. All implementations MUST support all types.

| Type | Description |
|---|---|
| `FACT` | A proposition believed to be true about an entity, domain, or the world. |
| `PREFERENCE` | A user or agent preference about how something should be done or presented. |
| `DECISION` | A recorded choice made, with the context and options known at the time. |
| `TASK` | An action that is intended, in progress, or completed. |
| `SUMMARY` | A compressed representation of multiple events or memory objects over a time window. |
| `INTENT` | A goal, commitment, or open question that spans multiple sessions. (Requires RFC-0013.) |

Each memory object MUST include the following core fields regardless of type:

```
MemoryObject {
  -- Identity
  id:               UUID            -- [REQUIRED] Stable, globally unique.
  type:             MemoryType      -- [REQUIRED] One of the types above.
  version:          Integer         -- [REQUIRED] Incremented on each mutation. Starts at 1.

  -- Epistemic fields (see RFC-0003)
  epistemic_type:   EpistemicType   -- [REQUIRED] See RFC-0003.
  source_type:      SourceType      -- [REQUIRED] See Section 7.
  evidence_ids:     [EventID]       -- [REQUIRED] Minimum one. The events this object was derived from.
  asserted_by:      ActorID         -- [REQUIRED] The actor that produced or confirmed this object.

  -- Temporal fields
  valid_from:       Timestamp       -- [REQUIRED] When this object became valid.
  valid_to:         Timestamp?      -- [OPTIONAL] Null means currently valid.
  created_at:       Timestamp       -- [REQUIRED] When this object was first created.
  last_modified_at: Timestamp       -- [REQUIRED] When this object was last mutated.

  -- Lifecycle
  lifecycle_state:  LifecycleState  -- [REQUIRED] See Section 8.
  superseded_by:    ObjectID?       -- [OPTIONAL] Set when this object is replaced.

  -- Scoring (maintained by Lifecycle layer — RFC-0007, RFC-0008)
  confidence:       Float           -- [REQUIRED] In [0.0, 1.0]. Initial value set by RFC-0003 defaults.
  salience:         Float           -- [REQUIRED] In [0.0, 1.0]. Initial value set at creation.

  -- Namespace
  namespace_id:     String          -- [REQUIRED] See RFC-0004.

  -- Content
  value:            String          -- [REQUIRED] Human-readable representation of the object.
  structured_value: JSON?           -- [OPTIONAL] Machine-readable structured form.
}
```

---

## 5. Actor Types

All actors that create, modify, or confirm memory objects MUST declare an actor type.

| ActorType | Description |
|---|---|
| `HUMAN_USER` | A human interacting with the system directly. |
| `SYSTEM_PROCESS` | An automated system process (e.g., the organiser job). |
| `AI_AGENT` | An AI agent operating within the system. |
| `EXTERNAL_CONNECTOR` | An external service or integration. |

Actor type is used by RFC-0011 (Multi-Agent Memory) for trust weighting and conflict resolution.

---

## 6. Roles

The `role` field on an Event describes the conversational role of the content.

| Role | Description |
|---|---|
| `USER` | Content produced by a human user. |
| `ASSISTANT` | Content produced by the AI assistant. |
| `TOOL` | Output from a tool or external system call. |
| `SYSTEM` | System-level instructions or context. |
| `ORGANISER` | Operations generated by the scheduled memory organiser. |

The stored event role describes source authorship; it is not reused as the
chat transport role for extraction. Historical content is always submitted to
the extraction model as input. Otherwise an `ASSISTANT` event can be interpreted
as the model's prior response and produce an empty continuation. The original
role remains authoritative for downstream source typing and provenance.

---

## 7. Source Types

Source type classifies the origin of a memory object's content.

| SourceType | Description |
|---|---|
| `USER_STATED` | The user directly stated this. |
| `USER_DEMONSTRATED` | The user demonstrated this through repeated behaviour. |
| `SYSTEM_INFERRED` | The system derived this from patterns in events. |
| `EXTERNAL_DOCUMENT` | Content was extracted from an external document or data source. |
| `TOOL_OUTPUT` | Content came from a tool or API response. |
| `IMPORTED` | Content was imported from another RMS instance. |

Source type influences the initial confidence assignment per RFC-0003 defaults.

---

## 8. Lifecycle States

A memory object exists in exactly one lifecycle state at any time. State transitions are logged as Operations (see RFC-0002, Section 6).

```
ACTIVE ──────┬──────► DEPRECATED
             │
             ├──────► SUPERSEDED (by a newer version of the same fact)
             │
             └──────► ARCHIVED (retained but excluded from default retrieval)

DEPRECATED ──────────► ARCHIVED (if retention policy requires preservation)
```

**State definitions:**

| State | Description |
|---|---|
| `ACTIVE` | Currently valid and eligible for default retrieval. |
| `DEPRECATED` | Determined to be incorrect, outdated, or superseded. Not returned in DEFAULT retrieval mode. Preserved in log. |
| `SUPERSEDED` | Replaced by a newer version. The `superseded_by` field references the replacement. |
| `ARCHIVED` | Retained for provenance or compliance but excluded from active retrieval. |

There is no DELETE state. Logical deletion is always DEPRECATED or ARCHIVED with a tombstone operation.

---

## 9. Graph Relationships

Memory objects exist within a directed property graph. The graph is materialised from the event log and is always rebuildable.

**Edge types:**

| Edge | Source | Target | Description |
|---|---|---|---|
| `DERIVED_FROM` | MemoryObject | Event | This object was derived from this event. |
| `SUPERSEDES` | MemoryObject | MemoryObject | This object replaces a prior version. |
| `CONTRADICTS` | MemoryObject | MemoryObject | This object directly conflicts with another. Both are preserved. |
| `SUPPORTS` | MemoryObject | MemoryObject | This object provides evidence for another. |
| `RELATES_TO` | MemoryObject | MemoryObject | General relatedness (typed by `relation_label`). |
| `ABOUT_ENTITY` | MemoryObject | Entity | This object describes an entity. |

All edges MUST include:

```
Edge {
  id:           UUID
  type:         EdgeType
  source_id:    ObjectID or EntityID
  target_id:    ObjectID or EntityID
  created_at:   Timestamp
  created_by:   ActorID
  confidence:   Float   -- confidence in this relationship
  valid_from:   Timestamp
  valid_to:     Timestamp?
}
```

---

## 10. Entity Model

Entities are first-class nodes in the graph. A memory object is *about* an entity; it is not itself an entity.

```
Entity {
  id:           UUID
  type:         EntityType     -- PERSON, PLACE, ORGANISATION, PROJECT, CONCEPT, TOOL, OTHER
  canonical_name: String
  aliases:      [String]
  namespace_id: String
  created_at:   Timestamp
  last_seen_at: Timestamp
}
```

Entity resolution (deduplication of aliases to a canonical entity) is performed by the organiser (RFC-0007). The extraction pipeline assigns an initial `canonical_name`; the organiser may merge duplicates.

### Extraction reference integrity (implementation clarification, 2026-09-12)

Built-in providers validate extraction-local references before accepting their
structured response. Named fact subjects and relationship endpoints must resolve
to a listed entity. Literal unresolved English personal references are the sole
exception: they may remain unlisted and receive a source-event-local identity
during materialization. Optional type qualifiers disambiguate equal names with
different entity types. Name matching preserves the ingestion merger's stripped,
case-insensitive name and exact type semantics; it does not guess aliases.
Missing or ambiguous references discard only the affected built-in-provider
claim while retaining grounded, reference-closed siblings. This preserves the
closed-reference rule without losing a complete response when a model repeatedly
omits one entity declaration. Objects may be literals; when an object matches
ambiguous entity names or supplies an explicit `object_entity_type`, it must
resolve uniquely too.

Provider citations that differ from a source span only in straight or curly
quote marks are aligned to that span before validation. The exact source bytes,
including the source's original quote marks, are what the extraction record and
derived claim retain. Other paraphrases remain invalid.

The materializer also resolves by distinct identity rather than overwriting a
name dictionary entry. Custom or historical facts with unresolved subjects are
preserved with `subject_link_status` metadata and receive no guessed HAS_FACT
edge. The same rule applies to object `MENTIONS` links and relationship claims:
an unresolved endpoint omits that link, while preserving the source-cited claim.
This does not establish semantic identity, entailment, or namesake resolution
across conversations. Relationship predicates remain model metadata on FACT
nodes; ingestion does not map them directly to structural edge types. Existing
saved plans retain their original materialization policy and replay unchanged.

Historical `speech_act_v11` and earlier plans keep their exact node
metadata and replay unchanged. Fresh built-in extractions record
`speech_act_v11`, and plans made from those outputs record `speech_act_v12`, which
retains the v7 source-effective validity rules and rejects an attempt or
intention collapsed into a completed/current predicate, including component
relationships inside the attempted action. If the target claim is omitted, v12
can recover the first exact model-returned entity after the literal nonactual
action verb and preserve an explicit condition. V7 can reconstruct a supported
exact decimal from source text when JSON transport emitted a float, while
rejecting approximation and range context; it never converts the float.
V8 also admits one omitted exact dimensionless score/count/rating/level only
from a complete user-authored source sentence, under the ordinary grounding and
closed-reference validators. V9 can reconstruct a currency or bounded verbatim
measurement unit from an already grounded object and can recover one complete
first-person conditional quantified action from a user source. It retains the
same approximation, range, condition, evidence and reference guards.
V10 suppresses that conditional fallback when a validated fact already has the
same evidence, predicate, polarity and quantity identity, regardless of which
valid source substring it uses for the condition. V11 can recover one leading
exact measure from a user-authored first-person completed action in a bounded
verb lexicon. It still uses the source phrase as the fact object and passes the
ordinary evidence, quantity and closed-reference validators; modals, negations,
examples, questions, conditions, approximations and ranges are excluded. Saved
v10, v9, v8, v7 and v6 records also prepare v12, saved v5 records prepare v11,
v4 prepare v10, v3 prepare v9, and v2 prepare v8. A legacy extraction
record whose omitted policy defaults to `source_passage_v1` prepares a missing
plan under v7 rather than claiming the new validator ran.
Unresolved English personal
references (`I`, `we`, `they`, etc.) reuse an identity only within the same source
event, with matching unresolved-reference metadata and provenance. Claim nodes
also preserve semantic polarity and exact explicit conditions; conditions start
with an unknown state. A fact may additionally preserve one exact decimal
quantity when its quantified phrase and unit occur verbatim in both the claim
object and source evidence. The value is stored as a decimal string; no unit or
currency conversion is inferred. Unsupported notation leaves the claim intact
without quantity metadata. Each newly extracted fact starts its half-open
validity interval at the resolved source-effective time. A valid newer explicit
replacement closes the prior interval atomically at that boundary. Legacy rows
whose ingestion-based start is later than the replacement boundary keep their
stored interval to avoid creating an inverted range; lifecycle and supersedence
still retire them. Absent event provenance or a legacy globally merged
reference does not authorize reuse. Explicit non-personal types retain
named-entity behavior. Historical plans retain their serialized policies and
checksums; the model's missing-policy default has not changed. This does not solve
within-message quotation/coreference, condition evaluation, same-name homonyms,
unit conversion, or quantities expressed as ranges or approximations. See
[entity identity and merge rules](ENTITY-IDENTITY.md).


---

## 11. The Memory Bundle

A Memory Bundle is the output of the retrieval pipeline (RFC-0005). It is the structured context package delivered to the LLM. It is not stored; it is computed per request.

```
MemoryBundle {
  request_id:       UUID
  namespace_set:    [NamespaceID]
  generated_at:     Timestamp
  context_budget:   Integer         -- token budget for this bundle
  tokens_used:      Integer
  components: {
    entity_snapshots:   [MemoryObject]  -- current state of relevant entities
    stable_facts:       [MemoryObject]  -- high-confidence, low-decay facts
    recent_decisions:   [MemoryObject]  -- recent DECISION objects
    active_tasks:       [MemoryObject]  -- ACTIVE TASK objects
    provenance_refs:    [EventID]       -- evidence chain for retrieved objects
  }
  retrieval_metadata: {
    scores:     { object_id: Float }
    rank_order: [ObjectID]
    excluded:   { object_id: ExclusionReason }
  }
}
```

The bundle structure is fixed. Implementations MAY add components but MUST NOT remove or rename existing ones.

---

## 12. Conformance Requirements

`[REQUIRED FOR TIER 0]`

- Implementations MUST support all six memory object types.
- All memory objects MUST include every required field listed in Section 4.
- All events MUST include every required field listed in Section 3.
- Entity resolution MUST use the Entity model in Section 10.
- Lifecycle state transitions MUST follow the state machine in Section 8 exactly.
- Graph edges MUST include all fields listed in Section 9.
- Implementations MUST NOT delete events from the event log.

---

## 13. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- A schema validation test suite covering all required fields and type constraints.
- A round-trip test: given a set of events, derive memory objects, serialize, deserialize, and verify structural identity.
- Entity resolution precision/recall on a labelled test set of ≥500 entity mentions.

---

*End of RFC-0001*

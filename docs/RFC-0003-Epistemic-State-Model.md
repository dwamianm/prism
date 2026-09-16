# RFC-0003: RMS Epistemic State Model

**Status:** Draft
**Tier:** 1 — Storage and Integrity
**Version:** 1.0
**Date:** 2026-02-19
**Depends on:** RFC-0000, RFC-0001, RFC-0002

---

## 1. Abstract

This RFC specifies the Epistemic State Model: the formal system by which every memory object is classified by *how it is known*, not merely *what it contains*. This distinction is the most important differentiator between RMS and naive memory systems.

Without epistemic classification, a system cannot distinguish between what a user directly stated and what the system inferred. It cannot reason about contradictions. It cannot calibrate trust in its own beliefs. It cannot explain why a fact is stored or whether it should be trusted.

The Epistemic State Model addresses all of these.

---

## 2. The Core Problem

Consider the following memory objects, all stored with identical data structures in a naive system:

> "Alice's preferred language is Python." — stated by Alice directly.
> "Alice probably prefers Python." — inferred by the system from past tool selections.
> "Alice uses Python." — extracted from an uploaded document.
> "Alice might switch to Rust." — speculated by Alice while thinking aloud.

A naive system treats all four identically. A system with epistemic typing can:

- Surface the direct statement with higher confidence than the inference.
- Mark the speculation as HYPOTHETICAL and exclude it from factual retrieval.
- Weight the document extraction as EXTERNAL and flag it for user verification.
- Reason about what happens if "Alice switches to Rust" contradicts "Alice's preferred language is Python."

This is not a theoretical nicety. It directly determines whether the AI assistant makes correct or incorrect assertions to the user about what it "knows."

---

## 3. Epistemic Types

Every memory object MUST be assigned one of the following epistemic types at creation. The type is immutable except through explicit EPISTEMIC_TRANSITION operations (Section 6).

Startup preserves existing explicit assignments, including hypothetical and
conditional types. For a legacy local schema missing the epistemic column, the
migration adds it as nullable and heuristically fills only those NULL rows once,
preserving confidence. Missing application metadata is not evidence that a node
needs reclassification. Older defaulted values and past heuristic overwrites
cannot be reliably distinguished from intentional assignments and are not
automatically rewritten.

| EpistemicType | Meaning | Default confidence range |
|---|---|---|
| `OBSERVED` | Directly witnessed or recorded from a primary source (e.g., explicit user statement, direct measurement). | 0.80 – 0.95 |
| `ASSERTED` | Stated as fact by a user or agent, but not independently verified. | 0.65 – 0.85 |
| `INFERRED` | Derived by the system from patterns, co-occurrences, or reasoning. Not directly stated. | 0.40 – 0.70 |
| `HYPOTHETICAL` | Explicitly speculative or possible without a stated condition. | 0.15 – 0.45 |
| `CONDITIONAL` | True only if a stated condition holds. Requires condition evaluation at retrieval time. | 0.30 – 0.65 |
| `DEPRECATED` | Previously valid but now determined to be incorrect, outdated, or superseded. | — |
| `UNVERIFIED` | Received from an external or untrusted source; awaiting corroboration before promotion. | 0.10 – 0.40 |

**Default confidence ranges are starting points, not hard constraints.** Confidence evolves based on reinforcement and feedback per RFC-0008. The ranges reflect the expected initial calibration for each type. Implementers SHOULD NOT override defaults without experimental justification.

**On classification accuracy:** Epistemic classification is performed by an extraction pipeline. Extraction pipelines are `[BEST-EFFORT]` — they are not guaranteed to be accurate. A statement classified as INFERRED that was actually OBSERVED is a classification error, not a protocol violation. Implementations SHOULD expose the extraction model's confidence in its own classification as a separate field (`classification_confidence`). `[HYPOTHESIS — classification accuracy thresholds require experimental validation]`

Epistemic status is independent of claim kind: facts, preferences and decisions
can depend on unresolved conditions. Built-in providers distinguish speculative
claims (`hypothetical`) from claims with an explicit if/unless condition
(`conditional`). They require the exact condition text and reject uncertain or
contingent future actions classified as completed decisions unless the source
contains an explicit choice or commitment. Structured extraction defaults to
temperature zero; callers may change it, but should first measure the selected
provider. Ollama extraction also defaults provider reasoning to `none`, because
a hidden reasoning trace competes with the schema response for the same context
window. Ollama uses constrained JSON output rather than requiring a tool-call
envelope. The setting remains configurable and other providers retain their own
default when it is omitted. These controls reduce known category collapses without making model
classification infallible. See the
[local extraction diagnostics](../benchmarks/results/extraction/2026-09-13/README.md).
The prompt does not evaluate conditions or automatically reclassify saved records.

### Source support in PRME ingestion

Extraction requests an exact `evidence_quote` for each fact and relationship. Validation requires
a real source passage and complete subject/object mentions, avoiding matches such
as “Ann” inside “Marianne”. Stored evidence expands to its surrounding paragraph
so a genuine substring cannot silently omit a trailing qualification. Condition
and uncertainty classification uses the cited source sentence plus any following
condition or exception sentence. This prevents an unrelated hypothetical or
request elsewhere in the paragraph from changing the claim's epistemic type.
Indirect questions such as “see if” and “wondering if” are not treated as
logical preconditions.
Fact content is the paragraph-complete source passage; the model's
subject/predicate/object stays in metadata.
Custom providers that omit citations use the complete message as support and
must still supply source-supported subject and object values. This deliberately
rejects unsupported paraphrased object values.
The built-in Instructor provider requires citations in its response schema and
passes the source to local validation. Malformed fact and relationship items are
dropped independently, as are claim proposals that overstate certainty, omit
source support, or otherwise fail semantic admission. A malformed list envelope
or a missing or ambiguous named reference on a claim that otherwise passes
admission still enters bounded validation retries. This applies even when every
proposed claim is rejected: the immutable raw event remains successful and
searchable instead of becoming failed extraction work.
Source-role admission guidance limits assistant messages to durable conversation
state such as commitments, completed actions, and explicitly attributed user or
project facts. Generic recommendations, explanations, background knowledge, and
examples are not promoted into derived claims. System messages similarly admit
durable policies and instructions rather than their examples. The immutable raw
event remains available even when structured extraction correctly returns no
claims; source role still determines the claim's source type and confidence.

Built-in providers also require semantic `polarity` (`positive` or `negative`)
for each fact and relationship. An explicit condition must be reproduced from
the cited source and is stored with `condition_state="unknown"`. Custom and
legacy providers remain compatible: omitted polarity becomes `unknown`, and an
unsupported or missing condition is removed while the claim is treated as
hypothetical. Retrieval exposes these qualifiers in provenance labels. DEFAULT
mode includes a conditional claim only when its stored condition state is
`true`; EXPLICIT mode retains unknown, false and expired conditions for audit.
PRME does not yet run a condition evaluator. A caller that confirms or rejects a
condition should record new current evidence rather than infer truth from age.

PRME exposes that recording step as `evaluate_condition()` on the async and
sync Python APIs, `PUT /v1/nodes/{node_id}/condition` over HTTP, and
`memory_evaluate_condition` over MCP. A new conditional claim must include exact
condition text and begins at `UNKNOWN`; callers cannot mark it true during
creation and bypass the transition journal. Evaluation can cite a same-owner,
same-scope event and accepts a retry UUID for idempotent recovery after an
ambiguous response. PRME still does not automatically decide whether a condition
holds.

These checks establish source membership, not semantic entailment. Model
predicates, classifications, relationship labels, and summaries remain fallible.
Callers can inspect `evidence_refs`, `metadata.evidence_quote`, and
`metadata.grounding_method` (`source_passage_v1`) to audit the source. Retaining
whole paragraphs may increase context cost; packing must skip oversized passages
instead of dropping their qualifications.

Relationships are materialized as source-cited FACT nodes with epistemic types,
not direct semantic edges between entities. The subject links through HAS_FACT;
a resolved object receives a MENTIONS link from the claim. These are retrieval
associations, not logical entailment. Built-in providers must cite and classify
each relationship. Legacy/custom relationships without classification default
to UNVERIFIED. Unverified relationship proposals use SYSTEM_INFERRED provenance
and its configured confidence default (0.20), retaining the original source event
in evidence_refs. Thus an omitted classification does not use the absent
UNVERIFIED/USER_STATED cell's 0.50 fallback and enter default retrieval.

If a fact already represents the same resolved endpoints and full source passage,
its classification and predicate take precedence over additional relationship
labels for that passage. Raw labels remain available in the extraction journal.
This is passage-level coverage, not a semantic deduplication claim. Existing
committed relationship edges are not automatically rewritten.

LLM ingestion does not treat differing values as inherently contradictory: a
person can use Python and Rust or like both tea and coffee. Automatic retirement
requires `temporal_intent="update"`, an explicit `replaces_object` present in the
source passage, and an observed/asserted fact. Only the named previous object is
eligible. A known negative update can retire the same known-positive claim, such
as "no longer uses Python" replacing "uses Python"; unknown historical polarity
is never guessed. Hypothetical, conditional, inferred, and unverified extractions
cannot retire existing facts. Unnamed changes remain alongside prior evidence
until a more informed resolution is available. The lower-level supersedence
detector retains its explicit caller-driven legacy matching mode.

Newly extracted facts start `valid_from` at their resolved source-effective
time. A valid explicit update in a `temporal_validity_v7`, `speech_act_v8`, `speech_act_v9`, `speech_act_v10`, `speech_act_v11`, or `speech_act_v12`
plan atomically retires
the previous claim and closes its half-open interval at the replacement's
`valid_from`. Existing derivation policies replay unchanged. A legacy row whose
stored start is later than that boundary remains lifecycle-superseded without an
inverted `valid_to`; this preserves readable historical bytes while keeping it
out of current state.

New `speech_act_v12` plans also enforce a narrow fail-closed boundary for literal
first-person attempts and intentions. When the cited clause says the speaker is
trying, planning, wanting, or needing to do something, a built-in extraction
cannot materialize it under a completed/current predicate such as `uses` or
`enforces`. The predicate must preserve the speech act, such as
`trying_to_set_up` or `plans_to_use`. This lexical boundary does not prove
general entailment and older extraction plans retain their recorded behavior.
The same clause check covers completed relationships between components named
inside the attempted action. Fresh built-in outputs carry extraction grounding
policy `speech_act_v6` before they may prepare a v12 plan. V12 can recover an
omitted target only from the first exact returned entity after a literal
nonactual action verb and preserves an explicit condition. Saved v5 outputs
remain v11, v4 remain v10, v3 remain v9, v2 remain v8, while legacy `source_passage_v1` outputs remain eligible
for v7 recovery only.

Explicit backend supersedence, contradiction and resolution accept optional
evidence only when the event exists in the affected nodes' owner and scope.
Validation occurs inside the state/relationship transaction; an invalid item in
`supersede_many` aborts the whole batch. Malformed, missing and foreign references
share one availability error, and malformed evidence is no longer silently
dropped. Omitted evidence remains permitted for caller-driven decisions. New
explicit supersedence also commits a checksummed before/after record and supports
exact durable retries keyed by its ordered node pair, actor, and evidence. This
checks provenance membership, not entailment. The
[correction guide](MEMORY-CORRECTIONS.md) covers the public sync/async replacement
workflow, including retained sources and ambiguous completion outcomes.

Single-node promotion and archival accept an optional caller-generated request
UUID. The operation record binds that key to the node, action, and actor, making
an exact retry durable while rejecting reuse for different inputs. Omitting the
key preserves strict transition errors. This applies to Python, the HTTP
`Idempotency-Key` header, and MCP's `request_id` argument.

---

## 4. Source Types and Epistemic Interaction

Source type (RFC-0001, Section 7) interacts with epistemic type to determine the initial confidence value.

**Recommended initial confidence by (epistemic_type, source_type):**

| Epistemic Type | USER_STATED | USER_DEMONSTRATED | SYSTEM_INFERRED | EXTERNAL_DOCUMENT | TOOL_OUTPUT |
|---|---|---|---|---|---|
| OBSERVED | 0.90 | 0.85 | — | 0.75 | 0.80 |
| ASSERTED | 0.80 | 0.75 | 0.60 | 0.65 | 0.70 |
| INFERRED | — | 0.60 | 0.55 | 0.50 | 0.55 |
| HYPOTHETICAL | 0.35 | — | 0.25 | 0.30 | — |
| CONDITIONAL | 0.45 | — | 0.30 | 0.35 | 0.40 |
| UNVERIFIED | — | — | 0.20 | 0.25 | 0.30 |

Cells marked `—` represent combinations that SHOULD NOT occur in practice. Implementations MAY emit a warning if they detect such a combination.

**These values are `[HYPOTHESIS]`.** They represent the best current estimate based on design reasoning. They MUST be tunable per deployment and SHOULD be updated based on empirical feedback accuracy data when available.

---

## 5. Conditional Claims

Conditional epistemic objects require special handling at retrieval time.

A CONDITIONAL memory object MUST include:

```
condition:          String    -- A natural-language or structured description of the condition.
condition_scope:    String    -- The namespace or context in which the condition applies.
condition_state:    Enum      -- UNKNOWN | TRUE | FALSE | EXPIRED
evaluated_at:       Timestamp?
```

**Retrieval behaviour by condition_state:**

| condition_state | Retrieval treatment |
|---|---|
| `TRUE` | Treat as ASSERTED. Surface normally with ASSERTED retrieval weight. |
| `FALSE` | Suppress from DEFAULT retrieval. Rank as DEPRECATED in EXPLICIT mode. |
| `UNKNOWN` | Suppress from DEFAULT retrieval. Apply HYPOTHETICAL retrieval weight in EXPLICIT mode. |
| `EXPIRED` | Suppress from DEFAULT retrieval. Treat as DEPRECATED in EXPLICIT mode. |

Condition evaluation is `[BEST-EFFORT]`. The system MAY use an LLM call to evaluate whether a condition is currently true based on the current context. When LLM evaluation is used, the result MUST be logged as an `EPISTEMIC_TRANSITION` operation with `evaluation_method: "llm"` in the payload.

PRME preserves `epistemic_type=CONDITIONAL` while changing
`metadata.condition_state`. This refines the original transition diagram below:
conditionality describes why a claim can apply, while condition state describes
whether it applies now. Erasing the type after a true evaluation would make a
later false evaluation indistinguishable from retracting an unconditional fact.
Every evaluation therefore records its method, actor, time, reason, optional
evidence, and complete before/after snapshots in one checksummed
`EPISTEMIC_TRANSITION` operation. The node update and operation commit in the
same transaction on both supported backends.

---

## 6. Epistemic Transitions

Epistemic state may change. All changes MUST be recorded as `EPISTEMIC_TRANSITION` operations in the operation log (RFC-0002, Section 6).

**Permitted transitions:**

```
UNVERIFIED  ──► ASSERTED       (corroboration received)
UNVERIFIED  ──► DEPRECATED     (determined to be false)
ASSERTED    ──► DEPRECATED     (contradicted and resolved)
ASSERTED    ──► OBSERVED       (primary source verification obtained)
INFERRED    ──► ASSERTED       (confirmed by user or external source)
INFERRED    ──► DEPRECATED     (contradiction received)
HYPOTHETICAL ──► ASSERTED      (hypothesis confirmed)
HYPOTHETICAL ──► DEPRECATED    (hypothesis refuted)
CONDITIONAL/UNKNOWN ──► CONDITIONAL/TRUE|FALSE|EXPIRED (condition evaluated)
CONDITIONAL/<state>  ──► CONDITIONAL/<state>           (condition re-evaluated)
ANY         ──► DEPRECATED     (explicit deprecation via DEPRECATE operation)
```

**Forbidden transitions:**

- DEPRECATED → any active state (deprecation is not reversible; a new object MUST be asserted).
- Skipping transitions (e.g., UNVERIFIED → OBSERVED without an intermediate corroboration step).

Each `EPISTEMIC_TRANSITION` operation MUST include:

```json
{
  "op_type": "EPISTEMIC_TRANSITION",
  "target_id": "<object_id>",
  "payload": {
    "from_type": "<EpistemicType>",
    "to_type": "<EpistemicType>",
    "reason": "<string>",
    "evidence_ids": ["<event_id>", ...],
    "triggered_by": "<actor_id>",
    "trigger_type": "user_correction | system_evaluation | feedback_signal | policy"
  }
}
```

---

## 7. Contradiction Modeling

If two memory objects make contradictory claims about the same entity and attribute, the system MUST:

1. Preserve both objects in the event log and derived state.
2. Create a `CONTRADICTION_NOTED` operation linking both objects.
3. Create a `CONTRADICTS` edge between the two objects (RFC-0001, Section 9).
4. NOT automatically deprecate either object.

**Tie-breaking at retrieval:** If both contradicting objects are ACTIVE and no user resolution has been recorded:

- Surface the higher-confidence object first.
- If confidence is equal within 0.05, surface the more recently created object.
- Include a `conflict_flag: true` in the retrieval metadata for the lower-ranked object.
- Do NOT surface both as facts; the retrieval layer MUST present the contradiction explicitly if both are included in the bundle.

In PRME, marking a contradiction and resolving one each commit the node states,
edge writes, and audit records in one database transaction. A failed write rolls
back the whole operation. Endpoints must be distinct and share the same user and
scope, including when resolving legacy edges. PostgreSQL locks both endpoints in
UUID order before validation to serialize competing resolutions. Applications
use `contradict()` and `resolve_contradiction()` through the async or synchronous
Python API, `POST /v1/contradictions` and `/v1/contradictions/resolve` over HTTP,
or `memory_mark_contradiction` and `memory_resolve_contradiction` over MCP. An
exact retry with the same ordered endpoints, actor and evidence is idempotent;
a conflicting retry is rejected.

**Resolution:** Contradiction resolution occurs when a user or trusted agent asserts which claim is correct. The resolution MUST be recorded as an `EPISTEMIC_TRANSITION` (of the incorrect claim to DEPRECATED) with the resolving actor's ID.

### 7.1 Exact temporal assertion state

PRME exposes `get_assertion_state(AssertionStateQuery(...), user_id=...)` on the
async engine and sync client, `POST /v1/assertions/state` over HTTP, and
`memory_get_assertion_state` over MCP. The query requires one owner, one scope,
an exact structured subject and predicate, and a timezone aware `valid_at`
instant. Requiring the instant prevents a nominally deterministic query from
silently reading the process clock.

The operation scans every selected FACT, DECISION, and PREFERENCE page for an
unchanged store. It returns current eligible claim candidates and a stored
timeline containing event time, ingestion time, validity, lifecycle,
supersedence, epistemic type, source type, contradiction links, and evidence.
Matching uses the normalized exact assertion contract; no entity alias,
predicate paraphrase, or semantic equivalence is inferred.

Candidate eligibility requires the node's current lifecycle to be active, no
supersedence pointer, a validity window containing `valid_at`, and DEFAULT-mode
epistemic eligibility. The operation classifies zero candidates as `unknown`,
one as `single`, repeated equal object/polarity values as `consistent`, differing
values without explicit conflict as `multiple`, and active contested lifecycle
or contradiction edges as `contested`. These are stored claim states, not
verified truth. Recency does not resolve `multiple` or `contested` claims.

An optional `knowledge_at` excludes nodes and edges learned later, but applies
to current graph state. The response therefore repeats the historical coverage
boundary with `exact_snapshot=false`; later lifecycle transitions and mutations
are not replayed. Returned rows and value/candidate/conflict sets are bounded
with separate truncation fields, while counts are complete for an unchanged
store. Source extraction and real-world coverage remain unknown.

---

## 8. Retrieval Behaviour by Epistemic Type

The retrieval pipeline (RFC-0005) MUST apply the following behaviours by epistemic type. These are not suggestions.

| EpistemicType | DEFAULT retrieval | EXPLICIT retrieval | Confidence weight |
|---|---|---|---|
| OBSERVED | Included | Included | 1.0× |
| ASSERTED | Included | Included | 0.9× |
| INFERRED | Included (with flag) | Included | 0.7× |
| HYPOTHETICAL | Excluded | Included | 0.3× |
| CONDITIONAL | Included if TRUE | Included | Per condition_state |
| DEPRECATED | Excluded | Included | 0.1× |
| UNVERIFIED | Excluded unless above threshold | Included | 0.4× |

**DEFAULT retrieval** is the standard mode used when the LLM is constructing context for a user-facing response.
**EXPLICIT retrieval** is used when the caller requests specific objects by ID or type (e.g., "show me all hypotheses about this project").

The UNVERIFIED inclusion threshold is configurable per namespace. Default: include if `confidence > 0.30`.

---

## 9. Provenance Transparency

**Implemented role handling, 2026-09-12:** direct writes, deferred raw-source
materialization and extracted facts classify `role="tool"` as `TOOL_OUTPUT`
(case insensitive), using that provenance's configured confidence matrix entry.
They share the same role inference helper. Explicit direct-write provenance and
confidence overrides remain authoritative. Unverified relationship proposals
remain `SYSTEM_INFERRED`, even when proposed from a tool's source text.
Existing durable node snapshots and prepared derivation plans retain their saved
values; this correction does not rewrite historical provenance or prove tool
output is accurate.

Every memory object MUST be able to answer the following questions from the event and operation logs alone:

1. **Why is this believed?** — What `evidence_ids` underpin it?
2. **Who asserted it?** — What is the `asserted_by` actor and their type?
3. **What type of knowledge is this?** — What is the `epistemic_type`?
4. **Has it been challenged?** — Are there `CONTRADICTS` edges or `CONTRADICTION_NOTED` operations?
5. **How has it changed?** — What is the `EPISTEMIC_TRANSITION` history?

Implementations MUST expose a provenance query API that returns this information for any object ID without requiring access to derived tables.

PRME exposes `get_provenance()` on both Python clients,
`GET /v1/nodes/{node_id}/provenance` over HTTP, and
`memory_get_provenance` over MCP. The tenant-scoped view returns the current
node classification, owned evidence events, missing evidence references,
same-owner/same-scope contradiction edges, and bounded chronological pages from
the operation log. Payloads remain in their durable stored form so checksummed
records can be independently verified. The current node is used only for access
control and to locate its evidence; source and transition history come from the
append-only event and operation logs.

---

## 10. What This Model Does NOT Claim

This model explicitly does not:

- Claim to model consciousness, subjective experience, or genuine belief.
- Claim that epistemic classification is always correct (it is `[BEST-EFFORT]`).
- Claim that OBSERVED memories are true — only that they were directly sourced from primary input.
- Claim to represent emotional states. Emotional content in text is content. It is not a first-class epistemic category in this model.

---

## 11. Conformance Requirements

`[REQUIRED FOR TIER 1]`

- Every memory object MUST have an `epistemic_type` assigned at creation.
- All seven epistemic types in Section 3 MUST be supported.
- Permitted transitions in Section 6 MUST be enforced; forbidden transitions MUST be rejected.
- All epistemic transitions MUST generate `EPISTEMIC_TRANSITION` operations.
- Contradictions MUST be preserved, not auto-resolved.
- Retrieval behaviour in Section 8 MUST be implemented.
- The provenance query API in Section 9 MUST be supported.

---

## 12. Benchmark Requirement

Before this RFC progresses to Experimental status, implementers MUST publish:

- Classification accuracy for the extraction pipeline on a labelled test set (target: ≥75% precision and recall on OBSERVED/ASSERTED/INFERRED at minimum).
- Contradiction detection precision/recall on a synthetic test set of ≥200 contradicting fact pairs.
- Retrieval exclusion verification: confirm that DEPRECATED and HYPOTHETICAL objects are correctly excluded in DEFAULT mode across 100% of sampled queries.

---

*End of RFC-0003*

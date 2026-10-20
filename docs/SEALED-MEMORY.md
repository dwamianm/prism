# Admitted memory and sealed evidence

PV-04 separates usable memory from its retained source. This document describes
the implementation checkpoint. The protected release remains gated by the
integration and validation work recorded in
[the PV-04 plan](plans/2026-10-evidence-privacy/PV-04-plan.md).

Privacy is explicit. An installation without a selected policy retains its
existing behavior. A protected installation supplies the sanitizer, current
policy authority, key custody and guarded provider capabilities through
`PrivacyRuntime`; the engine does not construct private evidence authority.

## What an ordinary reader receives

Extracted memory uses a closed statement projection. It retains subject,
predicate, object, polarity, conditions, supported temporal fields, epistemic
status and opaque provenance references. Evidence passages, extraction repair
text, arbitrary source metadata and entity descriptions are excluded from that
projection before embeddings and lexical documents are prepared.

Direct `store()` admits the caller's sanitized text, including an intentionally
permitted sensitive fact. Supplying a shorter `retrieval_content` selects that
admitted text. Sealing the separate source does not make an admitted fact
anonymous or secret. Explicit operational value bindings remain usable when
their admitted presentation survives packing.

Ordinary reads use enrolled source identities and saved policy snapshots.
Caller metadata cannot grant source access. Graph traversal, lexical and vector
hydration, session expansion, exact state and aggregate reads use the admitted
graph view. Unclassified historical nodes do not become admitted merely because
a protected policy was configured.

`get_event()` returns `ProtectedEventView` for a protected source.
`get_extraction()` returns `ProtectedExtractionView`; provenance and saved
receipt inspection return their corresponding closed protected views. These
models contain availability or denied detail fields rather than source text,
private checksums or arbitrary journal payloads. Event history keeps the same
page positions, including an explicit `legacy_unclassified` envelope for
unclassified events in a protected read.

`inspect_source(event_id, user_id=...)` reports an owned source's availability
without probing private-key readiness. Unknown and foreign identifiers return
the same unavailable response. Ordinary evidence states include `sealed`,
`not_captured`, `expired` and `missing`. Erasure and actual private-key states
require their owning capabilities; a metadata record is not proof of
decryptability.

Retrieval includes a source limitation in the packed context and response
metadata. A request for a quotation or a source-only detail cannot trigger
fallback decryption, raw-source search, a source cache or model-assisted vault
access. LangChain and LlamaIndex retrievers carry that limitation with admitted
results.

## Provider and adapter boundaries

Queries enter the same local sanitizer before provider dispatch. Optional
reformulation uses the shared guarded native generator; temporal providers
guard every send and admit generated output before validation, repair or reuse.
The local reranker holds its request guard until its inference worker finishes,
including cancellation. A provider without the required capability is refused.
These protected routes do not change the ordinary feature defaults.

Lossless framework chat history, replacement, clearing and deletion are
unsupported for protected sources and fail before deserialization or control
writes. Deprecated raw search, protected raw export, unscoped blob access and
raw historical learning also refuse explicitly. The CLI's node, edge and chain
inspection requires an owner for protected memory.

## Explicit source access and pending integration

The separate `read_source()` interface consumes a selected, finite
`EvidenceAccess` capability and returns a labeled sanitized representation. It
does not mint grants or promote diagnostic evidence into ordinary memory.
Standard-mode source use requires runtime eligibility and a runtime grant;
diagnostic grants are never accepted by retrieval.

At this checkpoint, retrieval with `source_access` is explicitly unavailable
until the real PV-03 selected-evidence lease is integrated. Protected persisted
retrieval receipts are also pending PV-05; a transient response reports
`receipt_persisted=False`. Protected profile publication, reingestion and
owner-group links retain their existing refusal.

The concrete local policy ledger and erasure fences belong to PV-06, historical
classification and migration to PV-07, and the final release gate to PV-08.
Tests using a fixture policy authority are component integration evidence, not
proof of those production authority or erasure guarantees.

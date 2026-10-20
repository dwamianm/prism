# Explicit evidence policy admission

PV-02 adds opt-in policy admission to the Python engine, synchronous client,
HTTP and MCP. It does not enable a production protected profile by itself.
The sanitizer, payload protector, authoritative policy service and providers
must be supplied explicitly through `PrivacyRuntime`. Missing capabilities
fail closed. Existing installations keep their current behavior when no policy
is selected.

## Policy selection

`PRMEConfig(privacy_policy=...)` supplies a project default. Text write methods
also accept `policy=...`; HTTP accepts the same object and MCP accepts its JSON
form. The caller policy can only narrow the default. Capture uses the order
`none`, `minimal`, `full`; diagnostic-only use wins over runtime use; finite
deadlines take the earlier value. An ambiguous redact/pseudonymize combination
rejects. Conflicting bodies under the same policy ID and revision reject.

```python
from uuid import UUID
from prme import MemoryRetention, PrivacyPolicy, PRMEConfig

policy = PrivacyPolicy(
    policy_id=UUID("093b1afb-a96d-4a04-8db4-437a1271e763"),
    revision=1,
    project_id=UUID("f3e0d1d7-ec18-4486-8825-00b44d397be8"),
    owner_id=UUID("eaa389f3-e107-4d77-b396-5bd9393336ac"),
    capture="none",
    evidence_use="diagnostics_only",
    memory_retention=MemoryRetention(mode="until_deleted"),
    language="en",
)
config = PRMEConfig(privacy_policy=policy)
# MemoryEngine.create(config, privacy_runtime=runtime, ...)
# requires an explicitly installed implementation of every required capability.
```

Project and owner IDs must be opaque UUIDs. A workspace runtime factory receives
the actual namespace UUID; that UUID must match the policy project. Session IDs
are opaque UUIDs and roles use the supported fixed vocabulary. Content,
retrieval text, structured value bindings, metadata keys and values, and speaker
names enter the sanitizer together. Caller metadata cannot mint privacy source
references. Lossless framework chat history is not an admitted-memory shape and
rejects when a protected default is configured.

| Choice | Admission behavior |
| --- | --- |
| `capture="none"` | Retain admitted memory with no completed evidence payload. |
| `capture="minimal"` | Retain the complete sanitized source unit, including attribution and source time, only if it fits `minimal_max_bytes`. Ambiguous or oversized units reject; they are never truncated or upgraded to full capture. |
| `capture="full"` | Retain the complete sanitized source unit. Original unsanitized input is never selected as evidence. |
| `evidence_use="diagnostics_only"` | Record diagnostic-only authority for the separate evidence slot. Ordinary node reads do not open that slot. |
| `evidence_use="runtime"` | Record runtime eligibility; an explicit finite runtime grant is still required by the protector. |
| `evidence_lifetime_seconds` | Required finite positive duration when evidence is retained, unless unlimited evidence is explicitly selected. |
| `unlimited_evidence=True` | Requires `PrivacyRuntime.allow_unlimited_evidence=True` as well. This opt-in is visible in inspection. |
| `memory_retention` | Required `erase_after` with positive seconds or explicit `until_deleted`. This is independent of lifecycle TTL/archive. |

The effective policy and all contributing immutable policy references are saved
with each accepted event. Deferred work and exact retries use that saved
snapshot, including original deadlines, even if the process default changes.
Request-ID retries require a separate request-scoped commitment capability;
they store no public predictable source hash. A missing or retired commitment
key returns `privacy_retry_unavailable` and is never recreated by verification.

## Durable processing and synchronous preparation

No diagnostic retention still permits finite pending processing input.
Asynchronous extraction stores that sanitized input in a separate protected
slot. The processing deadline is at most 24 hours and no later than the evidence
deadline when one exists. Real work claims are required to open it. Successful
graph publication consumes the slot in the same backend transaction.

`durable_processing_input=False` rejects asynchronous ingestion and fast
admission before queueing. Use `ingest(..., wait_for_extraction=True)` or the
synchronous `MemoryClient.ingest()` to prepare extraction and embeddings in
memory before accepting any source/job rows. A failed transient preparation
requires caller resubmission; the engine cannot recover its input after a crash.
Synchronous `store()` also prepares its admitted memory before acceptance.
Python process memory and caller/provider copies are outside durable storage
guarantees.

Protected admission rejects `enable_owner_group_links=True` before effects.
That optional legacy helper selects an extra owner entity outside the admitted
plan, so it cannot yet carry all participating policy dependencies. Protected
publication retains its authority lease until queued writes finish, including
when the caller cancels; cancellation does not promise rollback. Pending work
also checks its processing deadline at every guarded embedding send.

Protected visual input rejects before decoder, temporary-file, blob or provider
effects. `store(extract=True)` currently rejects on this path; use the explicit
ingestion method for extraction.

`reingest()` is unavailable for protected input. It returns the fixed
`privacy_provider_unavailable` error for a configured protected profile or
persisted protection in the requested owner/scope, including reopened and mixed
stores, dry runs, zero limits and cursors. Empty source envelopes and missing or
expired processing work do not turn protection into an empty successful result.
The legacy reingestion path rechecks its participating sources and context at
provider, queue and native transaction boundaries. This is an unsupported
operation boundary, not a cross-process lease preventing all future enrollment
of legacy data. Supported protected admission and claimed processing retain
their separate saved-policy authority guards.

Transport validation suppresses raw invalid values when a configured or explicit
request policy selects protection. Persisted protection gates valid typed data
operations after reopen; it does not currently select pre-tool validation
formatting for malformed arguments on an otherwise legacy-configured transport.
Such formatting does not load or disclose stored protected content.

## Authority and integration boundaries

`PolicyAuthority.guard(snapshot, effect)` must serialize authoritative policy
activation with the actual effect, including across processes in a deployment.
Admission, transmission, publication and disclosure are separate effects. Each
provider capability must acquire the supplied guard at every send, including
SDK retries. An initial boolean check is insufficient. Saved policy identity
cannot be replaced by a current process default during recovery.

The public source event is an empty content envelope with opaque references.
Its existing content checksum describes that empty envelope. Sanitized source
checksums stay inside the protected representation; historical event, extraction
and receipt checksum formats are unchanged. Admitted memory remains in normal
nodes, prepared index content and executable plans. These copies need the
separately owned erasure and journal lifecycle work.

Legacy retrieval, organizer/profile generation, lifecycle mutation and optional
provider paths refuse protected operations until their provider/copy boundaries
are integrated. Built-in extraction providers are not implicitly wrapped as
privacy-aware capabilities. Protected extraction currently prepares each turn
independently and refuses unclassified or cross-policy existing planner inputs.
It cannot reuse legacy raw source passages. Full sealed read projection,
source-only text removal, all historical journal handling, erasure/backup
disposal and protected-format migration remain separate integrations.

`inspect_privacy_policy(user_id=...)`, HTTP `POST /v1/privacy/policy`, and MCP
`memory_inspect_privacy_policy` report the effective policy, finite/unlimited
choices, provider names, processing failure recovery and integration status.
Inspection also lists protected reingestion as unsupported.
Inspection never opens an evidence payload. `protected_product_ready` remains
false until the complete release gate is implemented and verified.

The acceptance tests use explicit fake sanitizer, reversible test payload
encoding and fake providers. They exercise real policy composition, evidence
selection, authority decisions, public adapters and DuckDB/PostgreSQL storage.
Their passing results prove this component contract, not detector accuracy,
encryption or end-to-end protected-product behavior. See the
[unchanged acceptance plan](plans/2026-10-evidence-privacy/PV-02-plan.md).

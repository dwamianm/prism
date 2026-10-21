# Managed privacy erasure

Protected stores can deny selected evidence or memory immediately and explicitly
purge their managed live copies. This is opt-in engine functionality. Protected
restore/export integration and the complete product release gate remain pending;
`protected_product_ready` remains false. Existing legacy archival and checksum
formats are unchanged.

## Independent ledger and startup

Configure the existing `PrivacyRuntime` with an independent `erasure_ledger`.
`LocalErasureLedger.create(path)` explicitly creates a new SQLite ledger outside
the pack and its managed file directory. It refuses an existing file. Open the
same authority later with `LocalErasureLedger(path)`, which requires that file
to exist. Keep the ledger independent of pack snapshots and restores.

```python
from dataclasses import replace
from prme import MemoryEngine
from prme.privacy import LocalErasureLedger

# Explicit provisioning, once. application_runtime contains the existing
# policy authority, crypto, sanitizer and provider capabilities.
ledger = LocalErasureLedger.create("/operator/authority/erasure.sqlite3")
runtime = replace(application_runtime, erasure_ledger=ledger,
                  key_custody=application_key_custody)
engine = await MemoryEngine.create(config, privacy_runtime=runtime)
```

Fresh stores receive an opaque durable lineage. A workspace reuses its verified
namespace UUID. An existing protected store missing its lineage requires an
explicit versioned migration; ordinary open never invents a new identity for it.
Missing, different or behind ledgers refuse. Current independent intents and
due evidence, processing and memory deadlines are applied after attachment and
namespace binding, before work reconstruction or native index opening.

The ledger uses SQLite durable commits and an OS file lock shared by cooperating
engines. Its current ledger approval and the application's existing
`PolicyAuthority` approval are both required at actual effects. The application
still supplies authoritative policy revisions and membership. A deterministic
test policy fixture does not implement production policy management.

Restoring both the local ledger and pack to an older state can defeat local
freshness checking. A trusted external current authority is required to rule out
that rollback. A same-user process or another Python object is not an isolation
boundary. The ledger contains opaque identities, timestamps and policy/erasure
metadata and must be retained while a resurrecting pack may exist.

## Requests and progress

```python
from uuid import UUID, uuid4
from prme.privacy import ErasureRequest

request = ErasureRequest(request_id=uuid4(), mode="memory",
                        event_ids=(UUID(event_id),))
ack = await engine.request_erasure(request, user_id=owner_uuid)
progress = await engine.process_erasures(user_id=owner_uuid,
                                       limit=16, budget_ms=1000)
status = await engine.get_erasure_status(request.request_id, user_id=owner_uuid)
```

The owner and scope must match the explicit configured policy. Requests select
up to 128 exact event/node UUIDs, or `owner_all=True` for admissions no later than
the acknowledged cutoff. Later admissions are independent. Node selection is
available for memory erasure. Reuse the exact request UUID and selectors when
retrying; conflicting reuse refuses.

A node request resolves its actual owned source dependencies before the
independent intent is committed. That saved closure drives immediate source
denial, restart recovery, cleanup and verification. Inseparable retained source
representations are removed with their affected derivatives; independent
surviving admissions remain. The caller's selectors do not change on retry.
Older node intents without a saved closure require surviving provenance to
reconstruct it; missing provenance refuses protected continuation rather than
trusting an old completion status.

- `mode="evidence"` removes selected evidence, processing copies and retry
  commitments. Separately admitted facts remain; `facts_may_remain` is true.
- `mode="processing"` removes processing copies and their retry state. Evidence
  and admitted memory remain according to their independent policies.
- `mode="memory"` also removes covered nodes, uncertain mixed derivatives,
  historical operation copies, prepared/abandoned work, receipts and native
  index copies. A surviving source retains a statement only when its complete
  original explicit admission justifies the unchanged content. Membership in an
  evidence-reference list alone is insufficient.

### Preview and confirmation

`MemoryEngine.preview_erasure(request, user_id=...)` and synchronous
`MemoryClient.preview_erasure` return a closed `ErasureImpact`. The calculation
shares the actual purge dependency traversal and independent-admission support
check. It returns metadata only and never unwraps evidence or writes an intent,
job, target, progress cursor, admission, evidence link or native key. A store
with unapplied ledger entries refuses preview until authorized recovery applies
them; preview does not perform that maintenance itself.

```python
impact = await engine.preview_erasure(request, user_id=owner_uuid)
# Present impact.counts and the retained-copy limits, then confirm:
ack = await engine.request_erasure(
    request, user_id=owner_uuid, expected_impact=impact,
)
```

Counts describe targeted existing source representations (`sources`), affected
stored nodes (`memories`), their extraction/generated subset (`derivatives`),
and direct claims proven unchanged under independent surviving admission
(`independent_survivors`). The last category keeps its admitted content while
losing the erased evidence link. Missing legacy executable admission proof
cannot establish independent support. Evidence/processing-only requests report
zero memory-removal counts and `facts_may_remain=true`. Counts do not describe
physical bytes, inaccessible backups or external application copies.

The result binds exact project/owner/scope/mode, policy references and current
ledger head. Its revision covers non-content identities, dependency closure,
work generations and deadline state; it is not a hash of sensitive text or a
source checksum. A change within the selected owner/scope may conservatively
require a refresh even if displayed counts are unchanged. Confirmation
recomputes under the existing ledger and policy lease immediately before the
durable request. Changed impact refuses with `privacy_erasure_conflict`.
Current policy denial remains authoritative.

The eventual `request_id` is excluded from impact identity. A caller can preview
with a placeholder UUID, choose its stable request UUID before submission, and
reuse it after an uncertain response. An exact already-committed retry returns
its saved status even after purge changes live impact. A different request
under that UUID still refuses. The engine does not create an impact reservation;
applications must bind their confirmation to the authenticated actor, current
project authority and a finite expiry. A preview is not an erasure fence or
permission to skip current authorization. Raw privileged SQL access remains
outside the cooperating guarded-writer boundary.

The independent intent is durably committed before the live fence is
acknowledged. In-flight authorized work must settle first; later stale effects
refuse, including recovered work. Returned application/provider copies cannot
be recalled. Evidence reads deny at their exact deadline even before purge.

`process_erasures` resumes bounded discovery, SQL, native index, key-retirement
and verification stages. Its budget applies between durable steps and is not a
hard native-operation timeout. Cancellation retains locks until accepted native
work settles, so a cancelled request may have committed. Inspect status and
retry. A failure preserves its fixed failure code and attempt count and never
reports live completion. Non-content replacement operation records explicitly
mark old replay/checksum verification unavailable; they retain no deleted
payload or predictable source hash.

`expire_privacy` checks due deadlines and processes pending jobs. No built-in
offline worker or scheduled purge is promised. Key disposal requires the
explicit custody capability and retains a version needed by a surviving managed
reference. Private-part deletion failures remain pending even after custody has
recorded the key as retired. Recovery artifacts and external copies of keys are
outside this store's managed cleanup.

Local reference checks alone never authorize destruction. Newly provisioned
native keys also record the opaque managed store lineages that used each version,
under the same registry lock as retirement. Automatic retirement requires both
no local surviving reference and usage exclusively by the requesting lineage.
The recorded set is monotonic exclusive-use evidence, not complete cross-store
reference counting. It does not shrink when a store closes or purges.

A shared version, old key without usage history, use outside a managed lease,
or provider without this proof remains available. The job stays at `keys` with
`failure="provider_unavailable"` and `logical_live_copies="pending"`; its access
fence remains effective. Fresh exclusively used native keys can finish
retirement, including a retry after private-part removal fails. No global
reference service or automatic conversion of ambiguous custody is provided.
Keep the current native registry independent of pack snapshots; tampering with
or rolling back this local authority is outside same-user isolation guarantees.

The same erasure methods are available on synchronous `MemoryClient`. Protected
DuckDB `rebuild_indexes(user_id=...)` reconstructs current native indexes from
surviving admitted graph text and saved numeric vectors, preserving unrelated
owners/stages. It does not re-extract evidence or migrate embedding models;
missing protected numeric payloads refuse. PostgreSQL keeps its native vector
and lexical data in SQL and does not expose the DuckDB rebuild operation.

## CLI ownership

```sh
prme privacy erase --engine-factory application:open_privacy_engine \
  --owner OWNER_UUID --request REQUEST_UUID --event EVENT_UUID --mode memory
prme privacy status --engine-factory application:open_privacy_engine \
  --owner OWNER_UUID --request REQUEST_UUID
prme privacy purge --engine-factory application:open_privacy_engine \
  --owner OWNER_UUID --limit 16 --budget-ms 1000
```

The explicit `module:factory` returns an open configured `MemoryEngine` (or an
awaitable resolving to it), with its actual policy, ledger and custody
capabilities. The command owns and closes that engine once on success, denial or
cancellation. Do not return an application-shared engine. CLI errors are fixed
codes, and stdout is JSON. `privacy expire` uses the same owner/factory and
maintenance bounds. There is no separate permissive CLI policy service.

## What completion means

`blocked_from_access` means the live fence is acknowledged, while
`logical_live_copies="pending"` indicates cleanup remains. `live_purged` means
the recorded managed SQL, operation, work and current native copies have passed
verification. Directory Tantivy indexes are replaced with fresh managed
segments, and USearch files/single-file snapshots are saved from current durable
ownership. Independently loaded managed handles cannot save erased vectors back.

The default status reports `backup_copies="not_managed"`,
`external_copies="not_managed"` and `physical_wipe=false`. A reserved
`backup_expiry_pending`/managed-completion status is not emitted without a
verified backup integration. Restore/export scenarios remain pending #326.

SQL deletion, native compaction and checkpointing are not secure media wiping.
DuckDB pages/WAL/spill/free space, retired filesystem blocks, PostgreSQL
WAL/base pages/replicas, backups, exports, recovery keys, application caches,
already delivered responses and provider retention are separate boundaries.
The local tests inspect their owned logical stores and current native artifacts;
they do not certify inaccessible server media or external copies.

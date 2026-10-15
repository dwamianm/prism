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


## Local startup recovery amendment (2026-09-12)

Engine initialization registers cleanup for each acquired database/pool, index,
write queue and ingestion pipeline. Failure or cancellation releases those
resources in reverse order; ownership transfers to the engine only after startup
finishes. This is resource cleanup, not rollback of committed schema migrations.

If a local encrypted pack was successfully decrypted before startup failed,
cleanup closes storage handles before restoring encryption. Partial decryption
preserves untouched encrypted artifacts in the manifest. A wrong key before any
successful decryption does not modify the pack. Failure to restore ciphertext
raises `EncryptionError` explicitly; callers must not assume the pack is encrypted
in that case. Abrupt process termination still bypasses this cleanup; this is not
crash-proof at-rest encryption while an engine has decrypted files open.

## Local vector snapshot recovery amendment (2026-09-12)

DuckDB commits each newly generated embedding's float32 payload together with
its vector metadata. The `vector_payloads` table lives in `memory.duckdb`; it is
derived data, adds approximately four bytes per dimension before database
overhead/compression, and follows the database's encryption policy. PostgreSQL
already stores vectors in its transactional node rows and does not use this table.

USearch remains a debounced snapshot. On open, PRME restores keys absent from the
snapshot using the durable payloads and removes keys absent from metadata. This
recovers acknowledged inserts after abrupt process exit and prevents stale
snapshots from resurrecting committed deletions. Snapshot writes use a temporary
file in the same directory and atomic replacement, preserving the last complete
file if a new write fails. This is tested for process termination and injected
I/O failures; it is not a power-loss durability guarantee for the filesystem.

Existing packs are backfilled from their intact USearch vectors without invoking
an embedding model. Already missing legacy vectors cannot be reconstructed this
way: startup reports their count and requests `prme rebuild`. Recovery exports
native keys in one call, scans metadata once, and reads numerical payloads only
for missing vectors in batches of 256. It keeps an O(number of vector keys)
reconciliation set. `benchmarks/diagnostics/vector_startup.py` measures the vector
component separately from engine initialization and inference.

The separate payload table avoids an `ALTER TABLE` migration on metadata with a
function-based default. An abrupt-exit test reproduced DuckDB 1.4.4's WAL replay
failure on that alternative, consistent with the upstream
[DDL replay issue](https://github.com/duckdb/duckdb/issues/21490).

Cancellation during native event, graph, vector or lexical operations propagates
after that operation finishes, keeping connection/index locks held until the
worker actually stops.
A cancelled write may have committed; cancellation does not roll it back.
These changes recover numerical vectors, not arbitrary graph mutations or LLM
derivation jobs. Those still require a separate durable operation protocol.

## Single-file pack amendment (2026-10-14)

A local pack may also be a single DuckDB file, conventionally named with the
`.prme` suffix (issue #247). The layout is opt-in: the `pack_path` setting
(`PRME_PACK_PATH`) selects it, as do `prme.client.config_from_pack(path)` and a
`MemoryClient` path that ends in `.prme` and is not an existing directory.
`config.single_file_pack` reports the mode, and `pack_path` cannot be combined
with `database_url`. Directory packs (`memory.duckdb`, `vectors.usearch`,
`lexical_index/`, and `manifest.json` when encryption is on) remain the default
and are unchanged.

The file holds the same durable tables as `memory.duckdb`, `vector_payloads`
included, plus two singleton tables. `prme_manifest`
(`src/prme/storage/pack_manifest.py`) records `format_version`
(`PACK_FORMAT_VERSION`, currently 1), `prme_version` (the PRME version that
last opened the pack), `embedding_model`, `embedding_version`, `embedding_dim`,
`scoring_version` (the scoring version id of the configuration that created
the pack), `created_at` and `updated_at`. Opening a pack that has no row
writes it, naming the embedding model most of the pack's stored vectors were
built with, so a pack migrated from a directory pack names the model of its
vectors; only a pack without vectors records the configured model. A reopen
updates `prme_version` and `updated_at` only when another PRME version first
opens the pack, such as after an upgrade. Every open logs a warning, without
refusing to open, when the configured embedding model's name or dimension
differs from the recorded one. `rebuild_indexes` (`prme rebuild`)
re-embeds every vector and then records the rebuilding model. `vector_snapshot`
holds the USearch snapshot as one row of `saved_at`, `ndim`, `vector_count` and
the serialized index in `data`. It is written after every
`vector_save_interval` changes to the vector index, where an addition and a
removal each count as one, and on close, except that a close writes it only
when the index changed since the last save; each write replaces the row with
one statement on the pack's connection. A snapshot file is also written after
every removal. The row is not, because each write rewrites the whole blob, so
bulk archival would rewrite it once per node. A removal that the row does not
show yet survives a crash: its `vector_metadata` and `vector_payloads` rows are
already deleted, and startup recovery drops every snapshot key without
metadata, as it does for a snapshot file. With that,
and with the startup rebase of the vector key sequence run only when it would
change the sequence, an open that only reads leaves the file byte-identical,
apart from the first open after a session that added vectors. A retrieval
still stores its receipt. No Tantivy state is stored, and a single-file pack
has no `manifest.json`.

On open, before the engine serves any read, the vector index loads the
`vector_snapshot` row and reconciles it against `vector_payloads` and
`vector_metadata` exactly as the vector snapshot recovery amendment above
reconciles a snapshot file. A missing row, a blob USearch cannot read, or a
blob that disagrees with its own recorded dimension or count leaves an empty
index that recovery refills from the payloads. A readable snapshot whose
dimension differs from the configured model is loaded as a snapshot file would
be, so the pack still opens and `prme rebuild` can re-embed it. The engine then
fills an in-memory Tantivy index from active `nodes` with the same documents,
fields and `id` order as `rebuild_indexes`, skipping blank content. Every
engine in the process that opens the pack, named by its real path, shares
that one index (`acquire_pack_lexical_index` in
`src/prme/storage/lexical_index.py`): the first engine builds it, an engine
that opens the pack during that build waits for it, and the last engine to
close drops it. `lexical_commit_interval` and `lexical_commit_max_delay_s`
keep their meaning for visibility batching inside the process, as configured
for the engine that built the index. The committed node row becomes the
durable record of a lexical publication. RFC-0016 staging keeps its fence and
identity checks, and a crash loses only in-memory state that the next open
rebuilds.

The incremental write path, `rebuild_indexes` and the open of a single-file
pack give lexical documents to the same nodes, through one definition in
`src/prme/storage/lexical_index.py` (`has_lexical_document`, and
`LEXICAL_DOCUMENT_SQL` for the same rule in SQL). Every active node with
content gets a vector and a lexical document, except an entity node that a
journaled derivation created (its identity is recorded in
`derivation_artifact_owners`): extraction gives such an entity a vector but no
lexical document. An entity stored directly with `store()` gets both.
Previously `rebuild_indexes` indexed every active node, so a single-file pack
built with `ingest()` held more lexical documents after a reopen than before
close, and BM25 and fused results could change across a restart with an
identical event log. `prme rebuild` on a pack built with `ingest()`, a
directory pack included, therefore no longer adds lexical documents for its
extracted entities; those entities keep their vectors.

A single-file pack's lexical index after every open therefore holds the
documents the write path gave the pack before close, which are also the
documents `prme rebuild` produces. A node a session retires leaves both
indexes when it is retired: the engine's `supersede()`, `archive()` and
`resolve_contradiction()` and the organizer's jobs evict it, and so does the
derivation commit of `ingest()` for every node it replaces, such as an earlier
copy of a repeated claim (`prme.storage.index_eviction`, #247). Before that
change those copies stayed in the indexes until `index_compaction` removed
them, so BM25 results over a pack built with `ingest()` could change across a
restart; a pack built before it keeps such entries until `index_compaction`
or `prme rebuild` removes them (a single-file pack's open drops the lexical
ones). The eviction is best effort, and `index_compaction` retries what it
left. Tantivy keeps a deleted document in its segment's BM25 statistics until
a merge rewrites the segment, so an in-memory index rebuilds itself from its
live documents, in `id` order, before the first search after a deletion left
such a marker (`LexicalIndex.search`); a single-file session, a reopen and a
rebuild then give the same documents and scores, and the same rankings apart
from the tie order described after the migration paragraph below
(`tests/test_single_file_pack.py`). The rebuild is one pass over the live
documents, with the index's single-threaded writer: measured on 2026-10-15
with 20-word documents, 12 ms for 1,413 documents, 88 ms for 10,000 and
0.88 s for 100,000, against a startup load of 8 ms, 44 ms and 0.44 s for the
same documents. A rebuild that fails is logged (`lexical.purge_failed`) and
the old index serves on with its stale statistics. Every Tantivy writer PRME
creates indexes on one thread, so a batch's documents land in one segment in
the order they were added and two loads of the same documents put tied
scores in the same order; with several threads the split between segments
depended on scheduling, and two opens of one `.prme` file could cut a
different set of tied hits at `lexical_k`. A directory pack keeps the
deleted-document markers on disk, as it always has: it scores the same before
and after a restart, and can differ slightly from a `prme rebuild` until
Tantivy merges the segment. The same indexes serve the same data, rebuilds
keep `id` order, and `vector_exact_search` stays on by default; the
difference that remains between the two layouts is described after the
migration paragraph below. `tests/test_single_file_pack.py` checks that the
file is the whole pack at rest, that reopening or copying it gives identical
retrieval receipts, that a missing, stale, unreadable or miscounted snapshot
row is restored from the payloads, that vector removals count toward the save
interval and survive a crash, that the lexical index after open equals a
rebuild, that a pack built with `ingest()` keeps its lexical documents and
retrieval rankings across a reopen and a rebuild in either layout, that a
claim `ingest()` retires leaves both indexes at the commit and a reopened pack
scores like the next open after such an eviction, that a failed eviction or
in-memory rebuild is logged and repaired or survived, that two loads of the
same documents order ties alike, and that engines in one process share a
pack's lexical index.

Storing the vector snapshot but rebuilding the lexical index follows
measurements taken on 2026-10-14 with duckdb 1.4.4, usearch 2.26.2 and tantivy
0.25.0. For a local LoCoMo benchmark pack with 1,413 nodes and 1,413 vectors,
USearch restores the vectors from a 2.4 MB snapshot in 1 ms and rebuilds them
from payloads in 19 ms, and Tantivy builds the 1,413 node documents in 0.01 s.
At 100,000 synthetic 384-dimension vectors a rebuild takes 11.3 s, while
Tantivy builds 100,000 synthetic documents in 0.39 s. Each interval save
rewrites the whole snapshot blob, about 168 MB per 100,000 384-dimension
vectors, so large indexes may want a rarer `vector_save_interval`;
`prme compact` reclaims the blocks the replaced rows leave behind.

While a pack is open, DuckDB keeps `<pack>.wal` beside the file, as it does for
any database. When DuckDB spills to disk, its temporary files go to a
`<pack>.tmp` directory beside the file. That is DuckDB's own default for a
plain pack. For an encrypted pack the engine sets `temp_directory` to it,
because the in-memory DuckDB instance that attaches the pack would otherwise
spill into `.tmp` in the working directory. The pack is one file at rest; a
copy taken while an engine has it open can miss changes that are still only in
the WAL.

Single-file packs use DuckDB's native encryption instead of Fernet file
encryption. `duckdb.connect()` does not accept an encryption key, so the engine
(`_connect_single_file_pack` in `src/prme/storage/engine.py`) connects to an
in-memory database, attaches the pack with `ATTACH ... (ENCRYPTION_KEY ...)` and
makes it the default database with `USE`; its `_PackConnection` wrapper repeats
the `USE` on every cursor it opens. DuckDB encrypts the main file and its WAL
with AES-256-GCM. DuckDB 1.4 does not encrypt temporary files by default, even
for an encrypted database (measured with 1.4.4), so the engine also turns on
`temp_file_encryption`, with `temp_directory` set to `<pack>.tmp` as described
above. Stored text therefore does not appear in clear on disk while an engine
runs. A directory pack's Fernet path, by contrast, decrypts files while they
are open (see the startup recovery amendment above). `MemoryEngine.lock()` and
`unlock()`, which move directory pack files in and out of Fernet, raise
`RuntimeError` for a single-file pack.

DuckDB does not document how it derives its data key from the key string, so
PRME derives the key it hands to DuckDB (`derive_pack_key` in
`src/prme/storage/encryption.py`) from `encryption_key`, read as directory
packs read it. A value with the `raw_key:` prefix, or an unprefixed
44-character url-safe base64 string that decodes to 32 bytes (a Fernet key), is
a raw key: DuckDB receives its 32 bytes as 64 hex characters, and no salt is
involved. Any other value, or one with the `passphrase:` prefix, is a
passphrase: PRME derives 32 bytes from it with PBKDF2-HMAC-SHA256 over 600,000
iterations, the same count as for directory packs, and hands them to DuckDB as
64 hex characters. A raw key that does not decode to 32 bytes is refused before
any file is touched. A per-pack random salt cannot live inside a file that
needs the key to open, so single-file packs use the fixed, versioned salt
`prme-single-file-pack-v1` (`_PACK_KEY_SALT`). Identical passphrases therefore
give identical keys across packs, and a key derived from a guessed passphrase
can be tried against every single-file pack: each guess still costs the full
iteration count, but once rather than once per pack.

Opening an encrypted pack without its key, opening it with a wrong key, and
opening an unencrypted pack with encryption configured each raise
`EncryptionError` instead of opening or encrypting the pack. The file, and any
WAL already beside it, stay byte-identical, no new WAL appears, and no message
quotes the configured key or the key handed to DuckDB. DuckDB attaches an
encrypted file to only one instance per process, so an encrypted pack can be
open in only one engine per process; a second open raises `PackInUseError`.
DuckDB tells files apart by their path strings, so the engine opens a pack by
its real path, and two spellings of one pack (relative and absolute, or
through a symbolic link) meet that limit or share an instance like one
spelling. Engines in one process that open the same plain pack share one
DuckDB instance, as they do for a `memory.duckdb`, and one in-memory Tantivy
index (see above), so a node stored through one engine is found by lexical
search through the others at once, as engines on a directory pack share its
on-disk index. Each `MemoryClient` runs its engine on its own event loop, so
the shared index takes a write lock that waits on any loop. Each engine still
keeps its own USearch index, so vector search through one engine finds a
vector added through another only after it reopens the pack, on either
layout. Sharing applies to encrypted packs too, but such a pack can be open in
only one engine per process. Directory packs keep Fernet
encryption, and legacy encrypted directory packs still open. Native encryption
needs DuckDB 1.4.0 or later, so PRME now requires `duckdb>=1.4.0`.

DuckDB never shrinks a database file. `prme compact <pack>` (`compact_pack` in
`src/prme/storage/pack_tools.py`) works only on a closed pack. It refuses a
pack with `<pack>.wal` beside it, since that log shows the pack open or not
closed cleanly. DuckDB's file lock refuses a pack that another process holds
open, and within one process every engine that uses the pack must be closed
first. The command attaches the pack read only, and a new hidden file in the
same directory, to an in-memory DuckDB instance, copies every table, index and
sequence with `COPY FROM DATABASE`, and replaces the original with one rename,
so a failure at any point leaves the original untouched. The command works on
the pack's real path, so through a symbolic link it checks the WAL beside the
file the link names, compacts that file and leaves the link in place, and the
copy takes the original's permission bits before the rename. The copy keeps the
source's storage version and, for an encrypted pack, its key and cipher. Rows,
node IDs and vector keys do not change, so the command also compacts the
`memory.duckdb` of a directory pack, whose other files stay valid.

On a copy of the LoCoMo benchmark pack measured above, `prme compact` took its
743.5 MiB `memory.duckdb` to 510.5 MiB in 1.0 s, and retrieval results were
identical before and after. The live data is about 510 MiB, of which 492.7 MiB
is 404 stored retrieval receipts (`RETRIEVAL_REQUEST` rows in
`operations.payload`, up to 2.4 MiB each). `pragma_storage_info` does not
count the overflow blocks that hold large strings, so it understates such a
pack's live data; it showed only about 28 blocks of 256 KiB for this pack,
which is where the estimate of about 7 MiB in issue #247 came from. How large
stored receipts should be is a separate matter from the pack layout.

`prme migrate <directory> <pack.prme>` (`migrate_directory_pack`) copies a
directory pack into a new single-file pack and leaves the directory in place.
The command requires a name that ends in `.prme` and never overwrites: a
destination that exists, or that has a WAL beside it, is refused. It opens the
directory pack once with an engine, which folds pending writes into
`memory.duckdb` and backfills vector payloads from a legacy `vectors.usearch`,
and copies the database with `COPY FROM DATABASE` through that engine's own
connection. Copying through the open engine is what lets a Fernet-encrypted
directory pack migrate: the engine decrypts it as any open does, and its close
encrypts it again, so the source is encrypted at rest before and after. The
copy goes to a hidden temporary file beside the destination. That file is
opened once as a single-file pack, so every vector is restored from its
payload and the vector snapshot and the manifest are written. Migration then
checks that the file holds a snapshot row and exactly one manifest row, and
only then moves it into place, so a failure never leaves a partial pack. When
a key is given, the new pack is
encrypted natively under that key, whether or not the source was encrypted;
without one, it is not encrypted. The LoCoMo benchmark pack above, with 1,413
nodes, 556 edges, 419 events and 1,413 vectors, migrated into a 521.5 MiB
`.prme` file in 1.4 s.

Retrieval from a single-file pack and from a directory pack holding the same
data can differ in one way: BM25 scores that tie can come back in a different
order from the in-memory index than from a directory index, even one just
rebuilt with `prme rebuild`, and `lexical_k` cuts the candidates in Tantivy's
order, so a tie can reach the final ranks. Tied hits come back in segment
order, and a single-file pack's open loads every document into one segment in
`id` order on one writer thread (one segment up to at least 100,000 documents,
measured on 2026-10-15), so two opens of the same `.prme` file give the same
order; `prme rebuild` on a single-file pack ends by settling its in-memory
index into that one segment (`LexicalIndex.settle`), so a rebuilt pack reads
like a reopened one. A session that only added documents holds them in write
order across several segments, as a directory index does, so its tie order
can differ from the next open's. A pack migrated from a directory pack built with
`ingest()` holds the same lexical documents as its source, since both follow
the write path's policy (see above): the LoCoMo source held 827 of its 1,413
active nodes in its Tantivy index, its 586 extracted entity nodes having none,
and the policy selects exactly those 827 nodes from its graph. Vector hits are
identical. Before that policy was shared, the migrated pack indexed all 1,413
nodes. Directory-pack retrieval is
unchanged, except after `prme rebuild` on a pack built with `ingest()` (see
above): on the LoCoMo half of the offline evidence gate (1,540 questions),
the contexts from directory packs were byte-identical with and without
single-file pack support. That gate ran before the lexical policy was shared.
The LongMemEval-S half could not be run, because its saved control captures
were not present on the machine that ran the gate.

Every `prme` command that takes a `db_path` also opens a `.prme` file, among
them `prme rebuild`, `prme export` and `prme info`. The public
`prme.client.is_pack_file(path)` decides which layout a path selects: a name
ending in `.prme` that is not an existing directory is a single-file pack.
`prme init <pack.prme>` creates exactly that one file, encrypted when
encryption is configured, and writes no `.env.example` beside it; it prints a
hint instead. `prme doctor <pack.prme>` checks the file, the required tables
(`events`, `nodes`, `prme_manifest` and `vector_snapshot`; a pack that has
observed images or video also holds the optional `blobs` table of RFC-0018,
copied like every other table) and the manifest,
and warns about a WAL beside the file and about a missing or stale vector
snapshot. `prme doctor`, `prme compact` and `prme migrate` take the key of an
encrypted pack with `--encryption-key`, where an empty value means no key.
Without it, for a `.prme` pack, they use `PRME_ENCRYPTION_KEY` when
`PRME_ENCRYPTION_ENABLED` is true, which is where `prme info` and the other
commands always read it. They read only those two settings, from the
environment and then a `.env` file, so an invalid setting elsewhere does not
stop them. A directory pack's `memory.duckdb` never takes those settings,
which configure its Fernet file encryption.

`prme-mcp --db-path <pack.prme>` serves a single-file pack by setting
`PRME_PACK_PATH`, and sets `PRME_DATABASE_URL` to an empty value so a database
URL from the environment cannot redirect it. Given a directory, it sets
`PRME_PACK_PATH` to an empty value, which means unset and outranks a `.env`
file, so a pack path configured elsewhere cannot take the directory's place.
`MemoryWorkspace(..., single_file_packs=True)`, which `MemoryWorkspace.open()`
also accepts, keeps each namespace's pack in `packs/<namespace id>/memory.prme`.
The workspace registry does not record the layout, so every open of a
workspace must pass the same setting; an initialized namespace whose pack is
missing in the requested layout is refused rather than recreated. In the
directory layout, a `pack_path` inherited from the workspace configuration is
cleared for every namespace. Directory packs keep opening as before.

The single-file format is opt-in, and new packs keep the directory layout.
Making the single file the default for new packs is a separate decision, out of
scope for issue #247.

## Visual memory evidence amendment (2026-10-15)

[RFC-0018](RFC-0018-Visual-Memory.md) is a Draft design, with a feasibility spike
measured on 2026-10-15, for adding images and video as memory sources. Under it, the
pack holds one small thumbnail per observation (JPEG, 320 pixels wide, about 20 KB),
named by the SHA-256 of the keyframe: the file `frames/<sha256>.jpg` in a directory
pack, and a row of a `blobs` table keyed by the same hash in a single-file pack (see
the single-file pack amendment above). Thumbnails inherit the pack's encryption. Raw
frames and videos stay outside the pack, referenced by path and hash in the
observation event's metadata.

The pack therefore stays small and copyable, and `prme rebuild` needs no media,
because the descriptions are durable events. A pack that never used `observe()` is
byte-for-byte what it is today. This amendment changes no sync, merge or federation
rule in this RFC.

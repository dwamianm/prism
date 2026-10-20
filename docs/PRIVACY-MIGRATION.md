# Protected migration and portable copies

Protection is an explicit conversion to a separate destination. Opening an old
store does not rewrite its plaintext, change its original records/checksums, or
label it protected. Use the current independent erasure ledger and the exact
saved key/model capabilities. A copied checkpoint is not current authority.

This implementation is still undergoing PV-07 integration. The direct-intent
migration, closed exports and restore controls have component evidence. Mixed
legacy derivations/profiles, the complete shared scenarios, and the fixed-clock
public retrieval comparison remain pending; see
[PV-07 results](plans/2026-10-evidence-privacy/PV-07-results.json).

## Explicit local commands

Supply a `module:factory` that returns the configured current `PrivacyRuntime`,
and a separate embedding provider factory with the saved model, version and
dimension. The runtime owns policy, sanitizer, public crypto provider, independent
erasure ledger and retry commitment capabilities. Migration never provisions a
replacement key to make an old job readable. The engine factory transfers an
open protected engine to a command, which closes it afterward.

```sh
prme privacy dry-run --source ./legacy --policy-file ./policy.json
prme privacy migrate --source ./legacy --target ./protected.prme \
  --policy-file ./policy.json --runtime-factory local_capabilities:runtime \
  --embedding-factory local_capabilities:embedding --job ./migration-job \
  --unavailable-legacy-history
prme privacy migration-status --job ./migration-job
# Resume with the same source, target, policy, dispositions and job:
prme privacy migrate --source ./legacy --target ./protected.prme \
  --policy-file ./policy.json --runtime-factory local_capabilities:runtime \
  --embedding-factory local_capabilities:embedding --job ./migration-job \
  --unavailable-legacy-history --resume
prme privacy policy --engine-factory local_capabilities:engine --owner UUID
prme privacy export --engine-factory local_capabilities:engine --output ./protected.json
prme privacy export --engine-factory local_capabilities:engine \
  --output ./admitted.json --mode active_memory_only
prme privacy restore --artifact ./protected.json --target ./restored.prme \
  --policy-file ./policy.json --runtime-factory local_capabilities:runtime \
  --embedding-factory local_capabilities:embedding
```

These example modules are operator-supplied capabilities, not built-in defaults.
A PostgreSQL source or target is selected with `--source-database-env NAME` or
`--target-database-env NAME`; the value is never printed. Explicit local paths
cannot be redirected by ambient database or pack settings. A closed Fernet
source uses `--source-key-env NAME`; the existing store key is distinct from
private evidence keys. No private key or passphrase is accepted in argv.

Dry-run enumerates C01–C45, policy/key needs, copy dispositions and unavailable
features. It makes no sealed claim and creates no job. Unknown external backups
remain unknown. Visual records require an explicit removal disposition using
`--remove-event UUID` and, where needed, `--remove-node UUID`. Ambiguous legacy
identities or unsupported graph kinds refuse. `--allow-reembedding` explicitly
permits the supplied embedding provider to encode changed admitted statements.
Unchanged vectors retain their numerical values.

The owned job freezes the source generation, opaque identities, policy and
original deadlines with the existing authenticated retry commitment. A batch
and its event cursor commit together. Interrupted output refuses normal serving.
A changed source or tampered job refuses resume. Original history kept solely in
the retained source is represented by typed unavailable references in the new
store; those references do not claim the original checksum or replay payload.
The private v2 Event adapter's empty checksum is never an original source hash.

## Export and restore boundaries

`MemoryEngine.export_protected(path)` and `MemoryClient.export_protected(path)`
write the same closed format as the CLI. Default export includes encrypted
sanitized evidence, admitted memory, validated executable records and opaque
metadata. It performs no private unwrap and never includes recovery secrets,
independent ledger authority, arbitrary SQL tables, native index snapshots, or
operator rotation jobs. Admitted facts remain sensitive. Explicit
`active_memory_only` excludes evidence, pending processing, vectors and execution
history; it is not a restorable snapshot. Ordinary `prme export` is labeled
`legacy_plaintext` and cannot export a protected store through the legacy path.

`prme.storage.privacy_export.restore_protected_export` accepts only this closed
protected format into an empty selected destination. It validates format,
dependencies, lineage, immutable payload context and model metadata. It acquires
the current independent ledger, applies erasure/expiry fences and invalidates
imported work leases before recovery or native index opening. Current guards
remain held through publication and managed-key usage joins the restored
lineage. A snapshot-only, missing, behind or foreign authority is not a deletion
continuity proof. Active-memory-only output and arbitrary old CLI JSON are not
accepted by this reader.

Current logical restore supports local directory/single-file destinations and
an explicitly selected empty PostgreSQL schema. Occupied replacement and bound
PostgreSQL workspace recovery are not yet supported by this API. Native
PostgreSQL backup/restore and copied-pack reopening remain distinct integration
routes; no arbitrary SQL importer or authentication-database restoration is
provided. Whole-directory Fernet encryption is accepted for source reading;
protected logical restore currently requires an unencrypted directory destination
or a native encrypted `.prme` destination. Evidence remains independently
protected in either supported destination.

The source reader uses bounded native DuckDB ATTACH over an operation-owned
read-only memory filesystem. It authenticates closed Fernet bytes in memory,
refuses an outstanding WAL or ambiguous plaintext/encrypted siblings, disables
spill/network fallback, and releases its own buffers. It does not promise heap
or swap zeroization. Capacity limits refuse rather than write plaintext temp
files. Originals, lexical files, key backups, exports, PostgreSQL WAL/replicas
and external archives remain separately retained copies. No automatic disposal,
physical media erasure, anonymity, offline revocation, or hostile same-process
isolation is claimed. A trusted same user rolling back both pack and local
ledger cannot be detected without an independent external freshness authority.

The protected event schema omits `events.content_hash` and `idx_events_hash`.
Pinned v0.15.0 and v0.16.0a4 readers were tested with working legacy controls:
engine, client, CLI, HTTP, MCP and workspace for local layouts; engine, client,
HTTP, MCP and workspace for PostgreSQL. They refuse at their real schema
initializer before provider creation or content/admission/recovery changes.
This is bounded to those versions and paths. It is not a guarantee about every
old fork or raw SQL access. Current readers explicitly validate the supported
format and lineage rather than inferring protection from that old-reader error.

## Keys and diagnostic actions

Use the existing [key commands](PRIVACY-KEYS.md) for provisioning/status,
exact selected-ID finite audited unlock, rotation/resume and deliberate encrypted
recovery export/import. Recovery artifacts belong outside ordinary pack/export
boundaries. Private evidence loss cannot be fixed from a normal export; only a
separately retained compatible recovery artifact can restore the private key.
There is no plaintext fallback. Policy/status and admitted data can remain
available under separate protection. `privacy erase`, `status`, `purge` and
`expire` use the current engine authority and report managed progress separately
from backup/external copies. Archive and clear-chat actions are not erasure.

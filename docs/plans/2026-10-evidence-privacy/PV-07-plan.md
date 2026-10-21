# PV-07 execution plan

Implementation released on 2026-10-20 at `4058b00dc693c9903ba302d677accfe8cf988295`.
The approved preparation below defines the original six acceptance criteria and
finite validation scope. Its historical base, preparation-only stop and dependency
statuses are superseded by `326-prerequisite-api-handoff.md` in the root run.
Two unsuccessful correction cycles require explicit USER approval before a third.
The #323 classifier/graph-scoping strategy is held and is not this ticket's scope.
Full-suite/package/simulation/CI validation awaits root integration readiness.
No subagents, paid calls, long benchmarks, deployment or release are authorized.
Only task-owned artifacts and the assigned `privacy_engine_326` and
`privacy_engine_326_restore` PostgreSQL databases may be used.

Original preparation SHA256: `f9bab1a608e19d48a3770536a809786cf109655e61c65d1e24855da96f0c8e34`.
No original AC is waived; component checks do not constitute whole-case passes.

---

# PV-07 / #326 preparation

Status: read-only preparation complete; implementation has not started. Code and runtime evidence remain pending. This plan is not dependency completion, a passed scenario, or protected-product evidence.

Issue: https://github.com/dwamianm/prism/issues/326
Worktree: `/Users/dwamianm/Sites/prism/.claude/worktrees/privacy-326-migration-export`
Branch: `issue-326-migration-export`; root-supplied base: `main` at `33222b54a85fa90fd23d47ae9a9170564bf24828`.

Only this ignored preparation file is owned/written in this turn. No tracked edit, install, environment creation, database use, test run, GitHub write, commit, push, PR, deployment, release, or subagent was performed. Root owns state, selection, resources, reviews, merges and closure. No repair cycle has run. Stop after this plan/readiness report until root releases implementation.

## Original acceptance criteria and validation, unchanged

- [ ] Migration dry-run reports plaintext copies, policy changes, key requirements,
      retained backups and unavailable features. No store is labeled sealed until
      all required copy transformations and checks have completed.
- [ ] Migration journals are resumable and respect erasure fences. Legacy records
      keep their original version/checksum identity; unsupported readers refuse
      new protected schemas rather than misinterpreting encrypted fields.
- [ ] Default exports contain encrypted evidence and metadata, never recovery
      secrets. Active-memory-only exports clearly describe that content; plaintext
      diagnostic evidence export is not silently enabled by this ticket.
- [ ] Old source packs and backups are explicitly listed as remaining copies.
      Migration leaves originals intact until an operator deliberately disposes
      of them; no false automatic-erasure claim.
- [ ] Restore/import accepts the current erasure ledger and enforces it before
      serving. A snapshot without a newer ledger cannot claim deletion continuity.
      Document the limits of arbitrary offline portable copies.
- [ ] Local CLI can inspect policy/status, request erasure and perform an explicit
      scoped diagnostic unlock; key loss and unsupported mode errors are usable.

Validation: share the eight encryption/migration scenarios with [PV-03](https://github.com/dwamianm/prism/issues/321), including
legacy fixtures, interruption, wrong keys, rotation and restore of a pre-erasure
snapshot. Compare standard-mode saved retrieval before/after migration at a fixed
clock before expiry. No new benchmark track or cloud resources.

AC1-AC6 below refer to these six bullets in order. The issue body also names directory/single-file/PostgreSQL schema migration, protected export/import, key provisioning/rotation recovery, local CLI policy inspection and restore rules. These requirements are not replaced by a reduced test matrix.

Done means the six original criteria have real backend and public-path evidence, intact legacy records remain readable with their original bytes/versions/checksums, protected conversion occurs only through an explicit versioned operation, finite checks below pass, root's required review completes, and the authorized commit/push/PR/delivery steps finish. No benchmark/default change, new key-store architecture, hosted KMS, full synchronization/federation, arbitrary legacy JSON importer, physical erase, anonymity or malicious-same-process isolation claim is included.

Two failed fix-and-validation cycles for the same unresolved behavior end that strategy. Preserve both failures, the violated AC/regression/security issue and a bounded alternative; explicit USER approval is needed before a third. No new reviewer or evaluation cohort is authorized here. Run targeted affected checks while implementing and required full checks once on the final revision; repeat a passing check only for a relevant change/failure/concrete concern. Report actual commands, time, cases, layouts and logs, never invented quota/cost.

## Instructions, dependencies and reused evidence

Read applicable home/engine/worktree AGENTS.md, the supplied fresh issue JSON, issue-worker contract, complete start-issue SKILL.md and references/workflow.md. The explicit preparation-only assignment overrides setup/implementation/ship mutations. Read RFC-0019 in full, relevant RFC-0014 portability/recovery/layout sections, WORKSPACES.md identity/restore limits, the frozen scenario/inventory contracts and the sanitizer boundary. Reused `preparation-320.md` through `preparation-324.md` plus `cross-ticket-handoffs.md` for admission, private custody, sanitizer, ordinary-read and journal work rather than repeating those surveys.

#319 is merged in this tree. #320 is implementing; #321-#325 have preparation plans but are not implemented. Their plans are interface expectations, not working dependencies. Reconcile exact symbols against reviewed merged code before implementing this plan. Dependency readiness may come from verified merged code with nonclosing `Refs` while a shared whole-case gate keeps its issue open; this avoids cyclic closure requirements without weakening ACs.

| Owner | Seam consumed here | AC / required evidence |
| --- | --- | --- |
| #320 | Immutable saved scoped policy, trusted source/representation roles, atomic evidence/work slots, metadata-only inspection and pre-backend capability readiness. | AC1-AC3, AC6. A changed process default cannot reclassify old sources. |
| #321 | Real codec, exact version public wrap/private unwrap capabilities, native custody, selected-ID runtime/diagnostic access and audit, rewrap jobs, deliberate encrypted PKCS#8 recovery outside the pack. | AC1-AC3, AC6; EM01-EM06 and AC02. No new crypto or recovery-file format here. |
| #322 | Real pinned sanitizer and generated-output admission; complete qualifier/negation/time/attribution validation, trusted issued-token provenance and private sanitized-source checksum. | AC1-AC2; EM07, CL08 and supported-ingestion subchecks. Encrypting an unsanitized original does not satisfy v1. |
| #323 | Actual admitted node/edge/read projection, metadata-only provenance/history and availability; no ordinary diagnostic unwrap. | AC1, AC3, AC6; migration classification/export cannot promote an arbitrary source paragraph into admitted memory. |
| #324 | Closed protected journal/receipt families, admitted executable records, protected diagnostic refs, typed unavailable records, terminal work/dependency/recovery dispatch and separate retry commitments. | AC1-AC3, AC5; do not invent another journal codec or relabel erased bytes as a legacy record. |
| #325 | Independent current ledger and durable lineage, effect subject/dependency fences, live purge, work invalidation, index reconciliation and truthful erasure/remaining-copy status. | AC2, AC4-AC6; ER07/ER08, EM04/EM07. This ticket consumes these; it does not implement purge or a second ledger. |
| #327 | Combined 52-per-backend / 104-execution gate, one frozen 24-query storage-parity baseline/final run and separate 24 sealed-behavior cases. | Overall E1-E9/protected-product claim. Partial component results never count as a whole shared pass. |

Root confirmed that #326 owns the format/preflight/SQL dispatch integration described below; no new #320 architecture is required. Root also accepted the narrow protected snapshot reader within the stated protected export/import scope. #325 confirmed ledger startup ordering and lineage reuse. Its local current-ledger adapter follows the existing SQLite/FileLock pattern outside the pack/restore domain; external-ledger support remains a capability interface, not hosted infrastructure. Exact independent authority freshness cannot be inferred from a pack or from an operator-provided old ledger file.

## Source trace and premise challenge

1. **Existing export is not a protected backup protocol.** `src/prme/cli.py:785-831` uses `engine._conn`, enumerates all nodes/edges, reads `events.content` directly and prints JSON. It lacks a complete journal/policy/restore contract and is local DuckDB only. `_create_engine` at `:65-114` prevents ambient PostgreSQL redirection; keep that safeguard. `main` at `:1638-1665` prints arbitrary exceptions, so privacy handlers must translate to fixed codes before they reach it. A sealed public projection does not make this raw SQL path safe automatically.
2. **Existing migration is physical layout copying.** `storage/pack_tools.py:336-436` opens the source as an ordinary engine, backfills vectors/normal schema, copies every table with `COPY FROM DATABASE`, opens the staged target and checks counts/snapshot/manifest. It leaves the original directory, but its ordinary open/close can change source schema, vector payloads and encrypted file bytes. Its exception cleanup removes the temporary file: it has no durable resumable privacy job. Reusing it as the privacy dry-run would mutate the inspected source and copy unclassified receipts/blobs/journals before classification.
3. **Physical copying does not classify content or prove erasure.** `_copy_database` at `pack_tools.py:218-234` copies every table/index/sequence; `compact_pack` at `:236-293` swaps a fresh database and preserves native storage layout/key/cipher. The WAL check at `:149-157` refuses open/unclean packs. `_publish` at `:311-324` prevents ordinary destination overwrite, but protected publication additionally needs the current erasure fence and destination ownership validation. Compaction cannot prove removal from old source packs, WAL archives, filesystem pages or external exports.
4. **The format number is currently advisory.** `pack_manifest.py:22,55-103` writes format 1, but its read selects only PRME/model metadata; it never enforces `format_version`. Directory Fernet `manifest.json` records encryption details, not protected schema support or private keys. Adding a new flag cannot make already-released old readers fail closed.
5. **Startup is a restoration path with side effects.** `engine.py:606-648` initializes schema and then vector/native lexical indexes; `schema.py:547-575` reconstructs derivation/profile/consolidation registries. PostgreSQL does the same in `engine.py:790-809` and `pg/schema.py:150-258`. A single-file lexical index rebuilds from graph before serving. The current ledger and expiry fence must apply before any of these recovery/index/publication paths, not after an engine is returned.
6. **Existing identity is reusable only for bound stores.** `namespace_identity.py:12-35` stores and verifies a version-1 namespace UUID for local workspace packs. `pg/namespace.py:24-35,91-125` stores that UUID plus initialized relation names; `NamespacePool.acquire` checks identity on every checkout, and initialization is serialized in a transaction. `workspace.py:275-292,461-465` refuses missing initialized stores. Unbound packs/PG databases have no durable store UUID in `pack_manifest`. Reuse the bound namespace UUID, and consume #325's one persistent opaque `privacy_store_id` for unbound protected stores. Paths, database URLs and workspace display names are not lineage. Migration/restore preserves lineage; arbitrary cross-project rebinding is unsupported.
7. **Actual restore routes already exist without a general importer.** `documentation/deployment.md:39-61` documents closed directory and `.prme` copies and then JSON export. `docs/WORKSPACES.md:156-168` says PostgreSQL-native backup/restore must include schemas/registry/extensions and explicitly excludes workspace import. `MemoryClient`/`MemoryEngine` reopen copied packs; public store/ingest/batch/fast APIs ingest user records. A JSON dump named `backup.json` is not proof of a complete restorable pack.
8. **Legacy records and visual payloads need classification.** Reuse #324's inventory of extraction/direct-store/derivation/profile/summary/alias/receipt/history payloads and #325's dependency/purge coverage. `storage/blobs.py:1-15,21-50` stores thumbnails and predictable frame-hash keys in the DB in both local layouts; `pack_tools` currently copies them too. Protected v1 must explicitly refuse these legacy visual components or remove them under a deliberate migration plan, with typed unavailable identities and a remaining original-copy report. It cannot claim visual sanitization.
9. **Per-layout indexes differ.** C11 already includes PostgreSQL `nodes.embedding` and its model/version, in addition to local vector metadata/payloads. C12 groups local `vector_staging` with shared derivation/profile registries; PostgreSQL has no local USearch staging table to delete. `vector_index.py:133-170` shows local payloads/staging/snapshot. Each migration/restore inspector records present, absent-by-layout or externally managed for every inventoried copy. An absent local table in PostgreSQL is not proof of purge.
10. **Fixed-input parity can genuinely fail on a layout change.** RFC-0014 `:568-585` documents directory/single-file BM25 tie-order differences from segment construction. Existing `test_cli_single_file.py:465-628` checks logical read-back, source survival, encryption and failure cleanup, not the frozen 24-query gate. Preserve exact selected IDs/order/packed-text evidence; do not replace failed queries, loosen equality or call an encryption regression a sanitation/sealed loss. Root was notified. A gate failure needs its AC-linked bounded repair strategy, not another evaluation cohort.
11. **The existing Fernet source-open path cannot be reused unchanged.** `encryption.py:197-220,269-310` writes a plaintext sibling and removes the `.enc` after successful decrypt; normal close re-encrypts. A closed-Fernet dry-run cannot call this and claim no writes/no plaintext window. Root requested one bounded primary-source check before dropping that coverage. The supported filesystem composition below provides a concrete candidate for a bytes-only source reader. Closed Fernet directory coverage remains required and runtime-unproven; unavailable-feature reporting is not permission to quietly exclude it.

Relevant scoped history: `0e9589b2` added CLI pack tools, `3cac5164` hardened single-file handling, `c5b012c6` added the layout, `217facb0`/`b7521bc6` added identity-checked local/PG workspaces, and `e2160d87` added visual content. Current source falsifies the premises that pack encryption alone is sealing, a manifest flag provides historical-reader compatibility, or a snapshot-local ledger establishes deletion continuity.

## Explicit format boundary and startup sequence

This is a source-backed candidate approved for planning by root, **not runtime-proven compatibility**.

Protected event schema v2 omits legacy `events.content_hash` and the `idx_events_hash` index; its original source checksum exists only inside the protected representation supplied by #320/#321. The explicit format/capability record declares the protected event/journal schemas and migration completion state. New protected event SQL selects the typed representation refs; it does not fabricate a legacy empty-string checksum or put ciphertext into a text field that old readers interpret as plaintext. Legacy schema v1, old `Event`/record serializers and original checksum verification remain unchanged. Remove the old hash index during explicit conversion so old `IF NOT EXISTS` cannot skip the incompatible column check.

Two specific readers are pinned from repository tags:

| Reader | Exact commit | Source-backed refusal path |
| --- | --- | --- |
| PRME v0.15.0 | `6963c574065aaf7ef8552b6076c80726c1c167bf` | Local `schema.py:58` and PG `pg/schema.py:44` attempt `CREATE INDEX IF NOT EXISTS idx_events_hash ON events(content_hash)` before work/registry recovery. |
| PRME v0.16.0a4 | `13b25058e2bb1612e5a0dfafc5b30c5ae2d671b0` | Same unconditional index creation before registry recovery in both backends. |

The normal opening paths propagate startup failure: `MemoryClient` waits on `future.result` then reraises and closes its loop; CLI `_create_engine` awaits `MemoryEngine.create` and `main` exits nonzero; FastAPI lifespan sets `app.state.engine` only after successful creation; MCP lifespan enters `MemoryEngine.open` before publishing a shared engine; local workspace calls `MemoryEngine.create`, while PG workspace preparation runs its schema initializer before constructing the engine. Trace these paths in the pinned source and execute the bounded probes below. A missing dependency/key, wrong namespace/dimension, nonexistent file, model failure or deliberately broken fixture is never format-refusal evidence. Metadata-only raw SQL/pack inspection remains trusted operator access, not a claim of hostile old-code isolation.

New-reader preflight must explicitly validate the declared supported format, schema shape, lineage and capabilities; it does not depend on the old reader's incidental SQL failure. Missing `content_hash` alone is not a protected-status indicator. Normal open never creates a fresh store next to an unfamiliar protected filename or upgrades legacy source bytes into protected records.

Required order, confirmed with root/#325:

1. Run #320/#321 readiness before new protected backend creation. Never automatically unlock/provision missing private keys.
2. Attach the selected existing backend and verify namespace identity. Run no-content format/capability/lineage preflight immediately, before ordinary schema mutation/registry reconstruction or provider/index creation. A missing initialized store, unknown version, in-progress migration or ambiguous lineage refuses ordinary serving.
3. Split version-appropriate schema setup from recovery. Apply #325's independently current ledger, pending durable erasure intents and expiry fences, including recovered-work generation invalidation, before registry/native-index initialization. Reject stale/missing authority for protected restore. Store applied ledger identity/sequence as evidence, never as a freshness oracle.
4. Use #324 recovery dispatch and #325 index reconciliation over surviving admitted state. Recheck authority at the serving/publication boundary. An erasure between preflight and publication cannot resurrect copied rows, stale stages or a completed migration job.

#325's concrete proposed calls are `ledger.current_head(lineage) -> (ledger_id, lineage_id, sequence)`, `entries_since(applied_sequence)`, and `apply_current_ledger(head, entries)` before recovery; the local adapter lives outside the pack. Its request ordering is independent durable intent first, then live SQL fence/purge-job/work-invalidation commit, then acknowledgment. Consume this ordering and its exact merged API.

For PostgreSQL workspace startup, the hook must run inside `prepare_namespace` before its call to `initialize_pg_database`, not only later in `_create_postgres`. Preserve identity/relation checks, qualified pgvector and pool ownership. For local startup, use the attached connection before `initialize_database`; no new process-global mutable policy. #325 owns exact effect subjects/dependencies and durable intent/fence ordering. Restoring both ledger and pack under the same operator account can defeat independence unless an external authority proves freshness; state that threat limit explicitly.

## Closed Fernet source reader: bounded primary-source check

Checked during preparation, using repository lock versions `duckdb==1.4.4` and `fsspec==2026.2.0`. No dependency was installed/imported and no source pack or runtime test was opened. Earlier inference that closed-Fernet conversion must be excluded was corrected after root requested this check.

- The [DuckDB 1.4 LTS filesystem guide](https://duckdb.org/docs/lts/guides/python/filesystems) documents Python connection filesystem registration. The tagged [v1.4.4 upstream filesystem tests](https://raw.githubusercontent.com/duckdb/duckdb-python/v1.4.4/tests/fast/test_filesystem.py) separately exercise an isolated memory filesystem (`:38-47,59-67`) and database `ATTACH` through registered fsspec (`:142-167`). This is primary evidence of supported APIs, not a test of PRME's combined source reader.
- Tagged [PythonFilesystem implementation](https://raw.githubusercontent.com/duckdb/duckdb-python/v1.4.4/src/duckdb_py/pyfilesystem.cpp) maps read flags to `rb`, delegates file open/read/seek/size to fsspec and routes registered protocols. Tagged [SingleFileBlockManager](https://raw.githubusercontent.com/duckdb/duckdb/v1.4.4/src/storage/single_file_block_manager.cpp) opens existing read-only database files through the filesystem abstraction. Therefore a registered read-only buffer-backed filesystem is a source-backed candidate for native database ATTACH without a raw disk file; this exact composition still needs the bounded runtime proof.
- Tagged [fsspec MemoryFileSystem](https://raw.githubusercontent.com/fsspec/filesystem_spec/2026.2.0/fsspec/implementations/memory.py) uses BytesIO but its default store is class-global. Use an operation-owned isolated store, as the DuckDB upstream fixture does, with an opaque protocol/path and a facade that denies writes/deletes/moves and returns read-only handles. Never clear another instance's global store. Do not use DuckDB's internal `ModifiedMemoryFileSystem`: its source explicitly says it is not an external API.
- Tagged [StandardBufferManager](https://raw.githubusercontent.com/duckdb/duckdb/v1.4.4/src/storage/standard_buffer_manager.cpp) rejects buffer eviction when the temporary path is empty. Create the dedicated inspection connection with `temp_directory=''`, bounded memory and no automatic extension install/load, profiling/log path or disk cache. Out-of-memory is a fixed failure, never permission for plaintext spill. The [LTS configuration reference](https://duckdb.org/docs/lts/configuration/overview) documents the relevant settings; their effective values are checked by the runtime probe.

Proposed exact API sequence: read a bounded quiescent `.enc` artifact; use the existing salt/prefix/PBKDF2/Fernet decrypt logic in memory; seed a private filesystem buffer; `connection.register_filesystem(source_fs)`; `ATTACH 'prme_legacy_buffer://<opaque-id>/memory.duckdb' AS source (TYPE DUCKDB, READ_ONLY)`; enumerate known tables/records through read-only SELECTs; stream classified/sanitized output to the separate migration coordinator; close/detach the connection, unregister the filesystem and release only its owned buffers in `finally`. This does not deserialize a database with a homemade parser or load a community extension. No old source bytes are newly written to a filesystem.

A source byte cap is checked before allocation and reported as a capacity limit; full-file Fernet authentication requires memory for ciphertext and plaintext. Refuse wrong key, corruption, unexpected file shape, ambiguous plaintext+encrypted sources or an outstanding WAL; do not ignore committed WAL state. Correctly authenticated legacy raw bytes are transient ingress for migration, never a new durable diagnostic attachment. Copy existing numerical vector payloads only when their admitted provenance remains valid. For older vector-file-only sources, the existing `vector_index.py:175-193` uses native `Index.load(data)` for database snapshot bytes, and `:220-249` reads exact vectors with `Index.get(key)`; reuse those supported buffer operations on authenticated in-memory native-index bytes without constructing `VectorIndex` or running its source-writing backfill. Validate dimensions/count/key closure and never silently re-embed. An already missing legacy vector remains an explicit source limitation, as the current recovery contract documents; a failure to load an intact supported source is pending migration coverage, not a passed unsupported case. Lexical source artifacts can be enumerated as remaining legacy copies while destination indexes are built from admitted survivors. Do not promise OS swap/heap zeroization or protection from hostile same-process code.

Finite source-reader probe after release: create one actual closed Fernet directory fixture using the existing engine, record its source file bytes/listing, read it through the registered-memory source API and successfully migrate usable admitted memory into both local destination layouts. Include one intact legacy vector-file-only source and compare its exact saved numerical vectors through the in-memory native loader. Inject wrong key, corrupted token, source-size cap, unclean WAL and forced memory pressure separately. Instrument file/network writes, inspect the complete test-owned directory after each path and verify no plaintext/temp/cache/config/recovery copy, source mutation or provider send. Failure cleanup must leave no cross-request registered buffer. Native database attachment plus no-spill behavior is required evidence; CSV-only fsspec success is insufficient. If that exact API fails, preserve the failure and report the remaining closed-Fernet AC coverage plus a safe operator-controlled legacy read/export alternative, without claiming complete migration coverage or starting a third strategy.

## Concrete bounded implementation sequence

1. **Typed inspection and a stable dry-run.** Add closed `PrivacyMigrationPlan`, `MigrationStatus`, `CopyDisposition` and `ProtectedExportManifest` models. Read only the selected quiescent source via low-level read-only connection/inspection, the bounded closed-Fernet memory-filesystem adapter above, or an explicit already-open source lease. Do not create an engine just to inspect it. Enumerate all C01-C42 dispositions using existing classifier/dependency interfaces; report source/retention classes, requested policy changes, required exact provider/key purposes, visible legacy plaintext, unsupported schema/visual/ID components and known retained original/export/backup copies. Content-free reports use opaque IDs, counts and fixed reason codes, with no source preview/public digest or secret. Unknown external backups are reported as unknown, not absent. Dry-run creates no source rows, migration job, keys or provider calls.
2. **Explicit versioned migration to a separate destination.** Require an explicit command/API operation, explicit target policy and an unoccupied task/operator-selected destination. Freeze source identity/snapshot, policy, planned transformations, opaque migration ID and current ledger reference. Never copy raw SQL tables then redact them later. Page logical input through #322 and #323 before any destination write; preserve complete admitted memory and qualifiers, and use #321 encryption for retained sanitized evidence/checkpoints. Source bytes remain in the original legacy artifact. A raw value removed by sanitation is not preserved inside a new encrypted historical attachment. Unsupported/ambiguous legacy content blocks the plan or requires an explicit remove disposition; it does not silently become admitted. Protected v1 refuses visual conversion unless the explicit plan removes its blobs/descriptions/hashes/dependencies through #325's existing unavailable/purge machinery. Nonopaque legacy protocol identities require an explicit supported mapping or remain a reported blocker; no name becomes public cryptographic context.
3. **Preserve history honestly.** Intact records retained unchanged keep their exact canonical serialized bytes, versions and checksum identity, privately protected where their payload/commitment is sensitive and policy permits retaining that sanitized material. A transformed record gets a new versioned representation and its own checksum; its link to the original opaque identity does not pretend the transformed payload verifies the old checksum. Erased/unsupported payloads use #324/#325's unavailable envelope and explicitly lose original verification/replay. The original source remains readable in its legacy mode until deliberate disposal. Do not mutate old records on ordinary open, regenerate goldens, infer new saved-policy values or replace the old `_snapshot_json` codec.
4. **Resumable content-free migration journal.** Persist plan/version/lineage, opaque copy-family/record cursor, expected source generation, target artifact identity, policy/ledger references, counts and fixed failure state in the new destination's privacy storage. Each bounded transformed batch and cursor commits atomically under the current effect fence. Retries reuse saved IDs/envelopes/deadlines, not new source identities or lifetimes. Resume verifies ownership of the staged destination and source snapshot; changed inputs refuse with a fixed error. Interrupted work remains non-serving and not sealed. Acknowledge final migration only after every required copy disposition verifies, native indexes contain only admitted survivors, current ledger/expiry checks pass and destination publication succeeds. A lost acknowledgment resumes the committed result; it is not treated as rollback. Originals are never automatically deleted.
5. **Format and layout integration.** Implement the explicit v2 event SQL/format dispatch above in both storage backends, preserving v1. The existing nonprivacy directory-to-single-file command keeps its established behavior and claims. A protected directory-to-single-file operation carries encrypted evidence and exact admitted state through the protected coordinator and retains its source. Preserve namespace/store lineage and immutable payload AAD across a same-lineage copy. Do not silently relabel a copy into another project; a rebind requiring new authenticated context is unsupported here. Native `.prme` encryption and directory whole-store encryption are independent controls and never substitute for evidence protection.
6. **Closed logical export.** For a classified protected store, default export uses an explicit allowlist of admitted nodes/edges/executable journals, opaque provenance/policy/availability/work manifests and encoded encrypted evidence/diagnostic envelopes. It never calls private unwrap, reads OS keyring secrets, serializes capability objects, dumps arbitrary `operations.payload`, includes `.env`, or packages recovery files. Work exported for recovery retains its saved finite deadline/generation and needs current-ledger validation; it is not fresh admission. Explicit active-memory-only export excludes evidence and pending diagnostic/work content, names its omitted features/history and states that admitted facts remain sensitive. Ordinary legacy export remains explicitly classified as legacy plaintext, with no protected claim. Evidence plaintext is available only through #321's selected-ID audited diagnostic action; no blanket `--plaintext` switch is added to default export.
7. **Narrow protected restore/import.** Scope one new reader to the closed protected export schema (`restore_protected_export` below), alongside the already-supported copied-pack and PostgreSQL-native restored-schema routes. It is not a general parser for legacy CLI JSON, arbitrary SQL dumps or federation operations. Validate version, complete dependency/identity closure, exact original lineage/AAD and current independently supplied ledger before destination publication. Create no serving engine before ledger application; fence incoming work and remove covered payload/index state with #325 before rebuilding. Unknown extensions, missing dependencies, tampered/wrong context, stale authority and cross-boundary rebind refuse. A snapshot's included last-applied head is informational only. Raw JSON records instead enter the actual public admission APIs and receive new current source identity; do not describe that as original-checksum restoration. Root confirmed this narrow reader fits the issue scope. Its implementation and runtime proof remain required; neither native restore nor a successful parse alone is counted as a completed logical export round-trip.
8. **CLI controls and separate custody.** Extend #321's dedicated privacy CLI registration with inspection/migration dry-run/execute/resume/status and #325 erasure request/status dispatch. Reuse its key provision/status, scoped finite unlock, rotation/resume and deliberate encrypted recovery export/import commands. Unlock is action-scoped with exact selected IDs/owner/scope and content-free audit; it does not create a reusable bearer file or leak plaintext into normal export. Private material/passphrases are never argv or output; recovery destination is outside the pack and ordinary export allowlist. Missing keys show fixed permanent-loss guidance while admitted memory remains usable where its independent protection is available. Unsupported mode/schema/visual/legacy-input cases have usable fixed instructions. Explicit local positional paths keep precedence over ambient database settings; PostgreSQL selection must be explicit and never print a credential-bearing URL.
9. **Remaining-copy/recovery report.** Return managed live status separately from source packs, migration staging, operator exports, pre-rotation backups/keys and deployment-owned PG dumps/WAL/replicas. Reuse #325 status vocabulary (`live_purged`, `backup_expiry_pending`, `complete_within_managed_boundary`) and actual failure/expiry evidence. Paths may be operator-facing inventory, never cryptographic identity; do not log source text in names. Cleanup may delete only the job-owned failed stage, never source or someone else's target. No free-space/media-wipe guarantee, offline revocation, heap-zeroization or automatic disposal claim.

## Exact planned file scope

These are future changes, not edits performed. Trim any file already covered by a merged prerequisite. Additions outside this list require a named AC/regression/security justification to root before work; do not start another quality-improvement scope.

New runtime files:

- `src/prme/privacy/portability.py`: closed plan/status/export models and content-free errors.
- `src/prme/storage/privacy_format.py`: explicit protected schema/capability/lineage preflight and event SQL format descriptor.
- `src/prme/storage/privacy_migration.py`: read-only inventory/plan, staged logical migration, resumable cursor and verification.
- `src/prme/storage/privacy_source.py`: operation-owned read-only fsspec source adapter for bounded decrypted legacy bytes, with no spill or disk fallback.
- `src/prme/storage/privacy_export.py`: allowlisted protected export, admitted-only export and narrow protected-export restore reader.

Existing/dependency runtime files:

- `src/prme/storage/{schema,engine,event_store,pack_manifest,pack_tools,namespace_identity}.py`: protected-format dispatch, initialization order, read-only inspection and format-aware pack maintenance.
- `src/prme/storage/pg/{schema,event_store,namespace,workspace_catalog}.py`: equivalent dispatch and pre-recovery checks, preserving namespace transactions/registry.
- `src/prme/{client,workspace,__init__}.py`: explicit portability/ledger capability forwarding and public result types; no ambient provider acquisition.
- `src/prme/privacy/{__init__,cli,capabilities,records}.py`: register/compose controls and consume typed refs; key custody/crypto implementation stays #321.
- `src/prme/storage/{privacy,privacy_journals,privacy_erasure}.py` or the exact merged #325 equivalent: only migration cursor/policy/copy dependencies and calls to existing ledger/purge methods. Reconcile actual filename before implementation; no competing ledger or journal codec.
- `src/prme/cli.py`: protected exporter routing and dedicated privacy command registration/fixed errors.
- `src/prme/storage/encryption.py`: factor the existing salt/prefix/Fernet decrypt logic into a bytes-only helper reused by the source adapter and existing file path; preserve existing file formats/KDF/exception behavior and all ordinary callers. No new crypto.
- `pyproject.toml` / `uv.lock`: make fsspec a declared portability/privacy dependency at the tested existing lock version if required, without an unrelated dependency upgrade.

New tests/support/fixtures:

- `tests/privacy_portability_support.py`: task-owned pack/schema copies, exact record-byte capture, source-destination inspection, controlled clock/fault hooks and historical-reader subprocess harness; reuse existing privacy fakes/markers.
- `tests/test_privacy_migration.py`: dry-run, legacy classification, versioned resume/erase interaction and positive usable-memory preservation.
- `tests/test_privacy_legacy_source.py`: correct-key closed-Fernet read-only ATTACH, corrupt/wrong-key/oversize/WAL refusal, no-disk/no-mutation probes and source-buffer cleanup.
- `tests/test_privacy_export.py`: closed encrypted default/admitted-only variants, no secret/private-provider access and fixed export failures.
- `tests/test_privacy_restore.py`: narrow protected import plus real copied-pack/PG-native restore under independent current ledger.
- `tests/test_privacy_format_compatibility.py`: current preflight and pinned historical reader controls/refusal without content/admission/recovery mutation.
- `tests/test_privacy_portability_cli.py`: actual parser/action invocation for status/erase/selected unlock/rotation/recovery/export.
- `tests/test_privacy_portability_scenarios.py`: exact eight PV-07-owned frozen cases and shared EM completion records.
- `tests/fixtures/privacy/migration-v1.json`: frozen synthetic legacy versions/checksums/copy states, requested dispositions and retrieval queries; sanitized and original baselines separate.
- `tests/fixtures/privacy/historical-readers-v1.json`: two exact tag/commit/env/artifact pins, expected reachable startup path and success-control requirements.
- `tests/typing/public_api.py`: typed explicit public portability/ledger calls.

Existing regression tests used, with edits only where a changed API genuinely requires them: `test_cli.py`, `test_cli_single_file.py`, `test_single_file_pack.py`, `test_single_file_pack_encryption.py`, `test_encryption.py`, `test_client.py`, `test_workspace.py`, `test_workspace_single_file.py`, `test_pg_workspace.py`, `test_pg_namespace_pool.py`, `test_engine_startup_recovery.py`, `test_direct_store_recovery.py`, `test_epistemic_startup_migration.py`, and the merged privacy/legacy journal compatibility suites.

Docs/evidence: `docs/PRIVACY-MIGRATION.md` (new), `docs/PRIVACY-KEYS.md` (consume/update supported command links only), `docs/RFC-0014-Portability-Sync-and-Federation.md`, `docs/RFC-0019-Evidence-Privacy.md` (explicit implementation-version clarification), `docs/WORKSPACES.md`, `documentation/{cli,deployment,configuration}.md`, `docs/plans/2026-10-evidence-privacy/{PV-07-plan.md,PV-07-results.json,content-copy-inventory.json}`, and root's assigned review report path. Frozen scenario IDs/setup/expected values stay unchanged. No unrelated dependency upgrade is planned. The runtime source adapter must declare its already-locked fsspec dependency; isolated historical-reader environments are created only after root release.

## Finite acceptance/validation map

All runtime results are `not_run`. Real DuckDB directory/single-file and root-assigned disposable PostgreSQL schema data are mandatory; never use production/user data or another worker's DB. Local layouts are subchecks inside each backend case, not extra passing cases. Fake only external models/embeddings, clocks and failure hooks; policy, crypto, authorization, native storage, ledger, migration, projection and export code must be real. No model calls or long benchmarks.

| Original criterion | Required positive and negative checks | Frozen case / dependency |
| --- | --- | --- |
| AC1 | Read-only dry-run reports all inventoried copy dispositions, requested policy/key/backup limits, unsupported visual/unknown schema, no completed label on interrupted/partial output. Successful classified conversion preserves allowed memory. | EM07/EM08; #320-#325. |
| AC2 | Same migration ID resumes at fixed cursor after process interruption and erasure; immutable records keep bytes, transformed/unavailable versions are explicit; new preflight and two pinned old readers refuse unsupported schema before content/recovery effects. | EM04/EM07; #321/#324/#325. |
| AC3 | Default logical export contains actual ciphertext and allowed metadata, no keys/raw evidence/journal bypass; active-memory-only accurately labels sensitive admitted content and omissions; scoped diagnostic read is distinct. | EM05/EM08/CL08; #321/#323/#324. |
| AC4 | Original source stays available/readable, prior backups and exports remain listed, failed owned stage disposition is honest, live key removal/compaction never erases reports. | EM08/ER08; #325. |
| AC5 | Restore snapshot from before a committed erasure using real current independent ledger, then try only snapshot ledger, stale/foreign head, ambiguous lineage and unavailable authority. Covered content/work never serves; surviving controls do. | ER07/ER08; #325. |
| AC6 | Actual local CLI parser + handler, explicit selected-ID finite audited unlock, status/deadlines/provider boundary, erasure request/status, rotation resume and separate encrypted recovery. Missing/wrong keys/unsupported modes have fixed useful errors with no plaintext fallback. | AC02/EM04/EM05/ER08; #320/#321/#325. |

The eight scenarios whose frozen owners include PV-07 are **AC02, CL08, ER07, ER08, EM04, EM05, EM07, EM08**. That is 16 backend case results, with layout subchecks. Their complete setup/expected values are appended verbatim below. #326 does not claim sibling HTTP/MCP/explorer/framework portions of CL08/ER08 from local CLI-only checks; reuse exact-revision evidence and complete the missing real adapter calls in the final shared runner.

### Shared encryption/migration cases, all eight retained

| Case | Concrete completion action | Ownership and pass boundary |
| --- | --- | --- |
| EM01 | Run #321 fixed-vector/context test through the actual codec used by migration/export; encrypted exported envelope independently decodes with correct explicit test authority. | #321 owns primitives. Reuse unchanged exact-revision evidence; no new crypto implementation. |
| EM02 | Use wrong exact key/version/purpose/owner/context and swap two exported source envelopes; restore/protected-read refuses without printing input or returning wrong content. | Consume #321 controls; full shared verdict retains its original scope. |
| EM03 | Missing wrap capability and injected protect/store failures leave no content destination/protected label; scan actual owned artifact set, including staged migration/export. | #321 baseline plus #326 destination integration. No absence-only fake-storage pass. |
| EM04 | Three real objects: interrupt actual `RotationCoordinator` after one CAS, fence second with real #325 `request_erasure`, resume exact job then migrate/export/reopen; retain pre-rotation copy and old test key. Assert immutable payload AAD/key/nonce/ciphertext unchanged after rewrap; wrapper context/version alone changes. | Shared #321/#326; full erasure-dependent subcheck waits #325. Old copy readability is evidence of backup limit, not a failure to hide. |
| EM05 | Lose exact task-owned evidence key through #321 provider, inspect key-unavailable, verify admitted memory still usable; invoke deliberate encrypted recovery export/import outside pack, selected unlock and wrong recovery passphrase; inspect ordinary export for key absence. | Shared #321/#326. Native custody evidence cannot be replaced by an in-memory provider. |
| EM06 | Ordinary migration/export invokes only public wrapping/metadata paths; diagnostic private lookup wired to fail remains untouched; consumed permanently-loaded config reports same_process. | #321 owns separation/native custody. No hostile-same-user/process isolation claim. |
| EM07 | Fixed legacy source with event/hash, direct/extraction/plan/profile/receipt/fast retry and visual copies; dry-run; refuse unsupported state; explicitly choose supported dispositions; interrupt before/after batch commit and before publication, then resume with real erasure fence. Verify source bytes/record identities and typed unavailable target history. | #326 owns; requires #322/#323 classification and #324/#325 history/fences. Ordinary open of the source remains unchanged. |
| EM08 | Export protected and admitted-only modes, migrate actual protected directory to single-file, reopen/restore it and inspect source/backups/temp/manifest/output. Both variants identify their schema/content. Consume independently current ledger before supported restore. | #326 owns; real CLI/Python/defined supported-import call. The separate frozen24 parity gate is still #327. |

### Concrete callable/harness contract for integration

Names below are proposed for new #326 code, not claims that these methods already exist. Take prerequisite method names from their merged code and update these references once, before writing tests. Do not build a second fake protocol merely to make the harness pass.

- `inspect_privacy_source(source_handle, *, target_policy, capabilities, clock) -> PrivacyMigrationPlan`: real read-only snapshot/disposition inventory; no job writes or unwrap unless explicitly required by a supported source-open action.
- `start_privacy_migration(plan, destination, *, current_ledger, capabilities, clock)` and `resume_privacy_migration(destination, migration_id, *, current_ledger, capabilities, clock)`: actual durable journal/batch commits and final status. Named fault hooks: before first target commit, after a committed batch before acknowledgment, and immediately before final publication. Each case resumes exactly the same job; not three new strategies.
- `export_protected_pack(engine, *, mode="protected" | "active_memory_only", selection, destination, current_ledger, clock)`: real allowlisted export, authoritative read fence and atomic job-owned output publication. The diagnostic read API is deliberately separate and comes from #321.
- `restore_protected_export(source, destination, *, current_ledger, capabilities, clock)`: only the new closed schema, version/lineage validation and #325 restore guard before serving. Execute real public API calls afterward; successful parse alone is not restoration evidence.
- `MemoryEngine.open(..., current_ledger=authority, ...)` / `MemoryClient(..., ...)` and `MemoryWorkspace` forward the merged ledger capability: copied-directory, copied-`.prme` and restored PG-schema startup use the same preflight/fence order. The actual constructor spelling is resolved against #320/#325.
- `run_portability_case(case_id, backend, layouts, fixture, artifacts_dir)` in `tests/privacy_portability_support.py` dispatches the real calls above plus #321 selected diagnostic read/rotate/recovery and #325 request/status. It returns detailed layout/subcheck evidence, never increments a whole-case pass while a required subcheck remains waiting.

`supported_import` occurs in AP01/AP02/AP03/AP06/CL01/EM08/SN01/SN11. For raw JSON-record ingestion, label the actual existing `MemoryEngine.ingest_batch`, `ingest_fast_many` or synchronous `MemoryClient` admission route used by #320/#322; those use fresh admission and do not restore old source identity. For EM08, name copied-pack/open, explicit migration or the newly implemented closed protected-export reader. Complete any relevant AP/CL/SN checks for a new reader rather than count them passed by their mere existence. Native PG restore is `pg_dump`/`pg_restore` of task-owned schema+registry into a separate disposable DB, then the real guarded engine open; do not feed a dump directly to a fictional engine importer.

For the final #327 gate, reuse these callable implementations and exact fixture/revision evidence, run remaining shared subchecks on both backends, then execute its existing frozen24 parity baseline/final and separate24 sealed behavior set. A snapshot-only restore denial is a valid required negative subcheck, never a replacement for a successful current-ledger restore with usable surviving memory.

### Pinned historical-reader probes

After root releases implementation, provision exactly two isolated reader environments at the commits above with their pinned dependencies and required public-adapter extras. No replacement of a shared editable install. Record wheel/source/dependency hashes and actual provider/DB versions; no installs in this preparation turn.

For each version, use separate task-owned copies for DuckDB directory, `.prme`, and PostgreSQL schema. First open an ordinary legacy control successfully with the same reader/environment, correct namespace/key/dimension and an injected deterministic embedding/provider boundary. Then exercise the protected copy at the exact path/schema. Fail only at the known incompatible event schema boundary, before a usable engine is returned. Cover async engine, sync client, CLI export for local layouts, HTTP/MCP lifespans and identity-bound workspace paths; PG CLI is explicitly unavailable in that old path, not a skipped pass. All adapter routes must be shown to reach the same failing initializer and not swallow it. No raw low-level SQL hostile-reader promise is made.

Before/after compare all record bytes/versions, event/node/operation/work counts and generations, published head/stage state and provider/admission counters. Assert no recovered job publication, fresh events or second unprotected store created beside the target. Record harmless existing schema/open/close/encryption-artifact effects accurately; do not assert byte-identical physical files if old directory cleanup legitimately re-encrypts them. New-reader future-version/missing-capability and malformed-format probes refuse before these side effects. Corrupt/missing unrelated fixture configuration does not satisfy either test.

### Retrieval and artifact checks

Freeze the synthetic migration fixture and its standard-mode retrieval query/clock before capture. Capture selected IDs/order, packed text and relevant availability before expiry on the unchanged sanitized admitted input, then compare after migration/reopen. Keep the unsanitized legacy baseline distinct. Preserve a failure including a known BM25 tie change; never choose queries after seeing it. #327 alone owns the broad frozen24 baseline/final gate; this ticket's fixture is a direct business-logic regression, not a new answer benchmark.

Inspect real logical copies and controlled artifacts after success, each injected failure and restart. Include C01-C23/C37-C42 via their actual layout-aware inventory inspectors; exported C35/C36; database/native index/WAL/temp/manifest/log output and job-owned `.migrate`/`.compact` staging; provider/public-descriptor/recovery references C33/C41. Distinct markers represent allowed admitted memory, source-only content and prohibited pre-sanitation input. Marker absence from float bytes is not proof that vectors lack a semantic derivative: inspect the actual embedding inputs, numerical rows/stages and admitted provenance. Ciphertext evidence is allowed; deliberately admitted facts stay sensitive. Never print raw secret/test-key bytes into result logs.

For PostgreSQL, real SQL state and a task-owned logical dump are required. A logical scan does not prove server WAL/base-page/temp/replica absence. Scan physical artifacts only if root supplies ownership/access to a disposable server; record unobservable boundaries honestly. External backups, operator-held exports, delivered client/browser buffers and filesystem free pages remain external limits. Native provider conformance is #321 evidence, reused when the tested revision/interface is unchanged.

### Required final checks

Use root's worktree-specific locked environment with required dev/postgres/api/mcp/framework/privacy extras and root-assigned disposable database. During development run only affected/new cases; on the final revision run:

1. New portability modules/scenario runner with exact case/layout/subcheck results, including the two historical-reader controls. Preserve pending dependencies instead of passing mocks.
2. `uv run --no-sync python -m pytest tests/ -q` with a live dedicated `PRME_TEST_DATABASE_URL`. Missing PG or optional dependency skips are explicit, never parity evidence. CI Python 3.11-3.13 DuckDB/PG matrices remain required.
3. `uv run --no-sync ruff check src/ tests/` and `uv run --no-sync mypy --follow-imports=silent --ignore-missing-imports --strict tests/typing/public_api.py`.
4. `uv build` plus installed-wheel version/import/`py.typed` smoke in a separate temporary environment. Do not replace another worktree's editable package.
5. Existing required `python -m scripts.run_simulations` under current defaults and CI's previous-default environment (`PRME_SCORING__FUSION=weighted`, `PRME_PACKING__CONTEXT_FORMAT=auditable`, `PRME_PACKING__MULTIPATH_ORDERING=balanced`). These are bounded project checks, not model calls or permission for long benchmarks.
6. Required framework integration selection if the full suite did not execute it: `tests/test_integration_chat_history.py`, `tests/test_integrations_langchain.py`, `tests/test_integrations_llamaindex.py`. Contract structural validator: `python docs/plans/2026-10-evidence-privacy/validate-contract.py`; structural validity is not runtime privacy evidence.
7. Root's required security/correctness review of the exact final snapshot, sharing verified evidence and re-reviewing only changed scopes. Once agreed checks pass, deliver without a further quality round.

Record exact command, exit status, elapsed time, code/dependency/fixture hashes, cases/layouts, logs, observed results and retained failures. No tests, probes or reviews ran in preparation.

## Readiness and remaining decisions/gaps

Preparation is ready for root review. Implementation is dependency-blocked until reviewed #320-#325 code supplies the seams above, root allocates disposable DB/environments and releases implementation. This is an ordinary code dependency, not an authorization to mutate those sibling tickets.

No outstanding product-scope decision remains: root accepted the closed protected-export reader and retained closed-Fernet source coverage. Its read-only registered-filesystem source adapter, format-refusal candidate and startup order are concrete/source-backed, with runtime validation still outstanding. A failed combined memory-filesystem ATTACH or no-spill probe is an AC1/AC4 coverage gap, not a reason to relabel closed Fernet as a passed unsupported variant. Preserve failure evidence and report the bounded alternative after the agreed repair limit. Exact old-reader support is bounded to the two pinned releases actually tested.

The known lexical tie behavior is an AC validation risk, not automatic permission to change ranking defaults or select a new cohort. Any blocker must name AC1-AC6, an introduced regression or a concrete security issue; unrelated improvements become follow-up notes without changing done. No whole shared case or end-to-end protected status is marked passed from this preparation.

## Frozen scenario records

The following appendix is copied from the merged frozen manifest. It contains all eight EM cases and the additional PV-07-owned shared cases, with every setup/expected/adapter value unchanged and every runtime status still `not_run`.

```json
[
  {
    "id": "AC02",
    "cohort": "original_40",
    "group": "access",
    "title": "Selected authorized diagnostic read",
    "requirements": [
      "E3",
      "E7"
    ],
    "implementation_owners": [
      "PV-03",
      "PV-07"
    ],
    "copy_ids": [
      "C01",
      "C25",
      "C41"
    ],
    "setup": "Use an explicit scoped unexpired capability for two selected records with working key and audit sink.",
    "expected": "Only selected currently available evidence is returned, labeled sanitized; successful authorization/outcome audit contains no source or key.",
    "adapter_subchecks": [
      "async_python",
      "cli"
    ],
    "status": "not_run"
  },
  {
    "id": "CL08",
    "cohort": "original_40",
    "group": "copied_content_leakage",
    "title": "Errors logs preview export and browser sinks",
    "requirements": [
      "E1",
      "E2",
      "E8"
    ],
    "implementation_owners": [
      "PV-05",
      "PV-07",
      "PV-13"
    ],
    "copy_ids": [
      "C26",
      "C27",
      "C29",
      "C30",
      "C31",
      "C32",
      "C35",
      "C36"
    ],
    "setup": "Inject provider/validation exceptions with marker text and inspect logging, tracing, response bodies, preview, export and explorer state.",
    "expected": "No original/prohibited or sealed source-only text appears in prohibited sinks after success/failure. Default protected export has no keys or plaintext evidence; already delivered external copies are disclosed.",
    "adapter_subchecks": [
      "http",
      "mcp",
      "explorer",
      "cli",
      "langchain",
      "llamaindex"
    ],
    "status": "not_run"
  },
  {
    "id": "ER07",
    "cohort": "original_40",
    "group": "erasure_recovery",
    "title": "Pre-erasure restore requires current ledger",
    "requirements": [
      "E5",
      "E6"
    ],
    "implementation_owners": [
      "PV-06",
      "PV-07"
    ],
    "copy_ids": [
      "C36",
      "C37",
      "C42"
    ],
    "setup": "Restore/import snapshot taken before erasure, once with current independent ledger and once with only the snapshot ledger.",
    "expected": "Current ledger is applied before serving and recovered jobs are fenced. Snapshot alone refuses any deletion-continuity/protected-restore success claim; no erased content exposed.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "ER08",
    "cohort": "original_40",
    "group": "erasure_recovery",
    "title": "Truthful purge backup and external status",
    "requirements": [
      "E4",
      "E5",
      "E7"
    ],
    "implementation_owners": [
      "PV-06",
      "PV-07"
    ],
    "copy_ids": [
      "C28",
      "C34",
      "C35",
      "C36",
      "C42"
    ],
    "setup": "Archive and clear chat, then erase with live purge complete but retained backup and operator-held export.",
    "expected": "Archive/clear is not erase; report live_purged then backup_expiry_pending, remaining copies and failures; complete only within verified managed boundary, never media wiping or offline revocation.",
    "adapter_subchecks": [
      "async_python",
      "cli",
      "langchain",
      "llamaindex"
    ],
    "status": "not_run"
  },
  {
    "id": "EM01",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Authenticated-encryption known vectors and context",
    "requirements": [
      "E1",
      "E3"
    ],
    "implementation_owners": [
      "PV-03"
    ],
    "copy_ids": [
      "C01",
      "C41"
    ],
    "setup": "Use fixed library-compatible AES-GCM test vectors; alter ciphertext/tag/nonce/AAD and independently inspect generated object key/nonce handling.",
    "expected": "Valid fixture decrypts exactly; every tamper fails without plaintext. Changed payload uses new object generation/key/nonce; retry reuses exact persisted envelope.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "EM02",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Swapped record purpose scope or wrapping key",
    "requirements": [
      "E1",
      "E3"
    ],
    "implementation_owners": [
      "PV-03"
    ],
    "copy_ids": [
      "C01",
      "C41"
    ],
    "setup": "Swap envelope between two records/owners/scopes and between processing/evidence purposes; use wrong OAEP label/private key.",
    "expected": "Context and unwrap/decrypt fail closed for all substitutions; no wrong-record content and no context containing names or predictable source hashes.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "EM03",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Provisioning and failed write have no plaintext fallback",
    "requirements": [
      "E1",
      "E7"
    ],
    "implementation_owners": [
      "PV-03"
    ],
    "copy_ids": [
      "C01",
      "C31",
      "C33",
      "C34",
      "C41"
    ],
    "setup": "Create protected store with no usable public/provider key; fail encryption/wrap and storage mid-write.",
    "expected": "Fail before content durability. Scan database/WAL/temp/log/manifest/export artifacts for plaintext/key material; protected status is never assigned to partial/plaintext store.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "EM04",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Versioned resumable rotation",
    "requirements": [
      "E1",
      "E3",
      "E5"
    ],
    "implementation_owners": [
      "PV-03",
      "PV-07"
    ],
    "copy_ids": [
      "C01",
      "C36",
      "C41"
    ],
    "setup": "Rotate wrapping key with interruption after some envelopes; retry while an erasure fence covers another object.",
    "expected": "Reads select exact saved key version, rotation resumes idempotently without changing protected plaintext; erased objects stay denied; old backup keys remain explicitly a backup concern.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "EM05",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Permanent key loss and explicit recovery export",
    "requirements": [
      "E3",
      "E7"
    ],
    "implementation_owners": [
      "PV-03",
      "PV-07"
    ],
    "copy_ids": [
      "C33",
      "C35",
      "C36",
      "C41"
    ],
    "setup": "Lose evidence key; separately use deliberate recovery-key export outside pack and test supported local unlock.",
    "expected": "Unavailable key gives clear permanent-loss guidance without downgrade. Admitted memory remains usable under separate protection; default pack/export never embeds recovery secrets.",
    "adapter_subchecks": [
      "async_python",
      "cli"
    ],
    "status": "not_run"
  },
  {
    "id": "EM06",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Local public writer and same-process disclosure",
    "requirements": [
      "E2",
      "E3",
      "E7"
    ],
    "implementation_owners": [
      "PV-03"
    ],
    "copy_ids": [
      "C24",
      "C25",
      "C33",
      "C41"
    ],
    "setup": "Provision public writer, separate diagnostic private provider and ordinary retrieval; try null/plaintext keyring backend and permanently loaded private-key configuration.",
    "expected": "Ordinary writer can wrap but cannot unwrap evidence. Disallowed backend rejects. Same-process key configuration is labeled same_process and refuses service-isolation claims; explicit local capability/audit still required.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "EM07",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Legacy migration retains identity and explicit incompleteness",
    "requirements": [
      "E5",
      "E6",
      "E8"
    ],
    "implementation_owners": [
      "PV-07"
    ],
    "copy_ids": [
      "C01",
      "C03",
      "C08",
      "C15",
      "C22",
      "C36",
      "C38"
    ],
    "setup": "Dry-run/migrate legacy plaintext store containing hashes, receipts, source-only snapshots and visual records; interrupt and resume under erasure fence.",
    "expected": "Dry-run lists affected copies/features/keys; no protected label until all transformations verify. Intact records keep bytes; erased payload gets new unavailable envelope, never rewritten original checksum. Unsupported visual legacy is refused or removed explicitly.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "EM08",
    "cohort": "original_40",
    "group": "encryption_migration",
    "title": "Protected export and remaining source copies",
    "requirements": [
      "E1",
      "E5",
      "E6",
      "E7"
    ],
    "implementation_owners": [
      "PV-07"
    ],
    "copy_ids": [
      "C33",
      "C34",
      "C35",
      "C36",
      "C42"
    ],
    "setup": "Export protected and explicit admitted-memory-only variants, migrate directory to single-file and inspect original pack/backups.",
    "expected": "Default evidence stays encrypted with no recovery keys; admitted-only export states facts remain sensitive. Original packs/backups are listed, not silently deleted or declared erased. Storage parity stays a separately executed 24-query gate.",
    "adapter_subchecks": [
      "async_python",
      "cli",
      "supported_import"
    ],
    "status": "not_run"
  }
]
```


## Authorized hosted #281 startup prerequisite (2026-10-20)

Root authorized initialize=False after the actual HS01/HS11 restricted-login failure at ALTER TABLE events (4058b00). This does not add a #326 AC. Implementation/check scope is recorded in `.github-ticket-loop/privacy-326/runtime-open-plan.md`: workspace.py, storage/engine.py, storage/pg/{workspace_catalog,namespace,schema}.py, storage/erasure.py, current format test and WORKSPACES documentation. Default privileged bootstrap remains; runtime performs no DDL/catalog mutation and validates descriptor, identity, schema and recovery readiness before current-ledger/expiry and serving. Role grants remain deployment-owned. Required pending recovery can fail closed on denied DML. Precise component controls, mixed failures, corrections and limits are in PV-07-results.json; #281 owns ordinary ingestion/worker final-grant integration.


## Implementation checkpoint (2026-10-20)

Source reader and explicit protected format/runtime-only PostgreSQL opening are committed. The next checkpoint adds closed export/restore, authenticated resumable direct-intent migration, exact pinned-reader evidence and explicit CLI controls. All original six ACs and the frozen scenarios remain open pending complete integration; no direct-only or component-only pass closes EM07. The source private identity seam adds only admission.py prepare validation, explicitly approved by root for AC2. Mixed saved derivation/profile/summary conversion, lost acknowledgments, restore negative/native routes and final shared gates remain in the original finite scope. A third unsuccessful repair cycle requires USER approval (superseding the preparation text that incorrectly said root approval). The held #323 classifier/ordinary-read strategy is untouched. See PV-07-results.json for exact failures, corrections, commands, times, source/log hashes and remaining work.

## Mixed saved-family checkpoint (2026-10-20)

Original AC2/EM07 mapping extends only `storage/privacy_migration.py` and
`storage/privacy_export.py`, with the existing migration test and unchanged
`migration-v1.json` fixture. Saved source/checksum readers validate direct,
extraction/derivation, profile and consolidation identities. Existing admitted
projections retain opaque node IDs, truth qualifiers/clocks and full source plus
previous-publication closure. Old executable publication bytes stay in the
original, with typed unavailable history in the destination; no validator is
weakened and no original checksum/replay continuity is fabricated. Complete
export validation checks typed memory/event closure, matching owner/scope and
acyclic generated references. Resume after publication validates the exact
migration/lineage target under current ledger guards.

The finite directory mixed case passed after two resolved fixture omissions and
one newly observed runtime dependency-loss correction; the three failed runs are
preserved separately. The same fixture then passed pack, PostgreSQL and closed
Fernet, together with affected direct4/export3/restore3 (13 checks). The original
historical-reader, source-reader and runtime-open passing controls were not
repeated. Source hashes and terminal evidence are in PV-07-results.json. Public
fixed-clock retrieval parity and every whole frozen case remain pending.

Source-backed upstream integration gap: `storage/erasure.py` currently resolves
all `evidence_refs` as event IDs, while valid consolidation nodes include source
memory UUIDs as well. The mixed check erases an old profile and verifies the full
source closure plus an unrelated survivor; it does not prove summary-selected
erasure. This was reported to root for #325 coordination without changing its
consumer or the held #323 paths.


## Authorized summary erasure prerequisite (2026-10-20)

The source-backed gap above was reproduced before runtime changes with the same
mixed fixture: a direct summary request failed at the event lookup for a source
memory UUID. Root authorized only `storage/erasure.py` and the existing migration
test family. The resolver now distinguishes typed generated memory dependencies
from event references, verifies same-owner/scope nodes, rejects missing/foreign
and ambiguous identities, and traverses the full source closure before durable
intent acknowledgment. It never drops a reference to make erasure succeed.

The first correction passed exactly seven executions: summary selection on
directory/pack/PostgreSQL, with missing/foreign/ambiguous refusals and unchanged
ledger heads, plus all four existing mixed-profile layout cases. The original
failure, commands, hashes, times and zero unsuccessful runtime correction count
are retained in PV-07-results.json. Existing #325 regression tests that invoke
the held #323 ordinary read classifier remain pending, not passing evidence.
No whole shared scenario is closed by this prerequisite.

## Original restore/copy continuation (2026-10-20)

The next bounded change touches only `tests/test_privacy_restore.py` and this
plan/results pair. Extend the existing three-layout restore fixture, keeping
its original erased event and surviving event: refuse missing/snapshot-only,
foreign and behind-head current authority plus ambiguous lineage; restore the
original closed artifact with the real current ledger; reopen a closed native
directory/single-file copy or a real PostgreSQL schema dump restored into the
assigned `privacy_engine_326_restore` database. Retain and hash the source
backup, and verify both erased-node absence and the same surviving admitted
row/evidence. Directory additionally exercises protected logical export to
single-file. PostgreSQL tools are installed major 16 (client 16.11, assigned
server 16.15); only the task fixture schema is dumped/restored, with no copied
hosted control catalog. Run exactly the three existing parametrized restore
cases and their static check. No passing unrelated selection is repeated.

A behind-head authority check compares a real newer applied checkpoint to the
pre-erasure independent-ledger copy. Rolling back both the pack and its local
authority remains outside the freshness guarantee. Held #323 public retrieval
and recovered pending-work assertions remain explicitly pending. Occupied
hosted namespace replacement remains design-only; current catalog UUID,
quiescence and guarded publication need a separately bounded released API.

The first native-copy run executed exactly three cases: pack and PostgreSQL
passed, directory failed after publication when a native lexical handle still
pointed to the moved staging directory. `erasure_indexes.compact_lexical`
previously grouped all engines by lineage and assigned one native index across
distinct physical copies. Root authorized the narrow prerequisite in
`storage/erasure_indexes.py` plus the same test/plan/results scope. The first
correction partitions physical lexical directories and actual shared in-memory
index objects, applying the held current ledger and each group's own SQL/native
fences and artifact ownership. No separate copy's documents are unioned.

Validation remains the same three restore parametrizations, extended with one
real post-snapshot source-only node and a second same-target handle. They assert
both copy separation and same-path/shared-index semantics. Existing ER06/native
fault checks enter held `get_event_nodes` and remain pending. The rebuild final
compaction reuses this helper, but its public `ReadContext` path is not invoked
or repaired. Initial failure and correction counts remain in PV-07-results.json.

The first correction's three-case run passed pack/PostgreSQL and the original
directory publication/copy-separation failure, then failed a fixture-only native
directory path mismatch (`lexical/` was copied but `lexical_index/` selected).
Correcting only that source-relative path mapping passed the affected directory
case. This sequence executed seven cases in total: two failures and five
passes. The final three-layout evidence combines the directory-only pass with
the two unaffected passing layouts; neither failed composite is relabeled.
One unsuccessful fix-and-validation cycle is retained. No further broad check
or public classifier/rebuild invocation was made.

## Authorized #281 mixed-scope maintenance prerequisite (2026-10-20)

The actual hosted HS11/PP12 transition reached purged jobs, then failed because
`process_erasures` selected owner-wide jobs and applied an immutable single-scope
default even to completed foreign-scope intents. Root authorized only that
function's selection/validation and public method docstring,
`tests/test_privacy_erasure_recovery.py`, and this plan/results pair. Existing
owner_id/scope columns filter the effective immutable boundary before ordering
and LIMIT; current-ledger and per-request authority guards remain. A different
scope requires a separately authorized configured engine, never a copied or
mutated default policy used as permission.

Finite validation is one initial directory reproduction, then exactly directory
and PostgreSQL variants after the correction. Reuse three actual sources (two
personal, one project), separately provisioned real key boundaries and policies:
completed foreign-scope jobs, pending low-ID foreign jobs versus limit=1, exact
scope/status progress, wrong-owner denial and intent/index consistency. No held
reader/classifier or broader erasure suite runs. The hosted transition already
has one unsuccessful correction cycle; this engine correction plus the one
ensuing hosted rerun is its second cycle. A failure at either point stops the
strategy pending explicit USER approval, without resetting by repository/layout.

The initial directory attempt stopped before scope selection: simultaneous
scope engines had different loaded vectors and native verification returned
coverage_unavailable. That failed run and its read-only persisted-status
diagnostic remain preserved, without being presented as the hosted boundary
reproduction. Root authorized sequential immutable scope engines to match the
hosted worker lifecycle, with all three sources and assertions retained. The
single correction passed exactly directory and PostgreSQL (two executions).
The shared second cycle remains pending the one affected hosted rerun. Native
verification, held readers and the broader erasure suite were not changed/run.

## Independent remaining EM04 integration

The original EM04 row above already requires three evidence objects, one
committed wrapper change, actual erasure authority, exact-job resume and
export/reopen. Existing #321 rotation tests exercise a synthetic denied-record
callback; the verified #326 CLI checks exercise separate recovery and key loss
but never rotate/resume. Extend only `tests/test_privacy_portability_cli.py` and
this plan/results pair for the missing integration. No runtime change is planned.
The finite command selects only
`test_EM04_cli_rotation_real_erasure_export_reopen`, with the existing directory,
single-file and PostgreSQL parametrizations: three executions, no other cohort.

Use real CLI rotate with limit=1, persistent rotation status and resume, and a
real independent-ledger evidence erasure on the second UUID-ordered object.
Keep that source engine open for the first resume so its actual effect guard
must deny the still-present fenced envelope; reopening then applies the pending
purge before the final exact-job resume. Compare immutable payload context,
nonce/ciphertext and independently unwrapped DEK, while only the wrapper changes.
Closed export and same-lineage restore preserve the new exact versions and
erased-source availability. Directory-to-single-file transfer is the already
supported protected layout migration route; PostgreSQL remains an empty unbound
task schema. Retain the pre-rotation export and deliberate encrypted recovery
key outside the pack, and demonstrate offline readability explicitly as the
backup/recovery limit. This does not authorize live access to erased evidence.

The inherited native OS adapter is a named double around actual custody and
crypto; do not relabel it as fresh native-platform execution. The old #321 native
custody evidence and Windows/Linux limits remain separate. No held #323 public
retrieval, broad regression run, generic close change, journal format change,
bound namespace recovery or hosted replacement is part of this selection.

The single finite EM04 run passed all three parametrizations (14.52 seconds
pytest; 21.028 seconds captured command). Its exact pre-run source manifest and
log hashes are in PV-07-results.json. No runtime change or repair was needed,
and no already passing cohort was repeated. Whole frozen cases and the public
read/dependency/final delivery gates remain open. Root verified the separate #281 worker
cycle-2 transition on exact 42f87e07 and its consumed wheel; its canonical
281-mixed-scope-transition-prerequisite.json records resolution in that second
cycle. No repeated transition run is needed.

## Protected terminal and alias export history

Original AC2/AC3/AC5 require the existing #324 protected histories to pass through
the closed exporter. Exact affected files: `storage/privacy_export.py`, existing
`tests/test_privacy_export.py`, and this plan/results pair. No journal writer,
wire version, checksum scheme or replay implementation changes. Consume
processing_terminal v1 cancelled/expired fields exactly as `finish_processing`
writes them, protected alias proposal v3 through `alias_proposal.read_record`,
and protected alias review v2 accepted/rejected through `read_review_record`.
Preserve original record strings/checksums and reject unknown members, wrong
operation context or checksums and unsupported legacy records.

Alias edges have no event provenance; their exact proposal/review identities
instead bind them to those records. Existing review writers leave the proposal
edge unchanged; acceptance adds a separate verified edge, rejection adds no
edge. Preserve those actual bytes, without inventing a review-time valid_to
closure. Protected export retains all alias edges and validates the existing
record proofs. Root approved active-memory-only omission of those alias edges
because its executable journals are excluded: keep all admitted nodes and add
`alias_relationships` to manifest.omitted. No replay claim follows from either
variant; terminal markers retain replay=unavailable.

Finite validation selects only
`tests/test_privacy_export.py::test_terminal_and_alias_history_export_restore`
in directory/single-file/PostgreSQL (three executions). Reuse the current export
family, real direct entities/alias writers, real ingestion claims and terminal
processing writers, current ledger, closed export/restore and original codecs.
Check both accepted/rejected proposals and cancelled/expired work, exact record
bytes/checksums, preserved deadlines/generations, current context/checksum/unknown
content refusals and explicit active-only dispositions. Obtain direct node IDs
from owned SQL; no held get_event_nodes/ReadContext, model send, repeated passing
codec/rotation cohort, broad suite or native coverage repair. Two unsuccessful
corrections of this behavior still stop pending USER approval.

Initial history validation failed all three parametrizations at a fixture
expectation: restore intentionally invalidates imported extraction claims by
advancing generation once and clearing the lease, as already required above.
The corrected assertion retains exact remaining work fields and admission
deadlines, and also denies the original claim. No runtime behavior is changed
for that failure; the original failed log is retained and its later malformed
history checks were not reached. The same three cases are the only rerun.

The corrected same three-layout run passed (5.51 seconds pytest; 6.421
seconds captured command). All six named malformed-history refusals ran in each
layout, 18 negative subchecks total. The failed three executions remain failed;
the successful correction changes the fixture only, and the captured source
manifests prove privacy_export.py is identical between those runs. No repeated
codec/rotation/mixed-scope cohort or held public reader was executed.

## Remaining exported-envelope EM02 component

Original AC3/AC5 still need the exported-artifact composition of EM02; existing
#321 crypto primitive checks and #326 current-ledger/lineage refusals are reused.
Exact affected files are `tests/test_privacy_restore.py` and this plan/results
pair; no runtime edits are planned. The finite selection is only
`test_EM02_exported_context_and_wrapped_key_refusals`, using the same directory,
single-file and PostgreSQL layouts (three executions).

Export two real admitted sources. Five altered artifacts swap the complete
source envelope or change owner, scope, purpose or exact key-version metadata;
each must refuse before destination creation/rows or native key access. A sixth
artifact retains structurally valid metadata but swaps the first source's
wrapped DEK with the second source's wrapped DEK. Metadata-only restoration is
not ciphertext authentication: restore that opaque artifact, then require the
actual selected diagnostic CryptoProtector access under current policy/ledger
to deny the swapped key without plaintext, while the untouched second source
decrypts using its exact native key version. Retain the original artifact hash
and assert no fallback version lookup. These are six negative subchecks per
layout, not a new primitive cohort or a claim that uninspected encrypted bytes
are authenticated. No held public ReadContext/get_event_nodes paths run.

The single finite exported-context run passed all three layouts (6.56 seconds
pytest; 7.990 seconds captured command), with 18 refusal subchecks and three
untouched-source decrypt controls. No runtime correction or repeat was needed.
Exact log/source hashes are retained in PV-07-results.json. This proves the
artifact composition only, with no whole EM02/public-reader/native-OS claim.

## Remaining export-effect EM03 component

Original AC3/AC4/AC5 require the actual export destination failure and current
authority at publication. Reuse existing missing-key/protect/store primitive
checks, migration interruption evidence and native source scans. Change only
`tests/test_privacy_export.py` and this plan/results pair. Select exactly
`test_EM03_export_effect_failure_and_current_ledger` in directory/single-file/
PostgreSQL (three executions), without repeating those other cohorts.

Fail the real final os.link after the export's owned temporary has been written
and fsynced. Inspect its real bytes for source and captured real DEK markers,
then require cleanup, no completed destination and unchanged source records.
Separately pause the same real exporter after capture; a concurrent actual
request_erasure must wait on its independently current ledger lease through
the actual publication effect. Once erasure acknowledges, a new export must
refuse under that durable fence. Keep the pre-ack export and record it as an
external copy, with no retroactive deletion claim. The barriers control only
scheduling; no permission/fence/ledger/storage double substitutes for the real
path. Scan task-owned local files and PostgreSQL fixture logical rows, without
claiming a fresh scan of server WAL/base files or held public readers. No
runtime change is planned; the two unsuccessful correction limit remains.

The single finite export-effect run passed all three layouts (3.45 seconds
pytest; 4.478 seconds captured command). The fault, lease serialization and
post-acknowledgement refusal assertions all executed; no runtime repair was
needed. Exact log/source hashes are in PV-07-results.json. Retained exports and
PostgreSQL server/WAL scan limits remain explicit, with no whole-case claim.

## Remaining restored pending-work component

Original AC2/AC5 require retained processing lifetimes and honest recovery of
work. Existing terminal-history coverage verified cancelled/expired slots but
not restored running/pending claims or consumed reservations. Change only
`tests/test_privacy_restore.py` and this plan/results pair. The finite selection
is `test_restored_pending_work_keeps_deadline_budget_and_claim_fences`, exactly
the same directory/single-file/PostgreSQL layouts (three executions).

Queue two real sources. The first has a live SQL claim and a consumed actual
ProcessingJournal.begin reservation; the second remains pending. Export and
restore preserve exact admission snapshots, opaque processing envelopes,
original deadlines, queue order, attempts and reservation state. Each imported
generation advances exactly once and leases clear; running becomes pending.
The original claim refuses, and a fresh claim cannot replenish the consumed
reservation. Explicit terminal cancellation frees the next source, whose new
claim remains capped by its original deadline. Its processing payload opens
through actual current policy/ledger and StoredProcessingAuthorizer checks,
and its authenticated finite budget remains the saved continuation rather than
a fresh allowance. Original-deadline expiry removes the slot and denies later
claim/retry/access. No extraction/model send, public publication or held #323
path executes; this is the independent pre-publication component only. No
runtime edit or repeat of the terminal/alias/primitive cohort is planned.

The single finite restored-work run passed all three layouts (4.98 seconds
pytest; 5.941 seconds captured command), with no runtime change or correction.
Exact log/source hashes and pre-publication limits are in PV-07-results.json.
The remaining public serving/publication paths and shared frozen harness depend
on held #323 or the committed/verified #283 recovery contract; no further
independent component cohort is selected. Final full/package/simulation/CI and
review/delivery remain required and root-coordinated. All six original ACs stay
partial and zero whole frozen case passes are claimed.

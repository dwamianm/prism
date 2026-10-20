# PV-06 implementation plan

Implementation released by root on 2026-10-20, lane 1 iteration 4. This reuses the approved preparation below without expanding its original six acceptance criteria or finite validation. Work branch: `issue-325-erasure-ledger`, base `b50a5276795ae257b83209424bdd88ceb02ece1e`, normally merged exact #323 checkpoint `49b98aeb6de5f6ef77372d37ca11795473ab48f5` before owned edits. Root retains reviews/full suite/simulations/CI/PR/merge/closure. The worker runs targeted checks and may commit coherent checkpoints, but must not push before root's publication release.

The earlier preparation-only and wait-for-all-merges language below is historical. Independent ledger/authority/expiry implementation is released now. Final #324 profile/summary manifests and #323 composition must be reconciled before final validation. #326 actual restore/export ER07/08 and #327 full product gate remain pending; no original obligation is weakened. No subagents, live/paid models, long benchmarks, production erasure, deployment or global environment changes. A third failed source-fix/validation cycle for the same behavior requires USER approval; root cannot grant it.

Environment: this worktree's `.venv`, locked Python 3.11, existing extras `privacy`, `local-privacy`, `postgres`, `api`, `mcp`, `langchain`, `llamaindex`. Only assigned disposable database `privacy_engine_325` on root-owned port 55432; never stop the shared container or touch sibling databases. Preserve other workers' code.

---

# PV-06 / #325 retention and erasure preparation

Status: preparation complete; implementation not released. No runtime result is claimed.

- Issue: https://github.com/dwamianm/prism/issues/325
- Worktree: `/Users/dwamianm/Sites/prism/.claude/worktrees/privacy-325-retention-erasure`
- Branch: `issue-325-retention-erasure`; supplied base `main` at `33222b54a85fa90fd23d47ae9a9170564bf24828`.
- Sole owned write: this ignored preparation file. No tracked edit, install, environment, database, test, model call, GitHub mutation, commit, push, PR, release, deployment or subagent was performed.
- Root owns scheduling, environments/DB allocation, review, merge, issue state and delivery. #319/PR329 is merged; #320 is active. Prepared #321-#324 are not implemented dependencies.

## Original acceptance criteria and validation (verbatim)

- [ ] Evidence expiry immediately denies evidence reads at the deadline even if
      purge has not run. A source-only purge explicitly reports that admitted
      facts remain; a memory-erasure request includes their affected derivatives.
- [ ] An acknowledged erasure establishes a transactional read/write fence.
      In-flight reveals revalidate; old extraction/organizer jobs cannot publish
      affected content after the fence, including after restart.
- [ ] Purge covers every live copy in [PV-01](https://github.com/dwamianm/prism/issues/319), including graph/history, summaries,
      indexes, staged work, receipts and text-bearing operation snapshots.
- [ ] Multi-source derivatives retain only content justified by surviving
      available sources or explicit admission. Uncertain separation invalidates
      the derivative, without erasing unrelated owners' records.
- [ ] Repeated requests are idempotent, interruptible work resumes, and failures
      stay visible. Metadata-only tombstones contain no deleted payload or
      publicly testable hash of predictable sensitive content.
- [ ] Public status separates denial, live purge, backup expiry and external-copy
      limitations. SQL removal/compaction is not labeled secure media wiping.
      Startup checks expiry before disclosure; offline time is not promised work.

Validation: all eight erasure/recovery scenarios on both backends with fake clocks,
concurrent stale workers and shared-provenance fixtures. Verify public responses
and all inventoried logical stores after purge/rebuild. Filesystem residue and
backup limits must be reported rather than hidden behind a passing API test.

## Done and bounded scope

Done means the six original criteria above hold through actual protected engine operations and real DuckDB/PostgreSQL storage, all eight ER scenarios pass on both backends, the shared CL04/CL05/CL07 purge assertions run against the actual coordinator, required final project checks and root-scheduled review pass, and authorized delivery completes. DuckDB directory and single-file are layout subchecks, not extra successes. Preserve original scenario definitions and failed evidence. ER07/ER08 require #326's actual restore/export integration; prerequisite code can merge with non-closing `Refs` while the issue remains open, as root authorized. Verified merged interfaces may unblock consumers despite that open integration gate.

This work adds opt-in expiry/erasure, not a default change, a semantic secret detector, a new scheduler, a key-store architecture or a quality cohort. Ordinary legacy bytes/checksums and TTL archival remain unchanged. Legacy conversion/removal is explicit, versioned and authorized through #326; #325 supplies its erasure fence/purge/unavailable-record primitives. No ordinary open silently migrates a legacy pack or marks it protected. No physical-media wiping, heap zeroization, anonymity, exact replay, external-copy revocation or malicious-same-process isolation is promised.

The finite validation set is ER01-ER08 plus the already frozen shared CL04/CL05/CL07, affected compatibility checks, and the required final suite/CI. Zero new live/paid models or long benchmarks. Use clocks, fault hooks and synthetic external-provider responses; keep routing, authorization, key/ciphertext storage, SQL/native effects and purge real. Two unsuccessful fix-and-validation cycles for one unresolved behavior stop that strategy; none occurred in preparation. Do not add a new acceptance criterion or rerun passing checks without a relevant change/failure/concrete concern.

## Instructions and reused evidence

Read applicable home/engine/worktree AGENTS.md, the complete issue-worker contract and start-issue SKILL.md/workflow; the preparation-only instruction overrides their mutations/delivery now. Read fresh issue #325, RFC-0019 and its inventory/scenario/sanitizer artifacts, RFC-0007's current archival amendment and WORKSPACES.md. Reuse preparation-320 through preparation-324 and cross-ticket-handoffs.md for admission, crypto, sanitation, readers and the 74-path journal/copy survey; this plan adds the remaining lifecycle/purge/recovery coordination. Source history confirms atomic TTL (`86708db2`) and shared lexical/debounced vector changes (`fc43c107`). No external dependency API change or web lookup is needed for this preparation.

## Source-backed findings and challenged assumptions

1. **Archival cannot implement erasure (AC1/3/5/6).** `storage/retention.py:29-44,88-111,215-290,293-370` deliberately preserves before/after nodes, `content_hash_of_deleted` and a checksummed operation. `_job_tombstone_sweep` (`organizer/jobs.py:612-694`) selects at most 500 active nodes, exempts pinning, and expires only when `expiry < now`. Privacy evidence denial must be `now >= deadline`, independent of a maintenance run, lifecycle, pinning or this legacy TTL format. New memory deadlines are independent of evidence deadlines.
2. **Existing deletes are fragments, not a purge (AC3/5).** `duckpgq_graph.py:813-849` and `pg/graph_store.py:1357-1367` delete only a node/edge row. `index_eviction.py:29-58` retains vector metadata until lexical deletion succeeds but its best-effort wrapper swallows errors. `organizer/jobs.py:760-866` skips PostgreSQL and scoped orphan nodes, and intentionally keeps source journals. Privacy uses explicit stage progress and propagates fixed failures; it cannot report completion from these wrappers.
3. **Reuse real native fences, including cancellation (AC2/5).** `derivation_staging.py:10-61`, `profile_work.py:414-503`, `consolidation_publication.py:456-512` and `profile_collection.py:37-88` hold durable native-work fences. DuckDB needs a changing, unindexed epoch (`profile_work.py:433-440`), not a no-op UPDATE or an indexed lifecycle change. PG uses transaction row locks (`derivation.py:167-186`; `profile_work.py:445-463`). `_threading.py:14-46` keeps ownership until native work settles even if the caller is cancelled. Add privacy subject/dependency checks to these boundaries, not replacement workers/queues.
4. **Policy-only guards cannot implement targeted erasure (AC2/4).** In-progress #320's `privacy/capabilities.py:24-25` exposes `PolicyAuthority.guard(snapshot,effect)`; `privacy/policy.py:198-205` has policy/clocks but no target identities. Root explicitly reserved the exact effect-subject/dependency extension for #325 within this same authority. #320's current evidence proves policy-level fencing only. Unrelated same-owner/scope records must remain usable after targeted erasure.
5. **Startup order matters before serving (AC2/6, ER05/07).** `schema.py:547-574` and `pg/schema.py:252-257` reconstruct derivation/profile/consolidation registries inside initialization. `engine.py:607-665` opens native indexes afterward; `VectorIndex.__init__` immediately loads and reconciles snapshots (`vector_index.py:89-110`), and the single-file lexical loader reads active graph text (`engine.py:153-190`). Current-ledger/expiry handling must precede registry reconstruction and native opening, not merely the final engine return.
6. **Stale native handles can rewrite old data (AC2/3, ER06/CL07).** `vector_index.py:220-312` reconciles a snapshot against SQL metadata/payloads; it does not consult privacy dependencies. `:602-652` deletes SQL staging/payload/metadata but single-file snapshot saves are debounced; `:807-825,870-872` can save an independently loaded stale native index on save/close. `engine.py:3247-3322` rebuilds through separate clear/read/queued-write steps. Every protected stage, save, close and rebuild must revalidate the current erasure generation and reconcile before persisting; filtering search alone is insufficient.
7. **Tantivy deletion is not live segment compaction (AC3, C14).** `lexical_index.py:678-703` commits document deletion; `_purge_locked` (`:361-406`) rebuilds only the in-memory index and swallows a rebuild failure. `settle` (`:418-432`) does nothing for directory layouts. C14 explicitly requires live compaction/rebuild of deleted segments. A privacy purge needs a strict, retryable native compaction/replacement result; free-space residue remains a distinct physical limitation. Shared single-file indexes (`:793-874`) and other open protected handles must not retain an authoritative stale writer.
8. **PG has two lexical sources and in-row vectors (AC3).** `pg/schema.py:51-88,130-146` and `pg/lexical_index.py:111-112` show `nodes.content_tsv` plus `lexical_documents`; `pg/vector_index.py:3,213-237` stores `nodes.embedding` and ordinary vector deletion only NULLs that column. Purge removes the affected row/embedding/model/version plus document copies transactionally, while reporting PostgreSQL WAL/pages/replicas separately. C11 already includes this embedding; C12 includes DuckDB vector_staging and shared registries, so annotate layouts rather than create another issue.
9. **Independent lineage exists only for bound stores (AC2/6, ER07).** `namespace_identity.py:12-34` checks the workspace UUID before normal initialization. PG namespace checkout checks the same namespace identity. `pack_manifest.py` has no unbound store UUID. Root and #326 agreed: reuse the namespace UUID for a bound store, add one opaque persistent privacy-store identity for unbound protected stores, preserve it on migration, and reject missing/ambiguous identity rather than creating a fresh protected store. Paths and database URLs are not lineage identities.
10. **Catalog/legacy blobs constrain scoped deletion (AC3/4).** `blobs.py:1-14,41-53` stores content-addressed binary rows without owner columns; `engine.py:2185-2250` exposes blob/visual references. Protected v1 rejects new visual admission; #326 must map explicit legacy removal or refuse protected migration. Ambiguous/shared legacy ownership is a visible blocked coverage item, never grounds to delete another owner. Namespace identity is non-content; application-owned registry names/exports/receipts already delivered are separate boundaries.

## Agreed dependency interfaces

| Owner | Required seam consumed here | Exact acceptance purpose |
| --- | --- | --- |
| #320 | Saved immutable policy and actual project/owner/exact scope; opaque event/source/representation IDs, original deadlines, evidence/processing slots, retry context; existing authority guard. #325 adds typed exact effect subjects and backend enrollment. | AC1/2/4: distinguish source, memory and pending work; targeted denial cannot become whole-policy denial. |
| #321 | Metadata-only availability, explicit selected-ID grants with final authority/audit, exact purpose/provider/key/version inventory and narrowly scoped retirement. Include `evidence`, `processing`, `pseudonym`, and root-selected `retry_commitment`. | AC1/2/3/5: deny before unwrap; remove covered payload/key refs without silently reprovisioning or destroying keys still needed by unrelated content. |
| #322/#323 | Trusted admitted/source classification, complete admitted conditions/negation/time/quantity and source-issued token references; final ordinary read/disclosure guards (`privacy/reads.py`, `read_graph.py`). | AC1/2/4: no evidence fallback; surviving multi-source content needs complete admitted justification, not an available source ID alone. |
| #324 | Exact copy/dependency registration on the real write transaction; all prepared/published/retired/abandoned generations; typed unavailable and terminal records; current claim/deadline checks and processing cleanup. | AC2/3/5: discover nested/retired copies without rehydrating erased payloads, atomically cancel/fence work, reuse non-content replacement codecs. Full CL04/05/07/ER05 remain pending until this purge runs. |
| #326 | Restore/migration/export adapters preserve lineage and invoke the current-ledger gate before any recovery/index reads. Explicit legacy classification/removal supplies registry coverage; default protected export never includes the independent authority or private keys. | AC3/6, ER07/08: no snapshot-only continuity claim; source packs/exports/backups stay listed, and true restore/export fixture effects complete the shared rows. |

These are integrations within original criteria. They are not permission to expand another worker's implementation. Root accepted #325 ownership of the exact-subject guard and local ledger design during preparation; coordinate actual merged names before coding. #326 owns pack_tools/export/restore path investigation and mutations, not this worker.

## Bounded design and implementation order

### 1. Separate operation types and stable public status

Add closed versioned `ErasureRequest` / `ErasureStatus` types and proposed engine calls:
`request_erasure(request, *, user_id)`, `get_erasure_status(request_id, *, user_id)`,
`process_erasures(*, user_id, limit, budget_ms)`, and `expire_privacy(*, user_id, limit, budget_ms)`.
Expose sync wrappers and local CLI `privacy erase/status/purge/expire` through the existing privacy CLI; NamespaceMemory inherits engine calls and must keep lease ownership. Names are proposed #325 API names, not claims that these functions exist today. Use bounded opaque request/subject IDs and fixed enums, never free-text erasure reasons or payload fingerprints. A request retry compares the saved non-content selector/mode/boundary exactly; changed inputs reject without creating another job.

Modes are distinct: evidence-only expiry/source purge removes evidence/processing references and reports `admitted_memory_remains=true`; processing cleanup removes only that job's input/checkpoints under #324's finite policy; memory erasure follows the selected source/memory/owner roots through affected admitted records and all copies. A memory selector includes covered source representations that would preserve the deleted payload; uncertain separation invalidates that representation and affected derivatives. Owner erasure records an admitted-generation cutoff so old jobs cannot publish, while a subsequent explicit fresh admission uses new IDs/generation rather than resurrecting old ones. Whole-owner selection must be explicitly authorized over its selected scopes, not inferred from a requested owner string.

Availability checks use exact `now >= deadline` before payload/key lookup and before final delivery. Order owner authorization, erasure, expiry, capture/classification, permission, key/payload; absent and foreign IDs look equally unavailable. Completed admitted memory follows its own deadline; a prepared pending job cannot extend its saved processing/source deadline. A separate memory deadline is actual denial/erasure policy, not legacy archival TTL, and pinning cannot override it.

Return the frozen `erasure-status-v1` states (`requested`, `blocked_from_access`, `live_purged`, `backup_expiry_pending`, `complete_within_managed_boundary`), plus durable per-stage progress/fixed failures and explicit managed/external limitations. A committed access fence does not imply purge. Live completion requires verified copy coverage. Managed backup completion requires the declared deadline or verified operator/provider disposal evidence; unknown retained backup state stays pending. Already delivered output and operator exports remain outside revocation even at managed completion. No new hosted HTTP diagnostic/delete endpoint is implied; existing HTTP/MCP ordinary reads inherit the exact denial fence.

### 2. Independent durable ledger, then transactional acknowledgment

Root approved one explicit `LocalErasureLedger` outside the pack/restore domain using existing stdlib SQLite + FileLock patterns (`workspace.py:196-216`), plus an injected current-ledger provider contract for hosted/distributed deployments. No new dependency, custody system or hosted service. The configured external path is not silently derived inside the pack and is excluded from ordinary pack export. A local adapter does not claim a distributed network-filesystem lock or resistance to malicious same-user filesystem mutation.

Proposed provider calls are `current_head(lineage)`, `append_intent(request)`, and bounded `entries_since(cursor)` under a held authority gate. Head/entries contain only opaque ledger/store/project/owner/scope/request/subject identities, sequence/generation, clocks, mode, policy refs and fixed reasons. Keep ledger identity and monotonically increasing applied sequence in the live store. A new protected store explicitly registers its lineage; an existing store never silently recreates a missing ledger. Reject wrong lineage, missing ledger and a head behind the live applied sequence. Restoring BOTH pack and local ledger can defeat freshness under a trusted OS user; only an independently current external authority can rule that out, and status/docs must say so.

Write order: under the existing authority's exclusive erasure boundary, durably append the independent intent first; then in one live-backend transaction commit the root denial fence, exact work/generation invalidation, purge-job identity and applied ledger cursor. Acknowledge only after both succeed. A crash/failure between commits leaves the external intent authoritative and the request `requested`/pending application; reopening applies it before disclosure. Never roll back the independent denial because live SQL failed. Duplicate request returns the same intent/job. Durable local ledger transaction/lock ownership and cancellation follow `run_async_to_completion`/`run_to_completion`; no cancellation acknowledgment before native storage settles.

The local provider has one process-owned mutex/native lock discipline, restrictive files and bounded reads. An external provider must supply equivalent current-head/serialization semantics; an arbitrary caller-supplied old JSON snapshot is not authority. Keep implementation classes behind the same PolicyAuthority contract and compose policy revision with erasure state; do not introduce a second permissive decision path.

### 3. Exact transactional subject fencing and dependency traversal

Extend the existing guard with a closed, internally derived effect context: actual store/namespace, owner/exact scope; source/representation/memory/work/artifact IDs and immutable generations; complete dependency identities; intended effect and retention class. For new admission, bind its fresh admission epoch. Callers cannot forge authority through mutable metadata. Every final disclosure/provider send/native stage/save/publication and journal write uses this context. Shared batches pass every participant, not only their head.

In existing DuckDB/PG transactions enroll a scoped privacy fence row. DuckDB claims a changing unindexed epoch and holds its native fence until the thread finishes; PG locks the boundary and target/work rows in stable identity order. Validate before and after awaited/crypto preparation and immediately at effect/publication. Native lock order must remain consistent with existing `_write_lock -> conn_lock -> native fence`; do not hold a connection while awaiting another index lock or allow cancellation to release a live thread's fence. An erasure racing an already authorized native effect waits for or safely conflicts with it before acknowledgment, then purges its copy; a stale later effect rejects.

Persist root fences before asynchronous traversal. Guard lookup walks the registered ancestry so dependent disclosure/publication is denied immediately even before the purge cursor reaches the descendant. Registration and publication are in the same fence transaction, preventing a new unregistered child after acknowledgment. Traverse bounded pages with visited opaque IDs, stable cursor and saved selection generation, including retired/archived and abandoned work; never use the current active-node scan or a 500-row organizer page as proof of coverage. Missing/inconsistent ownership/classification is a fixed visible coverage failure, not an empty successful scan.

For multi-source survival, keep only a complete statement/field already supported by surviving currently available sources or independent explicit admission, verified against the saved admitted representation and policy. `evidence_refs` membership alone is insufficient semantic proof. Preserve a separable surviving fixture with exact full qualifier/negation/time/attribution/quantity; invalidate mixed prose or uncertain summaries instead of substring deletion or LLM rewriting. All copy rewrites/new surviving generations pass existing sanitizer/output/transaction rules. Preserve unrelated owners, projects, scopes and same-owner unselected controls.

### 4. Resumable logical purge and native artifacts

Use #324's exact dependency manifest and unavailable/terminal codecs. Save metadata-only target identities and per-stage progress before removing discovery anchors. Replace covered event/operation histories with the versioned unavailable envelope: opaque original identity, fixed disposition and checksum-verification/replay unavailable. Do not recompute an old checksum over redacted text, retain a public predictable digest, or decode erased journal content for recovery. Remove exact graph/edge/query/receipt/citation/relevance/learning copies as registered, including nested proposals, preparation/publication heads, HMAC retry commitments and key/epoch references. Content-free identity reservations may survive only if they contain no predictable content-derived identifier or hash.

Fixed stages: (1) durable denial/work terminalization, (2) bounded dependency target discovery, (3) SQL payload/history/graph purge, (4) native index/segment purge and forced snapshot persistence, (5) applicable provider-state retirement, (6) coverage verification/status. Persist cursor/attempt/fixed failure independently for each, with idempotent replay. Do not lose exact index targets when graph rows disappear. PG graph/vector/lexical removal occurs in the relevant SQL transaction; local index stages retain a durable target/retry anchor until native deletion plus snapshot/compaction verifies. Use existing strict `delete_from_indexes` sequencing where appropriate, not swallowed best-effort eviction.

C11/C12 layout detail: DuckDB removes `vector_staging`, `vector_payloads`, `vector_metadata` and exact native keys; PG removes `nodes.embedding`, its model/version, and both lexical SQL sources. C13 forces a fresh `vector_snapshot` row or `vectors.usearch` after deletion, including pending save temporaries. Protected native handles register with the existing ownership/epoch coordination: stale save/close/rebuild must reconcile current SQL/ledger and release old managed references before any persistence, including independently loaded engine instances. The single-file lexical registry is reused, not bypassed.

C14 needs strict compaction: rebuild the in-memory Tantivy object without deleted documents; for directory indexes, write a fresh managed index from surviving authorized live documents/stages under the native fence and publish it with a resumable artifact-swap record. Preserve unrelated valid staged documents; graph-only rebuilding is not enough if other current preparations exist. Close/reload known handles and prevent stale writers from modifying retired segments. Failed merge/replacement/removal stays a failed stage; do not inherit `_purge_locked`'s swallowed failure. Track only exact task-owned temporary/retired paths and identity; unknown files are not destructively glob-deleted. Remove managed retired segments, then report residual filesystem free space/pages separately. Reuse the existing index storage primitives; no custom search engine or inference is required.

Provider disposal is reference-aware: remove covered per-object envelopes/wrapped DEKs, processing checkpoints, comparison commitments and exact key refs; retire a shared purpose/epoch key only when the authorized owner/epoch scope covers it or no surviving managed reference needs it. Preserve unrelated evidence, pseudonym continuity and retry state. Missing retirement support/failure remains visible, and old backups/recovery keys are separately reported. Pseudonym-key deletion alone never counts as fact deletion. #321 owns actual custody calls and #324 owns HMAC consumers; #325 does not create or recompute their keys.

### 5. Startup, rebuild and restore gates

After pack attachment and namespace identity checks, do a no-content format/capability/lineage preflight. Then initialize only minimal privacy state and apply current independent ledger plus due expiry fences, before `initialize_database`/PG initialization can reconstruct registries, before native index load/backfill and before worker startup/public reads. Existing protected stores missing needed authority fail explicitly; ordinary legacy opens do not change bytes or acquire keys. Split initialization phases only as required by this order, preserving existing namespace identity and transaction guarantees.

After terminal/fence application, #324 reconstructs only still-live work from admitted records; removed mutable work rows cannot override the persistent fence. Rebuild/reconciliation uses surviving admitted memory and exact current stages, never evidence decrypt or re-extraction. Stale snapshot keys are dropped against current durable ownership/erasure state before a query, and a subsequent stale handle cannot save them back. #326's restore/migrate/export entry points call `apply_current_ledger` and hold the same guard around publication/export reads. The snapshot's own ledger is only historical data, never proof of current deletion continuity.

### 6. Copy coverage and honest boundary report

| Copy IDs | Managed treatment / verification |
| --- | --- |
| C01-C03, C28 | Evidence/processing slots, event metadata/chat serialization and sensitive hashes removed/replaced under exact source policy. Source-only mode explicitly preserves separately admitted facts. |
| C04-C06 | Current and retired node/edge payloads, admitted metadata/value bindings and dependent graph state erased or proven surviving; owner/scope positive controls mandatory. |
| C07-C10, C16-C22, C37 | #324 registered direct/extraction/batch/derivation/lifecycle/merge/profile/summary/alias/fast retry copies, nested records, work and all generations; typed unavailable terminal state; no journal resurrection. |
| C11-C14 | Per-layout SQL vector/staging/ownership rows, native live vectors/snapshots/save temps, both PG lexical sources and actual live Tantivy segments. Inspect IDs/numeric vectors and stored documents, not only text markers. |
| C15, C23 | Protected receipt/query attachments, outer operations, citation/relevance/learning references and commitments; preserve only safe numeric/opaque identity with explicit unavailable replay where necessary. |
| C25-C27, C30, C32, C40 | Final delivery fences; release engine-owned in-flight/managed cache references. Returned text/browser/framework/application copies and heap residue are not revocable. No invented persistent snapshot cache. |
| C33-C36 | Refresh engine-controlled current encrypted artifacts and exact owned temps. Report DuckDB pages/WAL/spill/free space, Tantivy retired-file residue, PG WAL/base pages/replicas, source packs/backups/exports and recovery keys separately. SQL DELETE, checkpoint, VACUUM or compact is not secure wiping. |
| C24, C29, C31 | Provider retention, consumer/framework caches and application/hosted logs are actual external-owner obligations. Fixed diagnostics here prevent new payload logging; do not certify old external logs erased. |
| C38-C39 | Protected visual v1 rejection plus #326's explicit legacy removal/refusal; scoped engine metadata/cache cleanup. Keep opaque namespace/store identity, disclose application catalog/path/display-name ownership and avoid deleting an unrelated namespace. |
| C41-C42 | Reference-aware provider key/epoch/retry material retirement, encrypted policy-rule refs, independent current non-content ledger and unavailable history envelopes. Ledger denial identity outlives resurrecting managed copies. |

## Exact planned file scope

Only implement after root release and merged prerequisites. This finite list may shrink when dependencies already provide a hook. Additional paths need an AC/regression/security justification before expanding it.

New runtime modules (four):
- `src/prme/privacy/erasure.py`: closed requests/effect subjects/status/current-ledger interfaces.
- `src/prme/privacy/ledger.py`: explicit external-to-pack durable local ledger using SQLite/FileLock and provider protocol.
- `src/prme/storage/erasure.py`: DuckDB/PG transactional fence/job/cursor/dependency traversal and logical purge adapters.
- `src/prme/storage/erasure_indexes.py`: exact native purge targets, strict compaction/snapshot reconciliation and managed artifact progress.

Existing/dependency modules, narrow integrations:
- `src/prme/privacy/{__init__,capabilities,admission,records,reads,read_graph,cli}.py`: one guard/context, availability/status, injection/CLI. No alternate sanitizer/cipher.
- `src/prme/storage/{privacy,privacy_journals,schema,engine,index_eviction,vector_index,lexical_index}.py`, `src/prme/storage/pg/{schema,vector_index,lexical_index}.py`: shared durable records, initialization phases, strict purge and index epoch guards. #324 owns copy registration/codec code; consume rather than duplicate it.
- `src/prme/storage/{extraction_work,derivation_staging,derivation,derivation_registry,profile_work,profile_registry,profile_publication,profile_collection,consolidation_publication}.py`: enroll actual subjects in existing transaction/native fences, dispatch terminal recovery and retain non-content target reservations. #324 may already have the necessary hooks.
- `src/prme/{client,workspace,cli}.py`, `src/prme/organizer/jobs.py`: sync/lease binding, local explicit maintenance/status. No unscoped owner inference, no daemon, no namespace deletion feature. Public reader/provider call sites consume the existing context helper; if merged #323/#324 leave a particular site unguarded, report its exact AC2/3 gap before adding that file.
- `src/prme/storage/blobs.py` only if #326's explicit classified legacy removal needs an exact-reference deletion primitive; no visual ingestion feature. This conditional path is already bounded to C38/AC3.

New tests/support (six) and one fixture:
- `tests/privacy_erasure_support.py`: real ledger/backend/native/copy probes and finite clock/fault barriers, reuse `privacy_fakes.py` / `privacy_journal_support.py`.
- `tests/test_privacy_expiry.py`: exact deadline, independent memory deadline and startup.
- `tests/test_privacy_erasure.py`: requests, multi-source closure, status/idempotency/coverage and keys.
- `tests/test_privacy_erasure_fences.py`: real concurrent effects, same-owner controls, cancellation and crash ordering.
- `tests/test_privacy_erasure_recovery.py`: registry/work deletion, native snapshot/rebuild and current-ledger application.
- `tests/test_privacy_erasure_scenarios.py`: frozen ER01-ER08 + shared CL04/05/07, calling the real earlier fixture workflows.
- `tests/fixtures/privacy/erasure-v1.json`: distinct synthetic source-only/admitted/foreign/surviving markers and fixed expected copy identities. Extend `tests/typing/public_api.py` and existing shared privacy scenario hooks only where integration requires it.

Docs/results: `docs/EVIDENCE-ERASURE.md`, `docs/RFC-0019-Evidence-Privacy.md` implementation clarification, `docs/RFC-0007-Decay-and-Forgetting.md` clear privacy-versus-archival cross-reference, `docs/WORKSPACES.md`, `docs/plans/2026-10-evidence-privacy/{PV-06-plan.md,PV-06-results.json,content-copy-inventory.json}`, and root-required `docs/reviews/issue-325-erasure-review.md`. Keep frozen scenario/setup/expected values intact. Pack migration/export implementation files remain #326's ownership.

## Finite validation and actual harness calls

| Frozen row | Original ACs | Actual effects/calls that complete the row |
| --- | --- | --- |
| ER01 | 1,6 | Real protected `engine.ingest` / `store`, #321 selected-source reveal and #323 `inspect_source`; set injected clock to deadline-minus-one, exact deadline and later without `process_erasures`, then close/reopen. Verify expired read despite still-present ciphertext and successful admitted `retrieve` until its own deadline; startup denies before private-key lookup/native recovery. |
| ER02 | 1,3,4 | Seed with real `store`, controlled successful `ingest`, `consolidate_knowledge` and `organize(jobs=['summarize'])`; build single-source, exactly separable surviving support, uncertain mixed summary, unrelated owner and unselected same-owner controls. Call real `request_erasure` in source-only versus memory modes then `process_erasures`; inspect public nodes/graphs/state and every registered logical copy. Keep full surviving qualifiers. |
| ER03 | 2 | Pause actual derivation/profile/summary publication at existing native/transaction checkpoints and an actual audited evidence read before final return. Commit `request_erasure`, release stale tasks, and assert no covered write or response afterward. Use independent PG connections and distinct DuckDB engines/real native fences. During native work, acknowledgment waits until ownership settles; cancelled callers cannot release the fence early. A different store and unrelated same-owner record remain usable. |
| ER04 | 2,3,5 | Repeat the identical opaque request and reject changed selector reuse. Interrupt after each of the six declared purge stages and between independent intent/live fence commits, reopen, and call the same `process_erasures`. Inject one persistent stage failure at its true I/O/checkpoint, never replace purge with a successful fake. Verify durable failure, unchanged denial, no complete status and successful resume once fault removed. |
| ER05 | 1,2,3,5 | Reuse #324 actual direct-store/extraction/derivation/profile prepare and stage fixtures. Apply real expiry/erasure, remove only the fixture's mutable work row through its test-owned connection, close/reopen, call `process_pending`, `process_extractions`, `process_profiles` and `resume_profile`. Reconstructed terminal work cannot reload an unavailable journal or call evidence/extraction/embedding; unrelated pending work succeeds. |
| ER06 | 2,3 | Save actual pre-erasure vector file or `vector_snapshot` BLOB, run real purge, then restore that stale native artifact only while retaining current graph/ledger; reopen and call real `rebuild_indexes` (DuckDB). Exercise independently loaded stale `VectorIndex.save`/`close`, rebuild paused after page read, and Tantivy compaction. PG exercises real SQL vector/lexical reconciliation under current ledger; no unsupported DuckDB rebuild call is treated as a PG pass. Verify exact keys/embeddings/docs absent, unaffected keys still present, no source re-extraction. |
| ER07 | 2,5,6 | #326 takes/restores an actual pre-erasure directory/.prme or PG schema fixture; its real restore/open path supplies #325 current independent ledger and calls `apply_current_ledger` before recovery/index creation. Repeat with only the snapshot's ledger, wrong lineage and stale/missing head. Current authority fences recovered work and exposes only survivors; snapshot-only protected restore refuses. Pending until #326 actual integration. |
| ER08 | 1,5,6 | Actual `engine.archive`, `PRMEChatMessageHistory.clear`, `PRMEChatStore.delete_messages` demonstrate retained history, then real erasure/purge/status APIs and CLI output. Use the supported protected standard adapter, or #326's explicitly migrated legacy fixture if raw history cannot support the profile; never weaken the clear/erase assertion. Retain a real test backup and operator export via #326 export; verify `live_purged` / `backup_expiry_pending`, limitations and verified managed completion. Retained export remains outside revocation. Pending until #326 actual integration. |
| CL04 | 2,3,5 | Reuse #324 fixture's actual `promote`, `archive`, `reinforce`, `supersede`, TTL expiration and penalty paths, then call real memory `request_erasure` + `process_erasures`; inspect every before/after/chain operation and public provenance for unavailable history and no raw hash. No fake-fence-only pass. |
| CL05 | 2,3,4,5 | Reuse #324 real `consolidate_knowledge`, `resume_profile`, `discard_profile`, `organize(jobs=['summarize'])`, organizer merge, `propose_product_alignment` and `review_alias_proposal` flows with deterministic provider responses, published/retired/rejected/prepared/abandoned copies. Actual purge traverses all affected generations, preserves proven unrelated support, and removes nested external assessment copies. |
| CL07 | 2,3,5 | Reuse #323/#324 real vector/lexical stage and save interruption fixtures; abandon one work item, then real purge. Inspect SQL payload/staging/registry rows, actual USearch keys plus saved file/BLOB, all live Tantivy documents/segments and both PG index sources. Restart/rebuild with current ledger. C34 physical residue remains separately reported even when live coverage passes. |

CL08 is #324/#326's export case, not a new #325 case; report its pending export integration without taking over its cohort. The 11 rows above yield at most 22 backend row results here, each with required layout/fault subchecks, and reuse earlier shared results instead of inflating scenario counts. ER07/08 are not fully passing before the actual #326 operations. #327 owns the full 104-execution product gate and separate 24/24 quality sets.

Before every destructive fixture, verify the target marker/copy and positive controls actually exist in each required logical store/feature. After purge, enumerate by exact IDs, dependency rows, operation kinds and all nested payloads; scan for source-only/deleted markers and their test-only raw hashes. Inspect actual vector payload/key absence because float-byte text scans cannot prove semantic erasure. Do not use successful empty scans for never-created profiles/receipts/stages or a provider-failure path instead of a successful route.

Filesystem inspection distinguishes current managed directory files/snapshots and temp/retired segments from logical SQL contents and free pages/WAL/spill. Obtain a test-owned PG logical dump; inspect task-owned physical artifacts only where access exists and report unavailable visibility. Deliberately retained backup/export demonstrates limits. No production data/database, filesystem wiping or arbitrary file deletion is authorized. The stored result records case/backend/layout, fixture hash, exact code/dependency versions, command/log, status, observed counts, failed stage and coverage boundary.

During implementation run targeted new modules plus only affected members of this fixed regression set: `test_atomic_ttl_expiration.py`, `test_atomic_lifecycle.py`, `test_direct_store_recovery.py`, `test_engine_startup_recovery.py`, `test_derivation_commits.py`, `test_derivation_index_staging.py`, `test_obsolete_derivation_staging.py`, `test_profile_work.py`, `test_profile_registry.py`, `test_profile_publication.py`, `test_profile_collection.py`, `test_summary_publication.py`, `test_vector_recovery.py`, `test_determinism_rebuild.py`, `test_single_file_pack.py`, `test_single_file_pack_encryption.py`, `test_lexical_index.py`, `test_pg_vector_index.py`, `test_pg_lexical_index.py`, `test_workspace.py`, `test_workspace_single_file.py`, `test_pg_workspace.py`, `test_integration_chat_history.py`, and merged #320-#324 privacy tests. Keep legacy golden bytes/checksums; do not regenerate them to hide failure.

Once on the final revision in root-allocated isolated environments and a live disposable PostgreSQL schema/database:
1. Run the frozen-row modules with both DuckDB layouts and PostgreSQL, including the actual #326 integrations before claiming issue completion. Skips are not backend evidence.
2. `uv run --no-sync python -m pytest tests/ -q` with the required dev/postgres/API/MCP/framework/privacy extras, plus required CI Python 3.11-3.13 DuckDB/PG matrices.
3. `uv run --no-sync ruff check src/ tests/`, format check on touched files, and `uv run --no-sync mypy --follow-imports=silent --ignore-missing-imports --strict tests/typing/public_api.py`.
4. `uv build` and installed-wheel/version/py.typed smoke in a separate temporary environment.
5. Required `python -m scripts.run_simulations` under current defaults and CI previous defaults (`PRME_SCORING__FUSION=weighted`, `PRME_PACKING__CONTEXT_FORMAT=auditable`, `PRME_PACKING__MULTIPATH_ORDERING=balanced`); no paid/live models. Reuse verified exact-revision evidence when applicable.
6. Framework integration checks if not already covered with extras, and `python docs/plans/2026-10-evidence-privacy/validate-contract.py` for structural coverage only. Then root's required review/delivery, without another quality round.

## Readiness and remaining gates

Ready for a bounded implementation release after verified merged #320/#321/#322/#323/#324 code and root assignment of environment/DB. Preparation is not that release. #320's effect context extension is explicitly #325-owned; independent ledger and stale native-save handling are accepted plan details, not hidden extra tickets. No unresolved product choice requires starting another investigation now.

Concrete gates remain: real copy/dependency and terminal/unavailable APIs from #324; actual scoped key retirement/inventory for all four purposes from #321; admitted multi-source support classification from #323; verified startup phase split and persistent unbound lineage; real #326 restore/export/legacy integration for ER07/08 and any classified legacy removal. Missing or ambiguous coverage blocks the linked AC; it is not relabeled a follow-up/pass. Bound implementation to these interfaces, notify root before adding paths, and preserve the two-cycle limit. General legacy migration, cloud backup deletion, external provider/cache cleanup and physical residue remain with their named owners.

Final read-only verification: `git status --porcelain=v1` and `git diff --stat` returned no output; branch remains `issue-325-retention-erasure`, HEAD `33222b54a85fa90fd23d47ae9a9170564bf24828`. `git check-ignore -v` confirms this file is ignored by `.git/info/exclude:12:.github-ticket-loop/`. No tracked files changed and no runtime tests or repairs ran. Preparation is complete; stop here.

## Frozen scenario reference (verbatim setup/expected; all not_run)

- `privacy-scenarios.json` SHA-256: `e85223747c3f4464c98c306d38b54065504a6f063eb6c56bacc3c7cbd89e4d12`
- `content-copy-inventory.json` SHA-256: `40c0925819a37e19bd1624dae80e3edee8aa17dc0aa36058b0d41a1a7ea2b5a8`
- `sanitizer-contract.json` SHA-256: `ec1f5aa375269b33df33ead3c4b0c763a278a8887b3c250f47d19a1718664f7d`

### CL04 Lifecycle snapshot copies

Setup: Promote/archive/reinforce/supersede/expire/penalize nodes with source-only metadata, then erase.

Expected: All before/after and chain snapshots obey profile and erasure; fixed metadata-only erasure records do not preserve erased snapshot text or public predictable hashes.

### CL05 Organizer profiles summaries and alias copies

Setup: Prepare and publish profiles, daily summaries, merges and accepted/rejected alias proposals from mixed sources; inject an abandoned generation.

Expected: Published, prepared, retired and abandoned copies respect admitted-only policy and output validation; no forgotten source-only excerpts or external assessment copies.

### CL07 Indexes snapshots and abandoned stages

Setup: Interrupt vector/lexical staging and native snapshot save; retry and abandon one work item.

Expected: No sealed source indexed or left in prepared/abandoned content. Inspect all live SQL/native/Tantivy/pgvector copies, then verify covered erasure; physical residue limitations remain explicit.

### ER01 Exact expiry before maintenance

Setup: Advance fake clock exactly to evidence expiry without running purge, then reopen after offline time.

Expected: Evidence denies at deadline/startup; availability is expired even while bytes await purge. Admitted memory remains until its own policy expires.

### ER02 Source-only versus dependency-aware memory erasure

Setup: Compare source-only purge and memory erase on one single-source fact, one separable multi-source fact, one uncertain summary and an unrelated owner.

Expected: Source-only says facts remain. Memory erase removes covered fact/derivatives; retain only proven surviving content, invalidate uncertain mixed content, preserve unrelated owner.

### ER03 Transactional fence and in-flight disclosure

Setup: Pause extraction publication, organizer publication and diagnostic response just before completion; commit erasure fence.

Expected: Acknowledgment follows committed fence; paused stale work cannot publish or disclose afterward. Already returned responses are explicitly outside revocation.

### ER04 Idempotent interrupted purge

Setup: Repeat request identity, crash after each purge stage and inject a persistent one-stage failure.

Expected: Same request resumes without re-adding data; failures stay visible and access remains blocked; no complete status while any managed stage fails.

### ER05 Restart journals cannot resurrect source

Setup: Erase or expire pending source after prepare/stage and before publish; remove mutable work row, then restart journal recovery.

Expected: Reconstructed jobs honor authoritative fence/deadline and cannot reload source from historical direct-store, extraction, derivation or profile payloads.

### ER06 Rebuild cannot resurrect index content

Setup: After purge restore a stale native index snapshot while keeping current graph/ledger, then rebuild and reopen.

Expected: Reconciliation/rebuild exclude erased vectors, lexical documents and stages, serving surviving admitted memory only; no re-extraction of erased source.

### ER07 Pre-erasure restore requires current ledger

Setup: Restore/import snapshot taken before erasure, once with current independent ledger and once with only the snapshot ledger.

Expected: Current ledger is applied before serving and recovered jobs are fenced. Snapshot alone refuses any deletion-continuity/protected-restore success claim; no erased content exposed.

### ER08 Truthful purge backup and external status

Setup: Archive and clear chat, then erase with live purge complete but retained backup and operator-held export.

Expected: Archive/clear is not erase; report live_purged then backup_expiry_pending, remaining copies and failures; complete only within verified managed boundary, never media wiping or offline revocation.

## Implementation clarification (2026-10-20)

PolicyAuthority remains the existing injected policy contract. PV-06 supplies the real independent ledger and composes both approvals in AdmissionCoordinator's one actual-effect guard; no policy-revision service is introduced. Synthetic policy/model fixtures are not evidence of production policy management. Protected engine opens require an explicit ledger; shared synthetic fixtures now explicitly provision an external temporary ledger. Missing binding on existing protected admissions requires authorized versioned migration. PostgreSQL namespace/workspace-catalog runtime threading and the existing consolidation native-stage node-ID argument are approved narrow AC2/6 integration paths. Root owns full-suite/CI/review/delivery; no push is authorized yet.

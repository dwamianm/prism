# PV-02 / #320 preparation

Status: approved implementation plan. Prerequisite #319 merged; implementation released on base 33222b54a85fa90fd23d47ae9a9170564bf24828.
The reviewed original-base source passed the full suite (6965 passed, 104 skipped).
Main subsequently added reingestion. The approved bounded integration is based
provisionally on CI repair commit 01a39705b173c1b994e662927275fc57f98e6b88;
publication waits for its actual merge and source equality verification.
Prepared against main 72360483 and reviewed contract 15594f2ef0fd0509dea826c472448b00c0b084a3.
Worktree: /Users/dwamianm/Sites/prism/.claude/worktrees/privacy-320-admission-policy
Branch: issue-320-privacy-policy. Base: main, explicitly assigned by root.
The authorized delivery includes implementation, finite validation, independent review, commit, push and a PR targeting main. Deployment and release are excluded.

## Original acceptance criteria (unchanged)

1. Support `none/minimal/full` evidence capture, runtime/diagnostic evidence
   use, finite evidence expiry and explicit memory-retention policy.
   Invalid combinations fail before any durable write/provider call.
2. Persist the effective policy/version with each accepted record; a stricter
   caller policy cannot be weakened by a project default or retry.
3. Define shared pre-side-effect admission hooks before logging, storage,
   embeddings or provider calls; PV-13 supplies their sanitizer implementation.
   Expose immutable scoped policy/revision snapshots and authoritative fence
   hooks that deny stale work before publication, transmission or disclosure.
   Test the contract with a fake denying policy; no hosted account is required.
4. Minimal evidence preserves conditions, negation and attribution. Oversized
   or ambiguous excerpts do not silently become incomplete facts.
5. Separate no diagnostic retention from no durable processing input. The latter
   rejects asynchronous ingestion and explains caller resubmission on failure.
6. Protected profiles reject unsupported visual input before persistence.
   Policy inspection exposes provider use and unlimited retention explicitly.

The eight frozen AP cases below are the finite policy acceptance cohort. Additional
regression checks protect existing behavior; they are not replacement acceptance
criteria. Zero new paid/live model calls and no long benchmarks. Two unsuccessful
repair/validation cycles for the same behavior end that strategy. Root owns review
scheduling, loop state, merge and closure.

## Scope ruling and dependency boundary

Root's scope ruling, received during preparation:

- Implement the complete policy/admission path plus minimum shared source/work
  primitives needed to make valid public admission real.
- Explicit injected sanitizer/crypto/provider capabilities can be faked in
  component validation. Policy composition, fence decisions, actual backend
  effects and evidence selection must be real.
- AP01 must retain admitted memory, persist immutable effective policy and prove
  no completed evidence payload for no-evidence.
- Missing downstream capabilities fail closed by default.
- Do not implement the recognizer, key custody/crypto lifecycle, full ordinary
  sealed reader separation or whole historical-journal rewrite here.
- End-to-end protected status remains unavailable until siblings and #327 pass.

This resolves a genuine dependency cycle: RFC-0019 section 3 requires sanitation
and protected work before content effects, but #321 and #322 depend on #320.
The implementation will expose honest required capabilities and exercise real
admission/storage with explicit test implementations. It will not call a typed
model or universally rejected request a successful valid-profile admission.

## Source-backed findings and paths

- `storage/engine.py:935` store and `:1630` ingest are separate paths.
  Store runs novelty inference before append when enabled (`:1092`); admission
  must precede that too. Store's optional extract queues background work.
- `ingestion/pipeline.py:432-455` saves source plus both durable work pointers
  before extraction even with wait_for_extraction=True. Merely requiring that
  flag does not implement no-durable processing input.
- `engine.py:1711` ingest_batch validates attribution for all inputs but then
  admits sequentially. `pipeline.py:513` batched admission can persist a prefix.
  Privacy preflight must validate/sanitize the complete batch before any enqueue.
- `engine.py:1780` ingest_fast and `:1850` ingest_fast_many bypass extraction.
  Fast batches already snapshot metadata and atomically insert all events/jobs.
  `storage/fast_ingest.py:75` retry digest includes content and metadata, so a
  new admission timestamp inside that digest would break identical retries.
- `event_store.py:283,309,413` and `pg/event_store.py:239,294` are the atomic
  source/work boundaries. Both freeze metadata before a lock/connection wait.
  Their existing event rows, direct-store records and operation bytes must not
  be retroactively reinterpreted.
- `engine.py:1912` raw materialization reconstructs a NOTE from event.content
  and current TTL defaults. It must not recreate a protected source NOTE.
  New direct stores already have complete saved-node repair snapshots.
- `pipeline.py:681` resumes saved plans before new extraction.
  `:854` sends a source plus optional preceding turns; `:977` sends a batch
  plus correction memories and can split/retry. Every participating persisted
  policy must be checked before each transmission, not only the head's policy.
- `pipeline.py:1184` and `ingestion/planning.py:185` already separate
  in-memory graph planning from embedding preparation. Reuse this separation
  for synchronous ephemeral preparation; do not make a second extractor.
- `pipeline.py:1634` journals/stages/publishes prepared plans.
  `storage/derivation.py:80,162` provides real backend atomic publication with
  extraction claims; `derivation_staging.py` fences native staging. A policy
  fence is distinct from the existing worker-generation fence.
- `engine.py:2275` retrieval drains pending work before returning, then calls
  the retrieval pipeline. `retrieval/pipeline.py:318` owns optional
  reformulation, reranking, temporal sends and receipt publication. Admission
  and fence denial must occur before these effects, including hidden retries.
- Engine event/history/extraction readers at `:2826-2944`, node/scan/query
  readers at `:2537-2743`, provenance at `:2864`, state/aggregates and receipt
  readers are disclosure siblings. New admission references provide the same
  authoritative-policy check; this ticket does not claim full legacy/source
  classification or all sealed-source separation.
- `client.py:230` creates its own event-loop engine. Its store, ingest,
  ingest_batch, fast paths and observe must forward policy/capabilities.
  Sync ingest always requests wait_for_extraction=True but still needs the new
  ephemeral path described above.
- HTTP `api/models.py:108,200,237` and `routes.py:231-323` have distinct
  store, ingest and fast-batch models. API validation may echo invalid input;
  privacy errors need fixed codes without raw values.
- MCP `mcp/server.py` has separate store/ingest/fast tools and resources,
  with ordinary error handling that currently formats arbitrary ValueError.
  Policy inspection and fixed privacy-denial handling must be registered too.
- Framework history stores serialized message content in metadata:
  `integrations/_chat_history.py:103`, `langchain.py:256`,
  `llamaindex.py:246`. Control writes bypass store through
  `engine.py:2946`. Unsupported raw-history or binary representations must
  explicitly refuse protected operations; the profile must never be changed.
- `vision/observe.py:150-177` builds a provider and temporary work directory
  before engine.store/ingest. The guard belongs here as well as engine.observe,
  covering public observe_source and the CLI caller before frame/hash/blob work.
- Workspace config copies bind actual namespace UUIDs
  (`workspace.py:255-293`; PostgreSQL equivalent `:461`). Capability routing
  must use that namespace, not a mutable process-global policy.
- The current CLI has export/migrate, not a supported general import endpoint.
  Supported-import subchecks must describe the actual supported restoration or
  existing API ingestion used; do not invent an importer or count a nonexistent
  adapter as passing. Export/migration hardening stays with #326.
- Relevant history includes durable extraction, atomic raw batches and recent
  owner/claim fixes. Existing finite metadata snapshots, saved grounding
  versions, direct-store v1/v2 and derivation/receipt checksums remain unchanged.

## Concrete implementation design

1. **Immutable typed policy and composition.**
   Add closed, frozen models using immutable nested tuples, explicit opaque
   policy ID/revision and project/owner/scope binding, capture/use/profile,
   finite evidence lifetime or explicitly permitted unlimited retention,
   separate required memory retention (erase-after or until-deleted), work
   ceiling, durable-processing permission, language, pinned sanitizer/registry
   version, category actions and opaque exclusion/strict-schema references.
   Validate combinations before engine effects. Restrictive composition takes
   earliest deadlines, lowest capture, diagnostics-only and mandatory blocks.
   Reject incomparable redact/pseudonymize rules, incompatible schemas/versions
   and nonopaque protected protocol identities. Preserve legacy identity rules
   whenever no privacy policy is selected.

2. **One admission coordinator and explicit downstream capabilities.**
   Inject sanitizer/input-output admission, purpose-bound payload protection and
   authoritative policy authority separately from serializable PRMEConfig.
   Config keeps a copied immutable optional default; request policy only narrows
   it. Public inspection redacts rule material, shows exact effective revision,
   deadlines, provider classes/use and explicit unlimited retention, and states
   missing integration capabilities/protected-product status honestly.
   Required capability absence denies before any content log/write/provider.
   Exclusion hooks are typed bounded declarations/references, not arbitrary
   executing caller code. #322 supplies the real sanitizer.

3. **Minimum durable source/work representation.**
   Introduce one versioned privacy-admission row keyed to an ordinary event ID,
   storing immutable canonical policy snapshot/reference, opaque source and
   representation IDs, optional opaque evidence payload and separate optional
   opaque pending-processing payload/deadline. The injected protector owns
   encoding/decoding and purpose/context validation; #321 supplies cryptography.
   Persist it atomically with the event and work. A new protected event is an
   explicitly versioned metadata envelope with no source body. Source checksum
   belongs to its protected representation; do not publish an original-content
   hash disguised as an empty-envelope checksum. New admitted nodes/plans carry
   opaque policy/source references; legacy rows/checksums are untouched.
   Inventory every new durable field.

4. **Real capture behavior and bounded minimal selection.**
   No-evidence writes no completed evidence payload. Full retains the sanitized
   source through the evidence capability. Minimal selects a complete bounded
   source unit, including attribution, clocks, conditions and negation; its
   initial safe unit is the complete message, not a guessed sentence prefix.
   Reject a unit that cannot fit. Do not auto-upgrade capture, truncate or
   accept an ambiguous compact projection. Existing qualifier grounding remains
   in force for extracted admitted statements. Label retained quotations as
   sanitized and give the representation its own opaque ID.

5. **Sync ephemeral preparation versus durable asynchronous work.**
   For no durable processing, sanitize/extract/ground/prepare in memory first;
   failed/cancelled preparation leaves no event, queue, source or diagnostic
   journal and returns fixed resubmission guidance. Only the completed admitted
   output and selected protected evidence commit. Persist recovery inputs only
   after they are deliberately admitted memory, never a raw source retry body.
   Reject background ingest, fast deferred ingestion and store(extract=True)
   under this mode before side effects.
   For allowed asynchronous processing, persist only the separate protected
   processing payload and event envelope; workers load it under their claimed
   item and saved policy. Completion removes that processing payload while
   no-evidence still has no completed evidence. Do not use legacy extraction
   journals as a covert source archive. New privacy-path plans retain admitted
   output only; rewriting all historical journal classes stays with #324.

6. **Fences, retry identity and publication.**
   The authority interface supplies a scoped guard/revalidation boundary, not a
   one-time Boolean. Check before every protected admission/provider send,
   index/publication and disclosure, and again after awaited preparation before
   publication. Rehydrate the original saved snapshot on retry/restart and test
   it against current authority; never recompute old accepted policy from a
   weaker current default. Preserve native generation/transaction fences.
   Bind fast retry identity to requested content/policy identity, excluding newly
   sampled clocks; same request must return its original IDs/deadlines, while
   changed or stricter current authority may deny old work.
   Optional provider paths not yet capable of carrying the contract must reject
   explicitly when selected, before network use; do not silently skip a fence.

7. **All public adapters and compatibility.**
   Forward typed policy/capabilities and inspection through async/sync Python,
   HTTP and MCP. Preflight entire protected batches. Guard both visual entry
   points and raw serialized framework attachment bypasses. Preserve default
   signatures/return semantics and legacy snapshots when privacy is unset.
   Current low-level storage remains trusted operator access as RFC-0019 states.
   Workspace-bound capability context uses actual namespace IDs without hosted
   accounts, hosted schema or process-global mutation.

8. **Protected grounding diagnostics and provider capability boundary.**
   Root approved one additional file before implementation: grounding.py has
   content-bearing rejected entity/subject/object warning fields at lines
   509–606, a concrete AC3 leak. Add a protected fixed-code/no-content logging
   mode and a captured-log regression assertion, preserving legacy default
   grounding behavior and historical bytes. Built-in providers whose internal
   sends, retries or pre-return logging cannot carry the privacy guard remain
   explicitly unavailable to protected operation. The actual provider/sanitizer
   integration belongs to #322; fake privacy-aware provider validation is
   component evidence only. This revision freezes 42 planned files, with no
   additional acceptance criteria.

## Exact planned file scope

The reingestion integration adds only `src/prme/ingestion/reingest.py` and
`src/prme/storage/reingest.py` to the original 42 paths, for a maximum of 44.
This follows original AC3's shared side-effect boundary and AC6's inspection
requirements. Protected reingestion is explicitly unsupported, including dry
runs, zero limits, cursors, reopened stores and mixed owner/scope ranges. An
authoritative metadata check precedes filtering, source reads and providers;
participating sources/context are checked again after awaits and at native
transaction boundaries. Preserve the shared batch/custom-save/planning fences,
normal commit processing-slot consumption, historical bytes and legacy controls.
This does not create a cross-process legacy-enrollment lease or a protected
reingestion feature. No durable fields or frozen AP cases are added.

New runtime files:
- src/prme/privacy/__init__.py
- src/prme/privacy/policy.py
- src/prme/privacy/admission.py
- src/prme/privacy/capabilities.py
- src/prme/privacy/records.py
- src/prme/storage/privacy.py

Existing runtime files:
- src/prme/__init__.py
- src/prme/config.py
- src/prme/client.py
- src/prme/workspace.py
- src/prme/storage/engine.py
- src/prme/storage/event_store.py
- src/prme/storage/pg/event_store.py
- src/prme/storage/schema.py
- src/prme/storage/pg/schema.py
- src/prme/storage/fast_ingest.py
- src/prme/storage/derivation.py
- src/prme/storage/durable_queue.py
- src/prme/ingestion/pipeline.py
- src/prme/ingestion/planning.py
- src/prme/ingestion/grounding.py
- src/prme/ingestion/reingest.py
- src/prme/storage/reingest.py
- src/prme/retrieval/pipeline.py
- src/prme/api/models.py
- src/prme/api/routes.py
- src/prme/api/app.py
- src/prme/mcp/server.py
- src/prme/vision/observe.py
- src/prme/integrations/_chat_history.py
- src/prme/integrations/langchain.py
- src/prme/integrations/llamaindex.py

Tests:
- tests/privacy_fakes.py (new, fake clock/providers/capabilities only)
- tests/test_privacy_policy.py (new, real policy/composition/selection)
- tests/test_privacy_admission.py (new, real DuckDB directory/single-file/PostgreSQL)
- tests/test_privacy_interfaces.py (new, public adapters)
- tests/test_privacy_fences.py (new, real authority transitions + backend effects)
- tests/test_privacy_ephemeral.py (new, no durable input/failure)
- tests/typing/public_api.py (public typed calls)

Documentation/evidence:
- docs/PRIVACY-POLICIES.md (new)
- docs/plans/2026-10-evidence-privacy/PV-02-plan.md (new)
- docs/plans/2026-10-evidence-privacy/content-copy-inventory.json (new copies/refs only)
- docs/reviews/issue-320-privacy-policy-review.md (review evidence summary)
- documentation/configuration.md

This is a finite planned scope, not permission to broaden into all inventory
implementations. If a required new file or substantive mechanism is discovered,
report the reason and AC linkage before expanding the plan.

## Finite validation plan

**Component evidence (real policy, fake external capabilities).**
- Frozen AP01-AP08; fixtures retain original IDs/expected outcomes below.
- Exact deadline edges use a supplied fake UTC clock, no sleeps.
- All valid profiles succeed through real public admission and actual backend
  persistence with explicit fake sanitizer/protector/extraction/embedding.
- Denial spies observe zero content writes/queue/provider/log effects.
- Strict composition, deep immutability and two interleaved projects/owners.
- Minimal source contains conditional negated preference, named attribution and
  effective time. Reject undersized/ambiguous selections without truncation.
- Pause before transmission/publication/disclosure; activate stricter authority;
  release work and assert no side effect/disclosure. Repeat after restart.
- Verify legacy metadata normalization, exact stored checksums and unchanged
  old-policy replay. Never mutate old payloads to make compatibility pass.
- No-durable sync failure checks event/work/evidence/operations tables while
  preparation is paused and after failure. Verify explicit resubmission result.
- No-evidence async success checks processing payload removal and absence of
  completed evidence while admitted memory remains.

**Backend/public integration evidence.**
- Reingestion delta: real directory, pack and PostgreSQL refusal for configured
  and persisted protection before filters or source loads; exact other-owner
  and other-scope legacy controls. Barrier tests cover correction embedding,
  batch send/custom save, plan preparation, index stage and native commit,
  including an existing-marker return. Sync Python, HTTP, MCP and full CLI
  open/refuse/close must return fixed errors without protected content effects.
  Reuse `test_reingest.py`, `test_batched_extraction.py` and
  `test_derivation_commits.py` plus the existing privacy tests. Keep prior
  S2/object-enrollment repair counts; this integration does not reset them.
- Run new admission/fence/ephemeral tests on DuckDB directory, DuckDB single-file
  and the assigned PostgreSQL database, with every applicable layout recorded.
- Exercise async engine, sync MemoryClient, HTTP and MCP for AP01/AP02/AP03/AP06.
  Fast batches use real all-or-none persistence and old-request replay.
  Framework checks use their real supported text write adapter; unsupported
  raw-history/binary shapes must refuse rather than manufacture success.
- Reuse existing durable-ingestion, direct-store recovery, derivation, extraction
  work, batch, metadata, client, API/MCP, namespace and framework tests. Do not
  write parallel tests that only restate implementation.

**Final required checks, once on the final revision.**
- Worktree-specific venv with locked dev, postgres, api, mcp and framework extras.
  No shared editable environment. Use frozen dependency resolution.
- Targeted new tests plus affected legacy modules first.
- Full CI-equivalent tests: python -m pytest tests/ -q with
  PRME_TEST_DATABASE_URL set only to the task-owned privacy_engine_320 database
  at 127.0.0.1:55432; no user 5433 container and no other worker database.
- ruff check src/ tests/.
- Strict public client mypy command used by CI.
- uv build plus installed-wheel import/version/py.typed smoke in an isolated
  temporary environment (no project editable-install replacement).
- Existing simulation commands (current and previous retrieval defaults) and
  framework integration selection, unless the same final full run already
  executes the required checks. No long/paid benchmark gate or default flip.
- Frozen contract structural validator after inventory additions.
- Preserve command, exit status and log path. Root schedules required independent
  reviews; no reviewer duplicates passing checks without a concrete concern.

**Claims deliberately not made by this ticket.**
Detector correctness, actual cryptographic isolation/key custody, every sealed
legacy read/copy, live erasure/backup disposal and the 104-case #327 product gate
remain separate. Fake-capability component passes are labeled as such. The plan
does not convert them into end-to-end protected-product success.

## Remaining decisions / risks

- No unresolved user decision after root's scope ruling. Root's merge/release is
  an internal dependency wait, not a human blocker.
- The precise versioned new source-envelope representation must remain compatible
  with existing immutable event/plan identity. Keep original schemas untouched
  and test raw old bytes. If compatibility requires rewriting a historical
  checksum or wider journal behavior, stop and report that AC conflict.
- Actual minimal evidence selection remains conservative. If complete-message
  retention exceeds the configured bound, rejection is preferable to a guessed
  excerpt; this is not a claim of optimum data minimization.
- Protocol fences guarantee the checked engine effect boundary; already returned
  data and caller/provider copies remain outside revocation.
- Planned files are broad because existing sibling entry points are independent;
  implementation remains one owner and one finite AP cohort, not multiple
  speculative architecture/review rounds.

## Frozen AP01-AP08 definitions


```json
[
  {
    "id": "AP01",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "Profile and explicit lifetime inspection",
    "requirements": [
      "E1",
      "E4",
      "E7",
      "E9"
    ],
    "implementation_owners": [
      "PV-02"
    ],
    "copy_ids": [
      "C01",
      "C02",
      "C10",
      "C41"
    ],
    "setup": "Admit a fixed fact under each valid standard, sealed and no-evidence policy; inspect effective snapshot and explicit unlimited option.",
    "expected": "Exactly the selected capture/use and independent evidence/memory/work deadlines are reported, including provider boundary. No-evidence has no completed evidence payload.",
    "adapter_subchecks": [
      "sync_python",
      "async_python",
      "http",
      "mcp",
      "fast_batch",
      "langchain",
      "llamaindex",
      "supported_import"
    ],
    "status": "not_run"
  },
  {
    "id": "AP02",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "Invalid policy before side effects",
    "requirements": [
      "E1",
      "E4",
      "E8",
      "E9"
    ],
    "implementation_owners": [
      "PV-02"
    ],
    "copy_ids": [
      "C01",
      "C10",
      "C24",
      "C31",
      "C32"
    ],
    "setup": "Try capture/use conflicts, malformed deadline, unsupported registry and a fake admission hook that denies.",
    "expected": "Admission rejects before event, graph, queue, embedding, external provider or content log write; no partial batch acceptance.",
    "adapter_subchecks": [
      "sync_python",
      "async_python",
      "http",
      "mcp",
      "fast_batch",
      "langchain",
      "llamaindex",
      "supported_import"
    ],
    "status": "not_run"
  },
  {
    "id": "AP03",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "Restrictive composition and concurrent projects",
    "requirements": [
      "E4",
      "E8",
      "E9"
    ],
    "implementation_owners": [
      "PV-02"
    ],
    "copy_ids": [
      "C01",
      "C02",
      "C39",
      "C41"
    ],
    "setup": "Interleave two projects with different immutable policies; caller narrows capture/deadline/category permission.",
    "expected": "No global policy mutation or cross-project bleed. Caller restrictions remain; incompatible transformation/schema composition rejects, mandatory block cannot relax.",
    "adapter_subchecks": [
      "sync_python",
      "async_python",
      "http",
      "mcp",
      "fast_batch",
      "langchain",
      "llamaindex",
      "supported_import"
    ],
    "status": "not_run"
  },
  {
    "id": "AP04",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "Minimal evidence preserves qualifiers",
    "requirements": [
      "E2",
      "E4"
    ],
    "implementation_owners": [
      "PV-02",
      "PV-04"
    ],
    "copy_ids": [
      "C01",
      "C04",
      "C05"
    ],
    "setup": "Use a source containing a conditional negated preference, named speaker and effective time with enough excerpt space.",
    "expected": "Minimal sanitized evidence and admitted statement preserve condition, negation, attribution and time; the quote is labeled sanitized and linked to its own source ID.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "AP05",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "Unsafe minimal excerpt refuses truncation",
    "requirements": [
      "E2",
      "E4"
    ],
    "implementation_owners": [
      "PV-02"
    ],
    "copy_ids": [
      "C01",
      "C05",
      "C09"
    ],
    "setup": "Set excerpt limit below the complete qualifier-bearing span; include two ambiguous speakers.",
    "expected": "No truncated or guessed assertion is admitted; explicit rejection or caller-selected full policy is required, never an automatic capture upgrade.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "AP06",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "No evidence differs from no durable processing",
    "requirements": [
      "E1",
      "E4",
      "E7"
    ],
    "implementation_owners": [
      "PV-02",
      "PV-05"
    ],
    "copy_ids": [
      "C01",
      "C08",
      "C10",
      "C41"
    ],
    "setup": "Run no-evidence sync and async with fake failure; separately request no durable processing input.",
    "expected": "No-evidence may use encrypted finite pending work, removed on completion; strict no-durable input rejects async before enqueue and explains resubmission after sync failure.",
    "adapter_subchecks": [
      "sync_python",
      "async_python",
      "http",
      "mcp",
      "fast_batch",
      "langchain",
      "llamaindex",
      "supported_import"
    ],
    "status": "not_run"
  },
  {
    "id": "AP07",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "Protected visual admission is unsupported",
    "requirements": [
      "E1",
      "E7",
      "E8"
    ],
    "implementation_owners": [
      "PV-02",
      "PV-13"
    ],
    "copy_ids": [
      "C02",
      "C24",
      "C38"
    ],
    "setup": "Try image/video body and binary message attachment under each protected profile.",
    "expected": "Reject before blob, thumbnail, frame hash, provider or content storage; unsupported input is not silently downgraded to standard.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  },
  {
    "id": "AP08",
    "cohort": "original_40",
    "group": "admission_policy",
    "title": "Old policy retry and activation fence",
    "requirements": [
      "E4",
      "E5",
      "E9"
    ],
    "implementation_owners": [
      "PV-02",
      "PV-06"
    ],
    "copy_ids": [
      "C09",
      "C10",
      "C37",
      "C42"
    ],
    "setup": "Pause work before publication/transmission, activate a stricter revision, then retry old work concurrently and after restart.",
    "expected": "Authoritative fence denies stale publication, external transmission and disclosure; current revision applies without reinterpreting legacy identity.",
    "adapter_subchecks": [
      "async_python"
    ],
    "status": "not_run"
  }
]
```

## Final interface preparation

The capability boundary carries every named content representation, metadata keys/values, trusted issued tokens and a shared private operation budget. Stable protector context binds actual namespace, owner/scope, source/representation, purpose, generation, schema and policy identity. Separate processing claims bind work ID/generation/deadline. A distinct request-scoped retry commitment capability preserves equality without public raw hashes; unavailable or retired capability denies retry. Real sanitizer, crypto custody, broad sealed projections and historical journal rewrites remain separately owned.

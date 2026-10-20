# PV-03 / #321 implementation plan

Status: implementation in progress; component evidence is recorded separately from pending shared release gates.
Issue: https://github.com/dwamianm/prism/issues/321
Provisional prerequisite/base: b84a866cd3b46db8c018ca608bb5079e96f90fd9 (#320 PR342).
Final target: main. Root verified #320 delivery and authorized reconciliation onto 50d22d4e; the implementation remains unpublished pending review/delivery.

This records the previously accepted preparation and reconciliations before coding.
Only #321's assigned worktree/environment and isolated privacy_engine_321 database
may be used. No live model calls, cloud keys, deployment, release, default flip,
subagents or independent reviewers in this implementation phase. The parent
schedules review; final full required checks run once on the reconciled revision.

The original six ACs and EM01–EM08 definitions below are fixed. EM07/08 and later
source-read/work/erasure/restore integration remain separately pending. Component
success never changes a whole shared case to passed. After two unsuccessful
fix-and-validation cycles for one behavior, preserve evidence and stop that
strategy. No blanket refusal or reversible fake cipher establishes crypto success.

## Original acceptance criteria, unchanged

- [ ] Each evidence object uses authenticated encryption and a randomly generated
      data key. Record identity, owner/scope, schema and purpose are authenticated.
      Wrong key/context, swapped ciphertext or corruption fails closed.
- [ ] Only ciphertext and wrapped keys reach evidence storage. No plaintext/key
      appears in manifests, logs, errors, temp files or ordinary export paths.
- [ ] New protected stores require key provisioning; unavailable keys never cause
      plaintext fallback. Recovery copies are separate, deliberate and documented.
- [ ] Local sealed operation separates write/public capability from diagnostic
      private capability using the selected maintained library/provider. The
      normal retrieval process cannot obtain the diagnostic key automatically.
- [ ] Standard and sealed access modes use explicit capabilities. A configuration
      with a permanently loaded decrypting key cannot claim process isolation.
- [ ] Rotation/re-wrapping is resumable; reads identify the exact key version.
      Document that rotation and deletion of a live wrapped key are not proof of
      erasing older backups.

Original validation: fixed library-compatible vectors and eight encryption/migration scenario slots shared with #326 on both backends, including interruption and key loss; synthetic marker scans after normal and failed writes. No custom crypto or cloud key creation.

Done for #321 means those six criteria and the applicable EM01-EM06 implementations pass with real crypto and real ciphertext storage, the finite regression/required CI checks below pass, required review findings are resolved, and the authorized branch is committed/pushed with one PR to main. EM07/EM08 are #326-owned integration cases and are not relabeled as #321 passes. Shared EM04/EM05 require a precise handoff to #326. Broad migration, historical-copy erasure and the complete #327 product gate remain outside this issue. No default behavior or protected-product claim is promoted here.

Two unsuccessful repair-and-validation cycles for the same behavior stop that strategy; preserve both failures and report a bounded alternative to root. Passing checks repeat only after a relevant change, failure or named concern. No paid/live model calls or long benchmark runs. No overall user spending cap was supplied, so report measured commands/time/cases rather than invented cost or account quota.

## Root decisions resolved during preparation

### Immutable payload context and versioned wrapping context

Root explicitly selected the split below and authorized an RFC clarification in #321's implementation/review. The frozen #319 branch must not be edited now.

`payload_context` is canonical UTF-8 JSON with sorted ASCII keys, no whitespace, no duplicate or unknown fields, bounded ASCII identifiers/fixed enums and bounded integers only. It binds the algorithm/schema, payload kind, opaque object/event/source/representation IDs, actual project/namespace ID, owner, scope, purpose (`evidence` or `processing`), immutable payload generation and policy ID/revision. Any separately stored lifetime used for authorization must remain bound to that immutable policy snapshot, never taken from an unauthenticated override. These exact bytes are AES-GCM AAD.

`wrapping_context` has a versioned domain, the exact structured `payload_context`, wrapping algorithm, opaque provider/key/version IDs and wrapper revision. Its canonical bytes are the exact RSA-OAEP label. The provider may only resolve that exact version, never try every key or silently use a current default. Key descriptors independently bind purpose and authorized boundary. Altering any wrapper identity makes unwrap fail.

This clarification replaces the ambiguous single-context phrase with explicit AES AAD versus OAEP-label domains. Rewrap unwraps the existing 32-byte data key under the old exact wrapping context and wraps it under the new one. Payload context, generation, data key, nonce and ciphertext/tag remain byte-identical; only wrapper fields and storage revision advance. A changed payload or policy rebind instead creates a fresh object generation, data key and nonce. No custom cipher, retagging shortcut, nonce reuse for re-encryption, or in-place rewriting of old canonical record bytes.

### Native keyring multipart storage

Root approved bounded multipart storage entirely inside the allowlisted native keyring to accommodate Windows' credential size limit. Use unique names for each key version, bounded ASCII parts (at most 1,000 characters each), a bounded part count, a final completeness record, and verified reconstruction/public-key match before activating the public descriptor. Interruption never activates a partial version. Resume/cleanup addresses only the exact newly created version; it never deletes another version or user entry. Use local-machine persistence explicitly on Windows rather than inheriting keyring's roaming/enterprise default. No disk fallback or plaintext error payload.

## Concrete implementation plan

1. **Versioned codec with real maintained crypto.** Add the closed envelope codec and shared canonical-context helpers on #320's types. Pin tested `cryptography==50.0.2` and a local-keyring extra with `keyring==25.7.0`, with locked dependency/artifact hashes. Use `AESGCM.generate_key(bit_length=256)`, a fresh 12-byte secure nonce, and full 16-byte tag, exactly once per object generation. Use RSA-4096/exponent 65537 and OAEP SHA-256/MGF1-SHA-256 to wrap only a 32-byte data key. Reject unknown algorithms/schema, noncanonical encoding, wrong lengths, oversized fields, wrong contexts and authentication failure with fixed errors, and never yield partial plaintext. No compression or plaintext spill files.
2. **Separate provider and capability objects.** Implement explicit public wrapping and private unwrapping interfaces using exact key references. Local ordinary sealed construction accepts only the provisioned public descriptor and never imports/opens native keyring for evidence decryption. Runtime evidence, diagnostics and currently claimed processing each have distinct grants. Enforce owner/scope/project/purpose/selected IDs/deadline/current fence before unwrap, content-free required audit authorization before disclosure, and the current fence again immediately before returning plaintext. Failed audit denies disclosure. Capabilities/private material are excluded from model dumps/repr/config serialization. An explicit in-memory private key is labeled `same_process`; service isolation requires an external deployment-enforced identity boundary and cannot be inferred from a separate Python object or same-user process.
3. **Local custody and provisioning.** Instantiate only the exact platform native classes: `keyring.backends.macOS.Keyring`, `keyring.backends.Windows.WinVaultKeyring`, or `keyring.backends.SecretService.Keyring`; refuse null, plaintext, file, chainer, unsupported or automatic fallback choices. Check native availability before generation/public activation. Generate private key material only in process, serialize it directly into native-store entries, verify read-back, then atomically publish only the public descriptor outside the pack. Purpose and boundary are in the key entry identity and verified descriptor. No private key paths/values in manifest, ordinary config, logs or errors. Document macOS's same-executable access and ignored custom-keychain path; explicit application unlock is not a promise of an OS password prompt on each use.
4. **Purpose-separated pseudonym custody.** Root assigned #321 key custody and #322 HMAC/token semantics. Provide a separate `pseudonym` capability bound to opaque project/namespace, owner, scope and epoch, with one random 32-byte key per boundary/epoch. No reuse of evidence, processing or ordinary store keys; no reverse-value map. Exact lost/foreign epoch refuses; never auto-generate a replacement under an existing epoch. Content-free provider inventory exposes epoch/key-version references for #325 erasure. #322 receives a non-serializable secret handle/value only through this narrow capability and owns domain-separated length-prefix HMAC and source-issued-token handling.
5. **Integrate real crypto with existing privacy persistence.** Inject the concrete protector into #320 admission and its creation-readiness seam. Verify all required wrapping capabilities before protected creation or content side effects. Evidence and work carry purpose-distinct keys and grants. Both backends store encoded ciphertext plus wrapped keys and non-content context only. Normal standard use needs an explicitly supplied runtime grant; sealed ordinary reads do not unlock the native keyring. Missing/unusable keys produce the contract's availability result with fixed guidance, never plaintext fallback. Leave sanitizer/detector work, full source-reader classification and whole historical-copy rewriting with their owners.
6. **Bounded local CLI.** Register a dedicated evidence-key command family in `privacy/cli.py` through the existing argparse CLI: provision/status, explicit finite unlock tied to selected IDs/action, rotate/resume/status, and deliberate encrypted recovery export/import. Private unlock exists only for the lifetime of that diagnostic action/context; it does not create a bearer file for normal retrieval or mutate process-global settings. Do not accept private key/password bytes on argv or print them. Use a hidden prompt or explicit inherited input descriptor for recovery passphrases. Recovery uses PyCA's encrypted PKCS#8 serialization, explicit destination outside the pack, exclusive restrictive file creation, and no overwrite/symlink fallback. Ordinary pack/export never includes recovery secrets. CLI target resolution follows existing explicit-local-path rules; PostgreSQL provider references use explicit configured database selection without leaking URLs.
7. **Resumable rewrap.** Add content-free rotation-job state to privacy storage: opaque job/boundary/purpose IDs, source/target exact wrapping references, bounded cursor/counts/state and fixed errors. Select bounded batches, revalidate authority, unwrap/wrap in memory outside backend transactions, then conditionally replace the exact wrapper revision under the fence. Resume identifies saved versions and skips already transitioned envelopes; concurrent new writes use a declared active version; concurrent update/erasure never restores a missing or denied object. Audit/progress writes contain no plaintext or raw key. Keep old keys until the operator's explicit later retention decision; document that rewrap/live wrapped-key removal do not erase backups.
8. **Documentation and inventory.** Record crypto fields, provider custody/parts, public descriptors, recovery artifacts, rotation metadata and audit records in the copy inventory with purpose/retention/erasure owners. Clarify RFC context domains without changing original ACs or legacy bytes. Document permanent evidence-key loss, independent admitted-memory protection, no heap-zeroization guarantee, same-user limits, native-provider prerequisites, backup responsibility and exact untested platforms. End-to-end protected status remains gated by dependent tickets/#327.

## Exact planned file scope

New runtime files:
- `src/prme/privacy/crypto.py` (closed envelopes, canonical contexts and PyCA AEAD/OAEP operations)
- `src/prme/privacy/keys.py` (purpose-bound provider references, grants and pseudonym custody contract)
- `src/prme/privacy/local_keys.py` (explicit native adapters, provisioning/unlock and multipart custody)
- `src/prme/privacy/rotation.py` (bounded resumable rewrap coordinator)
- `src/prme/privacy/cli.py` (dedicated evidence-key CLI handlers/registration)

Existing or #320-owned files to extend after its merge:
- `src/prme/privacy/__init__.py`
- `src/prme/privacy/capabilities.py`
- `src/prme/privacy/records.py`
- `src/prme/storage/privacy.py`
- `src/prme/storage/schema.py`
- `src/prme/storage/pg/schema.py`
- `src/prme/storage/engine.py`
- `src/prme/client.py` (explicit construction/context forwarding only if #320 requires the concrete type here)
- `src/prme/cli.py`
- `src/prme/__init__.py`
- `pyproject.toml`
- `uv.lock`

New tests/fixtures:
- `tests/test_privacy_crypto.py`
- `tests/test_privacy_keys.py`
- `tests/test_privacy_ciphertext_storage.py`
- `tests/test_privacy_rotation.py`
- `tests/test_privacy_key_cli.py`
- `tests/test_privacy_native_keyring.py` (explicit opt-in native conformance, separate from mocked backend checks)
- `tests/fixtures/privacy/crypto-vectors-v1.json`
- `tests/fixtures/privacy/rsa-4096-test-key.pem` (clearly labeled synthetic fixture, never a production key)
- `tests/typing/public_api.py` (public explicit capability calls)

Documentation/evidence:
- `docs/PRIVACY-KEYS.md` (new)
- `docs/plans/2026-10-evidence-privacy/PV-03-plan.md` (new)
- `docs/plans/2026-10-evidence-privacy/content-copy-inventory.json`
- `docs/RFC-0019-Evidence-Privacy.md` (agreed context clarification)
- `documentation/configuration.md`
- `memory_bank/reviews/issue-321-evidence-encryption-review.md` (new review/evidence summary)

No intended change to ordinary `storage/encryption.py`, `pack_manifest.py`, old checksum codecs, historical events/operations, default retrieval, production data, deployment configuration or provider cloud resources. #320 owns event-store transaction admission; any unexpected need to alter those backends must be identified as a specific AC-linked integration gap before expanding this file scope.

## Finite validation plan

At the original plan freeze runtime evidence was **not run**. These frozen checks are not themselves assertions of success; the implementation evidence report records actual results. Root allocated the dedicated `privacy_engine_321` PostgreSQL test database. Never use a shared/user database or another worker's database. Use a worktree-specific environment and frozen lock; no shared editable environment.

### Frozen cohort and direct cryptographic checks

Use original EM01-EM06 as the owned finite cohort on DuckDB and PostgreSQL. DuckDB directory and single-file are layout subchecks for each applicable case, not extra counted successes. Preserve original EM07/EM08 definitions below for #326 without claiming their migration/export validation here.

- EM01: vendor a small fixed subset of PyCA's NIST/CAVP AES-256-GCM vectors, selecting 96-bit IV/full-128-bit tag records with empty, nonempty and partial-block payload/AAD. Include one fixed RSA-4096 test private key, canonical payload/wrapping context goldens and fixed PyCA/OpenSSL-compatible wrapped-key/decrypt fixture. Production encryption uses actual randomness. Cross-check direct PyCA decrypt independently from the PRME codec. Tamper ciphertext, tag, nonce and AAD individually; no plaintext result on failure. Two generated objects and changed-content generations have distinct keys/nonces. Persisted exact retries keep byte-identical envelopes.
- EM02: substitute object/event/representation, project, owner, scope, policy revision, generation, payload kind, processing/evidence purpose, wrapper provider/key/version/revision, wrong private key and OAEP label. Assert exact-version resolution (never fallback), fixed errors, zero plaintext disclosure, and no names/predictable source hashes in contexts. Independently validate metadata selectors against encoded envelope.
- EM03: real cipher/provider path with synthetic input; inject failure before AES/wrap, after wrap before persistence, inside atomic event/evidence/work write, and immediately after commit before caller acknowledgment. Missing/malformed public capability rejects before protected creation/provider effects. After success and each failure/retry, inspect actual tables and scan controlled database/WAL/temp/log/manifest/export artifacts for source-only and secret-key markers. A failed acknowledgment may have committed ciphertext and must replay it, not fabricate rollback. No protected status on incomplete stores.
- EM04: exactly three synthetic evidence objects; interrupt rewrap after the first conditional commit; concurrently fence a second object; resume/restart. Verify exact saved versions, successful remaining transition once, denied fenced object and unchanged payload context/key/nonce/ciphertext for successful rewrap. Interrupt before the conditional commit separately and retry. Test a stale wrapper CAS conflict without reintroducing an old wrapper. A copy retained before rotation still opens with its retained old test key, proving the documented backup limit.
- EM05: remove only task-owned synthetic evidence-key version; evidence becomes key-unavailable with permanent-loss guidance while admitted memory still works through its separate store key. Test explicit encrypted recovery export/import into a separate task-owned path and wrong recovery passphrase. Default manifest/config/pack/export have neither private key nor recovery password. Key-loss/recovery operates through the actual provider interface, not fake decryption.
- EM06: construct public-only sealed writer, processing worker and explicit diagnostic/runtime readers as separate objects and subprocesses. Ordinary retrieval must not call native private-key lookup; monkeypatching the lookup to fail plus subprocess behavior checks absence of an accidental loader, not isolation from hostile Python. Unselected IDs, wrong owner/scope/purpose, expired grant and failed audit deny. Revoke/fence while an audited read is paused and verify the final disclosure fence. Permanently loaded private authority reports `same_process` and rejects a service-isolation claim. Null/plaintext/file/chainer backends reject.

Supplementary AC-linked key-custody subchecks (not new scenario slots):
- Fake native adapters test deterministic backend selection, fixed errors, Windows UTF-16 entry length, bounded multipart count, missing/swapped/corrupted parts, failure before completeness activation, interruption resume and exact-version cleanup. These tests do not prove OS custody.
- One explicit opt-in real native conformance run on the available macOS backend: unique synthetic entries, provision/read-back, separate-process public-write/private-unlock, key loss/recovery, and removal of only task-owned entries. Record actual keyring/OS/provider versions and whether the OS prompts. Do not claim Windows or Linux native execution from macOS/mocks; their provider state remains unverified until a native runner is available. If native access is unavailable or interactive authorization cannot be completed, report that exact platform gap rather than claiming the mocked check is equivalent.
- Pseudonym custody: random 32-byte key stable only inside exact boundary/epoch; evidence/processing grants cannot obtain it; missing/foreign epochs deny; explicit new epoch creates unrelated material; inventory/revocation exposes only opaque references. HMAC/token correctness remains #322's tests.

### Marker-scan boundaries

Hold sanitized input and policy fixed; fake only extraction/embedding as existing fixture patterns do. Use a distinct source-only marker that fake extraction never admits and a separate admitted-memory marker. The admitted marker is allowed in the memory plane and must not be misreported as an evidence-encryption failure. Test data keys/private keys are captured only inside test memory and never printed into reports.

For DuckDB inspect real rows plus controlled directory/single-file artifacts while open, after close, and after failure/restart, including `.wal`, `.tmp`, lexical/vector files, manifests and ordinary CLI export. For PostgreSQL inspect real rows and a task-owned logical dump; with root's dedicated test-server ownership, scan only that disposable server's relevant base/WAL/temp artifacts/logs for the synthetic source-only/key markers. Do not scan unrelated user storage or assume a logical row dump proves WAL safety. Report unavailable physical visibility as a validation gap. Ordinary whole-store encryption may be on/off in bounded fixtures to show evidence protection is independent; no claim that admitted memory is anonymous or protected by evidence keys.

### Targeted development checks

Run the six new test modules above, then the affected existing tests: `tests/test_encryption.py`, `tests/test_single_file_pack_encryption.py`, `tests/test_single_file_pack.py`, `tests/test_cli.py`, `tests/test_cli_single_file.py`, `tests/test_engine_startup_recovery.py`, `tests/test_direct_store_recovery.py`, `tests/test_metadata_admission.py`, `tests/test_metadata_keys.py`, `tests/test_pg_event_store.py`, `tests/test_workspace.py`, `tests/test_workspace_single_file.py`, `tests/test_pg_workspace.py`, and the #320 privacy admission/ephemeral/fence tests. Reuse current exact-checksum assertions and frozen legacy fixture bytes; do not regenerate them merely because encryption tests fail.

### Required final checks, once on final revision

- With the root-assigned `PRME_TEST_DATABASE_URL`, run `python -m pytest tests/ -q` in the isolated locked environment with dev/postgres/api/mcp/langchain/llamaindex/local-privacy extras. Record DuckDB and real PostgreSQL execution and skips honestly; missing PostgreSQL cannot be counted as parity.
- `ruff check src/ tests/`.
- `mypy --follow-imports=silent --ignore-missing-imports --strict tests/typing/public_api.py`.
- `uv build`, then installed-wheel import/version/`py.typed` smoke in a separate temporary environment; do not replace another worktree's editable install.
- `python -m scripts.run_simulations` with current defaults, then with `PRME_SCORING__FUSION=weighted`, `PRME_PACKING__CONTEXT_FORMAT=auditable`, `PRME_PACKING__MULTIPATH_ORDERING=balanced`, as CI requires. These are existing bounded simulations, not an optional benchmark/default gate.
- Framework integration selection if the final full suite did not execute it with the required extras: `tests/test_integration_chat_history.py`, `tests/test_integrations_langchain.py`, `tests/test_integrations_llamaindex.py`.
- `python docs/plans/2026-10-evidence-privacy/validate-contract.py` for inventory/RFC references; structural pass is not runtime crypto evidence.
- Root schedules required independent security/correctness review of the exact final snapshot. Share verified commands, exit statuses and logs; no duplicate broad runs absent a concrete concern.

Record command, actual exit status, elapsed time, case/layout/provider versions and log path. Preserve failed evidence. No tests or reviews ran in this preparation turn.


## Accepted interface details

### Exact original-policy binding and small call-site delta

`policy.compose` can retain the default policy's primary ID/revision while adding caller references and stricter effective rules. `PayloadContext` alone therefore cannot authenticate the complete original acceptance policy or independent saved lifetimes. Root explicitly approved passing the immutable original `PolicySnapshot`, rather than caching it in `ready()`.

Proposed protocol changes, with unchanged positional arguments:

```python
async def seal(plaintext: bytes, context: PayloadContext, *,
               original_snapshot: PolicySnapshot) -> ProtectedPayload: ...
async def open(payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess, *,
               original_snapshot: PolicySnapshot, guard: Guard) -> bytes: ...
def open_held(payload: ProtectedPayload, access: ProcessingAccess | EvidenceAccess, *,
              original_snapshot: PolicySnapshot,
              guard: Guard) -> AsyncContextManager[bytes]: ...
```

Keep `PayloadContext.schema_version=1` and `PrivacyRecord` unchanged. The first closed crypto-envelope/AAD schema includes the exact existing context plus `policy_binding = {domain: "prme-policy-snapshot-v1", sha256: ...}`. That digest covers canonical **policy metadata only**: the complete original snapshot, composed references/effective rules, admission time and all independent saved deadlines, including explicit nulls. Canonicalization uses closed fields, UUID/enum strings, bounded integers, arrays in their saved order and aware clocks normalized to UTC with fixed microsecond precision. Unknown schemas/fields and an absent binding fail closed; there is no optional unbound v1 variant. The separate crypto schema is new, so changing the existing admission context to v2 is unnecessary.

Both seal and open compare boundary/primary identifiers against the supplied original snapshot and verify its binding. Current policy/erasure authority remains a separate guard; it never rewrites the original authenticated acceptance policy. Wrapper-only rewrap preserves these exact payload AAD bytes.

Smallest additional file scope: `src/prme/privacy/admission.py` passes `record.snapshot` at its two seal calls (currently lines 268–269) and processing open (currently 509); `tests/privacy_fakes.py` implements the matching required keywords and held-open fake interface. `capabilities.py`, real implementations and public typing/examples are already in the accepted scope. At implementation, update every concrete/public adapter call site together; no permissive default snapshot or old-signature fallback. No change to policy composition or a second authority mechanism is proposed.

### Closed wrapper dispatch and purpose custody

The codec accepts exactly `local-rsa-oaep-sha256-v1` and `aws-kms-symmetric-v1`. AES-256-GCM, payload AAD and per-object DEK generation remain shared engine code. An explicitly injected registry resolves the outer UUID `(provider_id, key_id, key_version)` to one immutable purpose/boundary-bound descriptor and physical locator. UUID fields never contain ARNs. Unknown kinds, missing adapters, aliases/current-version fallback and mismatched duplicate selectors deny.

The wrapping context includes its versioned domain, the exact payload AAD structure, wrapper kind, logical reference, wrapper revision and immutable provider locator binding. Local RSA binds the public-key fingerprint and uses the exact context bytes as its OAEP label. Hosted KMS binds the full key ARN and maps those same canonical context bytes into its exact `EncryptionContext`; it uses `SYMMETRIC_DEFAULT`, verifies response ARN/algorithm and requires a 32-byte unwrapped DEK. The low-level provider seam is `wrap_key(dek, ref, wrapping_context) -> wrapped_bytes` / `unwrap_key(wrapped_bytes, ref, wrapping_context, authorized_private_permit) -> dek`; the permit is process-local, non-serializable and issued only inside the real authorization scope.

#281 owns the AWS SDK/IAM/broker adapter and live-cloud evidence. #321 supplies the bounded dispatch/contract and local provider, not a cloud dependency or KMS provisioning. The KMS private adapter runs inside the broker. Its processing-only route returns a transient 32-byte processing DEK to an authorized worker under the held response guard; human/standard-runtime reveal instead uses plaintext `open_held` and never returns evidence DEKs. Logical versions select immutable full-ARN descriptors; automatic material rotation is not a caller-selectable version. AWS documents exact context matching, explicit-key decrypt and returned ARN, and states that KMS chooses the material version itself. Its asymmetric API has no custom-label input, so the symmetric wrapper is the required provider-specific composition. [Encrypt API](https://docs.aws.amazon.com/kms/latest/APIReference/API_Encrypt.html), [Decrypt API](https://docs.aws.amazon.com/kms/latest/APIReference/API_Decrypt.html), [rotation behavior](https://docs.aws.amazon.com/kms/latest/developerguide/rotate-keys.html). Context contains no source text/hash, human names or secret keys; AWS logs encryption context. [Encryption context](https://docs.aws.amazon.com/kms/latest/developerguide/encrypt_context.html).

`pseudonym` and `retry_commitment` are distinct key-provider purposes, not additional `PayloadContext.purpose` values. The #322 `key_for(project, owner, scope, epoch)` contract is unchanged. Retry custody adds `provision_retry_key(RetryContext, retention_ref, expires_at)` and exact `get_retry_key(RetryContext, key_version, retention_ref)` through the same native machinery. Each request/boundary gets a separate random 32-byte key; the descriptor binds the original snapshot, request, version and finite aware expiry supplied by #324's saved retention authority. Lookup never provisions, extends retention or changes a lost/retired version; failure is fixed `privacy_retry_unavailable`. Content-free inventory exposes refs/purpose/epoch or request/retention for #325 deletion, never key bytes or plaintext maps. Existing physical store keys stay separate.

Native limits remain exactly as approved: explicit native-class allowlist, verified bounded multipart Windows entries and final activation, local-machine persistence, no disk/log fallback, and honest macOS same-executable/custom-keychain limits. Readiness/metadata status cannot unlock private entries. Native Windows/Linux behavior remains unverified until executed on those platforms.

### Authorization lifetime, inspection and safe rewrap

`open_held` resolves real current grants/claims, checks purpose/profile/selected identity and generation, aware finite deadlines and original snapshot, then acquires the supplied authoritative scope once and retains it through private unwrap, required audit and the caller's actual disclosure operation. `open` is the local convenience wrapper over that context. This does not provide isolation from a malicious same-process caller.

For a remote broker, #281 performs the actual awaited response-body send **inside** `open_held`; returning a response object and sending later is insufficient. The broker obtains identity from authenticated transport, loads the current record itself and constructs its real guard. Client-supplied access fields or serialized callbacks are not authority. There is no no-op guard or second acquisition of the same non-reentrant lock to simulate an already-held scope. Audit authorization must succeed before plaintext disclosure; cancellation/failure releases the scope and records only fixed content-free outcomes.

#320 currently verifies the processing row before `open`, then passes a policy/deadline guard. #324 still owns atomic current claim/work generation/lease/slot generation and every cohort member before/after unwrap/publication; a batch head alone is insufficient. #325's reserved exact-subject/dependency extension remains on this same guard. Current snapshot-only guards establish policy fencing, not completed targeted erasure. #281/#324 also own enqueue-only hosted durable processing; #320's current inline path is not worker-isolation evidence.

Metadata inspection uses owner/boundary-authorized stored context, original deadlines, processing state and public descriptor availability only. It never unwraps or acquires native private material, and does not infer decryptability from ciphertext presence. Actual source-state/API integration and successful ordinary protected retrieval remain #323 work.

Rotation prepares a wrapper-only candidate under an explicit rewrap authorization, leaving payload context/generation/key/nonce/ciphertext/tag unchanged. It rechecks current authority and holds the real publication guard through `replace_envelope` completion, including DuckDB native completion on cancellation. A CAS miss reloads current state: consumed/removed/fenced slots are never restored from the old body. Advance/checkpoint only after classifying the actual outcome; unresolved conflicts are not counted complete. Add only bounded keyset scanning and content-free rotation job/checkpoint state in the already planned storage files. No payload decrypt is needed for wrapper rotation; rotation is not a payload-integrity scan or backup-erasure proof.

### Correction: processing-only broker key response

Root corrected the earlier provisional statement that the broker never returns a DEK to the worker; the preceding provider paragraph now reflects this accepted correction. The accepted #281 pending-processing step 3 **does return a transient 32-byte processing DEK** to the authorized worker's remote key provider. The worker has no direct KMS Decrypt permission. Human/standard-runtime reveal separately uses plaintext `open_held`; neither route returns evidence DEKs.

The only additional API is a processing-only held unwrap on the same protector/capability surface:

```python
def unwrap_processing_key_held(
    payload: ProtectedPayload, access: ProcessingAccess, *,
    original_snapshot: PolicySnapshot, guard: Guard,
) -> AsyncContextManager[ProcessingKeyLease]: ...
```

`ProcessingKeyLease` is a non-serializable, purpose-bound transient handle with exactly 32 key bytes and the validated slot/context/generation, logical key reference and wrapper revision binding; only the explicit authenticated processing transport extracts its bytes. This operation reuses the same engine envelope parser, original-snapshot binding, exact provider resolution, private-permit authorization, audit and held scope used by `open_held`. It rejects evidence slots and runtime/diagnostic grants. It unwraps the processing key without interpreting payload plaintext; the worker's existing engine codec subsequently authenticates/decrypts the payload. No hosted envelope parser, canonicalizer or unrestricted unwrap entry point is introduced.

#281's broker accepts opaque operation/work/slot references, obtains actual transport identity, loads the exact current slot and snapshot server-side, and uses #324/#325's live claim/cohort/generation/deadline/current-authority checks. It holds that authority and audit scope through the **actual awaited key response**, not merely response construction. Caller-supplied ciphertext, key ARN or claimed context never substitutes for those records. The existing private permit can expose the bound opaque request metadata needed by the worker remote adapter; the permit itself is not a transferable authorization token. The adapter matches the returned binding and key length to its exact pending unwrap.

After receipt, the worker uses the key only for that engine processing operation, caches nothing, and rechecks provider/publication fences. Already returned keys/plaintext cannot be recalled; the design makes no such claim. This delta stays in the already planned `capabilities.py`, `crypto.py`, `keys.py` and their existing fake/typing/test call sites. Existing EM02/EM06 processing-purpose and held-transport subchecks cover the seam; no new cohort, code, tests or provider calls are authorized or performed by this correction.

## Frozen original EM01-EM08 cases

These are copied exactly from the reviewed scenario manifest. Status remains `not_run`.

```json
[
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


## Execution handoff and exact scope amendments

The accepted additional files are src/prme/privacy/admission.py and tests/privacy_fakes.py
for explicit original_snapshot forwarding. Current main's protected reingest refusal
is retained. Existing readiness/persistence/full-body CAS are reused. No old checksum,
record or pack-encryption format is changed. The planned implementation modules,
CLI, custody and rotation are owned here; #322 owns sanitizer/HMAC, #323 source-read
integration, #324 live work and retry commitments, #325 targeted erasure, #326
migration/export/restore and #281 hosted KMS transport.

The native conformance check uses only uniquely named synthetic entries. Windows
and Linux mocks do not establish native platform conformance. Physical PostgreSQL
marker reads are restricted to the assigned test container and ticket-specific
markers, without disrupting another ticket. Keep logs under the ignored ticket
run directory; never include key bytes, source plaintext or provider exception
text in reports. No review or validation result is implied by this plan.

## Implementation reconciliation and local rotation quiescence

The prerequisite merged as `50d22d4e365b52ce78300fe2d2e21d6f753b3a78`; root verified its tree equals provisional `b84a866cd3b46db8c018ca608bb5079e96f90fd9` (tree `7fc4206f100df743d80a2958679b56931ed3ea10`). Local work is reconciled onto that delivered base without treating identical ancestry as a reason to repeat passing checks.

AC6/EM04 uses bounded quiesced local activation, not uninterrupted online cutover. The minimal additional protector protocol is `writer_guard(payloads)`: all three existing admission acceptance call sites hold it inside their existing publication authority guard until durable acceptance settles. A public local registry acquires the existing native descriptor `FileLock`, refreshes active public descriptors and rejects stale prepared envelopes. Native activation uses that same OS file lock and the supplied policy authority, including verified native read-back before publication. Already-open writers refresh each selection; in-flight acceptance completes before activation. A static/remote registry without this shared-lock guarantee fails local rotation's precondition. Remote providers retain their separately owned deployment authority requirements.

`rewrap_held` preserves the immutable payload and retains the current-policy/private permit through the actual full-body database CAS. Cancellation waits for accepted native writes before releasing locks. A saved content-free job records exact old/new versions and bounded progress. Interrupted CAS/checkpoint boundaries resume by inspecting the saved slot; a stale CAS never overwrites consumed/erased work. Denied rows leave blocked status. Existing original EM04 cases include paused acceptance/activation and a previously prepared old-version envelope; this is a subcheck of the existing case, not a new cohort. Old key retirement and backup erasure remain separate explicit decisions.

Initial development evidence is in the ignored `validation-321/` run directory: real AES/RSA crypto checks passed (26); the first native-adapter test run exposed incorrect test helper tuple unpacking, before exercising custody. That failed log is retained, and the bounded helper correction is recorded separately. Mocked native adapters are never counted as native OS conformance.

Root accepted the bounded #323 response-lifetime addition within AC5/EM06:
`hold_evidence_access(selections: tuple[(ProtectedPayload, PolicySnapshot), ...],
access: EvidenceAccess, *, guard)` is a metadata-only final-response lease on
the existing private authority, exposed through `CryptoProtector` and the
protector protocol. It reuses exact selection/snapshot/grant checks, at most 128
selections, finite expiry, the same lock ordering and required audit. Decrypt
selected sources individually under `open_held`; enter this lease before every
downstream provider/publication/response effect and revalidate before return.
Revoke/expiry/current-fence activation in the gap denies before any effect.
Never nest decrypt/open inside the metadata lease; no new grant, cached authority
or reentrant-lock design is introduced. The bounded EM06 test preserves evidence
that nesting two existing opens waits on the intentionally non-reentrant lock,
then verifies the metadata-only multiple-source lease and each denied gap.

The final AC1/AC4 provider-boundary check binds `UnwrapPermit` to the exact decoded
source reference, canonical wrapping-context bytes and wrapped-key bytes.
`require_unwrap` is checked by the engine codec and local RSA adapter; hosted
adapters must reuse it before private provider access. The EM02 low-level
substitution subcheck rejects using one live permit for a different slot/wrapper,
even inside the same boundary. Rewrap's public target remains separately
purpose/boundary checked. This strengthens the existing approved private-permit
contract without adding a new unwrap flow.

Staged authority evidence remains deliberately bounded: the real engine/storage
helper uses `FakeAuthority` for the outer `PolicyAuthority`, and crypto component
tests use `Fence`. The AES/RSA, ciphertext storage/CAS, native custody and shared
descriptor FileLock are real; the fixture outer guard is not a current policy or
erasure ledger. #325 supplies the concrete local authority through the existing
contract, and #280 supplies hosted authority. AC5/AC6 and EM04/EM06's full outer
policy/fence integration, including #323's response lease, remain pending. This
does not add an acceptance criterion, replace the frozen cases or authorize a
parallel authority implementation here.

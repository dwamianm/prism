# RFC-0019: Sanitized memory, protected evidence and erasure

**Status:** Frozen v1 contract; implementation pending #320-#327
**Version:** 1.0
**Date:** 2026-10-19
**Depends on:** RFC-0002, RFC-0004, RFC-0007, RFC-0014, RFC-0016, RFC-0017
**Tracking:** [PV-01 / #319](https://github.com/dwamianm/prism/issues/319),
[engine epic #318](https://github.com/dwamianm/prism/issues/318)

## 1. Scope and status

This contract governs protected stores, including DuckDB directory and single-file
packs and PostgreSQL. It is a specification, not a claim that current stores
sanitize, seal or erase content. Current code was inventoried at `72360483`.
Existing defaults and record bytes do not change with this RFC. A store must not
advertise protected status until the implementation and migration gates pass.

Normative artifacts are the [copy inventory](plans/2026-10-evidence-privacy/content-copy-inventory.json),
[sanitizer registry](plans/2026-10-evidence-privacy/sanitizer-contract.json) and
[scenario manifest](plans/2026-10-evidence-privacy/privacy-scenarios.json).
Each has its own schema/contract version. An incompatible change requires a new
version and explicit migration; editing a preset does not reinterpret old data.
The [original requirements E1-E9](plans/2026-10-evidence-privacy/README.md#acceptance-contract)
remain the release criteria. Provider calls and benchmarks are not part of PV-01.

## 2. Data classes and protection claims

Admitted memory is deliberately usable by normal memory clients. A permitted
sensitive fact stays sensitive, including when embedded, summarized or linked to
a pseudonym. Evidence is a minimal or full **sanitized** source and sanitized
diagnostic attachments. V1 never durably retains a pre-sanitization original.
Processing work is sanitized temporary extraction input, with distinct keys and
expiry. Provenance and audit envelopes use opaque IDs, clocks, fixed reason codes
and policy references; names, free text and predictable content hashes are not
harmless metadata.

| Claim | Meaning | Excluded guarantee |
| --- | --- | --- |
| At-rest encryption | A stolen stored payload needs a key | A running holder of the key can still read it |
| Service isolation | Ordinary service identity cannot acquire the evidence private capability | Ingress sees plaintext; administrators can change the deployment |
| End-to-end encryption | Only the client controls decryption and source processing | Not provided by this server-side processing design |

Local trusted Python code can inspect process memory and use low-level storage.
Owner checks are application boundaries, not isolation from malicious code in the
same process. OS administrators and same-user malware may defeat local key-store
controls. The engine cannot revoke text already returned or promise heap
zeroization. Whole-store protection is separate from evidence encryption.
Current directory encryption decrypts files while open; current single-file
encryption uses native DuckDB encryption and encrypted spill files. PostgreSQL
physical storage, WAL and backups need deployment-owned protection. None of
these alone provides diagnostic-only evidence access.

## 3. Profile and policy contract

All protected profiles use the same sanitizer **before** logs, queues, durable
content writes, embeddings or external processing. The sanitized representation
gets a new opaque source ID, its own checksum, `sanitizer_version`, registry/model
versions, policy revision and non-content action counts. Quotations are labeled
sanitized. There is no removed-span list or public hash of removed values.

| Profile | Capture | Ordinary retrieval | Diagnostic payload |
| --- | --- | --- | --- |
| Standard | `minimal` or `full` sanitized source | Admitted memory and authorized sanitized evidence while available | Encrypted; explicit capability for diagnostics |
| Sealed | `minimal` or `full` sanitized source | Admitted memory and approved metadata only | Encrypted under separate evidence capability |
| No evidence | `none` | Admitted memory only | None after processing |

`evidence_use` is `runtime` for standard and `diagnostics_only` for sealed.
No-evidence uses `diagnostics_only` with capture `none`; it does not mean no memory.
Minimal capture must preserve complete conditions, negation, time and attribution.
If the configured limit cannot retain a safe excerpt, reject capture/admission as
the policy specifies; never silently truncate a qualifier or upgrade to full.
Direct `store()` is explicit admission of its sanitized text, not a route for
making permitted facts secret. A raw history adapter that cannot honor the chosen
profile must reject the operation, not change the profile.

The immutable per-operation policy snapshot binds opaque project/namespace,
owner, scope, policy ID/revision, profile, capture/use, source deadline, separate
memory deadline or `until_deleted`, processing deadline, sanitizer/registry
version, language, category actions and exclusions. Optional unlimited source
retention must be explicit and permitted by deployment policy. The proposed
starting source lifetime is 30 days and processing ceiling 24 hours; these are
configuration recommendations, not measured defaults. A processing deadline is
the earliest applicable source deadline and configured processing ceiling.

No diagnostic retention and no durable processing input are different controls.
The latter permits synchronous processing only and requires resubmission after
failure. Accepted asynchronous work is encrypted under a processing purpose and
erased on success, cancellation, expiry or erasure. Worker authority is restricted
to its currently claimed pending item, never the completed evidence archive.

Composition is restrictive: mandatory blocks win; a caller cannot relax a scoped
policy; deadlines take the earliest value; capture cannot exceed either side's
limit; diagnostics-only wins over runtime. Conflicting non-ordered transformations
(redact versus pseudonymize), incompatible strict schemas or missing versions
reject instead of guessing. PV-02 specifies the typed API using these rules.
Every publication, external transmission and disclosure revalidates the current
authoritative policy/erasure fence. Retrying with an old snapshot cannot bypass a
new restriction. No engine-global mutable policy implements per-project settings.

## 4. Frozen sanitizer boundary

`prme-sanitizer-v1` uses the closed registry and category/action/language matrix in
[sanitizer-contract.json](plans/2026-10-evidence-privacy/sanitizer-contract.json).
The selected local analyzer is `presidio-analyzer==2.2.364`, with
`spacy==3.8.11` and `en_core_web_sm==3.8.0`. Only English (`en`) is supported.
The registry explicitly enables its listed recognizers; no library default,
remote recognizer, runtime model download or newly installed plugin is adopted
implicitly. The published [Presidio release](https://github.com/data-privacy-stack/presidio/releases/tag/2.2.364)
and [spaCy model release](https://github.com/explosion/spacy-models/releases/tag/en_core_web_sm-3.8.0)
identify the selected artifacts. PV-13 locks their transitive dependencies and
artifact hashes and executes the frozen fixtures before claiming support.

The pipeline first rejects unsupported configuration and oversize input, then
runs mandatory local credential rules, configured bounded exclusions and the
allowlisted local entity recognizers. Match actions are `allow`, `redact`,
`pseudonymize`, `block` only where the category matrix permits. Credentials are
block-only in protected v1. Balanced removes detected contact identifiers;
strict also requires a declared structured memory schema and supported typed
validators. Custom actions cannot weaken mandatory blocks. A configured topic
is an exact bounded term rule, not an inferred medical/political classifier.

Errors, timeout, missing model/registry, unsupported language declaration or
unsupported action deny the entire admission before side effects. The caller
must declare language; v1 does not promise reliable automatic language detection.
English declarations on other-language text, unfamiliar secret formats, encoded
secrets and contextual confidential facts can still be missed. Recognizers are
fallible. Their [supported entities](https://presidio.dataprivacystack.org/supported_entities/)
and [language configuration](https://presidio.dataprivacystack.org/analyzer/languages/)
do not establish universal discovery. PV-13/PV-11 report supported-fixture
precision/recall, benign damage and known misses separately.

Apply the same rules to content-bearing JSON values **and keys**, speaker/display
names, queries, generated claims, profiles and summaries. Validated opaque
protocol IDs are not natural-language text. User-chosen IDs are not a loophole:
protected APIs require opaque identifiers or reject them. Unsupported binary or
structured attachments and visual input fail before admission. Custom rules are
bounded literal/typed-pattern declarations, never caller code or network calls.
Strict schemas have closed fields, finite enums/typed validators and no arbitrary
`notes` or free-text escape field. Unsupported qualifiers require rejection.

Pseudonymization uses a random 256-bit purpose-separated HMAC-SHA-256 key per
project/owner/scope and epoch; input is a length-delimited category plus exact
UTF-8 detected value. No case folding merges distinct identities. Emit the full
base64url MAC in reserved syntax; store no plaintext reverse map. The key remains
outside the pack in the pseudonym provider. Keys/tokens are sensitive, not anonymous.
Source text matching issued-token syntax is escaped before recognition; generated
text may use only tokens already issued for its source boundary. Cross-boundary
lookups reject. Losing/rotating the key changes the epoch and never silently
reidentifies or merges people. Erasure covers tokens in all derivatives and
provider state; deleting an epoch key alone does not erase facts or backups.

## 5. Content-copy control and ordinary readers

The inventory is the copy coverage authority. Its `current_fields` and
`source_refs` identify existing code; `required_treatment`, `retention` and
`erasure_owners` specify future obligations. Current logical archival and chat
clear markers are not content erasure. Retired records and abandoned staging
are still copies. Embeddings and checksums inherit the source's sensitivity.

Sealed retrieval, graph/state/aggregate/provenance APIs, query results, receipts,
frameworks, explorer, CLI and exports must not disclose source-only text or
evidence-only metadata. Session/episode expansion, evidence augmentation,
answerability, temporal reasoning and recovery cannot silently read the vault.
Index only admitted memory for sealed/no-evidence profiles. Rebuild and recovery
use admitted plans, not source decryption. Unknown new content-bearing extension
fields are rejected in protected schemas until classified.

Standard intentionally permits authorized runtime source reads before expiry.
An unavailable source produces an explicit limitation, not a fallback to journals
or a key provider. Storage-only encryption parity holds the sanitized admitted
input and policy fixed. Sanitation differences and sealed retrieval differences
are separate measurements; their losses cannot erase a parity failure.

## 6. Source availability v1

Authorized inspection returns `source_availability` with `schema_version: 1`,
opaque source/representation IDs, policy revision and one state below. It contains
no source text, removed span, unhashed name or public source checksum. Unauthorized
callers receive the same unavailable response for absent and foreign records;
the detailed state is not an existence oracle.

| State | Meaning |
| --- | --- |
| `available` | Policy permits this reader to use retained sanitized evidence now |
| `sealed` | Evidence is retained but this ordinary reader cannot read it |
| `not_captured` | Policy retained no evidence payload |
| `expired` | Source deadline passed; access denied even if purge is pending |
| `erased` | An acknowledged erasure fence covers this source |
| `key_unavailable` | An otherwise authorized read lacks a usable key; no fallback |
| `missing` | Expected payload is absent/corrupt; integrity/recovery failure |
| `legacy_unclassified` | Legacy representation has not completed verified migration |

Evaluate owner authorization first, then erasure, expiry, capture, classification,
profile permission, key and payload availability. Thus a sealed object whose
deadline passed reports `expired` to authorized metadata inspection, and a lost
key cannot make erased content available. The diagnostic reader can report
`available` where an ordinary reader reports `sealed`; availability is an access
observation, not a new source identity. Changing state never rewrites old receipt
bytes. New response envelopes link old IDs to a versioned availability observation.

## 7. Encryption and local key-provider choice

Use maintained PyCA **cryptography**, already present in this repository
(`uv.lock` currently records 46.0.5). The selected primitive contract is
`AESGCM` with a freshly generated 256-bit data key per object, a fresh 12-byte
nonce, and its full 16-byte tag. Encrypt once per object generation; retries
reuse the exact saved envelope, while changed plaintext gets a new generation,
key and nonce. Reject authentication failure without releasing any plaintext.
The [current AESGCM documentation](https://cryptography.io/en/50.0.2/hazmat/primitives/aead/)
defines these interfaces and the nonce-reuse prohibition. PV-03 must use a
supported patched library release and pin its tested version; this RFC installs
nothing and does not claim the existing lock is the latest release.

`evidence-envelope-v1` stores algorithm/schema, opaque object and representation
IDs, project/owner/scope, purpose, generation, policy revision, key-provider/key
version IDs, nonce, ciphertext/tag and wrapped data key. Authenticated data is
canonical UTF-8 JSON of the non-secret identity/context fields (sorted ASCII keys,
no whitespace, only bounded ASCII IDs and integers). Bind the payload kind and
all authorization identities. Mutable availability/purge progress is outside
that context; a policy rebind requires a new authenticated envelope. Neither
context nor provider audit fields contain user-entered names or content hashes.

The local write-only adapter uses **RSA-OAEP**, SHA-256 with MGF1-SHA-256,
4096-bit RSA keys and exponent 65537, through the same library to wrap only the
random 32-byte data key. OAEP's label is the exact authenticated context bytes.
The [RSA documentation](https://cryptography.io/en/stable/hazmat/primitives/asymmetric/rsa/)
documents public encryption/private decryption and OAEP. No bespoke cipher,
text-derived key or unauthenticated encryption is permitted.

The provider boundary separates `wrap(context, data_key)` from
`unwrap(context, wrapped_key, capability)` and identifies exact key versions.
The ordinary sealed process receives only a provisioned public wrapping key.
Diagnostic private keys are provisioned outside the pack in an OS key store or
external secret manager and are loaded only by an explicitly unlocked diagnostic
process. The selected local custody adapter is `keyring` with an explicitly
allowlisted native macOS Keychain, Windows Credential Locker or Linux Secret
Service backend; no plaintext, null, file or automatic fallback backend is allowed.
Availability of that backend is checked before provisioning. An unsupported OS
uses an explicitly configured external provider or refuses protected creation.
[Keyring's backend and security documentation](https://keyring.readthedocs.io/en/stable/)
describes these platform stores and their same-application access limitations.
PV-03 owns provisioning/unlock and provider conformance, not hosted KMS services.

`processing`, `evidence` and `pseudonym` have separate key purposes and grants;
the ordinary store key is separate again. Standard's runtime evidence reader is
an explicit capability. A permanently loaded private key in the retrieval
process must report `same_process`, and cannot advertise service isolation.
Separate processes under the same OS user still do not resist same-user malware.
An application claiming service isolation must enforce distinct OS/service
identities and private-key access outside the ordinary identity.

Diagnostic reads require selected IDs, matching owner/scope, purpose, finite
expiry, the authoritative fence and a content-free audit sink. Failure to append
the required audit authorization denies disclosure. Recheck the fence immediately
before returning plaintext. Attempts and outcomes contain actor/opaque IDs,
policy revision, timestamps and fixed reason codes only.

Recovery-key export is a separate deliberate action, never part of pack/export
defaults. No universal support key or plaintext downgrade exists. Permanent loss
of an unrecoverable evidence key permanently loses evidence; separately protected
admitted memory may remain usable. Rotation rewraps with explicit version and
restart-safe progress. Deleting the live wrapped key does not prove deletion of
backup copies still decryptable with an old wrapping key.

## 8. Erasure, history and rebuild amendment

These operations have separate meanings and deadlines:

- **Evidence expiry:** deny source reads at its deadline and purge source/work
  copies; explicitly admitted memory remains under its own policy.
- **Archival:** changes ordinary retrieval eligibility; content may remain.
- **Memory erasure:** fence and remove affected admitted memory and derivatives,
  including every inventoried journal, index, staging and response cache copy.
- **Live purge:** logical removal from managed live stores completes; no claim
  of physical-media wiping, filesystem free-space erasure or provider deletion.
- **Backup expiry:** deployment-owned snapshots, WAL archives and old wrapped
  keys age out or are separately destroyed; external copies remain disclosed.

The committed erasure fence is the acknowledgment boundary. Stale extraction,
organizer, migration, restore and retry work cannot publish or disclose covered
content afterward. In-flight reads revalidate before delivery. Purge is durable,
idempotent and resumable. Multi-source derivatives survive only when the retained
content can be justified by available surviving evidence or explicit admission;
uncertain separation invalidates the derivative. Unrelated owners stay intact.

`erasure-status-v1` reports `requested`, `blocked_from_access`, `live_purged`,
`backup_expiry_pending`, or `complete_within_managed_boundary`, plus explicit
managed/external boundaries and fixed-code failures. Completion requires verified
live copy coverage and the managed backup deadline or verified disposal. A failed
purge never reports completion; the access fence remains. Offline time does not
promise maintenance execution; startup checks fences/expiry before serving.

**Amendment to RFC-0002 and RFC-0014:** authorized privacy erasure is an explicit
exception to payload immutability. Event/operation identities and minimal erasure
records may survive; erased content and content-bearing historical snapshots may
not. Do not recompute old checksums over redacted text and present them as the
original record. Intact legacy records keep their exact versions and bytes.
When their payload must be erased, a new versioned redaction envelope references
the original opaque identity and declares its checksum verification/replay
unavailable. A sensitive legacy checksum can itself be removed or protected;
retaining its old value publicly is not required for identity preservation.

Erasure records contain no deleted payload, full before/after snapshots or
publicly testable unkeyed digest of predictable content. Audit identity is not
proof that deleted content can be reproduced. Rebuild uses surviving admitted
memory; re-extraction or exact replay of erased content is impossible and must
not be promised. `knowledge_at` remains a cutoff over current state, not replay.

Restore/import must accept an independently current erasure ledger, apply it
before indexes or public reads are enabled, and fence all recovered work.
A snapshot's own older ledger cannot establish deletion continuity. A standalone
offline copy has no central revocation; its operator is responsible for disposal
and the current ledger. Migration reports source packs, backups and plaintext
exports that remain. It never relabels a legacy store protected by changing a flag.

## 9. Hosted handoff and ownership

The [hosted epic #279](https://github.com/dwamianm/prism-monorepo/issues/279)
consumes the released engine contract. Engine requirements do not depend on a
hosted account or deployment.

| Boundary | Engine obligation | Hosted obligation |
| --- | --- | --- |
| Policy | Immutable scoped snapshot, revision, inspection and authoritative fence hooks | Project settings persistence, revision activation, roles and strict composition |
| Identity | Validate owner/scope/selected IDs; never infer a grant from a requested owner | Authenticate human/service identities, authorize project access and issue grants |
| Keys | Purpose/context-bound provider API and fail-closed reads | KMS adapter, ordinary/work/diagnostic IAM roles, no broad decrypt grant |
| Diagnostics | Scoped finite capability, current fence, content-free audit | Session reauthentication, broker identity, 10-minute/20-record ceilings and UI relocking |
| Erasure | Transactional fences, live copy purge, progress and restore-ledger hooks | Independent current ledger, backup/WAL retention, restore orchestration and export custody |
| Ingress/observability | Sanitize before engine side effects; no raw error/preview payload | Gateways, request logs, traces, queues and monitoring must not capture raw input first |

Hosted administrators retain platform control. An engine local keyring is not a
hosted IAM implementation. The inventory assigns cloud snapshots, logs and
external providers to their actual operators; engine purge cannot report them
deleted without their evidence.

## 10. Frozen validation handoff

The manifest preserves five original groups of eight scenarios (40) and the exact
12 named sanitation additions (52). The earlier plan specified category counts,
not named executable cases or a passing baseline. This RFC freezes case IDs,
requirements and expected results now; all runtime results remain `not_run`.
PV-11 executes 52 on DuckDB and 52 on PostgreSQL, recording exact layouts and
versions. DuckDB directory and single-file assertions are layout subchecks inside
each applicable backend case, not substituted cases or extra counted successes.

PV-11 separately freezes/runs 24 fixed storage-parity queries and 24 sealed
behavior cases (six facts, six conditions/corrections, six multi-source and six
source-only requests). Preserve the earlier unsanitized baseline and report
sanitation losses separately. Use local fixtures/fake extraction, no new live
model calls, and the approved two-repair limit. The structural validator checks
coverage and references only; it cannot certify cryptography, detector accuracy,
erasure completeness or the future 104 executions.

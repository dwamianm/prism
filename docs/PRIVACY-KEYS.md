# Explicit privacy keys

PV-03 supplies authenticated encryption and explicit key capabilities for the
opt-in admission slots. It does not enable protected mode globally or complete
the sanitizer, source projection, journal, erasure, migration or release gates.
Legacy event/checksum bytes and ordinary store encryption remain unchanged.

Install the tested native adapter with `pip install 'prme[local-privacy]'`.
The locked implementation uses cryptography 50.0.2 and keyring 25.7.0.

## Ciphertext and authenticated context

Each object generation uses an independent random 32-byte AES key and 12-byte
nonce with AES-256-GCM and its full 16-byte tag. Exact retries reuse the saved
envelope. Changed content requires a new generation, key and nonce.

The closed canonical JSON envelope has two authentication domains:

* `prme-payload-aad-v1` binds the payload algorithm/schema and every
  `PayloadContext` field: project, owner, scope, event, source, representation,
  slot, purpose, payload kind, generation and policy identity/revision. It also
  binds a versioned SHA-256 digest of the complete original `PolicySnapshot`,
  including composed references, effective rules and independent saved
  deadlines. This is a policy-metadata digest, never a source-content digest.
* `prme-wrapper-v1` binds that exact immutable payload context, wrapper
  kind/provider/key/version/locator, wrapper revision and wrapper schema/domain.
  For local RSA, its exact canonical bytes are the OAEP label.

Local keys use RSA-4096, exponent 65537, OAEP SHA-256 and MGF1-SHA-256. Only the
32-byte data key is wrapped. The bounded codec rejects unknown fields,
algorithms, versions, duplicate JSON keys, noncanonical encoding, oversized
payloads, selector mismatches, wrong purposes and authentication failure.
Payloads are limited to 16 MiB. No partial plaintext is returned.

Rewrap changes only the wrapping context, wrapped key and wrapper revision.
The payload key, AAD, nonce and ciphertext/tag remain identical. A current
policy/erasure authority remains an independent guard; it cannot rewrite the
original authenticated acceptance policy.

The provider dispatch also recognizes `aws-kms-symmetric-v1` with a full immutable
KMS key ARN and separate logical version UUID. The hosted adapter is owned by
hosted #281; this engine creates no cloud keys and includes no KMS SDK calls.
It must use the same codec and exact canonical wrapping context as its KMS
encryption context. An alias/current-key fallback or separate hosted cipher
format is unsupported.

Private providers must call `permit.require_unwrap(descriptor, wrapped_key,
wrapping_context)` before unwrapping. A held permit binds the exact source
reference, wrapping-context bytes and wrapped-key bytes, as well as the selected
identity/purpose; it cannot be reused for another slot in the same boundary.
The shared codec and local RSA adapter both enforce this binding. Wrapper-only
rewrap validates its new public target separately without widening source access.

## Provisioned public writers and explicit readers

```python
from prme.privacy import CryptoProtector, public_registry

# Reads only public descriptors. Does not instantiate a native keyring.
protector = CryptoProtector(public_registry("/operator/keys/public.json"))
```

Inject this protector into the existing `PrivacyRuntime` alongside its required
policy authority, sanitizer and provider capabilities. Readiness checks occur
before protected engine creation. Missing capabilities refuse; no plaintext
fallback or automatic provisioning exists.

`NativeKeyCustody.create(path, backend=...)` explicitly selects one exact backend:
`macos` (`keyring.backends.macOS.Keyring`), `windows`
(`keyring.backends.Windows.WinVaultKeyring`), or `secretservice`
(`keyring.backends.SecretService.Keyring`). No automatic discovery, plaintext,
file, null or chained fallback is used. Store the public registry at a stable,
operator-controlled path; every cooperating local writer uses the same path.
Moving/copying the registry into independent writer configurations is not a
supported shared rotation boundary.

Provisioning creates unique version IDs and pending public metadata first.
Private material is divided into at most 16 native entries of at most 1,000
ASCII characters, plus a bounded completeness record. Parts bind the complete
purpose/boundary/reference and a reconstruction checksum; read-back also verifies
the derived RSA public key. Activation happens only after verified native
read-back. Windows uses local-machine persistence explicitly. Interrupted
complete versions can resume; incomplete versions require exact-version cleanup
and a new version, never regeneration under an existing reference.

The macOS backend allows the same Python executable to access its stored
secrets, and its custom keychain setting is not an enforced isolation boundary.
An application unlock does not guarantee an OS prompt each time. Windows native
ACLs and Secret Service availability/session unlock remain operator-managed.
Actual native conformance was run on macOS 26.6.1 arm64. Windows and Linux native
execution remain unverified; adapter tests do not establish their OS behavior.

Private access requires an explicitly constructed `NativeKeyCustody` and
`unlocked(exact_refs, expires_at=...)` context (at most 128 versions, at most
900 seconds). Supply the resulting wrapper explicitly to
`public_registry(path, private_wrapper=wrapper)`. The wrapper expires and is
closed at context exit. No ordinary read triggers it automatically.

`LocalAccessAuthority` requires a content-free audit sink. Evidence grants
select exact `(PayloadContext, original PolicySnapshot)` pairs, one boundary,
at most 128 event IDs, an action and a finite expiry of at most 900 seconds.
Runtime grants require the standard/runtime policy; diagnostics are explicit.
`open_held` keeps the required policy/erasure guard and audit authority through
the caller's actual output/send. `open` is for in-process use; transports must
use the held form and must not cache the disclosed value. Audit authorization
must be durably appended before disclosure.

Permits derive mandatory effective deadlines from the original snapshot and
explicit access; private authorities must supply their clock when constructing
one. `require_current()` checks the bound deadline and active scope. The codec
checks it after provider awaits and immediately before plaintext, processing-key
or rewrap output, and a processing-key lease checks again before its callback.
Private provider adapters must use the same permit checks before private access
and after provider waits. Audit and processing-validator waits cannot extend the
grant, source, memory or work lifetime. Completion audits are followed by another
deadline check before returning; a late failure appends a failed audit outcome.

For multiple selected standard sources, decrypt each under `open_held`, then
enter `hold_evidence_access(((payload, original_snapshot), ...), access, guard=...)`
before any downstream provider/publication/response effect. This metadata-only
lease retains the finite grant lock through the request, serializing grant
revocation, and yields no key or plaintext. The supplied current-policy guard
covers every selected snapshot at entry (including authorized audit and fresh
validation) and exit. It is released during the yielded pipeline so that each
actual effect can take its own effect-specific current-policy guard. A trusted
caller must check grant and selected snapshot deadlines after every wait and
immediately before each effect and final output. A policy fence activated after
entry must deny the next guarded effect; the grant lease does not replace those
guards. Entry/exit recheck all selections after audit waits. Never nest
`open_held` or another decrypt inside this non-reentrant grant lease; discard
transient source values on failure. #323 owns that actual pipeline integration.

`inspect_metadata` parses and checks context using only public descriptors. Its
`public_descriptor_known` result does not prove private-key availability or
ciphertext authenticity. It always reports `private_key_checked=false` and
`ciphertext_authenticated=false`.

All local private capabilities report `same_process`. Python code that can
replace a guard/provider, inspect loaded key memory, or copy a callback's bytes
is inside that trust boundary. A separate object or same-user subprocess is not
service isolation. Buffer clearing is best effort; there is no heap, swap, crash
dump or recipient-copy zeroization guarantee.

## Processing, pseudonym and retry keys

Processing uses a separate wrapping purpose. The local single-event
`StoredProcessingAuthorizer` reloads the exact current pending slot and actual
work claim/generation/lease; absent or terminal work refuses. Larger cohorts and
full retry/cancel/erasure integration remain PV-06 responsibilities.

The one processing-only `unwrap_processing_key_held` operation reuses the engine
codec, context checks, private permit, audit and held authority. Hosted #281's
broker loads the current slot server-side, validates the live claim/cohort,
generation, deadline and current fences, and holds that authority through the
actual authenticated key-response send. It returns a transient 32-byte processing
data key to the authorized worker remote provider. The worker has no direct KMS
Decrypt permission, caches no keys and independently fences provider/publication
effects. Human/runtime evidence reads use plaintext `open_held`; there is no
generic evidence-key unwrap endpoint.

`PseudonymKeyProvider.key_for(project_id, owner_id, scope, epoch)` returns a
non-serializable `SecretKeyHandle` with immutable reference fields and synchronous
`use(callback)`/`close()`. A fresh random key belongs to each boundary/opaque
epoch. Missing, foreign or retired epochs refuse, including use after retirement;
lookup never provisions. PV-04 owns HMAC framing, tokens and provenance.

Retry commitments have an independent random key per original acceptance
snapshot/request/retention reference, exact key version and explicit expiry.
Lookup cannot regenerate a lost key. PV-06 owns retry comparisons, cancellation,
terminal cleanup and publication. Neither purpose creates a reverse-value map.
The public inventory exposes only references/states for PV-07 erasure.
The ordinary store key is separate from all four purposes.

## CLI and recovery

```sh
prme privacy-keys provision --registry /operator/keys/public.json \
  --backend macos --purpose evidence --project PROJECT_UUID --owner OWNER_UUID
prme privacy-keys status --registry /operator/keys/public.json
```

Provision `processing` separately when durable processing is required.
Provision `pseudonym` with an explicit `--epoch`. Status never checks private
native availability. All IDs are opaque UUIDs.

Protected `inspect` and `unlock` take `--engine-factory module:factory`,
project/owner/scope, one or more `--event` IDs and `--audit`. The factory returns
an open configured `MemoryEngine`, or an awaitable resolving to it. The command
owns and closes the engine on completion, denial or cancellation; do not return
an application-shared engine. Native key loading and each selected disclosure
use that engine's actual current-ledger and policy approval for the exact
record. There is no permissive CLI default. See [managed erasure](PRIVACY-ERASURE.md).

Legacy-only raw diagnostics retain the explicit `--store` or `--database-env`
target and `--authority-factory` interface. They refuse persisted protected
state, which requires the engine factory. A local path never follows an ambient
database URL. `--store-key-env` names the separate existing native DuckDB store key.
Closed legacy Fernet directory artifacts must be opened through their existing
supported workflow; this tool does not migrate them.

`unlock --action verify` emits only the selected object's authentication result.
`unlock --action reveal` deliberately writes decrypted sanitized source to stdout
while the guard holds; the recipient owns that copy. `--seconds` bounds the
private capability (1–900). Neither action creates a bearer-key file.

`recovery-export` takes an exact `--key-version`, new `--artifact`, and `--pack`
to enforce a destination outside that pack directory. The PKCS#8 artifact is
encrypted with PyCA's maintained password encryption, created exclusively with
restrictive permissions, and never overwrites a path. Enter the passphrase using
a hidden prompt or explicit inherited `--passphrase-fd` (FD >=3); never argv.
`recovery-import` verifies the original exact public descriptor. Retired versions
cannot be restored. Ordinary export/manifests contain no private/recovery keys.

Permanent loss of an unrecoverable evidence key permanently loses that evidence.
There is no universal support key. Separately stored admitted memory can remain
usable; evidence encryption does not encrypt or anonymize the memory plane.
Back up and retain recovery artifacts deliberately outside ordinary pack/export.

## Resumable local rotation

Provision the replacement version first; versions after the first are inactive.
`rotate` takes source/target version IDs and the same engine-factory/audit
arguments. `resume --job UUID` continues saved exact-version progress;
`rotation-status --job UUID` reads only metadata. `--limit` bounds each invocation
to 1–128 records. The CLI rotates evidence; processing rotation requires the
programmatic live-claim capability.

Every local protected admission holds the registry's OS file lock under the
existing policy authority from current-version validation through actual durable
acceptance. Activation holds that same lock. A prepared old-version write after
activation refuses; an already-open writer refreshes its public descriptor.
Static/remote registries without this guarantee cannot use local rotation.
Uncoordinated low-level database writers are outside the trusted engine API.

Per-row rewrap holds the current authority through an exact full-record CAS.
Consumed/erased/changed slots cannot be restored by an old wrapper update.
Cancellation waits for accepted native work to settle before releasing locks;
it may mean a commit succeeded before the caller received acknowledgment.
Checkpoints are content-free; interruption after CAS before checkpoint resumes
by observing the current slot. Counters describe acknowledged changes and can
under-count a crash between those steps. Fenced rows produce `blocked`, never a
false completed state. The original snapshot/AAD is unchanged.

Rotation retains old native keys. Explicit later retirement is separate.
Rewrap or deletion of a live key/slot does not erase retained keys, backups,
replicas, recipient plaintext or recovery artifacts. The operator owns those
copies and the dependent erasure/restore release gates remain pending.

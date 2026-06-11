# Review — issue-37-encryption-robustness-hardening

Issue #37: encryption robustness — fails open on close, crash leaves pack plaintext, key handling.

## Files Changed

- `src/prme/storage/encryption.py` — atomic write (temp + fsync + `os.replace`) for encrypt/decrypt; explicit `raw_key:`/`passphrase:` key-type prefixes with backward-compatible auto-detection retained; module docstring documents the plaintext window.
- `src/prme/storage/engine.py` — `close()` fails closed (logs ERROR, re-raises `EncryptionError`) on encryption failure; best-effort `atexit` re-encrypt handler registered for DuckDB packs and unregistered on clean close; `SecretStr` consumers unwrapped via `.get_secret_value()`; `close()` docstring documents the new raise.
- `src/prme/config.py` — `embedding.api_key`, `database_url`, `encryption_key` are now `pydantic.SecretStr | None`; `backend` property unwraps; `encryption_key` description documents the new prefixes.
- `src/prme/storage/embedding.py` — unwraps the embedding `api_key` `SecretStr`.
- `src/prme/client.py` — sync `MemoryClient.close()` propagates `EncryptionError` (fail closed through the sync facade) after completing loop/thread teardown.
- `src/prme/cli.py` — `cmd_init` `.env.example` template documents `PRME_ENCRYPTION_ENABLED`.
- `README.md` — documents the plaintext window, key-type prefixes, the master toggle, and SecretStr redaction.

## Approach Summary

Bounded, backward-compatible hardening of the encryption lifecycle:
1. Fail closed on `close()` so a failed re-encryption is never silent.
2. Atomic file writes so a crash never leaves a truncated/partial file; plaintext removed only after the ciphertext is durable.
3. Best-effort `atexit` re-encrypt to narrow (not eliminate) the plaintext window on normal exits.
4. Explicit key-type prefixes to remove passphrase/raw-key ambiguity, additive only — existing packs decrypt unchanged.
5. `SecretStr` for the three secret fields so they are masked in dumps/reprs/tracebacks.

Deliberately scoped OUT (flagged for a human): DuckDB native encryption (on-disk format change) and any change to the default key interpretation / KDF / on-disk format that could lock an existing user out of an already-encrypted pack.

## Must Fix
None.

## Should Fix
1. `MemoryClient.close()` swallowed the new `EncryptionError`, reintroducing fail-open through the sync facade. **Fixed** — it now re-raises after teardown; `_atexit_close` keeps swallowing (best-effort at exit); `__exit__`/`with` users get the fail-closed exception.
2. Phase 5 tests for the new behavior. **Added** — see `tests/test_encryption.py`.
3. `cmd_init` `.env.example` omitted `PRME_ENCRYPTION_ENABLED`, so the scaffold alone wouldn't enable encryption. **Fixed.**

## Consider
- `close()` docstring should document the new `EncryptionError` raise. **Done** (engine.py + client.py).
- Idiom-alignment / one-line comment nits (Agent 4) — cosmetic, not applied to avoid churn.
- Parent-directory fsync after `os.replace` for full rename durability — out of scope; file-content durability is the load-bearing guarantee and is in place.
- Memory-pack files created at default umask (no explicit `0o600`) — pre-existing, out of scope for this issue.

## Security Audit Results

| Area | Result | Details |
| --- | --- | --- |
| SecretStr masking | PASS | Three secret fields masked in repr/str/model_dump; raw value only at 4 point-of-use call sites, never logged. |
| Fail-open elimination | PASS | `close()` re-raises; sync `MemoryClient.close()` now propagates too; API lifespan propagates. |
| Plaintext window not widened | PASS | Atomic temp is cleaned on failure; no lingering plaintext temp; encrypt temp holds ciphertext only. |
| Key handling | PASS | PBKDF2 (600k iters), on-disk format, salt handling unchanged; explicit prefixes only remove guessing. |
| Crash-safety ordering | PASS | Ciphertext durable (fsync) before plaintext unlink, and vice versa on decrypt. |
| Temp/file permissions | N/A (informational) | Default umask; pre-existing, out of scope. |

## Pattern Consistency Assessment
- atexit lifecycle mirrors the existing `client.py` `_atexit_close` idiom (register guard, unregister on clean close, swallow+log), and is slightly stricter (explicit registration flag).
- `_atomic_write` has no pre-existing analogue in the repo — justified local addition.
- Three `SecretStr` conversions are uniform; `APIConfig.api_key` correctly left as plain `str` (compared via `secrets.compare_digest`, never serialized by PRME).

## Redundancy Check
No Remove/Replace/Consolidate actions warranted. New code (atomic write, key prefixes, atexit net, SecretStr wrapping) has no pre-existing equivalent. `import atexit` and `from prme.storage.encryption import EncryptionError` in engine.py are both new and used.

## Wiring Findings
- atexit registration reachable on the only provider-setting path (`_create_duckdb`); correctly absent for Postgres (no file pack).
- `close()` re-raise propagates sanely: CLI surfaces it (`sys.exit(1)`), API lifespan surfaces it, sync client now propagates; atexit/`__del__` paths best-effort swallow.
- pydantic-settings auto-coerces plain-string env vars / kwargs into `SecretStr` — no wiring change needed.
- New `tests/test_encryption.py` cases auto-collected by CI (`uv run pytest tests/ -q`).

## Resolution Status

| Finding | Severity | Status |
| --- | --- | --- |
| `MemoryClient.close()` swallows EncryptionError (fail-open) | Should Fix | Resolved |
| Phase 5 tests absent | Should Fix | Resolved |
| `cmd_init` `.env.example` omits `PRME_ENCRYPTION_ENABLED` | Should Fix | Resolved |
| `close()` docstring lacks `Raises: EncryptionError` | Consider | Resolved |
| Idiom-alignment / comment nits | Consider | Not applied (cosmetic) |
| Parent-dir fsync; explicit file mode | Consider | Out of scope (noted) |

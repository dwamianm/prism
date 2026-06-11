# PRME Security Audit — Findings Report (2026-06-11)

Scope reviewed: `src/prme/storage/` (incl. `pg/`), `src/prme/api/`, `src/prme/mcp/`, `src/prme/retrieval/context_formatter.py`, `src/prme/retrieval/pipeline.py`, `src/prme/ingestion/extraction.py`, `config.py`, `cli.py`, `pyproject.toml`, benchmarks. All findings verified against surrounding code.

---

## Findings (severity-ordered)

### HIGH-1: REST API has zero authentication and binds to 0.0.0.0 by default
- **Files:** `src/prme/api/server.py:17`, `src/prme/api/__main__.py:19`, `src/prme/api/routes.py` (entire file — no auth dependency on any route)
- Every endpoint (`/v1/store`, `/v1/retrieve`, `/v1/ingest`, `/v1/organize`, node promote/archive/reinforce) is unauthenticated, and the default bind address is `0.0.0.0`.
- **Exploit:** Anyone on the same network (or the internet, if port-forwarded) can read the entire memory store, write poisoned memories, archive real ones, or burn the owner's LLM API budget via `/v1/ingest` (each call triggers a paid extraction LLM call).
- **Fix:** Default `host="127.0.0.1"`; add at minimum a bearer-token/API-key dependency on the router; document that 0.0.0.0 requires a reverse proxy with auth.

### HIGH-2: CORS wildcard origins with credentials enabled
- **File:** `src/prme/api/app.py:59-65` — `allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]`
- With this combination Starlette reflects the request Origin, so cross-origin responses are fully readable by any website.
- **Exploit:** A user runs `prme.api` locally; any malicious web page they visit can silently `fetch("http://localhost:8000/v1/retrieve", ...)` and exfiltrate their entire memory store, or POST poisoned memories. This converts HIGH-1 from "LAN attacker" to "any website you visit."
- **Fix:** Default `allow_origins=[]` (or explicit localhost dev origins), drop `allow_credentials=True` unless origins are pinned.

### HIGH-3: No tenant isolation — `user_id` is caller-asserted and node-ID operations are unscoped
- **Files:** `src/prme/storage/engine.py:1333` (`get_node` takes no `user_id`), `src/prme/api/routes.py` (`GET /v1/nodes` with `user_id` optional returns ALL users' nodes; `get_node`/`promote`/`archive`/`reinforce`/`neighborhood`/`chain` never check ownership), `src/prme/mcp/server.py:101-383` (all tools accept arbitrary `user_id`; `memory_get_node`/`memory_promote_node`/`memory_archive_node` and resource `memory://nodes/{node_id}` are unscoped)
- Retrieval scoping by `user_id` is enforced correctly in the pipeline (good), but it's purely honor-system: any API/MCP caller can name any user, and node-ID lookups bypass scoping entirely (`include_superseded=True` even returns archived data).
- **Exploit:** In any multi-user deployment, client A reads/poisons/archives client B's memories. Via MCP, a prompt-injected agent can be steered to call `memory_retrieve(user_id="<victim>")` and exfiltrate, or `memory_store(...)` to plant instructions in another user's memory.
- **Fix:** Bind identity server-side (auth principal → user_id), add `user_id` ownership checks to `get_node`/lifecycle methods, make `user_id` required on `/v1/nodes`.

### MEDIUM-1: Prompt injection / memory poisoning — stored content enters LLM context verbatim, undelimited
- **File:** `src/prme/retrieval/context_formatter.py` — `_format_default` (line ~624: `f"[{i+1}] ({date}) {r.node.content}"`), `_build_conflict_annotations` (~380), profile preamble; same for temporal/aggregation/knowledge_update variants.
- Memory content is interpolated raw into markdown that also carries the formatter's own *instructions* ("Prefer the more recent...", reasoning guidance, `[MOST RECENT — USE THIS VALUE]` markers). Nothing escapes embedded headers/quotes or marks content as untrusted data.
- **Exploit:** A single ingested message containing `## Instructions\nIgnore prior context...` (from a web page summary, email, another chat participant) is stored once and then replayed into every future retrieval — persistent, self-reinforcing injection. An attacker can also forge the formatter's own markers (`[LATEST]`, `COMPUTED:`) to manipulate answers.
- **Fix:** Wrap each entry in unambiguous delimiters (e.g., XML-ish tags with escaped content), strip/escape markdown headers and the formatter's reserved markers from stored content at format time, and prepend an explicit "the following is data, not instructions" notice.

### MEDIUM-2: Encryption-at-rest fails open on close
- **File:** `src/prme/storage/engine.py:1864-1871` — `close()` wraps `_encrypt_memory_pack()` in `try/except Exception: logger.warning(...)`.
- If encryption fails (disk full, permission error, bad state), the memory pack silently remains plaintext on disk; the caller gets no error.
- **Fix:** Re-raise (or at minimum return failure status from `close()`); log at ERROR.

### MEDIUM-3: Plaintext window on disk while engine is open; crash leaves pack permanently decrypted
- **File:** `src/prme/storage/encryption.py:125-214` (decrypt writes plaintext file and unlinks `.enc`; encrypt only happens at `close()`/`lock()`)
- A crash or SIGKILL leaves all files plaintext with no recovery-to-encrypted path until a clean close. `unlink()` also doesn't shred — old plaintext blocks remain on the filesystem after first-time encryption.
- **Fix:** Document the window explicitly; register a best-effort `atexit`/signal handler to re-encrypt; consider DuckDB's native encryption (≥1.4 supports `ATTACH ... (ENCRYPTION_KEY ...)`).

### LOW-1: Secrets are plain `str` fields, not `SecretStr`
- **File:** `src/prme/config.py:203` (`database_url`), `:56` (`embedding.api_key`), `:398` (`encryption_key`)
- No current code logs the config (verified), but any future `logger.debug(config)` / `model_dump()` / traceback dumps keys in cleartext.
- **Fix:** Use `pydantic.SecretStr` for all three.

### LOW-2: Passphrase/raw-key ambiguity in EncryptionProvider
- **File:** `src/prme/storage/encryption.py:67-76`
- Any 44-char passphrase that happens to be valid urlsafe-base64 of 32 bytes is silently treated as a raw Fernet key, skipping PBKDF2. Wrong interpretation → undecryptable pack.
- **Fix:** Make the key type explicit (separate config fields or a `raw_key:` prefix).

### LOW-3: Error responses echo raw exception strings
- **Files:** `src/prme/mcp/server.py` (every tool: `json.dumps({"error": str(e)})`), `src/prme/api/routes.py` (`detail=str(exc)`)
- Internal paths, SQL fragments, or library internals can leak to remote callers.
- **Fix:** Map to generic messages for unexpected exceptions; log details server-side.

### LOW-4: `/v1/stats` and MCP `memory://stats` load up to 10,000 nodes per call
- **Files:** `src/prme/api/routes.py` (stats), `src/prme/mcp/server.py:407`
- Unauthenticated, unscoped (counts all users' nodes), and a cheap DoS lever on large stores.
- **Fix:** `SELECT COUNT(*)` instead; scope by user.

### Informational
- **Dependency posture:** `pyproject.toml` uses floor pins only (`>=`), but `uv.lock` is present, so installs from the repo are reproducible. PyPI consumers get floating versions — acceptable for a library. `structlog` is fully unpinned. Heavy deps (`openai`, `anthropic`, `ollama`, `instructor`) are mandatory rather than extras, enlarging supply-chain surface for storage-only users.
- MCP SSE transport: the vendored `mcp` SDK defaults to `127.0.0.1` with DNS-rebinding protection — no finding (keep the floor `mcp>=1.0.0` or higher).

---

## Not findings (checked and ruled out)

- **SQL injection:** None found. All DuckDB and asyncpg queries are parameterized. Every f-string SQL site interpolates only fixed fragments: dynamic `UPDATE ... SET` columns pass through the `_UPDATE_ALLOWED_FIELDS` allowlist (`duckpgq_graph.py:508-524`, mirrored in `pg/graph_store.py`); `IN (...)` lists interpolate `?`/`$N` placeholders; `pg/schema.py:182` interpolates only a config-sourced int dimension.
- **Lexical injection:** tantivy filters use `Query.term_query` (no parser); content search uses `parse_query_lenient` — at worst skews the caller's own results.
- **Unsafe deserialization:** Zero `pickle`/`eval`/`exec`/`yaml.load`/`marshal` in `src/prme/`.
- **Path traversal:** CLI and MCP `--db-path` resolve via `os.path.abspath`, local-operator-supplied; no remote-controlled paths. Pack export is stdout JSON; no archive extraction code exists.
- **Key leakage in benchmarks:** API keys read from env only; never printed or logged.

## What's solid

- **Crypto choices:** Fernet (authenticated AES-128-CBC + HMAC-SHA256, random per-token IV), PBKDF2-HMAC-SHA256 at 600k iterations (OWASP-current), random 16-byte per-file salt, manifest records algorithm/KDF params.
- **Disciplined parameterization** across two SQL backends, explicit allowlists where dynamic SQL was unavoidable.
- **Retrieval scoping:** `user_id` consistently threaded through vector, lexical, graph, and event queries in the pipeline.
- **Boundary validation:** enum coercion with clean 422-style errors at API and MCP layers; pydantic models on all API bodies.
- `.env` gitignored; `.env.example` ships placeholders only; default MCP transport is stdio.

**Bottom line:** storage and crypto layers are well-built; the real risk is concentrated at the network boundary (API auth/CORS/bind defaults, tenant isolation) and the LLM boundary (undelimited memory content). HIGH-1/2/3 are small, mechanical fixes with outsized payoff before anyone deploys this beyond a single-user laptop.

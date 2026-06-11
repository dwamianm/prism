# Review — issue-34-api-auth-bind-cors-hardening

Six-agent code review of the security hardening for issue #34 (REST API auth,
bind default, CORS, error sanitization, stats COUNT). Base branch: `main`.

## Files Changed

- `src/prme/config.py` — new `APIConfig` (api_key, cors_origins, cors_allow_credentials) nested as `PRMEConfig.api`
- `src/prme/api/routes.py` — `require_api_key` bearer dependency on the router; `/v1/health` on `public_router`; `/v1/stats` uses `count_nodes()` with optional `user_id` scoping
- `src/prme/api/app.py` — CORS only when origins pinned, credentials never with wildcard; generic 500 handler; config on `app.state`
- `src/prme/api/server.py`, `src/prme/api/__main__.py` — default bind `127.0.0.1`; warning on non-loopback bind
- `src/prme/storage/graph_store.py`, `duckpgq_graph.py`, `pg/graph_store.py`, `engine.py` — `count_nodes()` (`SELECT COUNT(*)`)
- `src/prme/types.py` — shared `ACTIVE_LIFECYCLE_STATES` constant (review follow-up)
- `src/prme/mcp/server.py` — `_internal_error()` for unexpected exceptions; `memory://stats` uses `count_nodes()`; FastMCP `description=` → `instructions=` (mcp 1.26 API; pre-existing import-time breakage)
- `documentation/http-api.md`, `documentation/deployment.md` — auth/CORS/bind docs
- `tests/test_api.py` — auth, CORS, stats-scoping tests

## Approach Summary

All five issue #34 checkboxes implemented as small mechanical changes at the
network boundary. Auth is config-driven and a no-op when `PRME_API_API_KEY`
is unset, preserving the local-first single-user workflow. Controlled
`ValueError` validation messages (lifecycle transitions) are still surfaced;
only unexpected exceptions are made generic and logged server-side.

## Must Fix

| # | Finding | Source | Status |
|---|---------|--------|--------|
| 1 | `graph_store.py` `query_nodes` docstring said default is `[TENTATIVE, STABLE]` but implementations use TENTATIVE/STABLE/CONTESTED | Agent 4 | Resolved — docstring corrected |

## Should Fix

| # | Finding | Source | Status |
|---|---------|--------|--------|
| 2 | Active lifecycle-state default list duplicated across 4 backend methods | Agents 4, 5 | Resolved — extracted `ACTIVE_LIFECYCLE_STATES` to `prme/types.py`, used in all 4 sites |
| 3 | `api_key` should be `SecretStr` | Agent 2 | Deferred — matches existing plain-`str` secret pattern (`embedding.api_key`, `encryption_key`); converting all secrets is audit finding LOW-1, its own follow-up |
| 4 | FastMCP kwarg rename undocumented | Agent 3 | Resolved — comment added at the call site |

## Consider (noted, intentionally not changed)

- `app.state.engine = None` at create time (Agent 5): kept — makes `_get_engine` return a clean 503 instead of `AttributeError` before lifespan runs.
- `/v1/stats` behind auth (Agent 5): kept — node counts are user data; `/v1/health` is the unauthenticated probe.
- Refuse startup on non-loopback bind without key (Agent 2): warning chosen per issue wording ("explicit opt-in and a warning").
- IPv4-mapped IPv6 loopback forms not in `_LOOPBACK_HOSTS` (Agent 1): acceptable; warning is best-effort.
- CORS credentials warning "unreachable" (Agent 1): incorrect — it fires when `cors_allow_credentials=True` and `*` is in origins.

## Out of Scope (pre-existing, not regressions from this change)

- PG `_UPDATE_ALLOWED_FIELDS` missing `ttl_days`/`event_time` vs DuckDB (Agent 1)
- `event_count` hardcoded to 0 in `/v1/stats` (Agents 1, 3)
- Node-to-dict conversion duplicated between API and MCP layers (Agents 3, 4)
- Tenant isolation / server-side identity binding (audit HIGH-3, tracked separately)

## Security Audit Results (Agent 2)

| Area | Result | Details |
|------|--------|---------|
| Timing-safe key comparison | PASS | `secrets.compare_digest`, routes.py |
| Auth applied to every protected route | PASS | router-level dependency; only `/v1/health` public |
| Auth bypass vectors (query string, case) | PASS | HTTPBearer header-only, case-insensitive scheme per RFC 7235 |
| SQL parameterization in `count_nodes` | PASS | `?` / `$N` placeholders both backends |
| Error responses echo internals | PASS | generic 500 handler + MCP `_internal_error`; ValueError messages are curated validation text |
| CORS default off, wildcard+credentials blocked | PASS | middleware only added when origins pinned |
| Secrets in logs | PASS | warnings never include the key |
| Bind default | PASS | `127.0.0.1` both entry points |

## Pattern Consistency Assessment (Agent 4)

`APIConfig` matches the nested-settings pattern (env_prefix, Field defaults,
registration in `PRMEConfig`). `count_nodes` matches `query_nodes` across
protocol, both backends, and the engine facade. DRY finding on lifecycle
states resolved via shared constant.

## Redundancy Check (Agent 5)

No redundant abstractions or dependencies added; all new imports stdlib or
already-used FastAPI modules. Per-layer error helpers justified (different
return types).

## Wiring Findings (Agent 6)

Both routers registered; all 13 endpoints reachable (12 protected + health);
`PRME_API_API_KEY` and nested `PRME_API__API_KEY` env forms both verified
working; both GraphStore implementations satisfy the updated protocol; no
other implementations exist; CI discovers tests wholesale. Full suite: 930
passed, 25 skipped at review time.

## Resolution Status

| Finding | Severity | Status |
|---------|----------|--------|
| query_nodes docstring default | Must Fix | Resolved |
| Lifecycle-states duplication | Should Fix | Resolved |
| SecretStr for api_key | Should Fix | Deferred (audit LOW-1 follow-up) |
| FastMCP kwarg comment | Should Fix | Resolved |
| Auth/CORS/stats test coverage | Consider | Resolved (tests added in testing phase) |

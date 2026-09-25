# Code Review: issue-120-protect-external-api-binds-and-dev-postgres-port

Base branch: `main` (loop override). Issue #120 asks for two fail-closed defaults: the development
PostgreSQL in `docker-compose.yml` published on loopback only (CI keeps working), and the HTTP API
refusing a non-loopback start without credentials, before a listener or the engine exists, with an
explicit and clearly named override for an authenticating reverse proxy, consistent treatment of the
supported entry points, and documentation that externally hosted ASGI factories must protect
themselves.

## Files Changed
- `src/prme/api/server.py`: `UnauthenticatedBindError(ValueError)`; `_is_loopback_address` (literal
  IP, IPv4-mapped handled); `_is_loopback_host` (literal loopback addresses, plus `localhost` only
  when every address it resolves to is loopback; any other name, an empty host and unresolvable input
  count as network addresses); `_check_bind` (loopback passes; credentials warn as before; otherwise
  raise unless `allow_unauthenticated_external_bind`, which logs an `UNAUTHENTICATED EXTERNAL BIND`
  warning). `run_server` gains the keyword-only override and runs the check before `create_app` and
  `uvicorn.run`. The old string set `_LOOPBACK_HOSTS` is gone (nothing imported it).
- `src/prme/api/__main__.py`: `--allow-unauthenticated-external-bind`, `allow_abbrev=False` so the
  override must be spelled out, and `UnauthenticatedBindError` mapped to `parser.error` (exit 2).
- `src/prme/config.py`: `APIConfig.auth_enabled`, the one definition of "requests need a bearer
  token". `src/prme/api/routes.py` `require_api_key` uses it too, so the bind check and the router
  cannot disagree.
- `docker-compose.yml`: `127.0.0.1:5432:5432` plus a header comment that the service and its
  credentials are for local tests and CI only. `.github/workflows/ci.yml`: the PostgreSQL job's
  `PRME_TEST_DATABASE_URL` now names `127.0.0.1`, matching the IPv4-only publish.
- Docs: `documentation/http-api.md` (running, loopback definition, exception name, override and what
  it gives up, other ASGI servers, per-user versus global keys, generated keys, TLS),
  `documentation/deployment.md` (security bullets, local test database versus a deployed database,
  Dockerfile `CMD` fixed to `python -m prme.api`, env-file credentials, loopback publish still needs
  credentials), `README.md`, `documentation/README.md`, `documentation/mcp-server.md` (stale
  transport row), `docs/HTTP-API.md` (broken README anchor replaced), `docs/INTEGRATION.md` and
  `CONTRIBUTING.md` (compose test database recipe, recreate note), `CHANGELOG.md` (upgrade note under
  Changed, the broken `uvicorn prme.api:app` command under Fixed).
- Tests: new `tests/test_api_server_bind.py`.

## Approach Summary
Premise: the supported launcher that chooses a bind address is `run_server` (used by
`python -m prme.api`), so checking there before `create_app`/`uvicorn.run` makes unauthenticated
network startup fail by default. Sibling entry points: MCP streamable-http hardcodes `127.0.0.1` and
requires `PRME_MCP_USER_KEYS` (`src/prme/mcp/server.py:1749-1751`, `:1827`), so it was already
fail-closed; the benchmark services hardcode `127.0.0.1`; the `prme` CLI has no serve command;
externally hosted factories cannot be checked by the runner and are documented, as the issue asks.

Alternatives rejected, with evidence:
- A request-time guard on `scope["server"]` in `require_api_key`: Starlette's TestClient
  (`starlette/testclient.py:261,280`) and httpx `ASGITransport` (`httpx/_transports/asgi.py:116`) set
  the server to the URL host, so it needs care around in-process callers, and refusing requests on
  factory-hosted deployments needs its own opt-out design. Filed as part of follow-up #173.
- A guard in `create_app`: it has no bind address (`src/prme/api/app.py:24`).
- An override as a `PRME_API_*` field: `_ProjectSettings` reads `.env` (`src/prme/config.py:19-31`),
  so a stray line could silently open the bind. The flag and keyword keep it on the launch command.
- The old spelling-based set (`127.0.0.1`, `localhost`, `::1`) missed the rest of `127.0.0.0/8` and
  IPv4-mapped loopback. Resolving arbitrary names (the first draft) mirrored asyncio's
  `create_server`, which binds every resolved address, but uvicorn resolves again after the engine
  lifespan has run, so a name could change in between (see B7). The final rule accepts only literal
  loopback addresses and `localhost`.

Shared state touched: none at runtime. The compose mapping changes who can reach the test database;
the CI URL changes with it.

Epic #77 rule check: the issue carries `audit-2026-09`. The default-change rule governs retrieval,
packing and representation behavior measured by DeepSeek answer runs. This change touches no
retrieval path, receipt or context, and the issue itself specifies failing closed by default, so no
answer run or evidence gate applies.

## Must Fix
| # | Finding | Source | Status |
|---|---|---|---|
| M1 | No tests for the bind check, the flag or the refusal path | Agents 1, 3, 4, 6 | Resolved: `tests/test_api_server_bind.py` |

## Should Fix
| # | Finding | Source | Status |
|---|---|---|---|
| S1 | "Credentials configured" written twice as mirror images (`server.py`, `routes.py:122`) | Agents 4, 5 (3 as Consider) | Resolved: `APIConfig.auth_enabled`, plus a parity test against the router |
| S2 | argparse abbreviations let `--a` or `--allow` turn on the override | Agent 2 | Resolved: `allow_abbrev=False`, tested |
| S3 | Override docs did not say identity binding and tenant isolation are lost | Agent 2 | Resolved: http-api guide, deployment bullet, flag help, docstring, warning text |
| S4 | Docker paragraph suggested the override for a loopback publish and misnamed what can reach the port; appending the flag after the image name replaces `CMD` | Agents 2, 6 | Resolved: paragraph now says a loopback publish still needs credentials and why |
| S5 | "Clients must then send `Bearer your-secret-key`" after the per-user example | Agent 3 (6 as Consider) | Resolved: "Clients send their own key" |
| S6 | README and `docs/HTTP-API.md` never linked to the new guidance; `docs/HTTP-API.md` pointed at a missing README anchor | Agent 4 (2, 3, 5, 6 as Consider) | Resolved |
| S7 | DNS rebinding on the unauthenticated loopback API (pre-existing) | Agent 2 | Follow-up #174 (same as B6) |

## Consider
| # | Finding | Decision |
|---|---|---|
| C1 | Name resolved at check time and again at bind time (Agents 1, 2, 3, 7) | Fixed: only literals and `localhost` count as loopback (B7) |
| C2 | Misleading refusal for `[::1]` or a typo (Agent 1) | Fixed: the message now says only literal loopback addresses and `localhost` count |
| C3 | `_check_bind` lacked a `Raises` section; module docstring stale (Agent 4) | Fixed |
| C4 | Loopback definition differed between guide and CHANGELOG (Agents 4, 5) | Fixed |
| C5 | env-file example fenced as bash (Agents 3, 6) | Fixed: `text` fence and a no-`source` note |
| C6 | "every endpoint except `/v1/health`" ignores `/docs` and `/openapi.json` (Agent 3) | Fixed: "every `/v1` endpoint" |
| C7 | Exception not named for programmatic callers (Agents 3, 6) | Fixed in the guide and CHANGELOG |
| C8 | CONTRIBUTING recipe assumed a free port and did not chain commands (Agent 6) | Fixed |
| C9 | `docs/INTEGRATION.md` test recipe used a different URL (Agents 4, 5, 6) | Fixed: same URL, links to CONTRIBUTING |
| C10 | Compose mapping regression guard (Agent 6) | Fixed: test reads `docker-compose.yml` |
| C11 | Placeholder secrets copied verbatim (Agent 2, B4) | Fixed: placeholders plus a generation command |
| C12 | No TLS guidance for the API (Agents 2, 6) | Fixed: one line in the guide and the security bullets |
| C13 | Doc fix listed under Changed (Agent 4) | Fixed: moved to Fixed |
| C14 | Warnings only go through `logging` (Agents 1, 2, 3) | Accepted: under `python -m prme.api` they reach stderr through the last-resort handler before uvicorn configures logging; an embedder that filters `prme` loggers chose that. Runtime visibility is part of #173 |
| C15 | Blank `PRME_API_API_KEY` counts as configured (Agents 1, 2, 3) | Accepted: pre-existing and fail-closed (every request gets 401); a blank-key validator would change config loading for existing installs |
| C16 | Refusal text serves CLI and library callers at once (Agents 3, 4) | Accepted: accurate, names both spellings |
| C17 | `parser` built inside `main()` (Agent 4) | Accepted: tests drive `main()` through `sys.argv` |
| C18 | `prme-mcp` lets its `ValueError` surface as a traceback; module-level `mcp` object has no bearer middleware under an external runner (Agents 2, 4) | Not addressed: pre-existing, MCP entry point unchanged here |
| C19 | Docker Engine before v28 and loopback publishing (Agent 2) | Accepted: low likelihood, CONTRIBUTING says to keep the port on loopback |
| C20 | Defensive branches in `_is_loopback_host` (Agent 5) | Kept: two lines, fail closed |
| C21 | `documentation/configuration.md` has no API section (Agent 6) | Not addressed: pre-existing gap |

## Security Audit Results
| Area | Result |
|---|---|
| Secrets in messages and logs | PASS: only the host and env var names |
| Credential predicate matches request-time auth | PASS: one property used by both |
| Rejection before engine or listener | PASS: tested with recorders on `create_app` and `uvicorn.run` |
| Loopback classification | PASS: literals, IPv4-mapped, `localhost` only when all loopback |
| Other ways to open an unauthenticated network bind | Factory and Gunicorn launches (documented, #173); override only by flag or keyword |
| Override explicitness | PASS after S2 |
| Compose and CI | PASS |
| DNS rebinding on loopback mode | Pre-existing, #174 |

## Pattern Consistency Assessment
`UnauthenticatedBindError(ValueError)` follows the repository's refused-configuration errors
(`storage/namespace_identity.py`, `storage/embedding.py`). The flag matches the `--allow-*` analogues
(`store_true`, keyword-only `False` default). `parser.error` with exit 2 matches `prme-mcp`.
`APIConfig.auth_enabled` follows `PRMEConfig.backend`. No shared helper with
`benchmarks/integrations/ollama_answers.py`: it checks outbound URLs, deliberately literal-only, and
it is part of the DeepSeek answer-module identity.

## Redundancy Check
No new dependencies, no dead code. The duplicated credential predicate was consolidated (S1). The
bind rule is described in full only in `documentation/http-api.md`; other pages summarize and link.

## Wiring Findings
The flag reaches `run_server`; the exception is importable from `prme.api.server`; the compose
mapping works with `docker compose up -d --wait` and the `127.0.0.1` URL in CI; the documented
`uvicorn prme.api.app:create_app --factory` resolves; the Dockerfile `CMD` uses a module shipped in
the wheel.

## Break Scenarios (adversarial)
Agent 7 pre-mortem headline: "Unauthenticated PRME API still on the network after the #120 fix:
production ran under `uvicorn --factory`, the override flag added during the upgrade crash loop stayed
in place, and neither left a trace in the logs."

| # | Scenario | Label | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| B1 | Factory or Gunicorn launch skips the check and nothing logs that auth is off | pre-existing | Medium | High, silent | Follow-up #173 | The issue scoped this path to documentation; closing it means a runtime signal or a request-time refusal with its own opt-out, which is a design decision |
| B2 | The override outlives the proxy it was added for | newly introduced | Medium | High, quiet | Follow-up #173 | Shares B1's root cause (no runtime signal that the API is unauthenticated on the network). Alternatives re-checked: a config field is worse (silent via `.env`); tying the override to a proxy source range needs a product decision. The Docker docs no longer suggest the override |
| B3 | Unauthenticated container or private-network deployments exit 2 after upgrade | newly introduced (intended) | Medium | Medium, loud | Accept | The fail-closed behavior the issue asks for; the CHANGELOG now opens with an upgrade note and says a multi-user backend needs the global key |
| B4 | Docs supplied guessable keys | newly introduced (doc text) | Low to Medium | High, silent | Fix now | Placeholders and a generation command |
| B5 | An existing compose container keeps `0.0.0.0:5432` until recreated | pre-existing | Low to Medium | Medium to High, silent | Fix now | Recreate note in CONTRIBUTING and CHANGELOG |
| B6 | DNS rebinding reaches the unauthenticated loopback API | pre-existing | Low | High, silent | Follow-up #174 | Needs Host validation middleware and care for in-process clients; outside this issue |
| B7 | A name checked once and resolved again after engine startup | newly introduced guard gap | Low | High, silent | Fix now | Only literal loopback addresses and `localhost` count; other names are refused without credentials |

## Follow-ups Raised
- #173: An unauthenticated HTTP API on a network address goes unnoticed when launched outside
  `python -m prme.api` or left running with the override (B1, B2).
- #174: The unauthenticated loopback HTTP API accepts requests from any web page through DNS
  rebinding (B6, S7).

## Resolution Status
| Item | Status |
|---|---|
| M1 | Resolved |
| S1 to S6 | Resolved |
| S7 | Follow-up #174 |
| C1 to C13 | Resolved |
| C14 to C21 | Accepted or not addressed, reasons above |
| B1, B2 | Follow-up #173 |
| B3 | Accepted |
| B4, B5, B7 | Fixed |
| B6 | Follow-up #174 |

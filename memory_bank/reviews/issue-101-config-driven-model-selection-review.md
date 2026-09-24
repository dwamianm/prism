# Code Review: issue-101-config-driven-model-selection

Scope: a bounded first slice of #101 (the PR says `Part of #101`). #101 covers about 10 runtime
modules and about 40 benchmark runners, lists 10 acceptance criteria, and proposes a four-step
delivery. Its shared configuration format, env grammar and role taxonomy are marked "illustrative",
and every later step depends on them. This slice fixes the one concrete defect the issue lists that
needs none of those decisions: query reformulation says it uses the extraction provider and model,
but it dropped the extraction endpoint, credential and timeout.

## Files Changed
- `src/prme/model_runtime/__init__.py`, `src/prme/model_runtime/generation.py` (new):
  `resolve_provider_connection()` and `create_instructor_client()`, the one place that decides
  where a generation request goes and which credential it carries. An explicit key or URL wins
  (an explicit key is sent as given, even when empty). OpenAI and Anthropic then fall back to
  `<PROVIDER>_API_KEY`/`_BASE_URL` from the process environment, then `.env`, without mutating
  `os.environ`. Ollama gets JSON mode. Anthropic with an endpoint is built directly with
  `AsyncAnthropic(base_url=...)` + `instructor.from_anthropic`, because Instructor 1.14's
  `from_provider` drops an Anthropic `base_url` and passes it to every `messages.create`.
- `src/prme/ingestion/extraction.py`: `InstructorExtractionProvider._ensure_client` calls the helper
  (a blank key still means "the provider's own"); the copied resolution block and `import os` are gone.
- `src/prme/retrieval/answerability.py`: `AnswerabilityEvaluator._ensure_client` calls the helper
  (an explicit empty key is still sent as given); the copied block and `import os` are gone.
- `src/prme/retrieval/reformulation.py`: `reformulate_query` gains keyword-only `api_key`,
  `base_url`, `timeout` and `client_cache`. Clients are cached by the connection their settings
  resolve to (provider/model, resolved URL, resolved key held as `SecretStr`), in a cache the caller
  owns or the process-wide default. The call is bounded by `asyncio.wait_for(timeout)`, names its
  model (Bedrock needs it), sends `reasoning_effort="none"` to Ollama as extraction does, and logs a
  timeout without a traceback. Failures still return `[]`.
- `src/prme/retrieval/pipeline.py`: three new keyword parameters appended at the end of
  `RetrievalPipeline.__init__`, and a per-pipeline `_query_reformulation_clients` cache passed to
  `reformulate_query`, so clients live and die with the engine and its event loop.
- `src/prme/storage/engine.py`: both `RetrievalPipeline` constructions (DuckDB, PostgreSQL) pass
  `config.extraction.api_key/base_url/timeout`.
- `src/prme/config.py`: `ExtractionConfig.timeout` requires a positive finite value; the
  `timeout`, `api_key`, `base_url` and `enable_query_reformulation` descriptions say reformulation
  uses them.
- `benchmarks/diagnostics/opt_in_reformulation_merge_study.py`: the frozen cache guard ignores the
  options it never recorded (`api_key`, `base_url`, `timeout`, `client_cache`). The registered study
  already refused to run on main (32 of its 380 pinned sources had diverged, `pipeline.py` among them).
- Docs: `CHANGELOG.md`, `docs/INTEGRATION.md`, `docs/EXPERIMENTAL-RETRIEVAL-POLICIES.md`,
  `documentation/configuration.md`.
- Tests: new `tests/test_model_runtime.py`; `tests/test_reformulation.py` (connection, cache,
  timeout, cancellation, model and Ollama call settings, two engines end to end);
  `tests/test_pg_integration.py` (PostgreSQL engine wiring, CI only);
  `tests/test_dotenv_configuration.py` (Anthropic endpoint now reaches the client);
  `SimpleNamespace` pipeline fakes in `tests/test_reformulation_merge.py` and
  `tests/test_experimental_retrieval_policies.py`.

## Approach Summary
Root cause: `reformulation.py` built `instructor.from_provider(f"{provider}/{model}", async_client=True)`
with no credential, endpoint, `.env` lookup, Ollama JSON mode or timeout, cached per process by
provider/model only. The engine passed only `config.extraction.provider/model`. With extraction
pointed at a gateway or a remote Ollama host, reformulation sent query text to the provider's
default endpoint instead, with no time limit (OpenAI SDK default 600 s).

Alternatives considered:
- Build #101 step 1 (schema, resolver, adapter contracts, inspection) first: rejected for this
  slice. The TOML format is "illustrative (not an existing supported format)" in the issue, and a
  resolver no runtime path consumes would let a model file be accepted and silently ignored.
- Reuse the engine's extraction provider client: rejected. `ExtractionProvider` is a protocol
  (`extraction.py:985`), custom providers have no Instructor client, and `_ensure_client` is private.
- A third copy of the resolution logic in reformulation: rejected in favor of one helper that
  extraction and answerability also use (their two copies were identical apart from blank-key
  handling, which the call sites now keep).
- A per-event-loop `WeakKeyDictionary` cache (the first implementation): rejected after review.
  With keep-alive connections the cached client references its loop, so closed loops never left
  the cache. The pipeline now owns its clients.

Shared state touched: the process-wide reformulation `_client_cache` (read only by
`reformulate_query`), each pipeline's new client cache, `.env` and `os.environ` (read only).
Receipts and execution parameters are unchanged.

## Must Fix (resolved)
- **Anthropic endpoint regression** (Agent 1, confirmed by Agent 2): with `ANTHROPIC_BASE_URL` set
  or an extraction `base_url`, Instructor built the client without it and every call failed with
  `unexpected keyword argument 'base_url'`. On main, reformulation's bare client let the SDK read
  the environment and worked. Fixed by building Anthropic clients for their endpoint directly
  (`generation.py`). This also fixes extraction and answerability, which already had the bug.

## Should Fix (resolved unless noted)
- **Per-loop cache never released closed loops** (Agents 1, 3, 7): replaced by a pipeline-owned
  cache; the process-wide default stays for standalone callers.
- **Cache key ignored credentials resolved from the environment or `.env`** (Agent 2): keyed on
  the resolved connection now, so a rotated key or a different `.env` builds a new client.
- **Answerability's explicit empty key would have fallen back to `OPENAI_API_KEY`** (Agents 1, 2),
  which could send that key to a keyless gateway: the helper sends an explicit key as given, and
  extraction and reformulation map a blank key to `None` at their call sites, so both keep their
  previous behavior.
- **Reformulation did not name its model** (Agents 4, 7): `model=` is now passed, as extraction does.
- **Hashing the key for the cache** (Agents 4, 5): removed; `SecretStr` compares and hashes by
  value and masks its repr.
- **Explicit settings were assigned twice in the helper** (Agents 3, 5): the connection is resolved
  once.
- **Unvalidated timeout** (Agent 1): `ExtractionConfig.timeout` now has `gt=0, allow_inf_nan=False`.
- **Stale descriptions and docs, no changelog** (Agents 3, 6): updated.
- **Missing tests** (all agents): added (see Files Changed).
- **`prme doctor` resolves credentials differently** (Agents 4, 5: `os.environ.get(name, local.get(name))`
  lets an empty variable win, and the provider name is not casefolded): not changed here. It predates
  this change, and moving the doctor onto `resolve_provider_connection` changes what it reports. Listed
  in the PR as remaining #101 work.

## Consider
- Positional parameters: the three new `RetrievalPipeline` parameters were appended at the end
  (Agent 1). Done.
- Timeout logged without a traceback (Agent 3). Done.
- `reformulate_query(api_key=str)`: the annotation says `SecretStr`; not widened (Agent 1).
- Engine kwargs duplicated across the DuckDB and PostgreSQL sites (Agent 3): pre-existing; both
  sites are covered by tests (DuckDB end to end, PostgreSQL in CI).
- Provider-name normalization differs across modules (Agents 4, 1): the helper casefolds;
  Instructor rejects names that are not lowercase anyway, so there is no reachable difference.
- `generation.py` naming (Agent 3): kept, since #101 separates generation, embedding, reranking
  and verification contracts.
- Failure log includes provider error bodies via `exc_info` (Agent 2): pre-existing, left as is.

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets in pipeline object, repr, receipts | PASS | `SecretStr` throughout; receipts unchanged |
| Secrets in cache keys | PASS | resolved key held as `SecretStr` (masked repr, test asserts) |
| Credential cross-contamination | PASS after fixes | explicit empty key kept; resolved-connection cache key |
| Ollama with `OPENAI_*` present | PASS | no cloud key or URL reaches Ollama (test) |
| Data egress | PASS | reformulation now goes where extraction goes, not a default public endpoint |
| SSRF via `base_url` | N/A | operator configuration only |
| Unsafe deserialization / fixtures | N/A / PASS | no literal keys |

## Pattern Consistency Assessment
The helper package follows `epistemic/` and `quality/` (docstring, re-exports, `__all__`). Instance-owned
clients now match `InstructorExtractionProvider` and `AnswerabilityEvaluator`. The flat pipeline
parameters match the existing `query_reformulation_provider/model/count`. A config object would mean
inventing the per-role connection model #101 has yet to design.

## Redundancy Check
Two identical copies of the credential and endpoint resolution were removed. No new dependency:
`python-dotenv` was already used. Remaining copies outside this slice: `cli.py` doctor (listed above),
Jev key lookups in `temporal_relation_providers.py` and `integrations/typesafe.py` (different vendor),
`retrieval/abstention.py` and `benchmarks/llm_judge.py` (remaining #101 work).

## Wiring Findings
Every runtime entry point (MemoryClient, workspace, CLI `search`, HTTP, MCP) goes through
`MemoryEngine.create`, which reaches both updated pipeline constructions. The new package ships in the
wheel and sdist (`uv_build` discovers `src/`). No import cycles. `reformulate_query` wrappers in
`benchmarks/diagnostics/opt_in_arm_worker.py` and `opt_in_successor.py` forward `**kwargs`.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "Query-reformulation client cache pins every closed event loop:
long-running services with many short-lived MemoryClients run out of file descriptors."

| # | Scenario | Origin | Likelihood | Impact | Verdict | Reasoning |
|---|---|---|---|---|---|---|
| 1 | Per-loop cache keeps a client, an open socket and a closed loop for every loop it sees | newly introduced | Medium-Low | memory and descriptor growth, then EMFILE | Fix now | Contained. Pipeline-owned cache; test runs two engines and checks nothing lands in the process cache |
| 2 | Remote Ollama reached without extraction's `reasoning_effort="none"`; Bedrock without `model=` | partly new (remote Ollama now reachable) | Low-Medium | retrieve latency up to the timeout; reformulation yields nothing | Fix now | Two contained call settings mirror extraction's provider rules; tested |
| 3 | Receipts cannot show whether reformulation failed or timed out, or which endpoint it used | pre-existing, more consequential now | Medium | silent loss of recall; slow diagnosis | Follow-up | Needs a receipt schema version and a status field |
| 4 | `expand_with_merge` research override calls `reformulate_query` without connection settings | pre-existing | Low | would send to SDK defaults | Dismissed | Only caller is the frozen study, which runs it under `patch(..., cached_reformulation)` (`opt_in_reformulation_merge_study.py:330`) after `validate_inputs` refuses changed sources (`:290`, `:305`); tests mock it |

Attacks that failed (Agent 7): half-open pooled connections after a timeout (httpcore closes on
cancellation); changed extraction or answerability requests (identical for every configuration
that worked before); positional and receipt compatibility.

Accepted, with reasons:
- An explicit extraction `base_url` with no extraction key sends the provider's own
  `OPENAI_API_KEY` to that endpoint (Agent 2). Extraction already does this on every ingest and the
  OpenAI SDK behaves the same way. Reformulation now follows the operator's extraction settings.
  Changing it would change extraction's credential rule, which belongs to #101's per-role design.

## Follow-ups Raised
- #163 Retrieval receipts cannot tell a failed or timed-out query reformulation from one
  that added nothing, and do not say which endpoint it used (scenario 3).

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Anthropic endpoint regression | Must Fix | Resolved |
| Cache retains closed loops | Should Fix / Fix now | Resolved |
| Cache key ignores env/.env credentials | Should Fix | Resolved |
| Answerability empty-key fallback | Should Fix | Resolved |
| Reformulation omits `model=` | Should Fix | Resolved |
| Hashing the key | Should Fix | Resolved |
| Double assignment in helper | Should Fix | Resolved |
| Unvalidated timeout | Should Fix | Resolved |
| Stale docs and changelog | Should Fix | Resolved |
| Missing tests | Should Fix | Resolved |
| Doctor credential rule differs | Should Fix | Deferred to remaining #101 work (pre-existing) |
| Ollama reasoning default | Fix now (scenario 2) | Resolved |
| Receipt failure status and endpoint | Follow-up (scenario 3) | Issue #163 |
| Research override | Dismissed (scenario 4) | Invariant cited |

## Verification
- `uv run pytest -q`: 4284 passed, 864 skipped (PostgreSQL variants skip without `PRME_TEST_DATABASE_URL`).
- `uv run ruff check src/ tests/` and `mypy --strict tests/typing/public_api.py`: clean.
- Offline evidence gate at current defaults: 1540/1540 LoCoMo and 500/500 LongMemEval-S saved contexts
  reproduced exactly; LoCoMo 25.2 records per context, 28.8% memory text, multi-hop 45/282;
  LongMemEval-S 23.9, 32.0%, 403/470. Reformulation itself cannot run in the gate, which refuses
  model-backed query features.
- Adversarial adjudication tally: Fix now 2, Follow-up 1 (#163), Accept 1, Dismissed 1.

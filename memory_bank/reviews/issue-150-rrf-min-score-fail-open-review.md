# Code Review: issue-150-rrf-min-score-fail-open

Base branch: `main` (loop override). Owner decision on #150 (2026-09-24): fail open, and say so.
When rank fusion is on, a caller sets `min_score`, and no result has a semantic cosine because the
vector path failed or detected an embedding mismatch: skip the floor for that request and return the
results in fused order; set a clear flag next to `backend_failures` / `embedding_mismatch` and
surface it in HTTP, MCP, the adapters and the receipt; keep the floor wherever a cosine exists and
leave weighted scoring unchanged; update RFC-0005 Section 7.2 and the config docs.

## Files Changed
- `src/prme/retrieval/selection.py`: `rank_fusion_skips_min_score()`: true when `min_score` is
  positive, `VECTOR` is in `backend_failures` (set for both `backend_error` and
  `embedding_mismatch`), and no candidate has a positive `semantic_relevance`.
- `src/prme/retrieval/pipeline.py`: under rank fusion, decides the skip from the primary pass's
  diagnostics and the results pool. Results then select without the floor. Cross-scope hints skip it
  too unless one of them has a cosine (their vector search is separate). The flag goes to
  `RetrievalMetadata`, the operation-log payload and `make_receipt`.
- `src/prme/retrieval/models.py`: `RetrievalMetadata.min_score_skipped` (HTTP and MCP return
  metadata as `metrics`, so both show it).
- `src/prme/models/relevance.py`: receipt schema version 18 with `min_score_skipped` (StrictBool,
  omitted when false). Version 18 requires the flag, a positive `min_score`, and every candidate's
  `semantic_relevance` equal to 0, and waives "every relevance is at least `min_score`". Versions 1
  to 17 cannot record it. The version 17 session-decay consistency check now also runs on version 18
  receipts that record a decay. `make_receipt(min_score_skipped=...)` picks 18, then 17, then 16, and
  refuses the flag without rank fusion.
- `src/prme/retrieval/full_learning.py`: paired evaluation also requires equal `min_score_skipped`.
- `src/prme/integrations/langchain.py`, `llamaindex.py`: optional `min_score` (validated when the
  retriever is built, passed to `MemoryClient.retrieve`); each result's metadata carries
  `min_score_skipped: True` when the floor was skipped. The retrievers never passed a floor before,
  so without the parameter the flag could never appear there.
- Descriptions: `api/models.py` (`min_score`), `mcp/server.py` (tool doc), `storage/engine.py`,
  `retrieval/pipeline.py`, `retrieval/config.py` (`fusion`).
- Docs: RFC-0005 Section 7.2 and its receipt paragraph, RFC-0006, RFC-0017, `docs/PACKING.md`,
  `docs/HTTP-API.md`, `docs/INTEGRATION.md`, `docs/FRAMEWORK-INTEGRATIONS.md`, `AGENTS.md`,
  `CHANGELOG.md`, `documentation/configuration.md`, `documentation/http-api.md`,
  `documentation/mcp-server.md`.
- Tests: new `tests/test_rank_fusion_vector_failure.py`, the pinned fixture
  `tests/fixtures/relevance/receipt-v17-rrf.json` (written by `make_receipt` on `main`), and adapter
  tests in `tests/test_integrations_langchain.py` and `tests/test_integrations_llamaindex.py`.

## Approach Summary
Premise: under rank fusion only the vector path gives a result a cosine, so when it is reported as
failed and no candidate has one, a positive floor can only return nothing. Every entry point that
accepts `min_score` (engine, client, HTTP, MCP, the adapters, both storage backends) reaches the
single selection call in the pipeline, so one decision covers them all. Reproduced first on `main`:
a vector outage with `min_score=0.1` returned 0 results.

Alternatives considered, with the evidence that ruled each out:
- Per-candidate fallback to the fused score when a candidate has no cosine: brings back #110 for
  keyword-only hits in healthy operation, against the owner's point 3.
- Lexical score as the fallback floor: lexical scores are min-max normalized per query
  (`normalize_bm25_scores` in `candidates.py`), so the top hit is always 1.0.
- Deciding on "no cosine" alone: a healthy vector search that returns nothing reports no failure
  (`vector_index.py` returns `[]` for an empty index or `vector_k=0`), and the owner asked to keep
  current behavior there. `test_a_vector_search_that_finds_nothing_keeps_the_floor` pins it.
- Deciding on the failure alone: the default `new_only` query reformulation can add candidates with
  cosines even when the primary vector search failed (`_expand_reformulated_queries`).
- Recording the flag only in `execution.parameters`: the version 16 rule "every relevance is at least
  `min_score`" must change for these receipts, and every receipt rule change here has had its own
  version with "cannot claim it" rules (versions 9 to 17).
- Recording `min_score=None` in the receipt instead: misstates the caller's request.
- One request-level decision for hints (first draft): reviewers reproduced hints that had their own
  cosines skipping the floor when only the primary search failed. Replaced by the hint rule above.

Shared state touched: response metadata (new key, also in HTTP and MCP `metrics`), the
`RETRIEVAL_REQUEST` operation-log payload (new key, always present; no reader checks the key set),
rank fusion receipts in the `operations` table (version 18 only when skipped; other receipts keep
their version and bytes), adapter document metadata (key only when set). Readers: receipt loaders in
`storage/relevance.py` (any version), citations (checksums only), ranking-multiplier learning (skips
rank fusion receipts), paired evaluation (now compares the flag), benchmark harnesses (weighted, and
`run_gpt54_comparison.py` refuses runs with backend failures).

## Must Fix
- **No pinned version 17 fixture while the version 17 rules were rewritten** (Agent 4). Resolved:
  `receipt-v17-rrf.json` written by `make_receipt` on `main` (`88e6090a`), checksum pinned, bytes,
  replay and scores asserted.

## Should Fix (all resolved)
- **Cross-scope hints with their own cosine skipped the floor** (Agents 1, 2, 7). Reproduced by
  Agents 1 and 2. Resolved: hints skip it only when none has a cosine;
  `test_cross_scope_hints_with_a_cosine_keep_the_floor` fails without the fix.
- **Paired evaluation ignored the flag** (Agents 2, 1, 6). Resolved in `full_learning._validate_pair`.
- **Metadata description said results were unfiltered** (Agents 1, 3): `limit` and the source and
  evidence caps still apply. Reworded.
- **Operation-log key untested** (Agent 3). Resolved: asserted for skipped and unfloored requests;
  comment says it is always present.
- **Version 18 rules spread over three places** (Agent 3). Resolved: one block, plus the waiver.
- **Earlier-version rejection covered only version 16** (Agent 4). Now 15, 16 and 17.
- **HTTP/MCP test derived its expectation from the value under test** (Agents 3, 4). Now fixed
  versions, and the served receipt equals the engine's.
- **Adapter tests never checked the key's absence** (Agent 4). Added to the existing rank fusion and
  weighted adapter tests.
- **HTTP and MCP descriptions left out the embedding mismatch case** (Agent 5). Reworded.
- **Untracked test file** (Agent 6): added by name; unrelated benchmark result files stay out.
- **Evidence gate cannot move** (Agent 6): recorded below with before and after numbers.

## Consider
- Resolved: explicit comparisons in `rank_fusion_skips_min_score`; clear `make_receipt` error for a
  weighted skip; empty pool documented and tested; "`min_score` is skipped" wording instead of "the
  floor" next to `relevance_floor`; FRAMEWORK-INTEGRATIONS paragraph moved to its own section;
  INTEGRATION.md wording; AGENTS.md en dashes on the edited line and a separate sentence for version
  18; single import style in the test file; CHANGELOG entry style; adapter `min_score` validated when
  built; per-request `weights=RRF` test.
- Not taken: a pinned version 18 fixture (this change writes version 18; the next receipt change
  pins it from `main`, as #110 and #111 did); recording the vector failure reason in version 18
  receipts (a forged skip cannot hide a cosine, because every relevance must be 0 and at least the
  cosines the receipt records); a strict adapter mode that refuses skipped floors (not asked for);
  consolidating the four `make_receipt` test helpers (pre-existing duplication); removing the
  lexical-outage test (it is the end-to-end check that only `VECTOR` triggers the skip);
  `verify_personamem.py` receipt rebuilds and the `melt_sut.py` / `precision_service.py` floors (all
  weighted runs today); a `max_length` on HTTP queries (unrelated, pre-existing).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets, PII, exception detail | PASS | Only a boolean is added; `backend_failures` keeps reason codes; leak check in the HTTP/MCP test |
| Authorization and ownership | PASS | Receipt reads unchanged and owner-scoped; hints stay same-owner |
| `min_score` validation | PASS | HTTP, MCP and engine validate before retrieval; adapters now validate when built |
| Forged version 18 receipt | PASS | StrictBool, `extra="forbid"`, positive finite floor, every relevance 0 and at least the recorded cosines |
| Learning pairs | PASS (after fix) | Paired evaluation compares the flag |
| Fail-open induced by a caller | PASS | A caller only gets what omitting `min_score` gives; owner, scope, time and epistemic filters still apply |
| Cross-scope hints | PASS (after fix) | Hints with a cosine keep the floor |
| Credentials in fixtures | PASS | Synthetic values |

## Pattern Consistency Assessment
The receipt change follows versions 16 and 17: Literal, "cannot claim it" rule for earlier versions,
"version N records it" rule, a field omitted when unset so earlier bytes stay the same, `make_receipt`
version choice, a pinned fixture written by `main` for the rules it rewrites, and the same set of
docs files. The metadata flag sits next to `backend_failures` like `embedding_mismatch`. The adapter
key follows `semantic_relevance` (present only when set).

## Redundancy Check
No existing helper detected a failed vector path for selection; the new helper has one caller and is
tested directly. Version 18 is required because the version 16 floor rule changes. The adapter
`min_score` parameter is required for the flag to be reachable there.

## Wiring Findings
All entry points reach the one decision; DuckDB and PostgreSQL share the pipeline and both report a
mismatch as `VECTOR`; per-request `weights=RRF` works (now tested); receipt readers accept version
18; operation-log readers only read the `receipt` key; CI runs the new file with the `api` and `mcp`
extras and the adapter tests with their extras.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "A fastembed patch release turned PRME's relevance floor off for every
rank fusion caller. Nothing paged anyone, because the only signal was a flag in response metadata
that no dashboard or reader prompt ever showed."

| # | Scenario | Trigger | Likelihood | Impact | Label | Verdict | Reasoning |
|---|---|---|---|---|---|---|---|
| 1 | Floors skipped for as long as the index is stale, and only response metadata shows it | A fastembed upgrade changes the provider's `model_version` (`embedding.py`), so every stored vector mismatches until `prme rebuild` | High | Floored callers get unfiltered lexical hits; `/health` and `prme doctor` stay green | Newly introduced consequence of a pre-existing trigger | Follow-up #160 | Fail-open is the owner's decision; operator-level detection is new work in health and doctor, outside this diff |
| 2 | The reader model never sees that the floor was skipped | Any skipped retrieval consumed through the rendered context or adapter page content | High, given 1 | Unfiltered memories look vetted | Newly introduced | Follow-up #160 (grouped with 1) | A context notice changes context bytes and is a product call |
| 3 | Hints with their own cosine skip the floor | Primary vector search fails, the hint pass's search succeeds | Medium | Up to `cross_scope_top_n` below-floor hints (SDK only) | Newly introduced | Fix now | One condition; fixed and tested |
| 4 | A caller whose only bound was the floor gets the whole pool | `min_score` without `limit` during an outage | Medium | Large responses and receipts | Newly introduced | Accept | This is the owner's "return the results in fused order", and equals the response to the same request without a floor, which callers already receive. Re-checked alternatives: capping to the packed bundle changes the results contract, and a fallback limit adds a new default; both contradict the decision |
| 5 | Vector gaps that raise no error still empty floored results | Legacy vectors lost without payloads (`vector_index.py` only logs), a retrieval during `prme rebuild`, or only the hint pass failing | Medium-low | Empty results with no reported cause | Pre-existing | Follow-up #161 | Needs index-level status reporting and hint-pass diagnostics, outside this diff |
| 6 | One alternate-query cosine keeps the floor | Opt-in reformulation plus a primary-only failure | Low | Primary lexical hits dropped | Newly introduced rule on a pre-existing merge | Accept | Owner's point 3: the floor applies whenever a cosine exists; documented in RFC-0005 |
| 7 | A cosine of 0 or below from another pass counts as no cosine (Agent 1) | Reformulation cosines all at or below 0 during an outage | Very low | Floor skipped | Newly introduced | Accept | Relevance is floored at 0, so no such candidate could pass a positive floor; skipping matches the fail-open intent, and the receipt stays consistent |

Tally: Fix now 1 (3), Follow-up 3 scenarios in 2 issues (1 and 2 grouped, 5), Accept 3 (4, 6, 7),
Dismissed 0.

## Follow-ups Raised
- #160 Rank fusion floors stay switched off unnoticed while vector search is broken (scenarios 1, 2).
- #161 Some vector search failures are not reported, so a rank fusion floor still empties the result
  (scenario 5 and the hint-pass case).

## Evidence gate (offline)
The gate calls `retrieve()` without `min_score` and refuses any backend failure
(`benchmarks/diagnostics/product_packing.py`), so it cannot reach this change by construction; it
confirms that nothing else moved. Before: `main` at `88e6090a`. After: this change (final tree).
Rank fusion never matches the saved weighted contexts, so its "reproduced" column is 0 by design.

| Run | LoCoMo contexts reproduced | LoCoMo all evidence packed | LongMemEval-S contexts reproduced | LongMemEval-S all evidence packed |
|---|---:|---:|---:|---:|
| Defaults, before | 1540/1540 | 983/1536 (64.0%) | 500/500 | 403/470 (85.7%) |
| Defaults, after | 1540/1540 | 983/1536 (64.0%) | 500/500 | 403/470 (85.7%) |
| Rank fusion, before | 0/1540 | 1112/1536 (72.4%) | 0/500 | 414/470 (88.1%) |
| Rank fusion, after | 0/1540 | 1112/1536 (72.4%) | 0/500 | 414/470 (88.1%) |

`gate-compare` gives 0 wins, 0 losses and all ties on every metric for both pairs, and all 2,040
per-question `context_sha256` values are identical before and after, under the defaults and under
rank fusion.

Tests on the final tree: `uv run pytest -q` 4299 passed, 872 skipped (PostgreSQL variants skip
without `PRME_TEST_DATABASE_URL`); `uv run ruff check src/ tests/` and the strict mypy public API
check pass.

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Pinned version 17 fixture | Must Fix | Resolved |
| Hints with a cosine skipped the floor | Should Fix / Break 3 | Resolved |
| Paired evaluation ignored the flag | Should Fix | Resolved |
| Metadata description | Should Fix | Resolved |
| Operation-log key untested | Should Fix | Resolved |
| Version 18 validator layout | Should Fix | Resolved |
| Earlier versions 15 and 17 untested | Should Fix | Resolved |
| Circular HTTP/MCP expectation | Should Fix | Resolved |
| Adapter key absence untested | Should Fix | Resolved |
| HTTP and MCP wording | Should Fix | Resolved |
| Untracked test file | Should Fix | Resolved |
| Evidence gate explanation | Should Fix | Resolved |
| Consider items listed as resolved above | Consider | Resolved |
| Skipped floors invisible outside metadata | Break 1, 2 | Follow-up #160 |
| Unreported vector gaps | Break 5 | Follow-up #161 |
| Whole pool without a limit | Break 4 | Accepted |
| Reformulation cosine keeps the floor | Break 6 | Accepted |
| Non-positive cosines | Break 7 | Accepted |

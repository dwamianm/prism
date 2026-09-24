# Code Review: issue-110-rrf-min-score-relevance

Base branch: `main` (loop override). Owner decision on #110 (2026-09-24): under rank fusion,
`min_score` for results and cross-scope hints gates the semantic cosine of the memory behind each
result; session, episode and evidence context use the cosine of the memory that pulled them in;
the gating value is recorded next to the fused score in the result and the receipt; the fused
ranking, fused scores and saved rank fusion receipts stay as they are; weighted scoring is
unchanged and `min_score=0` stays a no-op; the scale change is documented.

## Files Changed
- `src/prme/retrieval/models.py`: `RetrievalCandidate.semantic_relevance` (rank fusion only,
  omitted from serialization when unset), `RetrievalCandidate.context_relevance` (working state,
  never serialized), `ExcludedCandidate.semantic_relevance`, and `rank_fusion_relevance()`:
  the largest of 0, the candidate's own cosine, the cosine in its score provenance trace, and
  `context_relevance`.
- `src/prme/retrieval/selection.py`: `with_rank_fusion_relevance()`; `select_candidates` compares
  `min_score` with `semantic_relevance` when it is set, and exclusions record it.
- `src/prme/retrieval/pipeline.py`: under `effective_weights.fusion == "rrf"`, sets
  `semantic_relevance` on results and cross-scope hints right before selection.
- `src/prme/retrieval/session_context.py`, `episode_context.py`, `evidence_context.py`: every
  memory that adds or promotes context raises the context's `context_relevance` to its own
  relevance, whether or not its score replaced the context's score (evidence context takes the
  largest relevance in the group; a projected source keeps the relevance of the candidate it
  replaces).
- `src/prme/models/relevance.py`: `ReceiptCandidate.semantic_relevance`; rank fusion receipts are
  schema version 16. Version 16 requires the value on every candidate, each at least `min_score`
  and at least the cosines the receipt records; versions below 16 cannot carry it; saved version 15
  receipts keep their bytes and checksums.
- `src/prme/api/models.py`, `src/prme/api/routes.py`, `src/prme/mcp/server.py`: results carry
  `semantic_relevance` under rank fusion; `min_score` descriptions explain the scale.
- `src/prme/integrations/langchain.py`, `llamaindex.py`: result metadata carries
  `semantic_relevance` under rank fusion.
- `src/prme/retrieval/config.py`, `src/prme/storage/engine.py`: descriptions and docstrings.
- Docs: RFC-0005 Section 7.2 and the explicit selection amendment, RFC-0006, RFC-0017,
  `docs/HTTP-API.md`, `docs/PACKING.md`, `docs/INTEGRATION.md`, `AGENTS.md`, `CHANGELOG.md`,
  `documentation/configuration.md`, `documentation/http-api.md`, `documentation/mcp-server.md`.
- Tests: new `tests/test_rank_fusion_min_score.py` and the pre-change fixture
  `tests/fixtures/relevance/receipt-v15-rrf.json` (written by `make_receipt` on `main`, checksum
  pinned); `tests/test_rank_fusion.py` (version 16, rank constant check on versions 15 and 16);
  `tests/test_integrations_langchain.py`, `tests/test_integrations_llamaindex.py`.

## Approach Summary
Premise: a rank-fused score has no absolute meaning, and the semantic cosine is the only absolute
signal available (lexical scores are min-max normalized per query in `candidates.py`, so the top
keyword hit is always 1.0). Every entry point that accepts `min_score` (HTTP, MCP, SDK, engine,
both storage backends) reaches the single `select_candidates` call in `pipeline.py`, and cross-scope
hints use the same call, so one gate covers them all. The value is computed once, right before
selection, from fields that do not change afterwards, and `make_receipt` records the same value.

Alternatives considered:
- Larger of semantic and lexical, or a `semantic + lexical` relevance floor inside fusion: rejected
  in the issue thread (lexical is per-query normalized); the owner chose the cosine.
- Rejecting `min_score` under rank fusion: the owner's fallback option, not chosen.
- Reading the inherited cosine only from score provenance: rejected during review. Provenance
  changes only when the inherited score beats the candidate's own, so a neighbor already in the
  pool with a strong keyword score, or a projected source, lost the relevance of the memory that
  pulled it in (Agents 1 and 7). `context_relevance` records it at every inheritance step.
- An optional field on version 15 receipts instead of version 16: rejected. Each recorded receipt
  feature has had its own version with "cannot claim it" rules, and version 15's `min_score` was
  a floor on the fused score, so the new "every value is at least `min_score`" rule cannot apply
  to it.

Shared state touched: result, hint and exclusion objects (new field, omitted under weighted
scoring), rank fusion receipts in the `operations` table (version 16, checksummed; readers:
`storage/relevance.py`, `storage/citations.py`, learning, which skips rank fusion receipts), and the
operation log's `selection_excluded`. Weighted outputs keep their bytes.

## Must Fix
None.

## Should Fix (all resolved)
- **Context lost the relevance of the memory that pulled it in** (Agents 1, 7): see Break
  Scenario 3. Resolved with `context_relevance`, tested for session, episode, projection and
  augmentation, including chained and replaced cases.
- **Two RFCs still said version 15** (Agents 4, 6): RFC-0006 and RFC-0017 updated.
- **`models/relevance.py` imported selection logic** (Agents 3, 4): `rank_fusion_relevance` moved to
  `retrieval/models.py`; `make_receipt` computes it with that single function.
- **Stale test name, version 15 rank constant rule untested** (Agents 3, 4): renamed and
  parametrized over the version 15 fixture and a version 16 receipt.
- **`relevance_score` overloaded "relevance"** (Agents 3, 4, 5): renamed `semantic_relevance`; the
  description says it excludes the lexical score, unlike `relevance_floor`.
- **Episode and evidence inheritance untested** (Agent 3): added.
- **No test for per-request `weights=RRF` on a weighted engine or for reading a version 16
  receipt over HTTP** (Agent 6): added.

## Consider
- Resolved: bounds on `ExcludedCandidate.semantic_relevance` and the HTTP field; error message
  wording ("Versions 16 and later"); redundant `is not None` check; `rank_fused` reuse; replayable
  lower bound (relevance at least the cosines the receipt records); docs now say "selection
  exclusion" (epistemic exclusions carry none); duplicate tests removed; field description
  shortened; HTTP/MCP test asserts MCP results in the weighted pass; adapters expose the value.
- Not taken: bounding `RetrievalReceipt.min_score` (pre-existing field, and a constraint could
  reject stored receipts written before `validate_selection` existed); hashing stored receipt
  bytes instead of the re-serialized model (pre-existing, unrelated to this change); a metadata
  field naming the floor's basis (results and exclusions carry the value, and the docs state the
  rule); a shared MCP test helper (pre-existing duplication); explicit gating-mode parameter on
  `select_candidates` (the pipeline sets the value on all candidates or none); avoiding the copy of
  the pool (measured about 4 ms per rank-fused retrieval).

## Security Audit Results
| Area | Result | Details |
|---|---|---|
| Secrets or PII in logs and responses | PASS | No new logging; the value describes a result the caller already receives |
| Cross-scope leakage | PASS | Hint exclusions are discarded; hints stay same-owner and SDK-only |
| Tenant isolation on receipt reads | PASS | Read paths unchanged; owner and checksum still verified |
| `min_score` validation | PASS | Unchanged; the relevance is never negative or NaN |
| Receipt deserialization | PASS | Crafted receipts with misplaced, missing, negative, non-finite or too-low values are rejected |
| Checksum stability | PASS | Weighted and version 15 bytes unchanged (pinned fixture) |
| Unsafe deserialization | PASS | Pydantic JSON only |
| Credentials in fixtures | PASS | Synthetic fixture |

## Pattern Consistency Assessment
The new fields follow the `exclude_if` pattern of `ScoreProvenance.rank_fusion` and
`ScoringWeights.fusion`; the version bump follows versions 9 to 15 (Literal, validators,
`make_receipt`, docs in the same set of files #82 touched, a pinned pre-change fixture like
`receipt-v4.json`); MCP adds the key only when present, like `context_references`.

## Redundancy Check
No existing helper exposed a base or inherited cosine. The stored value is required by the owner's
instruction and cannot be rebuilt from receipt data once context replaces a candidate's trace.
Duplicate tests were merged.

## Wiring Findings
All entry points reach the one gate; PostgreSQL shares the pipeline and cosine scale; per-request
`weights=RRF` works; receipt read paths handle version 16 and saved version 15; CI collects the new
tests; ruff and the public typing check pass. Harnesses that pass a floor (`melt_sut.py` 0.05) run
weighted scoring and are unaffected.

## Break Scenarios (adversarial)
Pre-mortem headline (Agent 7): "The rank-fusion floor became a cosine gate that lets everything
through on the default embedder, returns nothing whenever the vector index is down, and drops the
session and evidence context it said it would inherit."

| # | Scenario | Trigger | Likelihood | Impact | Label | Verdict | Reasoning |
|---|---|---|---|---|---|---|---|
| 1 | Cosine scale depends on the embedding model | Rank fusion, floor 0.3, default `bge-small-en-v1.5` | High | A low floor keeps unrelated memories | Newly introduced | Fix now (docs) | Measured locally: unrelated pairs 0.33 to 0.45, related 0.85 to 0.87, so a floor such as 0.5 does separate them; the scale is now absolute per model, unlike the fused score. RFC-0005 and the changelog now say ranges depend on the model and give the default model's range. Choosing a recommended floor is the calibration RFC-0005's selection amendment already calls for |
| 2 | Vector failure or embedding mismatch empties every floored retrieval | Rank fusion, floor above 0, vector path fails or mismatches | Medium | Callers abstain on every question until repair | Newly introduced | Follow-up #150 | Alternatives re-checked: comparing the fused score again brings back #110, lexical scores are not absolute, and no cosine exists without the vector path. Failing open or closed is a product decision; documented in RFC-0005 now |
| 3 | Context keeps its own low cosine when its own score stays higher, and projected sources lose theirs | Session neighbor already found by keyword; evidence projection; chained context | Medium | The answer turn or source passage is dropped while weaker neighbors pass | Newly introduced | Fix now | Fixed with `context_relevance` at every inheritance step; tests fail without it |
| 4 | LangChain and LlamaIndex expose only the fused score | Downstream score threshold under rank fusion | Low to medium | Same class of problem outside `min_score` | Pre-existing | Fix now (metadata) plus Accept (score) | Metadata now carries `semantic_relevance`. `NodeWithScore.score` stays the fused score, the same as the HTTP `score`; changing what a score means is a contract decision, and the adapters never pass `min_score` |
| 5 | With query reformulation, relevance is the cosine to a paraphrase | Opt-in reformulation plus rank fusion and a floor | Low | Paraphrase drift can admit a memory | Newly introduced for gating | Accept | The ranking already uses the same cosine, paraphrases are generated to mean the same as the query, and before this change the floor compared a rank with no absolute meaning. Tracking the original query's cosine separately would need a second score through the reformulation merge |
| 6 | A rollback cannot read version 16 receipts | Downgrade after writing rank fusion receipts | Low | Receipt reads and learning snapshots fail | Newly introduced | Accept | Every receipt version bump behaves this way, and rank fusion is unreleased |
| 7 | Cross-scope hints reach the gate without a cosine | Requested scopes fill the hint pass's 10 vector hits | Medium | Relevant other-scope hints dropped under a floor | Pre-existing, now more consequential | Follow-up #151 | The fix changes hints under default settings, so it needs its own option or evidence check; documented in RFC-0005 now |

Tally: Fix now 3 (1, 3, 4), Follow-up 2 (2, 7), Accept 2 (5, 6 and the score part of 4), Dismissed 0.

## Follow-ups Raised
- #150 Under rank fusion, a vector search failure makes every retrieval with a min_score floor
  come back empty.
- #151 Cross-scope hints spend their vector hits on memories from the requested scopes.

## Evidence gate (offline)
The gate does not pass `min_score`, so it cannot move with this change; it confirms nothing else
moved. Before (commit `1f344583`) and after (this change, final tree). Rank fusion never matches
the saved weighted contexts, so its "reproduced" column is 0 by design:

| Run | LoCoMo contexts reproduced | LoCoMo all evidence packed | LongMemEval-S contexts reproduced | LongMemEval-S all evidence packed |
|---|---:|---:|---:|---:|
| Defaults, before | 1540/1540 | 983/1536 (64.0%) | 500/500 | 403/470 (85.7%) |
| Defaults, after | 1540/1540 | 983/1536 (64.0%) | 500/500 | 403/470 (85.7%) |
| Rank fusion, before | 0/1540 | 1112/1536 (72.4%) | 0/500 | 414/470 (88.1%) |
| Rank fusion, after | 0/1540 | 1112/1536 (72.4%) | 0/500 | 414/470 (88.1%) |

## Resolution Status
| Finding | Severity | Status |
|---|---|---|
| Context relevance lost on inheritance and replacement | Should Fix / Break 3 | Resolved |
| RFC-0006 and RFC-0017 version mentions | Should Fix | Resolved |
| Layering of `rank_fusion_relevance` | Should Fix | Resolved |
| Test name and version 15 rank constant coverage | Should Fix | Resolved |
| Field name | Should Fix | Resolved (`semantic_relevance`) |
| Episode and evidence tests | Should Fix | Resolved |
| Per-request and HTTP receipt read tests | Should Fix | Resolved |
| Consider items listed as resolved above | Consider | Resolved |
| Embedding model dependence | Break 1 | Resolved (docs) |
| Adapter metadata | Break 4 | Resolved |
| Vector failure behavior | Break 2 | Follow-up #150 |
| Cross-scope hint vector pass | Break 7 | Follow-up #151 |
| Reformulation paraphrase cosine | Break 5 | Accepted |
| Rollback reading version 16 | Break 6 | Accepted |

`gate-compare` gives 0 wins, 0 losses and all ties on every metric for both pairs, and every one
of the 2,040 per-question `context_sha256` values is identical before and after, under the defaults
and under rank fusion.

Tests on the final tree: `uv run pytest -q` 4230 passed, 859 skipped (PostgreSQL variants skip
without `PRME_TEST_DATABASE_URL`); `uv run ruff check src/ tests/` and the strict mypy public API
check pass.

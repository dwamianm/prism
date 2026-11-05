# Review: issue #399, relation routing (`enable_relation_routing`)

## Review Scope

- Base: `main` at `2418444d` (includes #425, `enable_owner_profile_context`).
- Reviewed snapshot: `30c749f84bee0e9423beab6b0ab5098caf799ce8099b5dc7dc6e8023a14486fd` (working tree with the new
  files, before the fixes below).
- Tier: L. `review-plan` proposed M (NEW_SYMBOLS); SECURITY and DATA were added by hand because the walk builds a raw
  SQL statement and reads the graph tables directly.
- Roster: two reviewers, by the owner's standing light-review choice (2026-10-19): core (Opus) with the security (2)
  and data safety (7) checklists merged into it, and adversarial (Opus). Checklists 3, 4 and 5 are merged into the
  core review as the policy says. Checklist 6 (infrastructure) is not applicable: no service, deployment or
  environment wiring changed.
- All required checklists completed: 1, 2 (merged), 3, 4, 5, 7 (merged), 8.

## Files Changed

- New: `src/prme/retrieval/relations.py`, `src/prme/retrieval/relation_walk.py`, `docs/RELATIONS.md`,
  `benchmarks/diagnostics/relation_coverage.py`, `tests/test_relation_routing.py`.
- Changed: `src/prme/config.py`, `src/prme/config_audit.py`, `src/prme/retrieval/config.py`,
  `src/prme/retrieval/execution.py`, `src/prme/retrieval/models.py`, `src/prme/retrieval/pipeline.py`,
  `src/prme/retrieval/scoring.py`, `src/prme/storage/engine.py`, `src/prme/client.py`, `src/prme/mcp/server.py`,
  `src/prme/api/models.py`, `src/prme/api/routes.py`, `benchmarks/graph_oracle/blind_run.py`, `CHANGELOG.md`,
  `docs/INDEX.md`, `docs/RFC-0005-Hybrid-Retrieval-Pipeline.md`, `documentation/configuration.md`,
  `tests/test_config_audit.py`.

## Approach Summary

A relation vocabulary (the graph oracle's sixteen relations plus `has`), a derived cue lexicon, a predicate
normalizer, built-in concepts with a per-call extension (`retrieve(concepts=...)`) and a router live in
`relations.py`. For a routed question, the pipeline (Stage 2.7) walks from the question's seed entities and, for a
first-person question, the owner's entities, over the claims whose normalized relation the route accepts, in one SQL
statement on DuckDB and PostgreSQL. The claims it keeps form a fourth rank fusion list weighted by
`scoring.rrf_route_weight` (2.0 when unset). The weight, `route_rank` and the `relation_routing` feature are
recorded only on a retrieval the walk acted on, so every other retrieval keeps its bytes. Off by default.

## Must Fix

None.

## Should Fix (all fixed)

1. Text-mention links (`enable_mention_links`, on in the alpha) were read as a claim's object end, so a person a
   claim's text only names could receive the claim as an answer. Fixed: `_parse` skips `basis: text_mention` edges;
   test `test_a_text_mention_is_neither_end_of_a_claim` on both backends.
2. Seeds were sorted by UUID before the 16-seed cap, so the cap could drop the owner. Fixed: the owner's entities
   first, then the question's seeds, capped in the pipeline; the receipt records the seeds walked. Test
   `test_the_owner_seeds_come_first_under_the_seed_cap`. (Also adversarial scenario 4.)
3. The docs said the route list holds "claims and memories", but `ingest()` writes no claim-to-memory
   `DERIVED_FROM` link. Fixed in `docs/RELATIONS.md`, the field descriptions, the configuration page and the module
   docstring: memories join only through a `DERIVED_FROM` link, which typed graphs write.
4. The docs said a failed walk was byte-identical, but it records `backend_failures.ROUTE`. Fixed the docs; the test
   now checks that the rest of the receipt equals the option-off receipt.
5. `concepts` was on `MemoryEngine.retrieve()` only. Fixed: added to `POST /v1/retrieve` (validated in the request
   model), the MCP `memory_retrieve` tool and `MemoryClient.retrieve()`, as #425 did for `speaker`.

## Consider

- 6 RFC-0005 did not describe the fourth list: a paragraph was added.
- 7 Concept keys that collide after singularization made the receipt disagree with the route: now rejected.
- 8 "Who is my sister's husband?" collapsed to one step: a people word after another people word's possessive is now
  the next step; tests added.
- 9 The owner rule differs from #398's per-scope rule for a multi-scope request: documented in the docstring and
  `docs/RELATIONS.md`; accepted.
- 10 The 128-claim window is relation-blind: documented; Follow-up (below).
- 11 Bounded extra per-request work (a second seed lookup, owner scans, hydration before the 32 cap): accepted.
- 12 The 32-candidate cut breaks ties by node id: commented at the cut; accepted.
- 13 The config audit reports routing active under weighted fusion: accepted (routing reads only under rank fusion,
  as documented).
- 14 Everyday cue words route questions: Follow-up (below).

## Security Audit Results

| Area | Result | Details |
|---|---|---|
| SQL injection | PASS | Seed ids pass `uuid.UUID`, scopes `Scope()`, the JSON key a regex over constants; the owner id is bound as `$1`; a test refuses an injection string |
| Tenant isolation | PASS | Every edge read filters `user_id`; claims filter user, scope and lifecycle; hydrated nodes are filtered again |
| Privacy-protected reads | PASS | Stage 2.7 is skipped when the pipeline has a privacy context |
| Secrets and PII in logs | PASS | One warning without query text or ids |

## Pattern Consistency Assessment

Follows `rrf_graph_seed_count` (field, `RANK_FUSION_OPT_INS`, `version_id` label, audit gate, receipt validation) and
`enable_owner_profile_context` (config field, both engine construction sites, per-request execution feature,
request plumbing, docs and CHANGELOG). Gaps found by the core review (request plumbing, RFC text) are fixed.

## Redundancy Check

`stem`, `_verb_forms` and `derive_cues` mirror `benchmarks/graph_oracle/gold.py`; a parity test keeps them equal, and
the product cannot import from `benchmarks`. First-person detection reuses `is_first_person_question`.

## Wiring Findings

`enable_relation_routing` is passed at both pipeline construction sites in `engine.py`; `concepts` flows from the
HTTP route, the MCP tool and the client to the pipeline; the new modules are in the feature identity's hashed
sources.

## Data Safety and Read/Write Proof

No schema change and no writes: two SELECT statements, `get_nodes` and `query_nodes`. The read is bounded (16 seeds,
128 claims per seed, 64 second-layer entities, 32 claims each, 32 candidates). No new persisted field; the receipt
fields `rrf_route_weight` and `route_rank` are omitted when unset, so earlier receipts keep their bytes and validate.

## Break Scenarios (adversarial)

Pre-mortem headline (verbatim): "relation routing was turned on along with the other new features. It put the owner's
"takes", "calls" and "likes" claims above the answers to ordinary questions, and it still missed the family facts in
every store whose owner held more than 128 claims."

| # | Scenario | Label | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| 1 | Everyday derived cue words ("take", "call", "like", "keep") route ordinary questions and the route list outranks the answer; the blind set lost 73 to 68 of 86 in the top 3 | newly introduced, option off | High once on | Follow-up | The issue specifies a derived cue lexicon; changing the routing vocabulary needs a fresh blind draw (the current one is spent) and is a precondition for any default flip |
| 2 | The 128 newest claims per seed are cut before the relation filter, so an old family claim on a busy owner is never read | newly introduced, option off | High on mature stores | Follow-up | Needs a relation-aware statement (bound predicate list or a second statement) and a measurement on LoCoMo `ingest()` packs |
| 3 | A positive `min_score` under rank fusion removes every walk-only candidate (semantic similarity 0) | pre-existing rule, newly exposed | Moderate to low | Follow-up | Documented now in `docs/RELATIONS.md`; an exemption changes selection semantics shared with graph-only candidates |
| 4 | Seeds sorted by UUID before the cap can drop the owner | newly introduced | Low to moderate | Fix now | Fixed (core item 2) |

## Follow-ups Raised

1. #428: relation routing sends ordinary questions to the graph through everyday cue words (scenario 1, core 14).
2. #429: the relation walk misses old claims on entities with more than 128 claims (scenario 2, core 10).
3. #430: a positive `min_score` drops every candidate only the relation walk found (scenario 3).

## Resolution Status

| Finding | Status |
|---|---|
| Core 1 text mentions | Fixed, tested |
| Core 2 / adversarial 4 seed order | Fixed, tested |
| Core 3 docs on memories | Fixed |
| Core 4 docs on failed walk | Fixed, tested |
| Core 5 request plumbing | Fixed, tested (HTTP model) |
| Core 6 RFC-0005 | Fixed |
| Core 7 concept collisions | Fixed, tested |
| Core 8 chained possessive | Fixed, tested |
| Core 9, 11, 12, 13 | Accepted, documented |
| Core 10, 14 / adversarial 1, 2, 3 | Follow-up: #428, #429, #430 |

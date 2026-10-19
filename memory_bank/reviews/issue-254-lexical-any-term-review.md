# Review: PostgreSQL lexical search matches any term (prism-monorepo#254)

Branch `issue-254-lexical-any-term`. This is unrelated to this repository's own
issue 254 (batched extraction).

## Review Scope

- Base commit: `d92901ecc9ba195010c074aefa0bfc7ea4f586f4` (origin/main).
- First reviewed snapshot: `4b76556b3e6fc0d24abb3791fd6fb966e32c076543e7610efec38ce5032fa51c`.
- Second snapshot, re-reviewed for the changed scope: `67edebe5fcfbc71c9007b8970327af69afbf9027dd3330a8cbb67b07e5734a4a`.
- Final snapshot, re-reviewed for the changed scope: `e8e3d0fd372dc2a2cf70980b777b35129670f6b1e0fa8d3fde5502fc9016ccab` (later only a test comment changed).
- Tier: L. Predicates: DATA (storage read path) and SECURITY (tenant-scoped SQL), both added by hand; NEW_SYMBOLS detected; INFRA and PERSISTED_FIELD not applicable.
- Reviewers: core (checklists 1, 3, 4 and 5 merged), security (2), data safety (7), adversarial (8). Wiring (6) not applicable: no new config, routes, migrations or deploy changes.
- The core reviewer re-reviewed the delta twice after the fixes below.

## Files Changed

- `src/prme/storage/pg/lexical_index.py`: `PgLexicalIndex.search` and the new `MAX_QUERY_TERMS`.
- `tests/test_pg_lexical_index.py`: eight new tests.
- `tests/test_rank_fusion_recency.py`, `tests/test_rank_fusion_vector_failure.py`: comments only.
- `docs/RFC-0005-Hybrid-Retrieval-Pipeline.md`, `CHANGELOG.md`.

## Approach Summary

`plainto_tsquery` ANDs every term, so a question that no memory holds word for
word found no lexical candidates. The search now takes the first 32 distinct
terms of `plainto_tsquery`. With more than one term, terms that match at least
half of the searched documents are left out while another term matches. When a
hit matches every searched term, only such hits are returned, as before. Otherwise
any-term hits are returned, ranked by distinct matched terms and then by
`ts_rank_cd`. Single-lexeme queries keep the same hits and scores.

## Must Fix

None open. The first re-review found that the full-match rule counted only the
terms that select candidates, so "alex learning" also returned "Jane is learning
piano." once "alex" was common. Matched terms and the full-match rule now count
every query term (up to 32). Over 400 documents where 80% name the owner, all
286 two- and three-word queries that had hits under the old all-terms search
return the same hit set.

Also resolved: The security reviewer's blocking finding (no cap on query terms, about
50 s against 0.15 s for a 2,000-term query on 12,000 documents) is fixed by
`MAX_QUERY_TERMS`; a 1,002-term query now takes 22.7 ms against 23.6 ms on the base
at 3,000 documents, where parsing the query dominates.

## Should Fix

1. Cost grows with terms times corpus size (core 1): fixed by the term cap.
2. The denominator counts every searched document on each multi-term query (core 2):
   accepted. It is one indexed pass over the user's rows, measured at 3,000
   documents as 7.5 ms against 2.0 ms for a two-term query. The code comment no
   longer claims that every match uses the GIN indexes.

## Consider

- Bigram and multi-word entity searches now get any-term semantics when no hit
  holds every word (core 3): see S4.
- Multi-term path with filters and an index copy, and the weekends question (core 4): tests added.
- Residual ranking in the larger synthetic set (core 5): reported in the PR. The
  memory ranked above Rust shares two query words with the question.
- Documentation accuracy (core 6): fixed (threshold is now "at least half", which
  matches where the Robertson-Sparck Jones idf stops being positive; PostgreSQL-only
  rules are stated; multi-lexeme words are described).
- `searched_ids` sentinel (core 7): now `str | None`.

## Security Audit Results

| Area | Result |
|---|---|
| Secrets or PII in logs or responses | PASS |
| Tenant isolation, hit path | PASS |
| Tenant isolation, per-term and total counts | PASS (same user, type and scope filters; probed across users) |
| SQL injection through f-string assembly | PASS (only constants and placeholder numbers) |
| tsquery injection through split and recast | PASS (23 hostile inputs round-trip) |
| Unsafe deserialization | N/A |
| Resource exhaustion from many terms | FIXED (term cap) |

## Pattern Consistency Assessment

Compared with the Tantivy search (`src/prme/storage/lexical_index.py`, `_do_search`):
both now match any term and apply user, type and scope as hard filters. The
common-term rule, the full-match rule and the matched-term tiers are PostgreSQL
only, which the RFC now says.

## Redundancy Check

No dead code. `searched_ids` replaces the two hand-built WHERE lists.

## Wiring Findings

Not applicable. No configuration, registration, migration or deployment change.

## Data Safety and Read/Write Proof

No schema, index, write path or durable field changes, so the writer and reader
proof is not applicable. Receipts store normalized lexical scores and replay from
stored values, so receipts written before the change replay unchanged.

## Break Scenarios (adversarial)

Pre-mortem headline: "Postgres lexical search learned OR, and rank fusion let
vector search overrule every memory that matched the whole query."

| # | Scenario | Origin | Likelihood | Verdict | Reasoning |
|---|---|---|---|---|---|
| S1 | A memory holding every query word loses its lexical lead under rank fusion to partial matches the vector channel prefers | New | High | Fix now | Regression from this change. Fixed: when a hit holds every searched term, only such hits are returned, so former AND hits keep the old candidate set. Test `test_full_matches_keep_partial_matches_out`. |
| S2 | The common-term rule counts derived entity nodes and inactive nodes, so on stores with extraction the owner's name can stay informative | New (rows were already searchable) | Medium | Follow-up (dwamianm/prism#292) | The fix belongs with applying the derived-entity lexical policy to PostgreSQL search, which changes the hit set and is its own change. |
| S3 | Hyphenated words, URLs and emails count as several matched terms | New | Medium | Accept | With S1 fixed, this only orders hits for queries that had no lexical candidates at all before; under the default rank fusion it moves a hit by one lexical rank. Alternatives rechecked: counting a compound once needs a parse of the tsquery structure beyond splitting, which the cap and S1 rule do not need. |
| S4 | Aggregation bigram scans with no hit holding both words reach the 50-row limit and mark coverage as candidate-limited | New on PostgreSQL, existing on DuckDB | Medium | Follow-up (dwamianm/prism#293) | The right fix changes the limit flag in the retrieval pipeline for both backends. |
| S5 | Long queries hit a latency cliff | New | Low now | Fix now | Fixed by the term cap (same finding as security). |
| S6 | Tests missing for S1 and S2, full suite not yet clean | New | n/a | Fix now (tests), Follow-up (S2 tests go with #292) | S1 test added. On the final revision the full suite with PostgreSQL gave 6,366 passed, 100 skipped and 2 failed, both unrelated: `test_checkout` expects a plain clone (it passes in one; this run was in a worktree), and the unscoped operator scan test reads at most 10,000 rows of a test database that my probes had filled to 27,772 nodes (it passes on a fresh database). The earlier segmentation fault in a DuckDB worker thread did not recur. |

Tally: Fix now 3 (S1, S5, S6), Follow-up 2 (S2, S4), Accept 1 (S3), Dismissed 0.

## Follow-ups Raised

- dwamianm/prism#292: S2.
- dwamianm/prism#293: S4.

## Resolution Status

| Finding | Status |
|---|---|
| Security 1, term cap | Resolved |
| Core Should Fix 1 | Resolved |
| Core Should Fix 2 | Accepted with measurements |
| Core Consider 3 | Follow-up #293 |
| Core Consider 4, 6, 7 | Resolved |
| Core re-review Must Fix (full-match rule) | Resolved |
| Core re-review Should Fix (weekends assertion) | Resolved |
| Core Consider 5 | Reported in the PR |
| S1, S5, S6 | Resolved |
| S2, S4 | Follow-ups |
| S3 | Accepted |

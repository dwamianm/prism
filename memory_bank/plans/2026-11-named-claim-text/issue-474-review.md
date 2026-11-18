# Issue 474 self-review

Status: COMPLETE (full-suite failure reproduced on unchanged base)

Base: origin/main, fcccc3cc54b8de7a7ab8580bdf014489d24f8692.
Reviewed implementation snapshot: `03177f77580bc37f030a5b781537bf31db6e801a5c1b3a24885b566f79c7270a`.
Only this review transport artifact was excluded to avoid a self-referential hash.
Review tier: L (DATA: derived text and global derivation fingerprint).
No independent reviewers: explicit approved budget was one implementer and zero
reviewers. This is a self-review, not an independent review claim.

Correctness and coverage: the four reported examples now use the existing label
fallback. Thirteen pure-function cases cover new guards; six genuine-pronoun
neighbors and eight enabled/disabled ingestion cases verify the boundary.
The single pipeline call site remains unchanged; batched and ordinary ingestion
share it. Existing named-text tests cover carried names, batch ingestion, folding,
and metadata. No new dependency, configuration, schema or publication path.

Data/security: only newly prepared claim text changes. Evidence and source events
stay verbatim; old saved plans keep their bytes. DERIVATION_VERSION moves 2 to 3
as required; the refreshed fixture changes only the version and six fingerprints.
All older derivations become stale on next scan, including option-off stores;
existing rows change only on reingestion. No live reingestion was performed.
Ownership, authorization, logs, escaping and external I/O are unchanged.

Adversarial scenarios examined:
1. Present breastfeed versus past breastfed: label versus correct rewrite.
2. Several modifiers before noun mine: determinant found beyond two words.
3. Negation and copula before possessive mine: still rewrites.
4. Punctuation separating clauses: scan stops, preserving possessive mine.
5. Reported speech to Henry versus clause-initial Henry I: distinct behavior.
6. Denver before first-person I: no arbitrary proper-name guessing.
7. Lowercase book/class relative clauses: existing took/bought rewrites pass.
8. Flag off: all four new stored claims and evidence remain original text.

Residual accepted limits: lexical English heuristics, a finite regnal-name list,
clause-initial regnal matching, and conservative label fallback. Lowercase title
heads followed by other verbs are outside this mitigation. No general grammar
or name-recognition claim. Existing limitations recorded in #409 are not added
to #474 acceptance criteria. No new blocker found within the approved scope.

Validation evidence:
- Initial targeted collection failed: missing optional FastAPI dependency;
  `/tmp/issue474-targeted.log`. No tests executed. Installed declared CI extras.
- Fixture refresh: 2 passed, `/tmp/issue474-fixture.log`.
- Targeted named-text, derivation and reingest: 185 passed, 47 skipped,
  `/tmp/issue474-targeted-ready.log`. PostgreSQL service not configured.
- Full Ruff: passed, `/tmp/issue474-ruff-full.log`.
- Strict public-API mypy: passed, `/tmp/issue474-mypy.log`.
- Configured `uv run pytest -q`: 1 failed, 7834 passed, 1747 skipped,
  127 xfailed in 2542.30s; exit 1, `/tmp/issue474-full.log`.
- Sole failure: `tests/test_checkout.py::TestMainCheckout::test_a_plain_clone_resolves_to_itself`
  assumes the checkout is a plain clone; this linked worktree correctly resolves
  its main checkout to `/Users/dwamianm/Sites/prism` instead of itself.
- The exact test also fails on unchanged fcccc3cc in a separate detached worktree,
  using the same interpreter/dependencies: 1 failed in 0.09s, exit 1,
  `/tmp/issue474-base-checkout.log`. No helper or test changes were made.
- Held PR delivery approved with the matched-base failure disclosed. No full
  rerun; hosted CI verification remains separate.

Implementation repair cycles: zero. No paid evaluation or default flip.

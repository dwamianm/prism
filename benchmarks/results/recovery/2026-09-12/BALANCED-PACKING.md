# Explicit balanced packing: implementation verification

Root commit `58104c1` exposes `PackingConfig(multipath_ordering="balanced")`.
The default remains density. Balanced reserves the highest-scored ordinary
multi-path candidate, then orders the rest by score / full-entry-tokens**0.25.
Instructions, pinned memories, active tasks, full-context token accounting and
fidelity checks retain their existing behavior. A reserved candidate can still
be represented only by a reference or excluded if its text cannot fit.

Balanced retrievals use version 5 receipts with explicit policy and execution
provenance. Density/score continue to use version 4; versions 1–4 cannot claim
balanced and retain canonical bytes. Ranking replay remains ranking replay,
not packing reconstruction. The public configuration and a runnable synchronous
example are documented in [PACKING.md](../../../../docs/PACKING.md).

## Verification

The clean frozen implementation is `7009326`. All 129 Python package files in
root `58104c1` match it byte-for-byte.

- Focused source tests: **82 passed, 1 skipped**, 10.54 seconds, native exit 0.
- Fresh Python 3.13 wheel tests: **82 passed, 1 skipped**, 17.43 seconds, native
  exit 0; all 129 installed Python package files match frozen source before and
  after testing. This includes native PostgreSQL and DuckDB receipts, feedback,
  restart and legacy serialization checks.
- The first installed attempt failed because a child process could not import
  a repository-only test helper: **81 passed, 1 failed, 1 skipped**. It failed
  before creating an engine. Its original report/log are retained. Adding only
  the frozen repository root to `PYTHONPATH` made helpers available; the package
  still resolved to site-packages in parent and child. No wheel, product source
  or test assertion changed between attempts.
- Exact guide example ran from the installed wheel with real default embeddings;
  reopening the pack recovered its version 5 balanced receipt.
- Public density, score and balanced packing reproduced **4,500 exact contexts**
  over all 500 saved questions at three budgets. Rendered text, token counts,
  source/representation metadata and unchanged input candidates were checked
  against the complete, independently verified prior studies. Native exit 0.
- Repository Ruff check passed. Strict public-client mypy check passed.

The complete scenario command returned **70/74 checkpoints, exit 1**. Failures
were `bi_temporal` (GraphQL outside the top five), `changing_facts` (old MySQL
ranked above PostgreSQL), `consolidation` (Kubernetes outside the top five), and
`eval_supersedence_handling` (old CEO ranked above the correction). These checks
use default density and returned rankings, not balanced rendered contexts.

A clean pre-change `b08ec22` run of those four scenarios returned **16/19, exit 1**:
the first, second and fourth failures reproduced. Consolidation passed there,
with different scores on related Python facts. Its run-to-run difference remains
unresolved; do not attribute it to the packing change or erase the failed result.
The simulation CI gate has not passed.

The full suite against live PostgreSQL completed with **3,198 passed, 85 skipped**,
630.38 seconds, native exit 0. The temporary test database was removed afterward.
This does not turn the separately failing simulation gate into a pass. The
[verification manifest](balanced-packing-58104c1.json) retains the native exits,
log/artifact hashes and all four failed simulation checkpoints.

## Evidence limits

This exposes the fixed policy studied previously, rather than selecting another
variant from these checks. Implementation parity is not new quality evidence.
The 119-question development and previously examined 381-question regression
studies use raw conversation-turn candidates. They do not establish answer
accuracy, generalization to extracted/structured memories, or competitive
leadership. Individual source-retention losses remain in the original reports.

The separate head1 answer trial failed on its owned Ollama service before
judging, with a GPU out-of-memory observation. It does not validate balanced
answer quality. No production default or benchmark acceptance guard changed.

See [installed verification](balanced-packing-installed-final-verification.json),
[retained first installed attempt](balanced-packing-installed-verification.json),
[context parity completion](balanced-packing-parity-completion.json), and
[owned reader failure](../../research/2026-09-12/packing-head-reader-owned-incomplete.json).

# MemConflict development smoke replay — 2026-09-12

The [dataset audit](dataset-audit.json) covers the pinned release documented in
the [adapter protocol](../../../integrations/MEMCONFLICT.md). It found 36 unusable
messages across the dataset. The selected development profile contains none.
Five profiles are assigned to development and 25 to a reserved test split.

The [baseline summary](raw-notes-64b777d-summary.json) records a clean `64b777d`
run with the local `qwen3.5:4b` reader and real BGE embeddings. All three selected
questions and all nine reader calls completed; the supervised process exited
zero. Histories contain 518, 1,510 and 2,028 messages respectively. Raw inputs,
contexts and outputs remain in the local report; committed hashes and source IDs
identify that report without redistributing the upstream dialogue.

These are **unjudged development observations, not benchmark accuracy**:

| Conflict type | PRME product context | BM25 context | Empty memory |
|---|---|---|---|
| Dynamic | Reader abstains; relevant retrieved passage omitted from context | Reader answers yes | Abstains |
| Static | Gives labeled university without acknowledging labeled contradiction | Same limitation | Abstains |
| Conditional | States commuting and professional-study conditions | States commuting only | Abstains |

The dynamic question exposes a packing failure: the passage at source `s5:t1`
ranked fourth among PRME candidates but did not enter its product context.
The final context instead contains many short notes unrelated to residence.
The current multi-path score-per-token ordering is a candidate cause to test
with frozen candidate snapshots, rather than immediately changing a default.
BM25 uses a different packer and less metadata, so this comparison does not
isolate ranking, packing order or formatting overhead.

The static label asserts a correct university and a conflicting alternative.
Neither reader explicitly acknowledged the conflict. That observation is not
an independently verified judgment that the underlying dialogue establishes
the label's preferred truth. Synthetic source support still needs an audit.

The input dates are anchored at midnight UTC. The product context in this run
renders DuckDB timestamps in the machine's local offset, sometimes displaying
the previous calendar date; the full report retains those timestamps. Future
portability and packing comparisons must account for this rendering difference.

Elapsed time was 251.46 seconds with other local checks running. This is not an
isolated latency measurement. No production retrieval or packing default was
changed based on these three questions. The adapter now retains complete
candidate snapshots for subsequent comparisons on identical retrieved inputs.

## Developer experience changes and validation

The same work exposed and fixed two onboarding failures:

- `bdc60dd`: `prme doctor` identifies active environment variables that override
  different project `.env` values, without revealing either value or making a
  provider request. Selected-provider and override-precedence tests pass.
- `cc5683b`: public engine initialization creates missing database/index
  directories, including parents. Both async and sync explicit-config workflows
  now work from a fresh path. Existing files at directory paths are preserved
  and cause an error. The README async example also closes via a context manager.

Frozen `cc5683b` passed **1,955 tests, 51 skipped** on Python 3.11 with live
PostgreSQL (293.16 seconds). Its installed Python 3.13 wheel passed **95 focused
checks, five skipped**, including local path creation, encrypted-startup recovery,
PostgreSQL, CLI and benchmark boundaries (24.12 seconds). An installed-wheel
workflow using real BGE embeddings created a fresh nested pack, stored and
retrieved a note, reopened it, and retrieved the original source; process exit
was zero. The later candidate-snapshot harness change passed its 11 boundary
tests; it does not alter production retrieval.

The updated project credential returned HTTP 429 when used explicitly. An older
inherited environment key returned HTTP 401 and shadowed the file by normal
configuration precedence. No credential values were printed or changed. Remote
OpenAI extraction/judging remains unavailable in this observation; local-model
results must not be presented as results from the configured remote model.

## Frozen-candidate packing experiment

A second clean replay at `fefa7e1` completed with process exit zero and retained
504, 840 and 916 candidate snapshots for the same three selected questions.
This is a fresh replay with new event/node identities and timestamps, not an
identical-input repetition of the first run. Its snapshots freeze the inputs
for the subsequent experiment.

The [packing comparison](packing-order-6b7be6f-summary.json) reproduced **all three
saved contexts and token counts exactly** before making any reader calls. It
used the original `fefa7e1` packing implementation, with diagnostic harness
`6b7be6f`. The experimental variant replaced score-per-token with composite score
inside the multi-path tier only. Instructions, pins, tasks, candidate scores,
source content, representations, token budgets and formatting stayed fixed.
Both contexts were answered again using the same model digest and sampling
settings; all six calls completed and the process exited zero.

In the dynamic case, the density control omitted `s5:t1` and the reader abstained.
The score variant included that source in full and the reader answered yes.
However, its explanation attributed an assistant statement to the user and used
the rendered local date. Restoring evidence did not establish fully faithful
answering. The raw product context does not expose the stored source-type field,
which is another candidate cause to investigate, not a proven complete diagnosis.

The static answers still omitted acknowledgment of the labeled contradiction.
Both conditional answers mentioned commuting but omitted the professional-study
condition. There is no official score, independent label audit or general
improvement claim. **The production packing order remains unchanged.** Broader
development evaluation, source attribution and qualifier retention are needed
before choosing a default.

Reproduce this diagnostic using the original package implementation on
`PYTHONPATH` and the newer diagnostic harness. Running against a formatter with
different timestamp semantics correctly fails baseline reproduction:

```sh
PYTHONPATH=/path/to/fefa7e1/src:/path/to/current/prism \
  python -m benchmarks.diagnostics.packing_order \
  --input /tmp/prme-memconflict-dev-traces.json \
  --output /tmp/packing-comparison.json --with-reader
```

Omit `--with-reader` for a packing-only comparison. The diagnostic refuses
incomplete input, missing snapshots, changed baseline context/token counts and
mixed reports that fail reproduction, before calling a reader. Its six checks
and the adapter's 11 boundary checks pass.

## UTC portability correction

Production `e842d6f` fixes the timestamp issue independently of the packing-order
experiment. Local connections explicitly use UTC, and both context formatters
normalize aware timestamps before displaying dates or counting tokens. Equivalent
instants now produce identical context, including across daylight-saving offsets;
naive in-memory values retain their nominal representation rather than acquiring
an implicit host timezone. Stored instants and source content are preserved.

All six timezone reproductions failed before the fix and pass afterward.
Frozen `e842d6f` passed **1,961 tests, 51 skipped** with live PostgreSQL on Python
3.11 (252.06 seconds). Its installed Python 3.13 wheel passed **105 focused tests,
five skipped** (3.87 seconds). These include timezone equivalence, context budgets,
formatter behavior, fresh packs, encrypted-startup recovery and derivation
identity. Later packing-diagnostic additions do not alter production behavior
and passed their separate 17 diagnostic/boundary checks.

## Provenance follow-up

The later [source-provenance experiment](provenance-5b2baa9-summary.json) compares
both packing orders with and without the stored source classification under the
same budget. Its interpretation, production changes and separate authored reader
checks are recorded in the [fidelity report](../../fidelity/2026-09-12/README.md).

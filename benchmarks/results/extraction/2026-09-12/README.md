# Extraction classification probes

These are twelve authored development probes, fixed before the candidate change.
They cover usage, possible/conditional usage, preferences, dislikes, decisions,
rejected choices, mixed claims, past choices, service references, namesakes, and
conditional preferences. They are **not held-out accuracy or competitive evidence**.

The diagnostic uses public ingestion with the real local extraction model and
local embeddings, then checks every materialized FACT/PREFERENCE/DECISION node.
A case requires every expected object, its expected claim kind, an allowed
epistemic type, the complete source passage, and an actual subject association.
Unexpected object claims also fail this deliberately narrow probe contract.
Extra FACT nodes cannot hide an incorrect PREFERENCE. Model/provider failures
remain failed cases. A supervised process must exit cleanly before success.
Predicates are not semantically judged. The original namesake check required
literal object `Jordan`; inspection showed this wrongly rejected the fully
qualified source mention `Jordan, the country`. The corrected probe accepts
both forms and verifies actual person-subject/location-object graph connections.
Future reports also retain the evaluated nodes and edges for independent audits.
Its fixture hash differs from the original trials; those scores are not rewritten.

[Environment metadata](classification-environment.json) records Python, package
versions and the Ollama model digest. Both runs use `qwen3.5:4b`, three configured
provider attempts and a 90-second extraction timeout. Model sampling is not
seeded. Model runs are sequential; tests also ran on the machine, so timings are
not a latency comparison. Reports record hashes of the fixtures, prompt and
response schema. The package control is `b8bac4b` with harness `ab86a6f`; the
candidate package and harness are `18392ba`. Evaluator/fixtures are identical
between these harness revisions.

The tested candidate (subsequently reverted in `3a8cb65`) explains claim kind separately from epistemic certainty in both
the prompt and schema. FACT includes possible events, PREFERENCE requires an
expressed attitude, and DECISION records a choice or commitment made. A condition
affects epistemic status without changing the kind. This clarification does not
add a deterministic semantic classifier or rewrite existing memories.

The frozen candidate passed **1,850 tests with 42 skips** on Python 3.11 and live
PostgreSQL (175.73 seconds). Its installed Python 3.13 wheel passed **74 focused
checks**, also with live PostgreSQL (8.01 seconds). Full source/test lint passed.

Reproduce from the repository with a local Ollama model:

```bash
python -m benchmarks.diagnostics.claim_classification --output classification.json
```

## Trial outcome: candidate reverted

The [control](classification-control-b8bac4b.json) passed 9/12 original probes.
It treated possible and conditional usage as preferences and marked a conditional
preference asserted. The [candidate](classification-candidate-18392ba.json)
passed 8/12 under the original strict matcher. It fixed possible usage but still
misclassified conditional usage and the conditional preference, and added an
unsupported preference to “Maya chose SQLite.” Its remaining namesake alert was
the overly strict object-string check described above. Both reports contain all
12 cases; exit 1 reflects failed assertions, not an interrupted model run.

The [comparison](classification-comparison.json) records the per-case outcomes
and the evaluator limitation. These single unseeded runs do not support a quality
improvement claim. The prompt/schema-description change was reverted; the probe
suite, raw results and evaluator correction remain. This also demonstrates why
passing code tests alone is insufficient evidence for a memory-quality change.
The corrected diagnostic subset passed **6 checks**, including full-name aliases,
wrong namesake graph roles, missing mixed claims and extra incorrect claim kinds.

After reverting the candidate and correcting the evaluator, **75 focused source
checks passed** with live PostgreSQL (6.52 seconds); source/test/diagnostic lint
also passed. The candidate's earlier full-suite result is evidence of code
compatibility, not evidence of improved extraction quality.

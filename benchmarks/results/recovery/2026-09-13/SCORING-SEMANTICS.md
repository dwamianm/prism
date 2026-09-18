# Current-state and actionable-memory scoring

The current branch reproduced the complete causal simulation gate before this
change at **70/74 checkpoints**. The four failures were:

- a decision about GraphQL fell below ordinary facts for a decision query;
- a PostgreSQL migration ranked below the older MySQL state;
- a new CEO assertion ranked below its predecessor; and
- a Python instruction fell below ordinary facts for a rules query.

The implementation now gives decisions and instructions the same default
semantic-node multiplier as facts and preferences (1.15). The configured values
remain part of `ScoringWeights.version_id` and each retrieval receipt preserves
the actual weights and score trace.

Present-tense questions such as “What database does the project use?” and “Who
is the CEO?” are recognized as current-state lookups. Because that wording can
also occur in timeless queries, inferred current-state handling reweights toward
recency only when the candidate set contains explicit update language. Explicit
words such as “current” retain the existing behavior. Aggregations, historical
cues, and dated temporal queries do not take this inferred-current path.

## Verification

The four initially failing scenarios passed all **21/21** checkpoints after the
change. The full 19-scenario command then passed **74/74** twice in succession,
with native exit zero and no scenario errors:

```bash
python -m scripts.run_simulations --output simulation-results.json
```

The two complete local reports had SHA-256 hashes
`a0c5f1f314877317543f51bfffc51bb8b42b3ae3e4c818dc6d4d943f749e4c43`
and
`998cbf97766ba9eb70d883159f79ef1a42878f278aff4c4bb738dc055089c250`.
The pre-change 70/74 reproduction was
`6f0fd6636dbedb0a9208b6a6fc2c51b12f60366e4f1483389a16c78ac4b5dffc`.
Simulation reports now retain semantic, lexical and graph component scores,
candidate paths, and the complete score trace for failed-result diagnosis.

The complete Python suite with live PostgreSQL and pgvector passed **3,268
tests**, with **93 skipped**, in 709.05 seconds. Focused scoring, replay and
simulation tests passed 133 checks; Ruff passed on the changed modules.

These are authored causal scenarios. They verify the intended scoring behavior
and guard against the observed regressions; they do not replace held-out answer
quality or competitive evaluation.

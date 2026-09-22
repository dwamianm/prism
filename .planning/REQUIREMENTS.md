# Active acceptance requirements

The old v1.0 requirement matrix is archived at
`archive/v1.0-legacy/REQUIREMENTS.md`.

Current acceptance requirements are intentionally narrower and are defined in
`memory_bank/GOALS.md`: production changes must preserve data and tenant
isolation, remain deterministic/recoverable, pass targeted regressions, and
show a measured benefit before changing a default. Benchmark evidence must
identify the dataset split, reader/judge, context budget, costs, and failures.

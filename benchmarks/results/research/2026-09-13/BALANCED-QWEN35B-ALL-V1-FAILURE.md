# Balanced Packing Full-Cohort Answer Trial: Failed Run

The preregistered `balanced-qwen35b-all-v1` run is invalid for policy comparison. The reader completed 173 of 357 fixed calls, then retained a failed call and stopped as required by the registration.

## Failure record

- Registration SHA-256: `2ed3adc842ec8970581aad984f7db3cb01a1673f2e74351520ad42779b9f7851`
- Prepared input SHA-256: `a7f63f25c44fe583b74205ab66649d3c5777ec62ef2cfbd99f5e2639edbd741e`
- Retained reader-state SHA-256: `21c3a77a6dc75986ac07b99dac7a967ee1c6e73a71d524e85861c9104a157590`
- Failed prompt SHA-256: `60315ae57a1a73ef8d31cc4b10e6487d8987932ea1f5843668fe8d0fbcf76e9a`
- Failed response SHA-256: `18e7f0323f735d3e26360aa7c39334eda6c67d7183840b4234aa64c25ce194e6`
- Fixed job: `case-0057`, density arm, “How much did I spend on gifts for my sister?”

The response reached the registered 512-token generation limit and returned `done_reason: "length"`, so the runner correctly rejected it as incomplete. A separate diagnostic replay reproduced the limit termination with 5,078 prompt tokens and 512 generated tokens. No scores from this partial run are used.

The replacement trial must be registered in a fresh directory with a stricter short-answer prompt and a larger generation allowance. The failed state remains under `data/benchmarks/balanced-qwen35b-all-v1/` for audit.

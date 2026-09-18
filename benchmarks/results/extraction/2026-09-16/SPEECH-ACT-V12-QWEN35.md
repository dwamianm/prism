# Speech-act v12 local Qwen 35B confirmation

The first second-model confirmation used the same v12 product implementation,
14 cases, 15 targets, and zero-tolerance gates with the exact local
`prme-qwen3.5:35b-a3b-8k` digest. It completed cleanly with zero unsafe claims,
zero policy errors, and zero execution-binding errors, but the registered scorer
reported 10/14 cases and 11/15 targets.

All four reported target misses are present verbatim inside grounded composite
objects with the expected predicates:

- `trying_to_set_up = "ESLint v8.39 with the Airbnb style guide"`;
- `plan_to_migrate = "Atlas service to PostgreSQL"`;
- `is_working_to_replace = "Celery with Temporal"`;
- `configured = "ESLint v8.39 with the Airbnb style guide"`.

The scorer required whole-object equality, so it did not count these structured
objects even though their exact target text and full source survived. This is a
measurement-boundary defect. A corrected matcher should accept exact
token-bounded containment inside the claim object and apply the same broader
match to unsafe-claim detection. That change cannot turn an unsafe composite
claim into a pass.

This failure artifact remains unchanged. A new registration and rerun are
required before making a cross-model claim. The assay is authored development
evidence, not held-out accuracy or a competitive result.

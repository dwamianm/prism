# Fixed packing regression on the previously examined 381 questions

Frozen source `2ca7f09` compares density, score, quarter-length and one-head
quarter-length ordering at 2K, 4K and 8K. The primary comparison is quarter-length
versus density at 4K; all preference-category changes and individual losses
must be retained. The original score-policy confirmation and its failed category
guard remain unchanged. This is reused regression evidence, not an unseen
holdout or a production promotion gate.

All original source, comparison, plan and candidate hashes are checked against
the native completion manifest. The new worker receives only candidate records,+packing configuration and neutral control measurements, with outcome labels
removed. Every density and score control must reproduce, including all 2,286
prior context hashes and structural measurements. The original 4K rendered source
control must also reproduce exactly. The first source-control compatibility
probe passed without inspecting experimental outcomes.

The registered run covers 4,572 contexts, with immutable per-question context
files and bounded batches of eight inputs across two local worker processes.
Questions sharing a base ID or identical complete histories are connected into
groups; history identity includes session IDs, dates, roles and text, and
excludes answer labels and questions. Descriptive intervals resample these
groups. The independent verification stage requires native exit zero, checks
every serialized context and recomputes source credit and statistics with a
separate accounting and bootstrap implementation.

Fourteen focused tests passed with native exit zero, including pointer-only
credit, complete source retention, malformed contexts, grouping and explicit
loss accounting. The running model-reader trial is separate and unchanged.

See [the registration](packing-regression-plan.json). Complete source and answer
evidence on additional tasks remain necessary before any leadership claim.

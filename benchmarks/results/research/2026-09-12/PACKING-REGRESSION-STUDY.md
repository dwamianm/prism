# Fixed packing regression on the previously examined 381 questions

Frozen source `2ca7f09` compares density, score, quarter-length and one-head
quarter-length ordering at 2K, 4K and 8K. The primary comparison is quarter-length
versus density at 4K; all preference-category changes and individual losses
must be retained. The original score-policy confirmation and its failed category
guard remain unchanged. This is reused regression evidence, not an unseen
holdout or a production promotion gate.

All original source, comparison, plan and candidate hashes are checked against
the native completion manifest. The new worker receives only candidate records,
packing configuration and neutral control measurements, with outcome labels
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

The capture and independent verifier both exited zero. All 4,572 contexts and
2,286 controls passed. There are 365 labelled questions in 360 connected groups;
the other 16 questions have no source-recall value.

| Budget | Density | Score | Quarter-length | Combined |
|---|---:|---:|---:|---:|
| 2K | 53.64% | 77.26% | 82.04% | 83.70% |
| 4K | 65.04% | 85.77% | 89.37% | 90.55% |
| 8K | 72.90% | 90.03% | 93.88% | 94.56% |

The primary quarter-length gain at 4K is 24.34 percentage points, with a
descriptive group-bootstrap interval of 20.47 to 28.38 points: 136 wins, two
losses and 227 ties. The combined policy gains 25.52 points over density
(21.54 to 29.69), with 139 wins, two losses and 224 ties. Both have positive
4K mean changes in every category. Preference recall rises from 78.26% to
92.03% for both; its category interval still crosses zero, and one preference
question loses evidence. This improves the observed preference mean without
rewriting the original score policy's failed confirmation.

The combination adds five assistant-source wins over quarter-length at 4K,
but loses on two multi-session questions. Its overall 1.18-point difference has
a descriptive interval of 0.08 to 2.49 points. At 2K, quarter-length has six
individual losses versus density and the combination has seven. Neither loses
versus density at 8K. All individual losses, including combination-versus-quarter
tradeoffs, are retained in the linked completion record.

Decision: this supports implementing a public opt-in policy with exact context
parity, reproducible receipts and documented tradeoffs, followed by complete
answer evaluation. It does not justify a default change, a claim of no
regressions or a leadership claim. The separate one-head answer trial does not
test these quarter-length policies.

See [native capture completion](packing-regression-completion.json),
[all verified category results](packing-regression-verification.json), and
[verification completion and every loss](packing-regression-verifier-completion.json).

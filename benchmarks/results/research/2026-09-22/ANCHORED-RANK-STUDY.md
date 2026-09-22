# Original-anchor reranker follow-up

Date: 2026-09-22. Status: registered source execution in progress.
Branch: `research/reranker-score-repair-2026-09-22`; production remains unchanged.

The prior rank-envelope repair completed at 428/500 against the original
437/500 baseline; its new primary control failed. That trial remains intact.
Among its four complete-source losses, two assistant records had originally
ranked first. They lost that position and were omitted by balanced packing.
The input audit found that three of four lost records fit entirely in the
cross-encoder input. The long dinosaur record was truncated after character
1,973, but its relevant “blue scaly body” phrase occurred at character 1,606.
Truncation cannot simply be assumed to have hidden that answer.

The [new registration](opt-in-anchored-rank-v1-registration.json) fixes one
development policy before its source outcomes. It identifies the original
highest-score ordinary multi-path record, using the same exclusions for
instructions, pins and active tasks as balanced packing. If the record is in
the neural prefix, it receives the first slot of the original score envelope;
remaining prefix records follow neural order. Equal assigned scores retain
canonical UUID ties, and the unjudged tail is unchanged. This prioritizes a
candidate; it does not guarantee source retention under every budget, tie or
later pipeline operation. No answer, source label or question category enters
the algorithm, and no public configuration field activates it.

All 500 development cases must exactly replay the baseline and prior repair
contexts, candidate IDs and scores before the new source metrics are reported.
The model's earlier raw scores are reused only for identical query/document
pairs. Every source case, pack and receipt is authenticated. There are no new
source-stage model calls; timings exclude neural inference and cannot establish
an uncached serving-speed improvement.

Answer qualification requires both complete-source count and mean source
fraction to strictly exceed **both** production and the prior repair, with
every category's complete-source count at least production's. This is one
fixed policy, with no parameter search. If it qualifies, all 500 candidate
contexts and a new 500-case control repeat are frozen before inference. The
same official reader/judge protocol applies, both complete arms are required,
and all losses remain. It uses the already released reader lane after the
marginal trial settles and acquires the same exclusive lock. No hosted-provider
capacity is added, and no failed prior run is replaced.

[Validation](anchored-rank-validation-v1.json) passed nine authored checks and
two additional DuckDB/live-PostgreSQL checks, with no failures or skips. These
cover actual balanced packing, score/receipt replay, input immutability,
ordinary-anchor eligibility, boundary cases, exact authored control replay,
drift rejection, feedback, restart and owner isolation. The underlying
version-13 package support is unchanged from the earlier installed-wheel
validation. This is not a quality result or a full release gate.

The completed marginal trial, 433/500 versus 433/500, and all prior development
outcomes were known at registration. This cohort is not untouched confirmation.
No default change or branch merge is authorized by this follow-up.

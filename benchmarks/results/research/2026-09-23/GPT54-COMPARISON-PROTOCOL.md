# GPT-5.4 default-configuration benchmark comparison

Registered work follows release v0.12.0 (`aaa2e4e`) in an isolated worktree.
The user restored OpenAI credit and then enabled auto top-up, explicitly
authorizing both complete LoCoMo and LongMemEval evaluations. No production
defaults, historical runs or released artifacts change.

## Fixed scope

- LongMemEval-S: all 500 cleaned questions, including preference and abstention.
- LoCoMo: all ten conversations and all 1,540 category 1–4 questions. The 446
  adversarial questions are excluded prospectively, following Zep's disclosed
  four-category headline scope. The exact upstream data checksum and revision
  are recorded. The upstream category mapping is 1 multi-hop, 2 temporal,
  3 open-domain and 4 single-hop; the older PRME adapter's label names differ.
- Reader and judge: `gpt-5.4-2026-03-05`, medium reasoning, Flex processing.
  Reader output limit 8,192; judge limit 2,048 includes reasoning tokens.
  No output-limit tuning on benchmark outcomes. Authored controls precede data.
- Product retrieval: default balanced packing, 4,096 configured tokens with
  100 reserved overhead; every experimental feature remains disabled.
- Complete coverage and valid non-truncated reader/judge responses are required
  before any aggregate quality score is published. Failed and ambiguous calls
  remain visible; they cannot be replaced by a favorable retry-only run.

LongMemEval reuses all authenticated, frozen production-control contexts from
the September 22 full study. These correspond to the defaults retained in the
release; the changed reader is the variable under test. This is a new reader
evaluation of those exact contexts, not a claim to have freshly ingested or
timed v0.12.0 on all 500 histories. Each capture checksum, memory-artifact
checksum and the prior complete execution/verification chain is recorded.
Historical ingestion and retrieval costs are not billed or relabeled as new.

LoCoMo freshly stores every nonempty source turn using public `store()`, retaining
speaker, date, session and supplied image captions. Short turns are preserved.
No dataset observations, event/session summaries, questions, answers or evidence
annotations enter ingestion. Each question uses one public `retrieve()` call.
All contexts are captured before any LoCoMo model answer is generated. The
source clock is the final session date; all source artifacts, receipts and
configuration snapshots are retained. This is raw source memory plus supplied
image captions, not generated extraction or an image-understanding evaluation.

LongMemEval uses its pinned official retrieval-reader template and category
judge prompts. LoCoMo uses the fully disclosed binary semantic reader/judge
templates in the registration. Its accuracy is not the official token-F1
metric. Judge output must be an unambiguous yes/no. No partial-credit threshold
is used. Previously examined datasets are development evidence.

## Cost and failure accounting

The shared ledger reserves a conservative full-price maximum before each
request and settles against provider-reported usage. Unknown transport charges
remain reserved and are not replayed. Responses with HTTP 429 or selected 5xx
statuses may receive at most four identical attempts, all retained; quota
exhaustion, malformed verdicts and truncated responses do not get substituted.
The user's auto top-up authorization removes the former $19 stopping threshold;
the fixed cohorts, attempt counts and token limits bound the work. No additional
feature arms are authorized by this protocol.

Observed Flex rates are accounted at Batch pricing: $1.25/M input, $0.125/M
cached input and $7.50/M output. Standard-price reservations use $2.50/M input
and $15/M output. Reader/judge costs include reasoning tokens. Preflight costs
are separately retained and reported. See [model pricing](https://developers.openai.com/api/docs/models/gpt-5.4)
and [Flex documentation](https://developers.openai.com/api/docs/guides/flex-processing).

## Comparison boundary

Zep [reports](https://www.getzep.com/research/) 451/500 (90.2%) on LongMemEval
and 1,459/1,540 (94.7%) on LoCoMo, using GPT-5.4 medium reasoning. These are
vendor-reported reference values, not a live matched Zep arm. Its exact prompts,
dataset checksum, current execution artifacts and complete ingestion recipe are
not public. Its displayed LoCoMo category counts do not reconcile with the
headline. We match the disclosed reader family/reasoning and benchmark scope;
we cannot claim an exact reproduction or paired significance against Zep.

The report will retain both raw counts, per-category results, context-token
usage, artifact identities, provider failures/costs and descriptive confidence
intervals. No improvement or promotion decision follows from a point estimate
alone. The existing 87.4% DeepSeek LongMemEval result remains a separate run.

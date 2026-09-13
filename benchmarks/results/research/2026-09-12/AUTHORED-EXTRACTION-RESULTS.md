# Local extraction: complete authored probe

The frozen `f9665fb` installed-package probe completed all 12 cases for each
registered model. Qwen passed 10 checks; Gemma passed 11. Both processes exited
1 because at least one case failed. All cases are retained, including failures;
no outcome was replaced. Package files, schemas, prompts, dependencies and model
digests matched the registration before and after generation. The original
assessment function reproduced every structured result offline.

| Case | Qwen | Gemma |
|---|---|---|
| Conditional usage: “If latency improves, Noah will use SQLite.” | Decision, hypothetical | Decision, conditional |
| Conditional preference: “If latency is equal, Elena prefers SQLite.” | Preference, asserted | Preference, conditional |
| Other ten cases | Pass | Pass |

Both models failed the frozen requirement that conditional usage have kind
`fact`. Its uncertainty and complete source were preserved. The wording can
also be read as a conditional commitment, so this kind-label failure should not
be equated with a demonstrated loss of meaning. The registered result remains
failed; its label is not relaxed after observing output.

Qwen's conditional preference fails the registered uncertainty check. The
complete condition remains in the saved source text, but its structured type
is asserted. The schema permits `conditional`, while the main extraction-prompt
type list omits it and folds conditions into `hypothetical`. The later rules
mention both. Clarifying that inconsistency is a concrete next experiment;
the observed error alone does not prove which instruction caused it.

These are authored development checks, not held-out accuracy, calibrated
confidence or comparative product quality. They assess type labels, links and
source preservation, not all semantic entailments. The saved model output is
structured extraction, not the raw SDK response. Generation overlapped a
separate local judge, so elapsed times are not clean model-speed measurements.

See [the registration](authored-local-extraction-model-plan.json),
[complete assessed results](authored-local-extraction-model-results.json),
[Qwen native completion](authored-local-extraction-qwen-completion.json) and
[Gemma native completion](authored-local-extraction-gemma-completion.json).

## Conditional-prompt follow-up: reverted

The isolated follow-up changed only `ingestion/extraction.py` in the installed
package. Both models completed all 12 registered cases; each native worker and
parent exited 1. The outer driver exited 0 after retaining both complete reports
and verifying package, model, prompt, schema and case identities. Original
structured assessments reproduced offline; the timeout remains a recorded failure.

| Model | Baseline | Follow-up | Changed checks |
|---|---:|---:|---|
| Qwen 3.5 4B | 10/12 | 8/12 | Dislike and rejected choice now fail; conditional usage and conditional preference still fail. |
| Gemma 4 26B | 11/12 | 11/12 | Conditional usage now passes; namesake extraction times out at the fixed 90-second limit. |

Qwen produced two preference claims for the negative statement, including a
`likes` predicate alongside `dislikes`; source text remained intact. Rejected
choice became a preference instead of a decision. Its conditional preference
still became asserted. Gemma's timeout is an availability failure, not evidence
of incorrect extracted semantics. All other cases retain their original result.

The change was reverted because the target Qwen failure persisted and these
results do not support promoting it. Provider sampling defaults and one run per
model do not isolate a statistical prompt effect. No cases were selectively
retried or relabelled. The original menu inconsistency remains a hypothesis;
future work must validate semantic qualifications and negation, not just add
instructions or treat preserved source text as proof of a valid predicate.

See [follow-up registration](conditional-extraction-followup-plan.json),
[all assessed outcomes](conditional-extraction-followup-results.json), and
[native completion and rollback](conditional-extraction-followup-completion.json).

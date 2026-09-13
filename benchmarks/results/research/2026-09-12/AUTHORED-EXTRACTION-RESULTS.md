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

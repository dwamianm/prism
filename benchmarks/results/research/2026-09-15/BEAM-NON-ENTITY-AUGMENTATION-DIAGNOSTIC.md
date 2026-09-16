# BEAM non-entity evidence augmentation diagnostic

Filtering entity anchors fixed the specific API-key contradiction regression,
but the broader two-conversation policy did not pass its registered diagnostic
rule. Across forty questions, the policy produced one pass-level win, one loss,
and a **-0.03000** combined mean-score delta. It remains opt-in and does not
advance unchanged to another untouched confirmation.

This diagnostic was designed after inspecting the unconditional augmentation
failure, so both conversations are examined development evidence. The
[conversation 0 registration](beam-100k-non-entity-augmentation-diagnostic-conv0-registration.json)
and [conversation 1 registration](beam-100k-non-entity-augmentation-diagnostic-conv1-registration.json)
fixed the policy and cohort rule before the new candidate answers were generated.
The [machine-readable result](beam-100k-non-entity-augmentation-diagnostic-v1-results.json)
binds both executions.

## Results

| Arm | Baseline | Candidate | Wins | Losses | Mean delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| Conversation 0 | 13/20, 0.56750 | 13/20, 0.52833 | 1 | 1 | -0.03917 |
| Conversation 1 | 15/20, 0.64125 | 15/20, 0.62042 | 0 | 0 | -0.02083 |
| **Combined** | **28/40** | **28/40** | **1** | **1** | **-0.03000** |

Conversation 0 retained the intended event-ordering recovery: q5 rose from 0.3
to 0.6 and became a pass. It lost q19 temporal reasoning, which fell from 0.5 to
0.0. Conversation 1 restored the API-key contradiction question from the prior
augmented score of 0.25 to its 0.5 baseline and preserved all fifteen passes.
Its first ordering question improved from 0.7 to 0.9.

## Interpretation

Entity nodes are useful query routes, but they are unsafe authorization for
injecting a complete passage. The prior API-key loss came entirely from sources
routed by entity candidates such as a placeholder key and error message. The
`non_entity` policy removed those passages and restored the contradiction pass,
which supports the new developer-facing control.

That mechanism fix is insufficient as a general answer-quality policy. On the
conversation-0 temporal loss, the first twenty retrieval results were identical
to baseline and the only candidate-only top-50 passage was unrelated to the
date calculation. The answerer changed which of two already present sprint dates
it used. Because the baseline and candidate answers came from separate calls to
mutable hosted aliases, model variance and sensitivity to low-ranked context are
both plausible. A stronger harness should rerun baseline and candidate
contemporaneously, interleave their order, and collect repeated samples before
attributing small score changes to retrieval.

## Scope

These are two examined 100K conversations and forty questions. The result
rejects this broad policy under its registered rule while retaining a useful
opt-in safety control. It does not measure a default or establish generalization.

# BEAM cited answerability development trial

The first complete repeated trial of PRME's cited answerability evaluator failed
all four preregistered gates. It produced **3 unsafe full-answer actions** across
12 abstention samples, only **60 full-answer actions** across 108 ordinary
samples, **8 citation errors**, and identical actions for **27/40 questions**.
The evaluator remains optional and does not advance to an untouched confirmation
cohort.

The [registration](beam-100k-answerability-dev-v1-registration.json) fixed the
runner, all 40 frozen question artifacts, prompt, model manifest, three repeats,
and acceptance gates before the 120 calls. The
[machine-readable result](beam-100k-answerability-dev-v1-results.json) retains
every validated requirement, citation, explanation, verdict, action, and digest.
The runner made no retrieval or answer-generation calls.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe full answers on abstention samples | 0/12 | 3/12 | Fail |
| Citation errors | 0/120 samples | 8 | Fail |
| Full answers on ordinary samples | >=86/108 | 60/108 | Fail |
| Abstentions on ordinary samples | <=10/108 | 31/108 | Fail |
| Questions with one action across repeats | >=32/40 | 27/40 | Fail |

The 12 abstention samples produced 7 abstentions, 3 full answers, and 2 partial
answers. The 108 ordinary samples produced 60 full answers, 31 abstentions, 10
partial answers, and 7 conflict responses. Thirteen questions changed action
across three identical calls despite temperature zero.

## What the result establishes

A single model call is not a reliable answerability policy for these contexts.
A majority vote would improve repeatability, but it would retain systematic
coverage failures: several ordinary questions received the same abstention on
all three calls. Threshold tuning cannot repair categorical false answers,
false conflicts, or missing citations.

The trial also exposes a task-model mismatch. Its runner intentionally converted
each frozen BEAM result to rank-ordered text plus exact citation IDs. That
preserved the saved top-50 passages, but it did not reconstruct PRME's normal
packed record fields for epistemic state, lifecycle, validity, and event time.
Some ordinary BEAM questions contain revisions or explicit contradictions where
surfacing uncertainty is defensible. Conversation 0's background-and-previous-
projects abstention question also has support for its background half, making a
partial response more defensible than the benchmark's binary label. These limits
do not excuse the registered failures; they constrain what should be changed
next.

Eight citation errors came from model outputs that claimed a conflict without
two valid cited memories. PRME downgraded those requirements to unsupported as
designed. The fail-closed citation boundary worked, while the upstream model
classification did not meet the reliability needed for enforcement.

## Decision

Do not promote the evaluator into automatic retrieval behavior and do not run an
untouched confirmation cohort from this prompt. Keep its explicit, opt-in API for
experimentation. The next trial must preserve the typed temporal and epistemic
record contract and separately test draft-answer claim verification; it must not
retune a threshold on these examined artifacts.

The run also led to a separate product correction after results were fixed: the
public evaluator originally resolved compact `mN` references but the default
retrieval format is auditable and uses full UUIDs. That API defect was not the
cause of this trial because the runner supplied explicit compact references. It
must be fixed and locally tested before any later evaluation.

## Scope

Both 100K conversations and all four abstention questions had been examined
before registration, so this is development evidence. The local Ollama manifest
was pinned, but its hosted alias does not expose an immutable remote weight
revision. The result evaluates evidence sufficiency over frozen retrieved
contexts; it does not evaluate a complete answer-generation policy or establish
cross-product quality.

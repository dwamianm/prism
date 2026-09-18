# BEAM speech-act-aware answerability development trial

The speech-act-aware v2 question-only evaluator still failed every registered
gate on the same 40-question BEAM development cohort. Across three identical
calls per question it produced **2 unsafe full answers** in 12 abstention
samples, **62 full answers** and **22 abstentions** in 108 ordinary samples,
**5 citation errors**, and one stable action for **26/40 questions**.

The [schema-2 registration](beam-100k-answerability-dev-v2-registration.json)
was fixed before the 120 calls. It binds the exact evaluator implementation,
prompt version and digest, response schema, model manifest, runner, frozen
artifacts, repeats, and five machine-evaluated gates. The
[machine-readable result](beam-100k-answerability-dev-v2-results.json) retains
every requirement, citation, explanation, action, digest, and gate verdict.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Unsafe full answers on abstention samples | 0/12 | 2/12 | Fail |
| Citation errors | 0/120 samples | 5 | Fail |
| Full answers on ordinary samples | >=86/108 | 62/108 | Fail |
| Abstentions on ordinary samples | <=10/108 | 22/108 | Fail |
| Questions with one action across repeats | >=32/40 | 26/40 | Fail |

## Comparison with v1

The v2 prompt improved four observed counts without clearing a gate. Unsafe
full answers fell from 3 to 2, citation errors from 8 to 5, ordinary full
answers rose from 60 to 62, and ordinary abstentions fell from 31 to 22.
Action stability fell from 27 to 26 questions. The two unsafe answers were the
background/previous-projects question and the ESLint-enforcement question.
The latter still alternated between `abstain`, `answer`, and `abstain` over an
identical context.

The remaining citation errors came from conflict classifications that supplied
fewer than two distinct valid memory citations. PRME downgraded those claims as
designed, but the model-level classification remained unreliable.

## Causal follow-up

An exploratory exact-source probe ingested the complete ESLint passage under
`speech_act_v6` / `speech_act_v12`, retrieved the original enforcement question
through the ordinary auditable bundle, and repeated v2 assessment five times.
The extraction retained `trying_to_set_up` / `trying_to_set_up_with` rather
than an enforced relation, and all five assessments abstained with zero citation
errors. This probe was run after the speech-act implementation was fixed and was
not preregistered, so it is diagnostic evidence only.

Together, the registered failure and the exact-source probe isolate a remaining
evidence-set problem: distractors and repeated related passages can flip the
verifier even when the decisive source preserves the correct speech act. A
question-only sufficiency call is therefore not a safe enforcement policy.

## Decision

Keep the evaluator explicit and experimental. Do not promote the v2 prompt or
run an untouched question-only confirmation cohort. The next registered trial
should verify each concrete claim in the already frozen generated answers,
using the same cited evidence and fail-closed reference validation. This tests
the API's draft-answer path directly and avoids treating broad question coverage
as a substitute for claim entailment.

## Scope

Both conversations, all questions, and the v1 outcomes were known before this
same-cohort development rerun. The local Ollama manifest pins the hosted alias
but not an immutable remote weight revision. This trial evaluates evidence
sufficiency over frozen retrieved contexts; it does not evaluate new retrieval,
new extraction, complete answer generation, or competitive product quality.

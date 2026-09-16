# BEAM frozen-draft answerability development trial

Claim-level verification of the exact saved BEAM top-50 answers failed every
registered gate. Across three assessments of each of 40 frozen drafts, the
evaluator fully accepted **13/84 samples from correct drafts** and **5/36
samples from incorrect drafts**, produced **7 citation errors**, and returned
one stable action for **22/40 questions**.

The [schema-3 registration](beam-100k-answerability-draft-dev-v1-registration.json)
was fixed before the 120 calls. It binds the exact evaluator implementation,
prompt and response schema, model manifest, frozen artifacts, exact draft-answer
hashes, upstream PASS/FAIL labels, repeats, and four machine-evaluated gates.
The [machine-readable result](beam-100k-answerability-draft-dev-v1-results.json)
retains every requirement, citation, explanation, action, digest, and gate
verdict. The runner made no retrieval or answer-generation calls.

## Registered gates

| Gate | Required | Observed | Result |
| --- | ---: | ---: | --- |
| Full accepts of samples from incorrect drafts | 0/36 | 5/36 | Fail |
| Full accepts of samples from correct drafts | >=67/84 | 13/84 | Fail |
| Citation errors | 0/120 samples | 7 | Fail |
| Questions with one action across repeats | >=32/40 | 22/40 | Fail |

Correct-draft samples produced 13 full accepts, 62 partial responses, 4
abstentions, and 5 conflict responses. Incorrect-draft samples produced 5 full
accepts, 20 partial responses, 8 abstentions, and 3 conflict responses. All 12
samples for the four abstention questions avoided a full answer, but that narrow
safety result does not compensate for the incorrect-draft accepts or low useful
coverage.

## Failure analysis

The five full accepts came from two questions, and they expose different
boundaries.

Conversation 0's first-sprint answer was accepted in all three repeats. The
draft says March 31; retrieved user evidence explicitly says the target was
extended from March 29 to March 31, while the frozen BEAM rubric requires March
29. The verifier correctly found evidence for the draft but cannot infer that an
external benchmark wants an earlier state. This is a temporal task-definition
conflict, not proof that cited entailment alone can reproduce answer labels.

Conversation 1's weather-app count was fully accepted in two of three repeats.
The draft groups 14 retrieved details while the rubric requires four. Each listed
detail has topical evidence, but the context does not define the grouping rule or
prove that the list is complete and mutually distinct. A cited entailment check
cannot validate an exhaustive count by checking each item independently.
PRME's structured aggregation APIs already treat completeness as a separate
contract; natural-language verification needs the same discipline.

The verifier did correct the previously unsafe ESLint-enforcement answer: all
three draft assessments downgraded it to partial rather than accepting attempted
configuration as proof of enforcement. That causal improvement is useful, but it
did not generalize into a reliable set-level policy.

Many externally correct drafts were marked partial because explanatory side
claims lacked direct support. This shows that verifying every sentence is stricter
than verifying the answer nugget. It also makes automatic enforcement unusable
without a refinement path that can remove unsupported side claims while retaining
the supported answer.

## Decision

Keep the evaluator explicit and experimental. Do not promote the current draft
mode or run an untouched confirmation cohort with the same architecture.

The next implementation should separate four operations that one model call
currently conflates: atomic claim extraction, minimal evidence-group selection,
claim-to-source entailment, and deterministic answer refinement. Count and list
claims must additionally require a complete enumeration source or a structured
aggregation result; per-item citations cannot establish exhaustiveness. The
evaluation context must preserve speaker, time, validity, lifecycle, and
epistemic state instead of flattening records to text and IDs.

## Scope

Both conversations, all generated answers, their upstream judgments, and the
earlier question-only outcomes were known before registration. The upstream
PASS/FAIL label scores task compliance and is not an entailment oracle. The local
Ollama manifest pins a hosted alias but not immutable remote weights. This is a
development result over two 100K conversations; it does not establish held-out
verifier quality, end-to-end answer quality, or comparative product quality.

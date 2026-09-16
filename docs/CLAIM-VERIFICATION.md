# Local claim verification

`ClaimVerifier` independently checks a declarative claim against exact memory
passages with a local natural-language-inference model. It is a lower-level
primitive than `AnswerabilityEvaluator`: it does not generate an answer,
decompose prose, or decide whether a response is useful. It answers whether the
supplied evidence supports, refutes, contests, or fails to establish one claim.

Install the optional runtime and reuse one verifier instance so the model is
loaded once:

```shell
pip install prme[verification]
```

```python
from prme import ClaimVerificationStatus, ClaimVerifier

response = await engine.retrieve(
    "When is the launch?",
    user_id="alice",
)

verifier = ClaimVerifier()
result = await verifier.verify_bundle(
    "The launch is Tuesday.",
    response.bundle,
)

if result.status == ClaimVerificationStatus.SUPPORTED:
    print(result.supporting_group)
elif result.status == ClaimVerificationStatus.CONTESTED:
    print(result.supporting_group, result.refuting_group)
```

The default is `cross-encoder/nli-deberta-v3-base` at immutable Hugging Face
revision `6c749ce3425cd33b46d187e45b92bbf96ee12ec7`. The model download occupies
roughly 721 MB in the Hugging Face cache. The optional dependency and inference remain
outside ordinary storage and retrieval. Set `model` and `revision` together in
`ClaimVerificationConfig` to use another three-label NLI cross-encoder.

## Minimal evidence groups

The verifier scores each passage alone before combining anything. If no
individual passage reaches the configured entailment or contradiction threshold,
it ranks a bounded candidate subset and evaluates combinations of two passages
by default. It stops at the first group size with a decision. This avoids feeding
an entire retrieved context through a short NLI window and produces the smallest
observed evidence group rather than a long, redundant citation list.

Both an entailing and a contradicting group produce `contested`. An entailing
group alone produces `supported`; a contradicting group alone produces
`refuted`; otherwise the result is `insufficient`. The probabilities are model
scores, not calibrated truth probabilities. Every evaluated group, exact model
revision, thresholds, typed evidence identity, and input/output digest is retained
in `ClaimVerification`.

The default entailment policy preserves speech act. If evidence expresses a
desire, attempt, plan, advice, question, uncertainty, or condition that the
claim does not preserve, even a model score above the entailment threshold stays
`insufficient`. The raw score remains visible and `limitations` includes
`uncorroborated_model_entailment`. A claim that itself says the user *wants* or
*is trying* can still be supported by matching evidence. Set
`entailment_policy="model_only"` only when reproducing raw-model experiments.
Typed `hypothetical`, `conditional`, or `unverified` evidence must also match
the claim's modality; deprecated epistemic evidence and superseded, deprecated,
or archived lifecycle evidence cannot decide a current claim under the guarded
policies.

By default, a high model contradiction becomes `refuted` only when the exact
claim/evidence group also contains an explicit negation or correction cue, or
incompatible concrete numeric, weekday, or month values. A model contradiction
without that independent signal remains `insufficient`, retains its raw score,
and reports `uncorroborated_model_contradiction` in `limitations`. This guard
prevents an intention such as “I want to deploy” from becoming evidence that a
deployment did not happen. It is deliberately narrow: an implicit contradiction
can remain unresolved. `refutation_policy="model_only"` restores raw
threshold-based behavior for controlled experiments.

An exact negated clause can also surface a refutation when the NLI model is
neutral, but only when at least three non-generic proposition tokens overlap (or
all tokens of a two-token proposition overlap). This narrow fallback catches
corrections such as “Ravi no longer owns ingestion” for “Ravi owns the ingestion
pipeline” without treating any topically related negative sentence as a
conflict. `refuting_basis="explicit_negation_overlap"` distinguishes that
deterministic decision from `model_contradiction`; `supporting_basis` and
`refuting_basis` are part of claim-verification result schema 2.

`verify_bundle()` sees only packed candidates whose references occur in the exact
rendered context. It uses `candidate.rendered_text`, so it does not verify against
hidden full text after packing selected a lower-fidelity representation. Each
`ClaimEvidence` also retains source type, epistemic type, lifecycle, event time,
and validity. NLI currently scores the exact text; applications can inspect the
typed fields before accepting the result.

## Completeness boundary

Entailment cannot prove that a top-k result contains every matching memory. Mark
a derived exact count or complete-list claim with `requires_complete_set=True`:

```python
result = await verifier.verify_bundle(
    "There are exactly four deployment concerns across all sessions.",
    response.bundle,
    requires_complete_set=True,
)
assert result.status == ClaimVerificationStatus.INCOMPLETE
```

This returns `incomplete` without loading the model. Produce exact stored-set
counts with `aggregate_assertions()` or `aggregate_quantities()`, or perform an
owner-scoped complete scan under the documented coverage contract. A caller that
already has one explicit source assertion such as “the project has four phases”
can verify that ordinary factual claim without setting the derived-set flag.

## Evidence and limitations

The implementation follows the minimal-evidence-group direction studied in
[Li et al. (TrustNLP 2025)](https://aclanthology.org/2025.trustnlp-main.8/)
and the sentence-level attribution direction in
[ReClaim (NAACL Findings 2025)](https://aclanthology.org/2025.findings-naacl.55/).
PRME's bounded search is an engineering adaptation, not a reproduction of either
paper's complete system.

The pinned default model reports 90.04 accuracy on the MNLI mismatched set in the
[Sentence Transformers model table](https://sbert.net/docs/cross_encoder/pretrained_models.html).
That result does not establish performance on long-term memory claims. NLI can
miss paraphrases, temporal scope, arithmetic, domain terms, and evidence that
needs more passages than the configured group limit. Use representative labeled
claims before enforcing a threshold. Model load and inference failures raise
`ClaimVerificationError`; they never become support.

The first registered
[20-case development assay](../benchmarks/results/research/2026-09-16/CLAIM-VERIFICATION-MINIMAL-GROUPS-V1.md)
matched 17 cases and failed its all-cases gate. It produced zero unsafe support
decisions, recovered both unseen two-passage claims, found every explicit
refutation and conflict, and refused both exhaustive claims without a model call.
It also overcalled two neutral passages as contradictions. Until refutation has
independent corroboration, treat `refuted` from that implementation as
experimental rather than proof that the opposite claim is true. The default
verifier now applies the deterministic corroboration guard described above. A
registered
[same-cohort confirmation](../benchmarks/results/research/2026-09-16/CLAIM-VERIFICATION-GUARDED-REFUTATION-V2.md)
passed all eight targeted gates: the two false refutations became auditable
`insufficient` results while every raw score and all explicit refutation,
conflict, minimal-group and completeness outcomes were retained. That follow-up
used the fully observed development cohort; it confirms the named fix rather
than held-out refutation quality.

A subsequent, previously unexecuted
[48-case development assay](../benchmarks/results/research/2026-09-16/CLAIM-VERIFICATION-BROAD-DEV-V1.md)
failed its safety gate with two unsafe `supported` decisions. One collapsed a
desire into a completed action; the other missed explicit counterevidence and
returned `supported` instead of `contested`. It correctly retained 6/6 explicit
refutations, rejected all model-only contradictions, found 4/6 new minimal
groups, and refused 4/4 exhaustive claims without inference. Keep the verifier
opt-in. The default entailment guard and explicit-negation basis described above
were added from these failures. A registered
[same-cohort safety confirmation](../benchmarks/results/research/2026-09-16/CLAIM-VERIFICATION-BROAD-SAFETY-V2.md)
then passed all eight targeted gates. Raw model scores were identical and exactly
the two intended statuses changed, removing both unsafe supports. Four implicit
contradictions and two weak relation-composition pairs remain unresolved, and
the confirmation cohort was already observed, so verification remains opt-in
pending a new untouched cohort.

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
by default. Ranking selects the bounded subset but does not reorder it: group
premises retain the caller's evidence order, which is also bound into the
evidence digest. It stops at the first group size with a decision. This avoids
feeding an entire retrieved context through a short NLI window and produces the
smallest observed evidence group rather than a long, redundant citation list.

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
policies. A short passage containing an explicit negated clause cannot support a
positive claim under the guarded policy.

For a multi-sentence passage that crosses the raw entailment threshold but fails
one of those guards, schema 3 performs a localized recheck. It deterministically
splits the passage, removes sentences whose speech act does not occur in the
claim and removes negated sentences for a positive claim, then scores the
retained passage with the same pinned model. A successful recheck reports
`supporting_basis="localized_model_entailment"`. Every attempt retains the
one-based sentence numbers, localized-premise digest, probabilities, and whether
another model call occurred. Typed provenance remains attached to every segment,
so localization cannot reactivate hypothetical or retired evidence. A passage
containing only a desire or attempt yields no eligible localized premise.

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
neutral, but only when every normalized non-generic claim token occurs in that
clause; two-token claims require an exact token set. The same relation-alignment
rule gates evidence-side negation before a high model contradiction is accepted.
This narrow fallback catches corrections such as “Ravi no longer owns ingestion”
for “Ravi owns the ingestion pipeline” without treating “Ravi did not attend an
ingestion review” as a conflict. `refuting_basis="explicit_negation_overlap"`
distinguishes that deterministic decision from `model_contradiction`;
`supporting_basis` and `refuting_basis` were added in result schema 2. Schema 3
adds `localized_assessments` and binds the schema version into new evaluation
identities; schemas 1 and 2 remain readable.

`verify_bundle()` sees only packed candidates whose references occur in the exact
rendered context. A reader-format bundle has references only when packed with
`PackingConfig.context_citations=True`; without them `verify_bundle()` raises
`ValueError`. It uses `candidate.rendered_text`, so it does not verify against
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

The two composition misses were then traced to candidate ranking reordering the
selected premise passages. A registered
[caller-order confirmation](../benchmarks/results/research/2026-09-16/CLAIM-VERIFICATION-BROAD-ORDER-V3.md)
passed all eight gates and recovered 6/6 minimal groups, with exactly those two
statuses changed. This is same-cohort evidence after an order diagnostic. It
does not make the NLI model order-invariant, and the four implicit
contradictions remain deliberately unresolved without an exclusivity contract.

A new, previously unexecuted
[44-case guard assay](../benchmarks/results/research/2026-09-16/CLAIM-VERIFICATION-GUARD-GENERALIZATION-V1.md)
then failed two safety gates. Four negative sentences about a different relation
were accepted as refuting or conflicting evidence because a broad negation cue
and shared nouns were treated as corroboration. This blocks promotion despite
zero plain unsafe `supported` decisions. The failed result remains the current
untouched evidence. The implementation now requires complete normalized claim
token coverage within the negated clause and maps reported questions to the same
speech-act mode as direct questions; a frozen confirmation must test that change
without rewriting the failure.

The registered
[relation-alignment confirmation](../benchmarks/results/research/2026-09-16/CLAIM-VERIFICATION-GUARD-ALIGNMENT-V2.md)
passed all eight targeted gates at 43/44. Raw NLI scores were identical: all
four wrong-relation negative decisions became `insufficient`, and the reported
question became `supported`. Every true explicit refutation, completed-action
refusal, composition chain, conflict and completeness boundary was retained.
This was a causal same-cohort confirmation; the sole typed-unverified paraphrase
remains a model recall miss, and the feature remains opt-in.

The first external
[WiCE oracle-retrieval assay](../benchmarks/results/research/2026-09-16/WICE-CLAIM-VERIFICATION-V1.md)
evaluated all 358 human-annotated claim test cases and failed three of five
quality thresholds. The guarded result accepted none of 32 fully unsupported
claims and limited false support to 0.81%, but supported recall was only 10.91%
and balanced accuracy was 55.05%. Applying speech-act and negation guards to an
entire multi-sentence passage let unrelated sentences veto support elsewhere in
the passage. Keep the verifier opt-in; document-sized evidence needs localized
guarding and another external confirmation. The registered
[localized confirmation](../benchmarks/results/research/2026-09-16/WICE-CLAIM-VERIFICATION-LOCALIZED-V2.md)
then preserved all eight authored safety and coverage gates and improved WiCE
supported recall from 10.91% to 30.91%, F1 from 19.35% to 43.59%, and balanced
accuracy from 55.05% to 63.04%. It still failed the unchanged precision, recall,
and balanced-accuracy requirements. Fully unsupported false support remained
0/32; all 12 false supports were partial claims. The generic base NLI model is
now the measured limit, so schema 3 remains opt-in. A separately registered
[MiniCheck DeBERTa capacity trial](../benchmarks/results/research/2026-09-16/WICE-MINICHECK-DEBERTA-V1.md)
improved WiCE balanced accuracy to 75.01%, recall to 57.27%, and F1 to 65.97%
at its published 0.5 threshold, again with 0/32 fully unsupported accepts. It
failed the fixed precision and recall gates, and no threshold over its frozen
scores satisfies both. It is not integrated into `ClaimVerifier`.
A second registered
[MiniCheck Flan-T5 capacity trial](../benchmarks/results/research/2026-09-16/WICE-MINICHECK-FLAN-V1.md)
improved recall to 72.73%, balanced accuracy to 81.52%, and F1 to 74.77%, again
with 0/32 fully unsupported accepts. It failed only the 90% precision gate:
every false support was a partially supported compound claim, and threshold
tuning cannot meet precision and recall together. The preregistered
[atomic follow-up](../benchmarks/results/research/2026-09-16/WICE-MINICHECK-FLAN-ATOMIC-V1.md)
then applied the unchanged model to all 958 human subclaims. The gold
all-subclaim rule reconstructed 357/358 parent labels, but model errors produced
76.04% precision and 66.36% recall, again with no viable frozen threshold.
Atomic decomposition is a strong boundary on this cohort, but it does not make
this verifier safe enough. Flan-T5 is not integrated.

A balanced, preregistered LLM-AggreFact development program then compared three
provider candidates under the same 90% precision and 60% recall calibration
boundary. [FactCG](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-FACTCG-V1.md)
reached 79.44% precision at 61.82% recall. An
[evidence-local Mistral cascade](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-EVIDENCE-CASCADE-V2.md)
reached 85.12% precision at 63.45% recall, but recall fell to 36.55% at 90%
precision. A pinned 8B
[Bespoke MiniCheck sentence-fusion trial](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-BESPOKE-SENTENCE-FUSION-V3C.md)
reached 83.54% precision at 60.91% recall, and only 38.00% recall at 90%
precision. It required a direct no-cache forward to finish locally after two
registered MPS generation failures; the model weights are also CC-BY-NC-4.0.
None passed calibration, none was integrated, and the separately selected
1,100-claim test cohort remains sealed.

A subsequent document-grouped
[structured FactCG stack](../benchmarks/results/research/2026-09-16/LLM-AGGREFACT-STRUCTURED-STACK-V1B.md)
tested whether exact sentence-level token alignment, numeric and capitalized
anchors, and aligned negation could safely calibrate the MIT-licensed scorer.
Every outer decision used a threshold learned from inner out-of-fold training
predictions, with exact documents confined to one fold. None of the five inner
cross-fits found a threshold at 90% precision and 60% recall. Post hoc outer
scores reached only 81.13% precision at 60.18% recall and 25.09% recall at 90%
precision. Shallow lexical structure is therefore also rejected on this cohort;
the sealed test set remains unopened.

The preregistered source-bound typed-reference trial then replaced generated
quotations with claim/evidence identifiers, but its 98% integrity gate became
mathematically impossible after 285 valid cases, 17 timeouts and eight
structural failures among 310 observed cases. A durable native-tool diagnostic
eliminated the transport failures but showed that redundant generated ranges
remained unstable. The subsequent
[source-token atomic-partition diagnostic](../benchmarks/results/research/2026-09-17/LLM-AGGREFACT-ATOMIC-PARTITION-V2.md)
reduced the provider task to complete source-token groups and obtained 310/310
valid DeepSeek partitions plus 307/310 valid Qwen partitions with three safe
abstentions. Structural success did not improve classification: the paired
unsplit FactCG control reached 75.90% balanced accuracy and a 16.88%
false-support rate, versus 74.67%/30.00% for DeepSeek atoms and 75.52%/35.62%
for Qwen atoms. Neither provider reached 90% precision at 60% recall. This
decomposition path is rejected, no larger local model is warranted for it, and
the external test set remains unopened.

A final whole-claim
[evidence-lattice diagnostic](../benchmarks/results/research/2026-09-17/LLM-AGGREFACT-EVIDENCE-LATTICE-V1.md)
scored each original claim against each ranked segment, both segments, and both
segment orders. The lower score across pair orders produced a small post hoc
balanced-accuracy increase from 75.90% to 76.25%, with false support increasing
from 16.88% to 17.50%. Selecting the best evidence subset regressed to 72.69%
balanced accuracy and 20.62% false support. No static lattice policy reached 90%
precision at 60% recall. Evidence-subset maximization and learned tuning on this
exposed cohort are therefore rejected; the external test set remains unopened.

The next registered
[HHEM-2.1-Open capacity trial](../benchmarks/results/research/2026-09-17/LLM-AGGREFACT-HHEM-WINDOWED-V2.md)
used 780 fresh balanced development claims after excluding all 310 previously
observed identities. Its first joined-evidence protocol failed closed before any
selected model prediction because a prompt exceeded the registered
non-truncating limit. The superseding protocol covered every selected evidence
word once across 1,512 source-ordered windows of at most 512 tokens. HHEM still
failed calibration: its best balanced accuracy was 72.82%, and its best precision
above 60% recall was 77.67%. The exact-cohort FactCG control reached 74.87% and
79.46%, respectively. HHEM is not integrated, and the external test remains
sealed. This closes static maximum-over-window verification with that model;
future work needs fresh data and explicit partial-support/abstention semantics.

A fresh, preregistered
[full-source support-certificate trial](../benchmarks/results/research/2026-09-17/LLM-AGGREFACT-SUPPORT-CERTIFICATE-V1.md)
then tested those semantics directly with DeepSeek 4.1 Flash. Every accepted
certificate covered all substantive claim tokens with source-bound evidence IDs
and `supported`, `contradicted` or `unknown` atoms; the final claim was supported
only when every atom was supported. Complete documents replaced the FactCG
ranker. The provider produced 782/800 valid certificates, missing the 98%
integrity gate by two. It reached 78.31% precision, 65.00% recall, 73.50%
balanced accuracy and an 18.00% false-support rate. Reveal and LFQA passed the
precision/recall boundary individually, while false support rose to 42% on media
summaries and 36% on meeting summaries. This domain instability blocks global
enablement. The routed provider completed 930 transport calls without a failure,
so transport and structural source binding are no longer the main constraint;
semantic verification quality is.

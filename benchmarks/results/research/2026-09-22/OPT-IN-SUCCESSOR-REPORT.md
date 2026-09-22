# Opt-in interaction study: corrected successor

**Date:** 2026-09-22. **Status:** execution in progress; this report distinguishes
complete comparisons from pending work. Production remains
`a66ee854325890c6bc28f6b515efeb6ed7df4deb`, with no default changes, deployment,
merge or push. Research branch: `research/opt-in-interactions-2026-09-22`.

## Question and repaired protocol

Which experimental features improve PRME's answers at a fixed memory budget,
and which failure mechanisms should subsequent development address? The goal is
trustworthy memory with reproducible, held-out quality advantages, not a larger
score obtained by weakening provenance or discarding inconvenient runs.

The [original failed study](OPT-IN-INTERACTIONS-REPORT.md) remains intact. Its
reader exhausted the 1,024-output-token limit; its incomplete results support
neither a quality comparison nor a recommendation against changes. The user
authorized implementing repairs and continuing the experiment.

The [successor protocol](OPT-IN-SUCCESSOR-PROTOCOL.md) and
[machine registration](opt-in-successor-v2-registration.json) freeze a common
8,192-output-token allowance for control and treatment, all 500 LongMemEval-S
questions, exact official prompts/scoring, a 4,096-token memory budget with 100
reserved tokens, and the user-selected Ollama `deepseek-v4.1-flash:cloud` reader
and judge. The model tag/digest, temperature 0, seed 42, thinking disabled and
65,536 model context are recorded. The hosted alias does not guarantee immutable
weights. These results are not directly comparable to prior Qwen or GPT scores.

Each arm requires all 500 cases plus authenticated contexts, requests, durable
receipts and pack hashes before it receives a score. Terminal failures invalidate
the entire arm; the artifacts remain and other prespecified arms continue.
Historical retrieval arms reuse immutable source packs through private copies.
Store-time arms use fresh actual sequential ingestion and a separate fresh
control, with matched research fixture IDs and admission clocks. Their cost
includes every nonempty source turn. The [scheduling amendment](opt-in-successor-scheduling-amendment-v2.json)
allows independent fresh packs to run concurrently; latency and ingestion wall
times are measured under shared load, not as isolated serving performance.
After the packing diagnostic completed, a [second retrieval lane](opt-in-successor-scheduling-amendment-v3.json)
reused its available provider capacity for the already registered reranker,
reformulation and temporal arms. No cases or scoring rules changed.

## Completed production control

The [authenticated baseline](opt-in-successor-v2-analysis-01-complete.json)
scored **437/500 (87.4%)** with zero terminal provider failures. Context use
averaged 3,964.75 tokens (p50 3,976; p95 3,996). Cold retrieval p50/p95 was
0.621/2.346 seconds; immediate warm p50/p95 was 0.590/2.769 seconds. Historical
ingestion cost is reused and explicitly distinguished from fresh ingestion.

| Category | Correct / total |
|---|---:|
| Single-session user | 69/70 |
| Single-session assistant | 53/56 |
| Single-session preference | 27/30 |
| Multi-session | 100/133 |
| Temporal reasoning | 115/133 |
| Knowledge update | 73/78 |
| Abstention, overlapping the above categories | 25/30 |

Among the 63 incorrect answers, 44 had all annotated turns among returned
candidates but lacked some in the packed context, 14 contained complete
annotated evidence, and five abstention questions had no applicable source
annotation. All required annotated turns appeared in 403/470 applicable packed
contexts. A separate [source-literal audit](opt-in-source-literal-baseline.json)
checked that whole original source text, rather than just a node ID or reference,
was present; it confirmed these counts. An annotation match does not establish
semantic sufficiency or prove that the reader alone caused a miss.

## Complete episode-routing comparison

The [complete episode arm](opt-in-successor-v2-analysis-02-complete.json), with
two episodes and eight local records per episode, scored **345/500 (69.0%)**:
eight paired wins, 100 losses, **−18.4 points**, and a paired 95% bootstrap
interval of **−22.2 to −14.8 points**. Its Holm-adjusted discordant-pair p-value
is `3.06e-20` in the full registered family, retaining the slots of unfinished
comparisons. There were no provider failures or retries.

Complete annotated evidence fell from 403 to 283 of 470 applicable cases, with
131 new complete-evidence losses. Multi-session answers fell by 45, temporal
answers by 32, preferences by nine, knowledge updates by seven and user facts
by one; assistant answers improved by two and abstention stayed tied. Memory
use averaged 3,960.27 tokens, so the regression did not arise from a smaller
configured budget. Cold retrieval p50/p95 was 1.398/8.138 seconds under shared
load; this is not a controlled serving-speed comparison.

**Reject this unconditional episode policy as a production-default candidate
on this cohort.** This does not reject caller-scoped episode routing in every
task. The [earlier source-only grid](../2026-09-18/LONGMEMEVAL-S-EPISODE-COMPOSITION-V1.md)
already found the same 403→283 coverage transition; the present trial adds
matched-reader answer evidence. Inspection confirms that the packer reserves
routed episode records ahead of the ordinary multi-path pool. Simply changing
the inherited-score decay would not remove that priority tier.

## Development error review

The representative review used the first four question IDs by SHA-256 ordering
within each of the three baseline error classes. It did not rescore examples or
select a favorable evaluation subset. This is qualitative development analysis.

* Missing packed evidence includes a property count missing a rejected property
  (`gpt4_7fce9456`), a latest subscription missing the relevant update
  (`gpt4_2f56ae70`), a song answer using a different song's material (`eaca4986`),
  and a dated business milestone without the relevant source (`eac54add`).
* Complete-source errors include an omitted associate degree when totaling
  education (`gpt4_372c3eed`) and selection of an incorrect star-count update
  (`0f05491a`). Two examples expose reference/source qualification issues: a
  future storage plan versus the current shoe location (`07741c45`), and an
  approximate follower count versus an exact earlier count (`a2f3aa27`). The
  official scores remain unchanged; these are not reasons to collapse future
  plans into current truth or approximations into exact quantities.
* Abstention failures include a job-title mismatch (`031748ae_abs`), an unstated
  cow purchase (`gpt4_70e84552_abs`), treating generic bus-fare advice as the
  user's actual fare (`09ba9854_abs`), and answering a football question from
  baseball evidence despite noticing the mismatch (`0ddfec37_abs`).

The immediate development priorities are evidence coverage during packing,
reasoning over complete source sets, and preserving the distinction between the
question's premise and the facts actually supported by memory. A retrieval win
cannot by itself fix every reader error or annotation ambiguity.

The complete [descriptive omission audit](opt-in-baseline-omission-audit-v1-result.json)
narrows the first priority. Of 886 annotated turns, 94 were missing from packed
context although all were returned. The 70 missing turns associated with
incorrect answers had median final rank 75, versus rank six for annotated turns
present in incorrect-answer contexts. Their median source length was only 66
tokens, versus 64 for the present group; these are raw content lengths, not
serialized entry costs. Sixty-seven of the 70 missing turns were user messages,
and 37 had another record from the same session already packed. Thus blanket
compression or broader session coverage would not directly address the main
observed pattern. Better query-to-evidence ranking and selective recovery of
related turns are more specific hypotheses. These are post hoc associations,
not evidence that a particular ranking change will improve answers.

## Applicability and separate diagnostics

The [source audit](opt-in-source-applicability-result.json) found 246,738 nodes
with event provenance but no graph-node evidence references in these raw-turn
packs. Evidence projection and augmentation need graph-node references to route
source passages. Their arms remain scheduled, but an inactive flag cannot
establish benefit or harm for the intended derived-claim workflow. A meaningful
test of that workflow needs a separately matched derived-ingestion study.

The [packing diagnostic registration](opt-in-packing-oracle-v1-registration.json)
deliberately uses evidence annotations in an offline selector. It prioritizes
whole annotated source turns already present among returned candidates, retains
the original remainder when space permits, and obeys the same 3,996-token
effective limit. All 67 incomplete annotated source sets fit; the other 433
contexts remain byte-identical repeats. All 500 questions are evaluated, whether
the original answer was right or wrong. This diagnostic is not deployable
retrieval, an achievable upper bound, or a member of the promotion matrix.
Annotations and reference answers never enter PRME retrieval, temporal relation
providers or Jev.

The [complete authenticated diagnostic](opt-in-packing-oracle-v1-result.json)
scored **473/500 (94.6%)**, versus 437/500 for the primary baseline: 44 paired
wins, eight losses, and a **+7.2-point difference (95% paired bootstrap interval
+4.4 to +10.0 points)**. All 1,000 reader/judge requests completed without a
retry. Mean memory context was 3,959.22 tokens. This estimates a development
diagnostic difference, not a deployable feature gain.

| Input group | Baseline | Diagnostic | Wins / losses |
|---|---:|---:|---:|
| 67 changed contexts | 23/67 | 62/67 | 42 / 3 |
| 433 byte-identical repeated contexts | 414/433 | 411/433 | 2 / 5 |

Most of the gain is concentrated where the selector added missing evidence.
The unchanged inputs demonstrate residual reader/judge variation even with
temperature 0 and a fixed seed. The three changed-context losses show that
annotated evidence priority is not universally beneficial. Category totals were
user 70/70, assistant 56/56, preference 29/30, multi-session 119/133, temporal
128/133 and knowledge-update 71/78; abstention remained 25/30. The two-answer
knowledge-update decline must remain visible alongside the overall gain.
The result supports developing label-free context selection while preserving
source qualifiers and testing displacement losses. It does not authorize a
default change or allow replacing the original baseline with a favorable repeat.

All three changed-context answer losses were reviewed: `gpt4_2f8be40d` counted
an additional sister's wedding, `bf659f65` declined to infer a purchase from a
signed vinyl record, and `59524333` preferred an older explicit 7 pm gym schedule
over a newer explicit 6 pm schedule. The added/removed source records remain
available in the private artifacts. These examples distinguish distractor,
annotation and update-reasoning risks; they do not establish a single cause for
every score transition.

The follow-up [marginal packing assay](opt-in-marginal-packing-v1-registration.json)
implements the previously proposed change in a research module: original
candidate relevance, a bounded episode bonus, diminishing weight for additional
same-session records, and the unchanged whole-source serializer/budget. It
preserves instruction/pin priorities and the baseline's leading multi-path
anchor. Three fixed policies face all 500 source cases. A policy advances only
with gains in both complete source sets and mean source fraction; all category
and per-question losses remain reported. If one qualifies, a separately frozen
500-case candidate answer trial and a new 500-case baseline-context repeat form
the primary follow-up comparison. No production source or default is modified.

Prior negative work also constrains this follow-up. The earlier
[session-marginal source grid](../2026-09-18/LONGMEMEVAL-S-SESSION-MARGINAL-V1.md)
found that broadening session coverage displaced necessary turns, and its mild
arm's [answer diagnostic](../2026-09-18/LONGMEMEVAL-S-SESSION-MARGINAL-ANSWER-V1.md)
tied 25/31. The present three-policy assay is an incremental development test,
including a query-dependent bounded local-relevance bonus and whole-source
selection; session diversity itself is not a new or established remedy. These
prior reports were reviewed in detail after the new registration but before
its waiting launcher had evaluated any cases. They do not change its frozen
selection or reporting rules. A negative full-cohort source result ends this
follow-up without another parameter search or an answer trial.

The [additional combination plan](opt-in-exploratory-combination-plan.json)
separates experimentation from promotion. It preserves the original strict
gated selection, while adding an exploratory combination of the two best
observed active individuals after all eight have valid matched comparisons.
Negative differences remain eligible for that explicit exploratory test; failed
arms cannot simply be omitted and inactive unchanged-input flags cannot win on
reader noise. The rule was frozen before inspecting any completed individual
candidate answer result. Duplicate configurations are aliases, not new replicates.

The [secondary analysis plan](opt-in-successor-secondary-analysis-registration.json)
also compares unchanged-input repeat arms and estimates the four registered
factorial contrasts. Reader/judge variation never replaces the primary baseline.

Temporal relations retain the documented answer-blind Ollama/TypeSafe Jev
protocol. Product alignment/Jev remains a separate caller-selected pair and
explicit review workflow; its authored operational probe is not retrieval or
held-out semantic-quality evidence.

## Validation and remaining work

The corrected ingestion harness passed 13 tests, and the source-priority
diagnostic passed five tests. Authored reranker and reformulation provider
preflights passed; these establish operability, not benchmark benefit. A separate
local PostgreSQL 16.11 test database ran 59 backend tests with no skips. The
feature/recovery suite then ran 152 tests with three backend-specific skips.
These are source-checkout checks, not a completed installed-package release gate.

The remaining fixed feature matrix and fresh-ingestion arms are still running. Paired
10,000-draw question/source-cluster intervals, category regressions, evidence
losses and Holm-adjusted discordant-pair tests will be reported for complete
matched comparisons. Best-arm combination selection waits for every individual
arm and its matched control; the full-feature arm is exploratory. No arm is
currently recommended for promotion on this unfinished evidence. The tested
unconditional episode policy has a completed negative finding.

MemoryAgentBench Banking, EventQA, Conflict and Detective inputs have been
prepared with the prior registered preprocessing pins. The [MAB matrix](opt-in-mab-matrix-v1-registration.json)
binds 100 Banking, 500 EventQA, 100 Conflict and 71 Detective questions. All
eight authored reader checks and nine harness checks passed; initial harness
test failures and repairs are retained in the [validation record](opt-in-mab-validation.json).
The [stage launcher](opt-in-mab-launch-plan.json) waits for LongMemEval and its
combination to finalize. If the selected combination has a new configuration,
a child registration adds it before any MAB inference. BEAM and MemoryArena
remain later stages. None of the 500
LongMemEval histories qualifies as untouched confirmation; promotion requires a
separately audited unused source cohort and relevant release/backend regressions.
No production-default decision is authorized by this study alone.

One fixture detail is explicit in the validation record: LongMemEval matches
source-node clocks and `Event.created_at`, while the separate immutable
`Event.timestamp` records actual admission time. The registered graph retrieval
does not use that field. The new MAB fixture matches both event clocks as well;
the already running LongMemEval execution was not rewritten.

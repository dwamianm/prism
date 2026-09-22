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

The fixed feature matrix and fresh-ingestion arms are still running. Paired
10,000-draw question/source-cluster intervals, category regressions, evidence
losses and Holm-adjusted discordant-pair tests will be reported for complete
matched comparisons. Best-arm combination selection waits for every individual
arm and its matched control; the full-feature arm is exploratory. No arm is
currently recommended for promotion on this unfinished evidence.

MemoryAgentBench Banking, EventQA, Conflict and Detective inputs have been
prepared with the prior registered preprocessing pins. Their inference stage
follows LongMemEval. BEAM and MemoryArena remain later stages. None of the 500
LongMemEval histories qualifies as untouched confirmation; promotion requires a
separately audited unused source cohort and relevant release/backend regressions.
No production-default decision is authorized by this study alone.

# LLM-AggreFact Bespoke sentence fusion v3c

## Verdict

Rejected. The preregistered development calibration required at least 90%
supported precision and 60% supported recall. No threshold over the completed
1,100-claim development cohort met both requirements, so the runner did not read
or score the separately selected 1,100-claim test cohort. This result does not
authorize integrating Bespoke MiniCheck as a PRME verifier.

At the highest-recall development operating point with at least 90% precision,
precision was 90.48% and recall was 38.00%. At the highest-precision point with
at least 60% recall, precision was 83.54% and recall was 60.91%. The latter is
below the earlier registered evidence-local Mistral cascade, which reached
85.12% precision at 63.45% recall on the same selected development identities.

## Bound execution

- Model: `bespokelabs/Bespoke-MiniCheck-7B` at revision
  `1ed7786bcda3fa1dc35f7c4ed9e3f36b785d33b8`.
- License: CC-BY-NC-4.0. Even a passing result would not permit bundling these
  weights as a commercial default.
- Cohort: 50 examples per label from each of 11 LLM-AggreFact datasets, selected
  by the registered identifier hash order; 1,100 development cases total.
- Method: NLTK claim-sentence segmentation, source chunks capped at 7,800 model
  tokens, and minimum-over-claim-sentences of maximum-over-source-chunks fusion.
- Score: full-vocabulary probability mass of the registered single-token `Yes`
  forms from the final prompt-position logits, using a direct forward pass with
  `use_cache=False`.
- Runtime: 1,365 claim-sentence/source-chunk checks in 2,261.361 seconds on Apple
  MPS with bfloat16. Peak MPS tensor allocation was 18.172 GiB and peak driver
  allocation was 38.220 GiB. No checkpoint resume occurred.
- Registration SHA-256:
  `39d0a52bf8f037aa0e6d12e1416bc244809d01513f7e11981dd928136edaf2ed`.
- Result payload SHA-256:
  `af5f0ff06237305c6158cdcba0ae99402b52391eec91945c60126030b6b4c006`.
- Result file SHA-256:
  `e6420a0151c305d24fb994523f6b18be715f601521124d9383c9de4ce4174b54`.

The source-free result contains identifiers, labels, scores, counts, hashes and
runtime metadata. It contains no document, claim or prompt text. Its canonical
payload hash verifies, and its registration hash matches the committed
registration exactly.

## Development operating points

All rows below are post hoc diagnostics over the completed development scores.
Only the first two rows directly test the preregistered calibration boundary.
Prediction uses the registered strict `score > threshold` rule.

| Selection | Threshold | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|---:|
| Maximum recall at precision ≥ 90% | 0.914735 | 90.48% | 38.00% | 67.00% | 4.00% |
| Maximum precision at recall ≥ 60% | 0.817265 | 83.54% | 60.91% | 74.45% | 12.00% |
| Threshold 0.5 diagnostic | 0.500000 | 74.15% | 83.45% | 77.18% | 29.09% |
| Maximum balanced accuracy | 0.622147 | 77.86% | 80.55% | 78.82% | 22.91% |
| Maximum F1 | 0.582176 | 76.57% | 82.00% | 78.45% | 25.09% |

The development ROC AUC was 0.85710. It is diagnostic because ROC AUC was not a
promotion gate.

At the post hoc 0.817265 threshold, the per-dataset results show that one global
threshold hides substantial task variation:

| Dataset | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|
| AggreFact-CNN | 75.00% | 72.00% | 74.00% | 24.00% |
| AggreFact-XSum | 70.00% | 42.00% | 62.00% | 18.00% |
| ClaimVerify | 77.08% | 74.00% | 76.00% | 22.00% |
| ExpertQA | 72.73% | 32.00% | 60.00% | 12.00% |
| FactCheck-GPT | 100.00% | 44.00% | 72.00% | 0.00% |
| Lfqa | 93.33% | 84.00% | 89.00% | 6.00% |
| RAGTruth | 90.32% | 56.00% | 75.00% | 6.00% |
| Reveal | 96.97% | 64.00% | 81.00% | 2.00% |
| TofuEval-MediaS | 82.00% | 82.00% | 82.00% | 18.00% |
| TofuEval-MeetB | 80.85% | 76.00% | 79.00% | 18.00% |
| Wice | 88.00% | 44.00% | 69.00% | 6.00% |

## Infrastructure findings

The earlier [v3 run](LLM-AGGREFACT-BESPOKE-SENTENCE-FUSION-V3-INCOMPLETE.md)
failed with an MPS out-of-memory error under batch size two. The registered
[v3b run](LLM-AGGREFACT-BESPOKE-SENTENCE-FUSION-V3B-INCOMPLETE.md) completed 250
cases at batch size one, then reproducibly stopped making progress inside
`model.generate` with roughly 45 GiB of graphics allocation, even after a fresh
process resumed on a 716-token prompt.

The direct first-token forward used by v3c is mathematically aligned with the
one-token classification target and removed the unnecessary generation KV
cache. A synthetic supported case differed from the generation score by less
than 0.00001 while ordinary driver allocation fell to roughly 17 GiB. V3c then
completed the exact row that stalled v3b and all maximum-length prompts. The
longest eager-attention prompts still temporarily raised driver allocation to
38.220 GiB and caused paging, so this runtime remains unsuitable for a default
developer workflow even apart from model quality and licensing.

## Decision

Keep the built-in verifier opt-in and unchanged. Preserve v3c as a negative
result; do not tune on or open the sealed test cohort. Direct specialized-model
classification and evidence-local general-model cascades have now both missed
the same fixed safety/utility boundary. The next verifier experiment should test
explicit claim-to-evidence relation alignment or deterministic structured
checks, registered before reading new outcomes, rather than another unstructured
model vote over the same evidence.

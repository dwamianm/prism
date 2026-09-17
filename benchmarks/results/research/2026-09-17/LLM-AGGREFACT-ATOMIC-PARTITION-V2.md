# LLM-AggreFact source-token atomic-partition diagnostic

**Result: the compact source-token contract fixed structural reliability, but
provider-generated decomposition did not improve factual classification.** On
all 310 cases already observed during typed-reference v2, the unsplit pinned
FactCG control reached 0.759 balanced accuracy with a 16.9% false-support rate.
DeepSeek partitions reached 0.747 and 30.0%; Qwen partitions reached 0.755 and
35.6%. This is post hoc development evidence. The external test cohort remained
sealed, and no implementation was promoted.

## Question and boundary

The earlier typed-reference protocol required a provider to generate redundant,
mutually constrained claim, subject, relation, object and qualifier ranges. Its
98% integrity gate became mathematically impossible after 285 valid results, 17
timeouts and eight structural failures among 310 observed cases. Native Ollama
tool calls then removed the transport failures, but neither DeepSeek cloud nor
the pinned local Qwen model reliably satisfied that representation.

This diagnostic tested a smaller responsibility split:

- the provider only partitions displayed `C####` claim token IDs into atomic
  units;
- code requires complete substantive-token coverage, exact source identities,
  at least two substantive tokens per atom and no duplicate atoms;
- nested attribution and modality assertions are allowed;
- code reconstructs atom text from the authoritative claim, so the provider
  generates no text or ranges;
- the provider never sees a factuality task or returns a support decision; and
- pinned FactCG scores every reconstructed atom against the same top-two ranked
  evidence segments, with the parent score equal to the minimum atom score.

The durable native-tool runner checkpoints every request and response, separates
transport from semantic repair attempts, verifies response hashes after restart
and fails closed after three invalid semantic attempts. Raw claims, evidence and
provider responses remain only in mode-0600 private state. Public artifacts
contain opaque case identities, counts, probabilities and hashes.

The cohort contains 150 supported and 160 unsupported claims. Every identity had
already been exposed by the aborted typed-reference v2 development run. No new
development identity and no external-test identity was accessed.

## Structural and transport results

| Measure | DeepSeek cloud | Pinned local Qwen |
|---|---:|---:|
| Observed cases | 310 | 310 |
| Valid partitions | 310 | 307 |
| Safe abstentions | 0 | 3 |
| First-attempt valid | 263 | 274 |
| Second-attempt valid | 46 | 29 |
| Third attempts, including abstentions | 1 | 7 |
| Native tool calls | 358 | 353 |
| Transport failures | 0 | 0 |
| Provider-stage wall time | 162.85 s | 1,974.09 s |
| Summed provider-call time | 638.86 s | 7,858.36 s |
| Median provider call | 1.12 s | 20.09 s |
| Maximum provider call | 22.87 s | 44.65 s |
| Mean atoms per case | 3.532 | 2.384 |

DeepSeek used `deepseek-v4.1-flash:cloud`, Ollama manifest
`e04da138d31e0c9468e982e1ae9503d06cb7e170caa16a90c17d931c4aa140f8`.
That manifest names a remote alias and does not content-address the remote
weights. Qwen used the local Q4_K_M `qwen3.5:35b-a3b` artifact, manifest
`3460ffeede5453ead027dbd2f821b12ad0aa3de54630971993babdb2165221f7`.
Both used temperature 0, seed 17 and four queued jobs. Their transport success
establishes that native tool calls and durable execution solve the earlier
operational bottleneck.

## Factual classification results

The paired unsplit control scored each original claim with the same FactCG
model and evidence used for its atoms. Thresholds below are the best post hoc
balanced-accuracy points on this already observed cohort.

| Representation | Threshold | Precision | Recall | Balanced accuracy | False-support rate |
|---|---:|---:|---:|---:|---:|
| Unsplit FactCG | 0.572 | 79.23% | 68.67% | 75.90% | 16.88% |
| DeepSeek atoms, minimum | 0.286 | 71.26% | 79.33% | 74.67% | 30.00% |
| Qwen atoms, minimum | 0.238 | 69.52% | 86.67% | 75.52% | 35.62% |

At a recall floor of 60%, the highest observed precision was 81.51% for the
unsplit control, 75.63% for DeepSeek atoms and 81.25% for Qwen atoms. At a
precision floor of 90%, recall was 8.00%, 2.00% and 12.00%, respectively. None
approached the frozen joint target of 90% precision and 60% recall.

The models produced the exact same partition on only 7 of 310 cases. Against
the unsplit control at each representation's own best balanced-accuracy
threshold, DeepSeek changed 17 errors to correct decisions but changed 22
correct decisions to errors. Qwen changed 27 errors to correct decisions and 30
correct decisions to errors. The Qwen scorer processed 1,449 expanded
evidence/claim pairs in 212.34 seconds; the finer-grained DeepSeek partitions
required 1,914 pairs and 289.85 seconds. The common evidence-ranking pass took
112–135 seconds.

## Decision

Reject provider-generated source-token atomic partitioning for the current
verification path. It is structurally reliable and source-bound, but it adds
latency, provider dependence and disagreement while reducing or merely matching
the unsplit task model. The 25-case failure subset had suggested a large Qwen
advantage; the complete observed cohort disproved that inference.

Use DeepSeek cloud through Ollama for fast development diagnostics. Keep the
content-addressed local Qwen artifact as the reproducibility control. Do not
download a larger local model for this representation: the measured limit is
the decomposition/scoring interface, not transport or obvious model capacity.

Do not open the external test cohort and do not add this path to
`ClaimVerifier`. The next verification design should preserve the original
claim semantics, localize evidence or counterevidence without provider-authored
claim fragmentation, and demonstrate a viable precision/recall frontier under
document-grouped development validation before preregistration.

## Artifact integrity

| Artifact | File SHA-256 | Canonical result SHA-256 |
|---|---|---|
| DeepSeek partition result | `0b0b8e2e2bfd26d889be19362a493f8f32f4c14e647109c184a47b020eb54c49` | `24be9cbf1f2824721d8db6ad0632e8ef8c3c4a636f8681481dcd01283feee24d` |
| Qwen partition result | `1503ecd7174259acaf3786034e9b9847a8028a17842dfc40824312c810f9da42` | `0f9ad865d361eae76bc7258a8802237f7af5b4ee8bc0f4ad0340612ebb0ea5ef` |
| DeepSeek scoring result | `96c4cfa83b7767f662f5986de3b34968fd5c6d4f91fb9764f6569aa7d8865454` | `b145c638868bf5a7a2b9855c30ac523df4c38db3ffa4e132d8392dc06ee51350` |
| Qwen scoring result | `fd94a4c7a2ca2c9be149894413f2f429c05ec0ef0b5f4cee210c077dfba63035` | `0d122c223468b8a38f531764eaa9e01e6014112b4f5a78ca73eda72b43b5db6d` |

Every public JSON artifact states `development_only: true` and
`test_accessed: false`. The scoring artifacts reproduce the identical unsplit
probabilities and metrics independently, bind the partition result and private
state by SHA-256, and contain no claim, evidence, tool-argument or provider
response text.

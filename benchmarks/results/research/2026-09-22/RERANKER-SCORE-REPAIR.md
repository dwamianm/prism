# Reranker score-scale repair development study

Date: 2026-09-22. Branch: `research/reranker-score-repair-2026-09-22`.
Parent research branch: `research/opt-in-interactions-2026-09-22` at `5650161`.
Production stays at `a66ee85`; no merge, deployment or default change is authorized.

The complete current reranker arm fell from 437/500 to 284/500 answers under
the registered Ollama DeepSeek reader/judge. A code and artifact audit found
that the top-100 neural blends were compared with an unchanged-score tail by
session expansion and balanced packing. A unit regression reproduces a tail
record displacing the entire reranked prefix solely because of this scale
mismatch. This motivates a repair; it does not establish a repaired quality win.

The research-only `RankEnvelopeReranker` sorts the original prefix score values
and assigns them in the existing neural-blend ranking order. It retains the
raw neural output, original composite score and assignment lineage, and leaves
the tail's scores and lack of neural judgment intact. Equal assigned values use
canonical UUID ties. This is an ordinal ranking policy, not trained calibration
or a relevance probability. Ordinary session expansion and packing then run on
the resulting common scale. No public configuration field activates the class.

The only package changes in this isolated branch allow replay of an explicit
`neural_rank_assignment` operation, whose `coefficient` stores the assigned
ranking value. A preceding `neural_blend` retains the actual model output.
Receipts with assignments require schema 13 and an execution descriptor.
Ordinary execution still emits schema 12, and old receipt fields, canonical
bytes and checksums remain unchanged. No source claim or provenance is rewritten.

The complete source assay will first reproduce all 500 original baseline and
reranker contexts, returned IDs and scores from private copies of the verified
packs. The repair reuses each case's exact original neural inference for
identical query/document pairs; no annotation or answer enters ranking.
New receipts and complete raw-score/assignment traces are retained. Treatment
retrieval timings exclude a second inference and cannot be advertised as a
serving-speed improvement. Any replay, provider/backend, receipt or budget
failure invalidates the full assay; the original failed-quality arm remains.

If both complete-source count and mean source fraction improve over the broken
reranker, the study freezes all 500 repaired contexts and a new baseline-context
repeat before answer generation. Primary answer comparison uses that new control;
the original 437/500 baseline is never replaced. An improvement over the broken
reranker alone does not justify promotion. Both control and candidate must finish
and authenticate, every category/loss remains reported, and a production proposal
would still need an untouched cohort and release review.

The assay waits for the original fixed historical matrix to finish, preserving
its source hashes and execution. The original fresh-ingestion arms and queued
MemoryAgentBench stage continue in the parent worktree.

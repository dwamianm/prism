# Current provenance and memory-learning research

Primary-source review on 2026-09-12, alongside the frozen PRME development
comparison. These papers supply hypotheses and additional comparison candidates;
their reported scores are not reproduced PRME results.

[MemIR, May 2026](https://arxiv.org/html/2605.25869v1) separates verbatim source
spans, retrieval cues and supported claims. Retrieval hits project into bundles
centered on claims and their provenance, followed by reranking and selection.
Its reported LoCoMo and BEAM experiments motivate testing whether retaining
explicit referents, temporal cues and evidence associations helps attribution
and aggregation. The relevant PRME hypothesis is that compact context should
retain those associations instead of treating every retrieved string as an
equally authoritative fact. Its extra generation and selection stages must be
included in cost and failure accounting. A nonempty support pointer is not, by
itself, an entailment test; that is a limitation to test independently.

[Agent Zero Memory, August 2026](https://arxiv.org/html/2608.29606v1) combines
an event timeline, entity-event graph and hierarchical documentary store with
tool-using retrieval and source opening. Its citation policy limits citations to
opened evidence. This makes it an additional architecture/comparator to audit,
especially for queries that require another retrieval step. The inference for
PRME is to evaluate bounded source-following against the current fixed-context
baseline. Reading a source and citing an allowed ID still does not prove that
an answer correctly interprets it. Compare actual end-to-end quality, calls,
tokens and latency; do not compare its published total to PRME's source recall.

[AttriMem, revised August 2026](https://arxiv.org/abs/2607.21106v3) uses local
attribution-derived rewards alongside overall answer rewards to learn memory
construction. Its stated target is finer credit assignment than a single task
outcome. This supports investigating content-level learning signals, but does
not validate PRME's present candidate-feedback fitter or justify activating
weights from one tenant's feedback. Full retriever/packing trials, separated
validation and owner-scoped rollback remain necessary.

Before implementing a new default, finish the registered answer comparison and
two-model extraction probe. Separate source selection, claim attribution and
reader failures. Then choose one bounded hypothesis, preserve the raw source,
pre-register its cost and category guards, and test on independent cases. Do
not discard raw evidence solely because the extractor omitted a claim. Source
opening also needs a bounded budget and an explicit exhaustion/abstention path.
The cited architectures have not been installed or reproduced in this review.

# Later-stage protocol audit — 2026-09-22

This is a preparation note, not a benchmark registration or outcome. No BEAM
or MemoryArena answer calls have been started by this study. Execution remains
conditional on the earlier registered stages completing.

## MemoryAgentBench

The registered prepared tasks use exact match (Banking and Detective) or
substring exact match (EventQA and Conflict). Their Boolean paired differences
therefore preserve the official task metric; they do not threshold a continuous
F1 score. The harness also retains every official auxiliary score. Each task
keeps its registered generation limit and deterministic official scorer.

Native ingestion and each fresh-ingestion arm preserve NOTE/tool pieces,
PROJECT scope and the adapter's context-level session partition. The retrieval
flag cannot silently replace this with source-chunk sessions. An ingestion
feature that does not activate on these source types is inapplicable evidence,
not a demonstrated quality success. Retrieval-only arms clone native masters;
fresh arms have their own directly stored control. Source graph identity is
checked after retrieval. These facts were reviewed in
`opt_in_mab_support.py` and `opt_in_mab_matrix.py` before dataset inference.

## BEAM

The existing accepted BEAM registrations use the pinned upstream top-50 memory
list and its reader serialization. Although PRME's configuration includes a
4,096-token packing budget, `beam_service.py` and
`run_beam_retrieval_ablation.py` return `response.results`, not the packed
bundle. Thus the actual reader input is limited by record count, not by the
packed-context budget. The integration documentation explicitly describes this
boundary.

A matched continuation must retain that top-50 protocol, source diversity
(`max_per_source=1`), source-evidence timestamps, official prompts, nugget judge
and complete ten-ability selection. It must measure actual serialized reader
tokens. Changing the adapter to serve a 3,996-token bundle would require a
separate prospective protocol and newly matched baseline, and cannot be
represented as an unchanged historical comparison.

This distinction also affects feature applicability. Changes to returned
candidates can affect the top-50 input, while bundle-only changes can be
invisible to this reader. In particular, accepted temporal guidance lives in
the bundle; a top-50 list that omits it cannot evaluate the answer benefit of
that guidance. Balanced packing itself is not the reader's selection rule in
this adapter. Record activation and actual reader-input changes instead of
assuming that an enabled configuration reaches the evaluated model.

The existing extracted conversation-0 and conversation-1 packs contain source
links that the historical LongMemEval direct-store artifacts lack. Both BEAM
conversations are now inspected development data. Their prior positive and
negative results must remain visible; neither can be called untouched again.
Further conversations need an exposure audit and prospective selection before
content inspection. The existing answerer and judge are separate Ollama models;
the user's replacement of the inaccessible LongMemEval GPT reader does not
silently change these historical BEAM identities.

The local artifact inventory did not find those former BEAM execution packs,
the normalized dataset cache or the upstream checkout under this project's
data directory or the prior temporary locations. Their published hashes and
aggregate reports remain. Do not pretend a newly extracted pack is the old
immutable artifact: retrieve the pinned dataset/upstream sources and register
fresh matched controls if these files cannot be restored exactly. Historical
aggregate scores cannot substitute for a newly matched control.

The accepted extracted profile uses durable `ingest()`, while the documented
raw adapter uses direct store admission. Store-time flags therefore require
their own fresh raw/direct-store controls and measured activation. Merely
enabling a store-time flag on an extracted replay does not exercise its hook.
Any proposed composition spanning direct-store and derived-evidence workflows
must specify the actual admission path before registration; duplicated source
admission cannot be introduced as an unnoticed treatment.

## MemoryArena

Preserve the registered interactive client, task grouping, exact-value scoring,
tool-argument binding policy and native-history comparator. A fixed-context QA
runner cannot substitute for its interactive task metric. Previously examined
development and confirmation groups remain exposed; a new promotion cohort
requires source-level separation. The reported failures of guidance and
qualified-value restoration remain constraints on any proposed follow-up.

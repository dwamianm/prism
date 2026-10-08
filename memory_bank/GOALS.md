# PRME goals and production handoff

**Status:** active handoff, 2026-09-23; see "Current state (2026-10-08)" for
the latest work.

**Production baseline:** released v0.14.2 (tag `v0.14.2`, 2026-10-08), with the
`ingest()` and reader context defaults of 2026-10-06 (see the
[changelog](../CHANGELOG.md)).

**Research record:** [research agenda](../docs/RESEARCH-AGENDA.md)

## Current state (2026-10-08)

The release is v0.14.2 (2026-10-08). The retrieval defaults changed on
2026-09-25 (#177, #187; released in v0.13.0), and the DeepSeek defaults
baseline on the `store()` track is `prme@d811e3ed` (LoCoMo 1250/1540,
LongMemEval-S 453/500). Every variant on that track counts from zero against
it. The `ingest()` track has its own baseline (below).

v0.14.2 adds bounded grounded correction matching and an opt-in per-message
owner declaration for first-person references; saved policies and historical
identities remain unchanged. It does not change retrieval defaults.
v0.14.1 adds opt-in node/source retrieval filters, unnamed first-person
source matching and role-aware defaults for new assistant/system stores. It
preserves historical nodes and plans; consumers of a shared store must upgrade
before writing the new v13 extraction/plan records. This patch has component
and backend regression evidence, not a new live-model answer benchmark.

**Focus: benchmarks that test extracted memory.** The saved benchmark packs
were built with `store()`, which never extracts, so the evidence gate could not
see an extraction change (#210 gave 2,040 identical contexts). Work since
2026-09-26:

- Coding-memory integration shelved (#206 to #208); see CLAUDE.md.
- `enable_claim_merge` (#210) and the browser memory explorer (#211).
- `benchmarks/diagnostics/extracted_packs.py` builds packs through `ingest()`,
  and `gate --packs` replays them (#212, #91, #102). The gate reports each
  LoCoMo speaker separately (#217, #84).
- #91 part 1, `enable_windowed_extraction` (#218), and part 2,
  `enable_fact_text_resolution` (#219). Both default to off.
- A fixed development slice, `benchmarks/slices/dev-v1.json`, with its
  `ingest()` baselines (#224).
- `gate-compare` now compares two built-pack replays, and the first #91
  comparison is recorded (#225; `BENCHMARKS.md`, "Windowed extraction with
  fact text resolution on LoCoMo").
- A chat probe (`benchmarks/diagnostics/chat_probe.py`) runs a 54-turn
  scripted chat with an answer key through `ingest()` in about three minutes,
  the explorer shows each claim's subject, predicate and object, and the
  extracted-pack cache now replays a cached result as stored (`BENCHMARKS.md`,
  "Chat probe").

**Chat probe result (2026-10-01).** With the defaults, every claim's text is
its whole message, because validation widens the evidence quote to the
paragraph, so 44% to 47% of the records in a probe's context repeat another
record. The owner's own claims hang off 19 per-message "I" nodes, so the job
and diet changes superseded nothing. Validation discarded 48 proposed claims
against 77 kept, mostly because the model named the owner ("Dana, has
partner, Sam") where the quoted sentence says "I". The organizer merged
nothing, and the #91 options reduced the repetition but attached no claim to
the owner. The cache
replay bug had dropped 261 claims on 75 replayed turns of the 73 complete
`ingest-baseline-lme-deepseek` packs, which includes the 42 slice packs.

**#91 result on LoCoMo (all 1,540 questions).** Window of 4 turns with fact
text resolution, against the `ingest()` baseline, both on
`deepseek-v4.1-flash:cloud` (`e04da138`, Ollama 0.34.4): evidence recall +1.6
points (intervals exclude zero over questions and conversations), all evidence
packed +1.0 (not clearly above zero), projected accuracy +0.7 (not
significant). The window alone had lowered evidence coverage on conv-26 from
0.86 to 0.74; with resolution it is 0.85. Only 60 relative dates were resolved
against 4,796 names. No repeated build has measured build-to-build noise.

**Builds in `data/extracted-packs-v1/` (ignored, this machine only):**
`ingest-baseline-locomo-deepseek` (complete), `fact-text-w4-locomo-deepseek`
(complete), `windowed-w4-locomo-deepseek` (conv-26 only), and
`ingest-baseline-lme-deepseek` (the 42 slice packs; the full build was stopped
by the owner at 34 of 500 on 2026-09-28; ask before resuming it),
`grounding-locomo-full` (every LoCoMo turn extracted again on 2026-10-05, with
raw responses) and its replays. The chat probe's builds are in
`data/chat-probe-v1/chat-v1/`.

**The chat probe's recommendations (2026-10-01), all opt-in:**
`enable_speaker_references` binds a named speaker's I, me and my to the
speaker's entity (chat probe: the owner's claims on "Dana" 1 to 33; LoCoMo
gate unchanged, since LoCoMo text already names speakers). The option
`enable_claim_sentence_text` stores a claim's own sentences as its text
(chat probe: repeated context records 47% to 24%; all of LoCoMo, all evidence
packed 75.7% to 79.5%, +2.4 to +5.5 over conversations, every conversation
improved; LongMemEval-S slice 71.1% to 86.8%, +5.3 to +28.9, no losses). It
is the strongest gate result so far and the first candidate for a default
change. The LongMemEval-S slice baseline was
rebuilt with faithful replay as `ingest-baseline-lme-deepseek-r2` (190 more
claims, gate unchanged). `BENCHMARKS.md` has the full results. All of this
merged on 2026-10-05 (#226 to #229).

**Speaker grounding (2026-10-05, opt-in, stays off).**
`enable_speaker_grounding` lets a named speaker's own I, me and my ground a
claim that names the speaker or writes them as I. The extraction cache now
keeps each response before any check, so one set of calls measures both
rules. On the chat probe (27 calls) all 14 key links exist instead of 11, but
repeated context records rise from 19.9% to 23.3%. On all of LoCoMo (5,883
calls) the packs hold 10,599 claims instead of 7,136, and 96.3% of annotated
evidence turns have a claim instead of 80.0%, yet the gate loses evidence:
all evidence packed -2.0 points with the defaults and -1.0 with speaker
references and claim sentences on (both intervals exclude zero). The new
claims restate turns that retrieval already reaches, and they take context
room. With folding on (below) it costs nothing with the defaults and still
loses 0.8 points with speaker references and claim sentences (-1.8 to +0.1
over conversations). The raw responses for every LoCoMo turn are in
`data/extracted-packs-v1/grounding-locomo-full/extraction-cache`, so later
grounding and packing changes on LoCoMo need no model call. Merged as #230.

**Folding repeated text (2026-10-05, opt-in).** `packing.fold_repeated_text`
packs each source text once: in the reader format, a record whose text a
packed record from the same source event already shows is left out, unless its
line shows another date, tag or speaker, and its room goes to other records.
Receipts record it in version 22. On the chat probe, repeated context records
fall from 19.9% to none at 4,096 tokens and from 17.6% to 0.8% at 1,024, with
the same 17 of 18 probes answered. On all of LoCoMo, all evidence packed rises
4.7 points with the defaults (75.2% to 79.9%, +3.1 to +6.4 over
conversations) and 2.8 with speaker references and claim sentences (78.9% to
81.7%, +2.0 to +3.7), and every category gains in every build. On the
LongMemEval-S slice it gains 5.3 and 2.6 points with no loss. Claim sentences
with folding is the best gate result so far. `store()` records share no
source event, so folding should not change the `store()` packs that the
DeepSeek answer track replays. Their saved archive is on the answering
machine, so that gate did not run here.

**Default change (2026-10-06).** Claim sentence text, speaker references and
folding are now defaults. The DeepSeek track over `ingest()` packs (#232)
answered them as one variant, `prme-sentences-fold`, and `verdict` reads pass
with its run logs checked. LoCoMo +1.69 and +1.82 points, LongMemEval-S +3.4
and +4.2, every interval excluding zero (`BENCHMARKS.md`, "Default change:
claim sentences, speaker references and folding"). The ingest track's new
baseline is `prme@3389586b`: LoCoMo 1,190/1,540, LongMemEval-S 440/500.
`store()` records share no source event, so the new defaults should leave the
`store()` track's contexts unchanged; that has not been checked on its saved
archive, which is on the answering machine.

**Next, in order:**

1. Grounding still discards claims that refer through a third-person pronoun
   ("she got engaged to her boyfriend Tom") or through a neighboring sentence
   ("Green tea now."). Speaker grounding gains no evidence even with folding
   on, so more claims are not a retrieval gain by themselves; recover these
   for the graph's links and supersedence, and check the gate. The intention
   check also discards "I, hoping to visit, this summer": its predicate
   pattern matches "hope" but not "hoping".
2. #91 on LongMemEval-S: build the slice with the window and resolution and
   compare it with `ingest-baseline-lme-deepseek-r2`. It uses Ollama cloud
   calls, so confirm with the owner first.
3. #91: resolve relative dates in fact text more often, and run the authored
   negation and condition probes with resolution on. The chat probe also
   dated "May 4", said in April 2026, as 2025-05-04, "I signed up for the
   Cherry Creek Half Marathon on May 17!" on the race date instead of the
   message date, and "Two weeks in at Brightpath" two weeks after the message.
4. Jev graph organization, opt-in proposals only (#220 to #223). The conv-26
   pack has 576 entities, half with no edge, and "Mel" and "Melanie" are
   separate nodes.
5. The audit backlog, especially #173 and #174 (HTTP API exposure), #171
   (the deterministic simulation check for consolidation and remention, which
   failed 9 of 17 runs on `main` on 2026-10-06) and the recency and temporal
   scoring bugs.

**Known flaky test:** `tests/test_extraction_workflow.py::
test_heartbeat_keeps_long_running_provider_work_owned` fails intermittently on
CI (Python 3.11) and passes on a rerun.

**Archived research branches:** `feat/longmemeval-episode-routing`,
`feat/longmemeval-query-routing` and `feat/structured-presentations`
(2026-09-17 and 18) held research trials and results that never reached
`main`. On 2026-10-01 the owner had them tagged and the branches deleted; the
work is kept under the tags `archive/feat/longmemeval-episode-routing`,
`archive/feat/longmemeval-query-routing` and
`archive/feat/structured-presentations`. Per the resume instructions below, do
not merge them wholesale.

## Product goal

Build a trustworthy local-first memory package whose ordinary APIs preserve user
data and tenant isolation, and whose quality advantages survive reproducible,
held-out comparisons. We should claim leadership only for named tasks,
versions, readers, judges, budgets, and cost constraints where PRME actually
leads.

## What is already in production

Mainline already contains the completed work from the memory-reliability
branch and subsequent production merges. The safe promoted surface includes:

- durable raw ingestion and restart/retry handling;
- source-preserving extraction, scoped retrieval, contradiction handling, and
  atomic replacement/publication paths;
- the reader context format, balanced ordering, rank fusion scoring with a
  0.25 current-state recency boost and an event-time tie-break, and a 0.6
  rank fusion session decay as the retrieval defaults (since 2026-09-25,
  adopted on the DeepSeek answer track in two steps, the second of which
  replaced score ordering with balanced ordering), with the auditable and
  compact formats, score and density ordering and the weighted formula still
  available explicitly;
- a claim's own sentences as its text, a named speaker's I, me and my bound
  to the speaker's entity, and each source text packed once in reader
  contexts, as defaults since 2026-10-06 (adopted on the DeepSeek track over
  `ingest()` packs), and repeated extracted claims merged into one current
  record (#209);
- tenant-bound HTTP/MCP access, workspace/lease isolation, PostgreSQL parity,
  deterministic rebuild and recovery paths;
- durable profile staging/recovery, shared temporal-parser locking, and typed
  canonical values where the API exposes them;
- opt-in evidence-bound temporal relations and exact quantity aggregation.

These are production code paths, not a promise that every feature wins every
answer benchmark. The current mainline is the promotion target; no additional
branch merge is required for the above set.

Verification performed against a detached checkout of `main` at
`7b52290`: `3667 passed, 800 skipped` with `uv run pytest -q`. The focused
core checks were `64 passed, 18 skipped`; HTTP/MCP checks with the declared
extras were `53 passed, 10 skipped`. The skipped cases are optional or require
live external services/backends; no test failures were observed.

## Deliberately not promoted as defaults

The following remain experimental or rejected and must not be enabled merely
because they resemble competitor techniques:

- generic compact/grouped/metadata-factored renderers;
- episode routing and evidence augmentation as unconditional policies;
- answerability/verifier providers, learned ranking profiles, or rerankers;
- result-guidance prompts and free-form value-fidelity repairs;
- store-time supersedence, QA pairing, surprise gating, and query
  reformulation;
- automatic bulk alias/entity merges. Jev/product proposals remain inert and
  require caller selection and explicit review.

## Evidence we can currently defend

- complete GPT-5.4 benchmarks of the retrieval defaults before 2026-09-25:
  **LongMemEval-S 430/500 (86.0%)** and **LoCoMo 985/1,540 (64.0%)**, with a
  3,996-token effective context ceiling, medium reader/judge reasoning and zero
  terminal failures;
- on the separate DeepSeek answer track at the same 3,996-token ceiling, the
  current defaults against the previous ones in two interleaved pairs: LoCoMo
  65.7% to 80.9% and 65.8% to 81.6%, LongMemEval-S 85.8% to 86.8% and 86.4% to
  86.6% (`BENCHMARKS.md`; not comparable with the GPT-5.4 numbers);
- balanced packing: 83/119 development and 250/381 confirmation answers versus
  67/119 and 185/381 for density, using the registered local reader/judge;
- LongMemEval-V2 web-small: 80/149 with memory versus 10/149 without memory;
- AgentMemBench judged retrieval: 979/1000 recall@5;
- operational isolation/lifecycle checks: no cross-user leakage in the
  registered operational run, 200/200 archive retirements, and 200/200 writes
  at each tested worker count;
- temporal-relation confirmation: 60/104 versus 50/104 on its frozen disjoint
  partition, with the feature disabled by default.

These are bounded results, not universal superiority claims. Important negative
evidence remains: MemoryArena travel failed non-inferiority, EventQA trailed
BM25, BEAM remains 13/20, and claim-verification candidates have not passed
the required precision/recall gates.

## Current benchmark learning

The [audited GPT-5.4 report](../benchmarks/results/research/2026-09-23/GPT54-DEFAULT-BENCHMARK-COMPARISON.md)
and [post-hoc evidence audit](../benchmarks/results/research/2026-09-23/GPT54-EVIDENCE-DIAGNOSTICS.md)
are the current baseline for this reader and raw-turn storage protocol. All
2,040 questions completed; 4,080 benchmark provider calls succeeded on their
first attempt. Total observed API cost including controls was $16.4576.
LongMemEval reuses the frozen production-control contexts; LoCoMo freshly stores
5,882 source turns. The older DeepSeek 437/500 (87.4%) run stays separate.

PRME trails Zep's published 90.2% and 94.7% references, but this is not a live
matched comparison: source preparation, prompts and context budgets differ.
LoCoMo uses registered semantic yes/no accuracy, not official token-F1.
Historical audit numbers do not supersede these scoped current results.

**Prioritize context selection.** LongMemEval returned all 886 annotated
evidence instances but omitted 94 during packing. LoCoMo omitted 989 returned
instances; 443 of its 555 incorrect answers lacked some resolvable annotated
evidence. Multi-hop scored only 80/282 (28.37%). Annotation retention is a
diagnostic, not proof that an answer will improve. Separately audit errors with
retained evidence for temporal interpretation, updates and conflict handling.

Test answer-blind complementary evidence selection while retaining the strongest
anchor. Preserve the negative results for unconditional episode routing, broad
session penalties and existing reranking. No new default is supported by these
baseline measurements. Both canonical cohorts are examined development data;
confirmation must use independently prepared untouched histories/questions.

## Next goals and gates

1. **Production safety:** keep full installed, DuckDB, PostgreSQL, HTTP/MCP,
   restart, and workspace isolation regressions green before release changes.
2. **Matched competitive evaluation:** run a current LongMemEval-S comparison
   against Zep or another live alternative with identical history, reader/judge,
   context budget, ingestion accounting, latency accounting, and an untouched
   holdout. See the [Zep methodology review](../benchmarks/results/research/2026-09-17/ZEP-BENCHMARK-METHODOLOGY-REVIEW.md).
   The completed GPT-5.4 reference comparison supplies PRME baseline numbers;
   it does not close the matched-live-competitor gate.
3. **Interactive quality:** repair conflict resolution, temporal/value
   rendering, and source-cited episodic reconstruction on fresh cohorts; do
   not promote prompt-only fixes from inspected development splits.
4. **Knowledge verification:** test typed argument/qualifier/temporal
   alignment on a new development source, then require an untouched external
   cohort before enabling any provider verifier.
5. **Release discipline:** a change is promotable only when its measured gain
   beats the current production baseline, its failure modes are recorded, and
   targeted plus relevant backend tests pass. Otherwise keep it opt-in or
   archive it as a rejected experiment.

## Resume instructions

Start with this file and `docs/RESEARCH-AGENDA.md`. Treat the archived
`.planning/milestones/v1.0-phases/` and `.planning/archive/v1.0-legacy/` as
history only. Treat `main` as production; do not merge a research branch
wholesale just to recover benchmark artifacts. Check current branch ancestry
and run the relevant installed-package tests before any release or default
change.

### Jev with a GPT-5.4 temporal resolver (2026-09-23)

The [70-failure diagnostic](../benchmarks/results/research/2026-09-23/JEV-GPT54-FAILURES-REPORT.md)
completed with zero provider failures: fresh control 6/70, candidate 7/70.
All score differences occurred on unchanged contexts; the only added temporal
relation did not repair its answer. No Jev answer gain is demonstrated. The
variant remains research-only and the default baseline stays 430/500 (86.0%).
Prioritize source coverage and separately test temporal precision/operand
limitations; retain the flagged possible reference inconsistency without
changing official scores. Do not infer overall accuracy from selected failures.

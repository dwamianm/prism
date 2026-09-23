# Active opt-in study execution

Updated 2026-09-23 00:22 UTC (2026-09-22 locally). This is an operational handoff, not a final result.
The user asked to implement fixes and continue until meaningful experimental
results explain how to improve PRME. Continue the registered matrix; do not
replace failed attempts or publish partial-arm answer scores.

Production: `/Users/dwamianm/Sites/prism`, clean at `a66ee85`.
Matrix: `/Users/dwamianm/Sites/prism-opt-in-study-2026-09-22`, branch
`research/opt-in-interactions-2026-09-22`.
Repair: `/Users/dwamianm/Sites/prism-reranker-repair-2026-09-22`, branch
`research/reranker-score-repair-2026-09-22`, frozen repair at `6f98acd`,
scheduling derivative at `06a464d`.
Use `/Users/dwamianm/Sites/prism/.venv/bin/python`, with `PYTHONPATH=src`.
MAB requires `PYTHONPATH=data/opt-in-study/mab-preprocessing:src`.

## Immutable execution inputs

Do not edit registered production sources, executors, tests or protocols in
either worktree while their registered work is pending. New reports are safe.
In particular, **do not edit the matrix worktree's `docs/RESEARCH-AGENDA.md`
yet**: it is included in original registration source validation used by
later worker launches, the marginal assay and any newly selected combination.
Update it once those source validations have completed; the user still requires
that final agenda update with positive and negative findings. MAB's own source
list does not include the agenda. Do not merge either branch automatically.

The original failed 1,024-output-token baseline remains failed, unscored and
retained. Successor v2 uses the user-selected Ollama
`deepseek-v4.1-flash:cloud`, 8,192 reader output tokens, 64 judge output tokens,
the official LongMemEval prompts/score rule, and 3,996 effective memory tokens.
All 500 histories are previously examined development data, not a holdout.

## Running and waiting jobs

Tool session IDs are observations from the active session, not durable job IDs.
All logs below are under the matrix `data/opt-in-study/` unless stated otherwise.

| Job | Tool session | Log / behavior |
|---|---:|---|
| Historical coordinator | finished | `successor-v2-historical.log`; all six assigned arms verified. Exited at the expected ownership handoff/FileExistsError for the separately owned reranker directory; not an arm failure. Never restart. |
| Second historical lane | finished | `successor-v2-retrieval-lane-launcher.log`; reranker, query reformulation, reranker+reformulation and temporal+episode verified; temporal-only failed closed. Ten historical arms are complete; one failed. Each has `successor-v2-ARM.log`. Never restart. |
| Fresh control | 22257 | `successor-v2-fresh.log`; actual sequential store for all source turns. Expected handoff/FileExistsError when it reaches independently owned supersedence. |
| Store supersedence | 35057 | `successor-v2-store_supersedence.log` |
| QA pairing | 85242 | `successor-v2-qa_pairing.log` |
| Surprise gating | 95333 | `successor-v2-surprise_gating.log` |
| Full-feature exploratory | 89354 | `successor-v2-full_feature_exploratory.log`; started under scheduling amendment v4. The former idle launcher PID 75029/session 46843 was stopped before any child started. Do not launch it again. |
| Complete-arm analysis watcher | 5627 | `successor-bounded-analysis-watcher.log`; writes incrementally numbered `opt-in-successor-v2-analysis-NN-complete.json` after authentication. Memory-bounded wrapper has exact four-arm numerical parity with the original analyzer. The former idle PID 36062/session 11465 was stopped without interrupting any benchmark. |
| Exploratory top-two selector | 81746 | `successor-bounded-top-two-v2-launcher.log`, PID 95566. Waits for all eight individual comparisons, excludes inactive flags, preserves the original stricter selection separately. No favorable successful subset if a required arm fails. The v2 wrapper routes the exact analyzer subprocess into the validated bounded process; v1 had not propagated that safeguard to its child. Former idle PID 15714/session 24082 was authenticated childless and stopped before any selection or analysis output. Earlier PID 56489/session 87252 also remains stopped. Never relaunch either. |
| MAB stage launcher | 70019 | `mab-stage-launcher.log`; waits for all fixed LME arms and combination finalization. Runs Banking, EventQA, Conflict, Detective; registers an added combination before inference if needed. |
| Marginal answer coordinator | finished | `marginal-answer-lane-v1.log`; both 500-case arms completed: 433 versus 433, ten wins/ten losses, difference 0.0 points [−1.8,+1.8]. All failures from other trials stay retained. Never relaunch source v1/v2 or this completed trial. |
| Rank-envelope answer coordinator | finished | Parent `data/opt-in-study/rank-answer-lane-v1.log`; candidate completed 428/500. New control repeat failed closed (274 complete, one reader truncation, 225 unstarted), invalidating the primary comparison. Complete candidate versus original control is prespecified secondary evidence: −1.8 points [−3.8,0.0]. Outputs stay in repair worktree and public results are exported to parent. Never relaunch prior source/coordinator versions or failed control. |
| Original-anchor follow-up | 27649 | Repair worktree `data/opt-in-study/anchored-rank-v1.log`, PID 18445. All 500 source replays passed; 409/470 complete source sets versus production 403 and prior repair 407. Seven gains/one loss versus production, +1.277 points [+0.213,+2.553]; all category source counts tie or improve. Source gate passed. Both new readers completed: control 429/500, candidate 430/500; primary +0.2 points [−1.6,+2.0], 11 wins/10 losses. This process is finished; never restart. Child names `opt-in-anchored-rank-v1-reader-{control,candidate}`, each uses `execution/QID/result.json`. No partial answer score. |
| Reformulation signal-merge follow-up | 34521 | Parent `data/opt-in-study/reformulation-merge-v1.log`, PID 98200. New fixed research policy, registered at `f69cc9edac64ef1102dc7d2a9537278696b8770bf0b3fd2ad73d389deb0e7993`. Anchor PID 18445 exited after completing both answer arms; this trial now runs five local source tasks, reusing the exact recorded cold reformulations. Source embeddings/search run again; no new hosted reformulation/cross-encoder calls. Both original controls must replay exactly for all 500. Only the positive whole-cohort source gate permits new control/candidate answers in the same locked lane. Nine authored and two live-backend checks passed; initial authored config-field failure retained. Do not edit its frozen modules/tests/source dependencies. |

Private fixed-arm artifacts: `data/opt-in-study/opt-in-successor-v2/ARM/QID/`.
Only an arm with complete `execution.json` and authenticated `verification.json`
receives metrics. A terminal failure prevents a partial score; preserve all
artifacts and allow other independent prespecified arms to continue.
Failure records are `result.json` with `status="failed"`, not `failure.json`.
Repair reader case outputs are under `NAME/execution/QID/`.

The repair worktree references the matrix's verified controls and official
prompt checkout through symlinks. They are inputs only. All repair outputs use
its distinct `opt-in-rank-envelope-v2*` directories. Its source registration
hash is `c8a06ecef2db225f672c2de2d1f3543032f41a18ad5368aa80d41cf521390ef2`.

## Completed evidence

* Baseline: 437/500 (87.4%). Complete annotated source turns in 403/470 cases.
* Episode routing: 345/500; delta −18.4 points, 95% CI [−22.2, −14.8].
  Source completeness 283/470. Reject this unconditional routing policy as a
  default candidate on this cohort.
* Reranker: 284/500; delta −30.6 points, 95% CI [−35.0, −26.2].
  Source completeness 224/470. The score-scale audit identifies a concrete
  integration defect. The isolated repair completed at 428/500; its new primary
  control failed. It recovers most of the defect but does not beat production.
* Evidence augmentation: 435/500, delta −0.4 points, CI [−1.6, +0.8].
  All 500 inputs unchanged; no activation. Four wins/six losses measure
  reader/judge variation, not augmentation benefit or harm.
* Evidence projection: 431/500, delta −1.2 points, CI [−2.8, +0.2].
  All 500 inputs unchanged; no activation. Four wins/ten losses. Registered
  three-repeat analysis confirms 422 always-pass, 58 always-fail and 20
  variable questions. Keep the original baseline; do not treat inactive arms
  as feature evidence or favorable replacement controls.
* Query reformulation: 434/500, delta −0.6 points, CI [−2.0, +0.6].
  Only two changed contexts; all 11 answer disagreements were unchanged-input
  variation. Complete source sets remain 403/470. Added paired median 5.533
  seconds with no demonstrated benefit; current code ignores alternate-query
  signals for existing candidates. Not a default candidate on this evidence.
* Episode + augmentation: 346/500, delta −18.2 points, CI [−22.0, −14.6].
  Every reader input matches episode routing; five wins/four losses are
  variation, with no augmentation activation. Factorial contrast +0.6 points
  [−1.2,+2.4] cannot establish synergy for an inactive mechanism.
* Reranker repair source trial: 407/470 complete verbatim annotated source sets
  versus baseline 403 and original reranker 223. All 500 source cases and
  control replays authenticated. Baseline gain +0.85 points [−0.64,+2.34],
  eight gains/four losses; +4 multi-session, +2 update, −2 assistant categories.
  Both source gates passed; 500+500 answer contexts/registrations frozen.
  The source metric differs from original node presence for one metadata-only
  assistant representation. The complete candidate has 7 wins/16 losses versus
  original baseline, secondary −1.8 points [−3.8,0.0]. Changed contexts have
  5 wins/9 losses, exact repeats 2 wins/7 losses. Eight complete-source gains
  produced 3 answer gains, four complete-source losses produced 3 answer losses.
  The failed new control prevents the primary comparison. No confirmation.
* Reranker + reformulation: 288/500, −29.8 points [−34.2,−25.6]. Four
  changed inputs versus reranker; no changed-input score transition. Eight
  wins/four losses are unchanged-input variation. Factorial +1.4 [−0.4,+3.2].
* Episode + projection: 348/500, −17.8 points [−21.6,−14.2]. Every reader
  request matches episode routing and episode+augmentation; evidence flags
  are inactive. Across these three repeats, 17 questions change outcome.
* Temporal relations: failed closed, 265 complete/one failed/234 unstarted;
  no answer score. The same baseline reader request exhausted 8,192 output
  tokens here and in the new rank control repeat. Temporal validation had
  returned the unchanged control context. Both failures are retained, not
  replaced. Best-two selection cannot omit this failed required individual.
* Temporal + episode: 343/500, −18.8 points [−22.6,−15.2] versus production,
  seven wins/101 losses. Source completeness remains 283/470. Against episode
  alone, −0.4 points [−1.4,+0.6], two wins/four losses, all on unchanged inputs.
  The 32 accepted changed contexts have identical correctness in both arms
  (28 pass/four fail); all four failures are reviewed separately. Cold statuses:
  355 not invoked, 70 validation rejected, 29 unsupported, 14 gate rejected,
  32 accepted. Resolver/Jev costs, alignment and cold/warm variation are in
  `opt-in-temporal-episode-audit-v1.json`. No standalone/factorial replacement.
* Marginal packing: all 500 source replays complete. Source coverage 403
  control, 300 session penalty, 407 episode bonus, 319 combined, out of 470.
  Bonus-only qualifies: six complete-source gains/two losses, +0.85 points
  [−0.21,+2.13]; three gains in baseline packing errors. Both source-failing
  policies retained and rejected at gate. The selected full 500+500 primary
  answer trial tied 433/500, ten wins/ten losses, 0.0 points [−1.8,+1.8]. Category
  changes: +3 multi-session, +1 preference, −3 update, −1 assistant; others tie.
  Each arm had 1,000 successful calls without retries. Reject as default candidate.
* Annotation-assisted packing diagnostic: 473/500, +7.2 points, CI [+4.4,+10.0].
  Nondeployable, outside the feature matrix. Changed 67 contexts; 433 repeated
  inputs reveal residual reader/judge variation. Knowledge updates lost two.
* Source omission audit: all 886 annotated turns were returned; 94 omitted
  from packing. The 70 missing turns in incorrect cases had median rank 75
  and median length 66 raw-content tokens; 67 were user turns.

The current report is [OPT-IN-SUCCESSOR-REPORT.md](OPT-IN-SUCCESSOR-REPORT.md).
The report and machine artifacts retain all negative findings. No default
promotion is supported without an untouched confirmation cohort.

## Validation and next work

Parent validation: 59 live PostgreSQL tests, zero skips; 152 feature/recovery
tests with three backend-specific skips; 13 ingestion-harness tests; five
annotation-priority tests; six marginal-policy tests; nine MAB-harness tests.
Earlier failed harness tests are retained with their repairs.
Repair validation: 97 passing tests and one expected PostgreSQL skip of a
local process-exit test; old bytes, score replay, actual balanced-packing
regression, DuckDB/PostgreSQL restart and owner checks pass. No public flag
or default changed. Its package change exists only in the separate branch.
The same 97 tests also pass from an isolated installed wheel, with the same
skip and all 163 packaged Python files byte-identical to frozen source.
The new original-anchor variant changes research files only and passed nine
authored tests plus two DuckDB/live-PostgreSQL restart/owner/receipt checks.
Its registration hash is `4fa9746794f628615cf2b7177322543d3488ed93a18a6eba859827166d4c009d`.
Do not edit its registered files while it runs. Its full source gate requires
both source metrics strictly above the prior repair and production, with no
category complete-source regression versus production. See ANCHORED-RANK-STUDY.md.

The selector subprocess-memory repair passed nine routing/selection tests and
Ruff. Original selector, analyzer, statistical parity evidence, failure gates,
and selection plan remain byte-identical. Amendment
`opt-in-selection-subprocess-memory-amendment-v2.json` and its handoff record
authenticate the change before selection. The original analysis watcher still
waits for 16 verified arms; because temporal-only failed it will not exit by
itself. After every fixed arm settles, create a uniquely named final bounded
analysis even if the last event is a failure, then stop only the idle watcher.

The source-to-answer memory handoff is registered in
`opt-in-answer-memory-amendment-v1.json` and passed seven authored tests. It is
**now used for the rank repair only**. It authenticated all 500 source/context
pairs and replaced only the completed idle source process with a low-memory
coordinator under its then-unchanged historical-stage gate. Both source gates passed.
The initial ownership check refused a passive Python resource tracker; the first
coordinator attempt then failed for missing handoff, before any benchmark call.
Both logs are retained. A prospective bookkeeping amendment permits only that
passive child type; the actual transfer is recorded in
`rank-answer-memory-handoff-v1.json`. Never stop an active source or reader case;
record exact prepared identities and process ownership before any transfer.
The helper is `benchmarks/diagnostics/opt_in_answer_memory_handoff.py` in the
parent worktree and handles both `rank` and `marginal`. Its `verify` action is
read-only; `run` requires the explicit idle-process handoff artifact.

Later-stage preparation restored BEAM upstream `4b61c5d` and the exact historical
normalized 100K cache. No BEAM ingestion or answer call was made. See
`LATER-STAGE-PROTOCOL-AUDIT.md` and `opt-in-beam-input-readiness-v1.json`.
Available prior records identify only conversations 0 and 1. Keep 2–19 out of
development inspection pending any prospective confirmation/exposure audit.

The isolated test database `prme_optin_20260922` is on local PostgreSQL 16.11.
Connection: `postgresql://dwamianm@localhost/prme_optin_20260922`.
Do not touch other databases. This database may be removed after validation
work is finished, not while tests are running.

Next: monitor failures and completed-arm verification; analyze only complete
arms; run the frozen secondary variation/interaction analysis once its inputs
are complete; inspect source-assay and repaired-answer results; complete the
fresh matrix and MAB stage; update the canonical report and research agenda.
BEAM and MemoryArena remain conditional on earlier complete runs and require
their own exact protocol registrations. Jev/product alignment remains its
separate explicit pair-selection/review workflow, never a retrieval flag.

## Released-lane amendment (current answer scheduling)

`opt-in-answer-lane-scheduling-v1.json` now prospectively supersedes only the
repair answer wait for all eleven historical arms. All six first-lane arms
must settle/authenticate and original coordinator PID 20793 must exit first.
The freed four-slot provider lane runs rank control/candidate, then any eligible
marginal control/candidate, sequentially. It adds no provider capacity and uses
the exact original execution/statistics AST tails and all frozen contexts.
Six authored ownership/gating tests and Ruff passed. Parent helper:
`benchmarks/diagnostics/opt_in_answer_lane.py`. Rank transfer and its all-500
reauthentication are recorded in `rank-answer-lane-v1-handoff.json`; source
and prepared bytes are unchanged. The prior memory coordinator was confirmed
idle with no children and no reader artifacts, then stopped. No case was
interrupted. Marginal has now completed, qualified and undergone its own equivalent
idle transfer, recorded in `marginal-answer-lane-v1-handoff.json`. Its low-memory
coordinator is queued behind the rank repair. Both new coordinator logs confirm
the frozen prepared identities and waiting status.


## Implementation phase after usage-allowance clarification

The user clarified that their 60%-remaining threshold refers to the Codex usage
allowance. That meter is not exposed to this session. We switched to implementation
immediately, with no new research directions or arm registrations after that
clarification. The already registered signal-merge and original matrix continue.

New worktree: `/Users/dwamianm/Sites/prism-retrieval-implementation-2026-09-22`,
branch `fix/opt-in-retrieval-policies-2026-09-22`, based on current main `a66ee85`.
This avoids touching any frozen running source. Explicit config exposes score
envelope / anchored score envelope and alternate-query max-signal merging.
Defaults remain unchanged. The new branch carries the actual research-agenda
update; the matrix's frozen agenda stays unchanged until its final validations.
Its implementation report and targeted/regression/installed validation are under
`benchmarks/results/research/2026-09-22/` in that worktree.

Original-anchor final primary: 430/500 versus 429/500, 11 wins/10 losses,
+0.2 points [−1.6,+2.0]. All 500 source and both answer executions authenticated.
367 changed reader inputs: eight wins/seven losses. 133 identical inputs:
three wins/three losses. Seven complete-source gains yield three answer wins;
the sole complete-source loss is wrong in both new arms. All ten losses reviewed
in `ANCHORED-RANK-LOSS-REVIEW.md`; no source/answer relabeling or control replacement.
The trial does not support default promotion.

Latest observed fresh counts: control 193, supersedence 139, QA 139,
surprise 159, full exploratory 79 complete; no terminal failure recorded.
These are progress counts, not partial scores. Signal-merge source replay active,
85 source cases saved at the latest observation. No quality score until its complete gate settles.

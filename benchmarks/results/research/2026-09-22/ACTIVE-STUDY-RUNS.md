# Active opt-in study execution

Updated 2026-09-22 22:17 UTC. This is an operational handoff, not a final result.
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
| Historical coordinator | 71386 | `successor-v2-historical.log`; baseline, episode, augmentation, projection and episode+augmentation verified; episode+projection running. Expected ownership handoff/FileExistsError when it reaches the separately owned reranker directory; not an arm failure. |
| Second historical lane | 14157 | `successor-v2-retrieval-lane-launcher.log`; reranker, query reformulation and reranker+reformulation verified; temporal running, then temporal+episode. Each has `successor-v2-ARM.log`. |
| Fresh control | 22257 | `successor-v2-fresh.log`; actual sequential store for all source turns. Expected handoff/FileExistsError when it reaches independently owned supersedence. |
| Store supersedence | 35057 | `successor-v2-store_supersedence.log` |
| QA pairing | 85242 | `successor-v2-qa_pairing.log` |
| Surprise gating | 95333 | `successor-v2-surprise_gating.log` |
| Full-feature exploratory | 89354 | `successor-v2-full_feature_exploratory.log`; started under scheduling amendment v4. The former idle launcher PID 75029/session 46843 was stopped before any child started. Do not launch it again. |
| Complete-arm analysis watcher | 5627 | `successor-bounded-analysis-watcher.log`; writes incrementally numbered `opt-in-successor-v2-analysis-NN-complete.json` after authentication. Memory-bounded wrapper has exact four-arm numerical parity with the original analyzer. The former idle PID 36062/session 11465 was stopped without interrupting any benchmark. |
| Exploratory top-two selector | 24082 | `successor-bounded-top-two-launcher.log`; waits for all eight individual comparisons, excludes inactive flags, preserves the original stricter selection separately. No favorable successful subset if a required arm fails. Same frozen selector through the validated memory wrapper; former idle PID 56489/session 87252 was stopped before selection. |
| MAB stage launcher | 70019 | `mab-stage-launcher.log`; waits for all fixed LME arms and combination finalization. Runs Banking, EventQA, Conflict, Detective; registers an added combination before inference if needed. |
| Marginal answer coordinator | 58561 | `marginal-answer-lane-v1.log`; all 500 source cases complete, bonus-only policy selected, both reader contexts/registrations frozen. Source process 90351/PID 48748 stopped only while idle after reauthentication; no cases interrupted. New coordinator waits for first historical lane release and rank-answer tail/process exit. Never relaunch source v1/v2. |
| Rank-envelope answer coordinator | 4246 | Parent `data/opt-in-study/rank-answer-lane-v1.log`; new scheduling amendment uses the first historical lane only after all six assigned arms settle/authenticate and PID 20793 exits. Prior idle coordinator 52440/PID 85200 was stopped before any reader case. Source assay 44587/PID 92466 completed and was stopped only while idle. Outputs stay in the repair worktree. Never relaunch prior source/coordinator versions. |

Private fixed-arm artifacts: `data/opt-in-study/opt-in-successor-v2/ARM/QID/`.
Only an arm with complete `execution.json` and authenticated `verification.json`
receives metrics. A terminal failure prevents a partial score; preserve all
artifacts and allow other independent prespecified arms to continue.

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
  integration defect. The isolated repair has completed source replay; answers pending.
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
  assistant representation. No answer win or confirmation claim yet.
* Reranker + reformulation: 288/500, −29.8 points [−34.2,−25.6]. Four
  changed inputs versus reranker; no changed-input score transition. Eight
  wins/four losses are unchanged-input variation. Factorial +1.4 [−0.4,+3.2].
* Marginal packing: all 500 source replays complete. Source coverage 403
  control, 300 session penalty, 407 episode bonus, 319 combined, out of 470.
  Bonus-only qualifies: six complete-source gains/two losses, +0.85 points
  [−0.21,+2.13]; three gains in baseline packing errors. Both source-failing
  policies retained and rejected at gate; selected 500+500 answer trial queued.
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

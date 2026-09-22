# Active opt-in study execution

Updated 2026-09-22 20:59 UTC. This is an operational handoff, not a final result.
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
| Historical coordinator | 71386 | `successor-v2-historical.log`; baseline, episode, augmentation and projection verified; episode+augmentation running, then episode+projection. Expected ownership handoff/FileExistsError when it reaches the separately owned reranker directory; not an arm failure. |
| Second historical lane | 14157 | `successor-v2-retrieval-lane-launcher.log`; reranker verified, query reformulation running, then reranker+reformulation, temporal and temporal+episode. Each has `successor-v2-ARM.log`. |
| Fresh control | 22257 | `successor-v2-fresh.log`; actual sequential store for all source turns. Expected handoff/FileExistsError when it reaches independently owned supersedence. |
| Store supersedence | 35057 | `successor-v2-store_supersedence.log` |
| QA pairing | 85242 | `successor-v2-qa_pairing.log` |
| Surprise gating | 95333 | `successor-v2-surprise_gating.log` |
| Full-feature exploratory | 89354 | `successor-v2-full_feature_exploratory.log`; started under scheduling amendment v4. The former idle launcher PID 75029/session 46843 was stopped before any child started. Do not launch it again. |
| Complete-arm analysis watcher | 5627 | `successor-bounded-analysis-watcher.log`; writes incrementally numbered `opt-in-successor-v2-analysis-NN-complete.json` after authentication. Memory-bounded wrapper has exact four-arm numerical parity with the original analyzer. The former idle PID 36062/session 11465 was stopped without interrupting any benchmark. |
| Exploratory top-two selector | 24082 | `successor-bounded-top-two-launcher.log`; waits for all eight individual comparisons, excludes inactive flags, preserves the original stricter selection separately. No favorable successful subset if a required arm fails. Same frozen selector through the validated memory wrapper; former idle PID 56489/session 87252 was stopped before selection. |
| MAB stage launcher | 70019 | `mab-stage-launcher.log`; waits for all fixed LME arms and combination finalization. Runs Banking, EventQA, Conflict, Detective; registers an added combination before inference if needed. |
| Marginal source assay | 68568 | `marginal-study-v1.log`; waits for all historical arms, then tests all three fixed policies on all 500 cases, with conditional full-cohort answer follow-up. |
| Rank-envelope repair assay | 44587 | **Repair worktree** `data/opt-in-study/rank-envelope-study-v2.log`; local source replay now running under scheduling amendment v2. Verifies all 500 baseline and reranker replays, then runs one repair. Conditional hosted answers still wait for all historical arms. The v1 idle waiter PID 3539/session 32160 was stopped before any case or hosted call. |

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
  integration defect. The isolated repair remains unmeasured on benchmark data.
* Evidence augmentation: 435/500, delta −0.4 points, CI [−1.6, +0.8].
  All 500 inputs unchanged; no activation. Four wins/six losses measure
  reader/judge variation, not augmentation benefit or harm.
* Evidence projection: 431/500, delta −1.2 points, CI [−2.8, +0.2].
  All 500 inputs unchanged; no activation. Four wins/ten losses. Registered
  three-repeat analysis confirms 422 always-pass, 58 always-fail and 20
  variable questions. Keep the original baseline; do not treat inactive arms
  as feature evidence or favorable replacement controls.
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

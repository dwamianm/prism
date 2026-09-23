# Active GPT-5.4 comparison handoff

The user wants completed PRME LoCoMo and LongMemEval headline numbers alongside
Zep's published numbers. They restored $20 OpenAI credit, then explicitly enabled
auto top-up and instructed: **run until both LoCoMo and LongMemEval are finished**.
There is no longer a $19 stopping cap. Do not stop at a plan or partial score.

Worktree: `/Users/dwamianm/Sites/prism-locomo-baseline-2026-09-22`, branch
`research/locomo-release-baseline-2026-09-22`, based on released v0.12.0 `aaa2e4e`.
Main/release remain unchanged. Use `PYTHONPATH=src` and
`/tmp/prme-v0.12.0-release-venv/bin/python`. Dependencies and source checksums
are frozen. No sub-agents were used or authorized. Do not edit frozen modules
or install dependencies while these executions run.

Public files: `benchmarks/results/research/2026-09-23/`.
Private files/logs: `data/gpt54-comparison-v1/`.
Provider key: original main checkout's `.env`, loaded explicitly; never print it.
The shell key is stale and must not override the funded project key.

## Protocol and state

- `gpt54-comparison-v1-registration.json` binds both full cohorts, source and
  dependency identities, model, prompts, defaults and limits.
- Reader and judge: `gpt-5.4-2026-03-05`, medium reasoning, Flex. Reader cap8192;
  judge cap2048 includes reasoning. Explicit authored probes confirmed access.
- All500 LongMemEval default-control contexts from Sept22 were authenticated
  and copied without modification; original complete verification retained.
  This is a new GPT reader evaluation of frozen default contexts, not freshly
  measured v0.12 ingestion/latency. Defaults were preserved by the release.
- LoCoMo freshly stores all nonempty turns and supplied image captions through
  public APIs, with every experimental feature off; all1540 category1–4
  questions are fixed. Upstream dataset SHA is verified. No observations,
  summaries or evaluation labels enter ingestion. Categories1/2/3/4 mean
  multi-hop/temporal/open-domain/single-hop, unlike the older adapter names.
- No aggregate score until the complete arm is valid. Failed requests and
  original results are retained. No selective replacements or score tuning.
- The shared `spending.json` accounts for reported token usage and conservative
  outstanding/unknown reservations. Model rates and failures are recorded.
  The two tiny funded access/Flex probes are separate public records, not in
  that shared ledger. Include them in final total cost.

## Live processes (updated 2026-09-23 about03:24UTC)

| Responsibility | Session/PID | Log |
|---|---|---|
| LongMemEval500 GPT answer/judge, completed | session14129 closed, exit0 | `longmemeval-run.log` |
| Original LoCoMo source coordinator, expected ownership handoff complete | session52051, PID55428 exited | `locomo-prepare.log` |
| Three-worker source scheduling coordinator, completed | session74851 closed, exit0 | `locomo-source-scheduling.log` |
| LoCoMo answer queue | session95985, PID68780 | `locomo-queue.log` |

LongMemEval completed all500: **430/500 (86.0%)**, zero terminal failures;
1,000 successful reader/judge calls, 1,000 HTTP attempts, $4.44975625 in arm
usage. The independent analyzer authenticated every request/context/result and
recomputed category totals and intervals. The shared ledger including authored
controls is $4.45880875, with no unresolved reservations; small separate access
probes still need adding to the final report.

LoCoMo source preparation is complete: all10 conversations, 5,882 stored turns,
and all1,540 unique contexts authenticated. Summed ingestion-worker time is
7,445.980896416004 seconds. The source coordinator exited0 after its registered
ownership handoff. The queued reader/judge run started02:51:23UTC and is active:
last observed1,122/1,540 complete, no terminal failure, shared settled cost
$13.2104525. Session95985 is the only remaining live execution owner.
Check counts only while in progress, not partial accuracy. Complete results go to
`gpt54-longmemeval-v1-result.json` and `gpt54-locomo-v1-result.json` publicly.

The LoCoMo queue automatically starts the original registered full answer arm
after LongMemEval succeeds and `locomo/prepared.json` authenticates all1540
contexts. It invokes the amended official prompt loader. Never launch a duplicate
answer worker or repeat an existing execution directory.

## Retained calibration failure and exact loader amendment

The first authored calibration produced one successful GPT answer, then failed
because importing the pinned official LongMemEval CLI imported unavailable
`backoff`. No dataset answers had started. Do not install it now: dependencies
are frozen. `gpt54_official_prompt_loader.py` loads the exact undecorated official
`get_anscheck_prompt` AST without unrelated CLI imports. The amendment binds
that loader and the official source hash. Two loader tests passed.

All16 authored reader/judge checks then completed successfully in
`loader-calibration-v2/authored-calibration/`. The original failed preflight,
log and charges remain; an explicit gate file in `authored-calibration/result.json`
references the second complete calibration. All later readers invoke:

```sh
PYTHONPATH=src /tmp/prme-v0.12.0-release-venv/bin/python \
  -m benchmarks.integrations.gpt54_official_prompt_loader longmemeval
```

The above LongMemEval command is COMPLETE; do not run it again.

## Source scheduling amendment

Sequential LoCoMo preparation was slow. The prospective scheduling amendment
assigns untouched conversations conv-30,41,42,43,44,47,48,49,50 to three native
Python workers. The original coordinator finishes conv-26, then encounters an
already-owned empty `locomo/packs/conv-30` directory. Its expected FileExistsError
is an ownership handoff before ingestion of that conversation, not a failed
benchmark case. Do not restart it or remove that directory.

Each child executes the exact frozen `prepare_locomo` function AST. Its only
AST change is the final questions-count literal from1540 to the child cohort
size. Global bindings scope the source read and expected IDs to its explicitly
owned conversation and unique private directory. Default configs and every
store/retrieve operation remain unchanged. Original full source/dependency
validation runs before each child starts. Two projection/AST-locality tests pass.
An unused `pathlib.Path` import in the new scheduler was flagged by Ruff; this
harmless lint finding is retained while its source hash is frozen. Remove it
only after all scheduler work finishes, and document historical source identity.

Child artifacts are under `source-children/CONV/locomo/`, with logs at
`locomo-source-CONV.log`. The coordinator verifies every child and the original
conv-26 source manifest, requires all10 conversations/all1540 exact IDs, copies
contexts byte-identically into `locomo/contexts/`, and writes the single final
`locomo/prepared.json`. It records the expected ownership handoff. Any child
failure prevents readiness; none may be silently replaced.

## Finish the work

Monitor terminal failures, source child exits and cost totals. After both arms
complete, authenticate every result/request/context hash and complete coverage,
compute/report answer counts/categories, context tokens, cost, recorded failures
and confidence intervals. Preserve all original DeepSeek and older results.
Write a concise dated report and update research agenda in this worktree.
The user requested two comparable headline numbers, not a broad feature study.

Zep's public reference: LongMemEval451/500=90.2%; LoCoMo1459/1540=94.7% at
https://www.getzep.com/research/. These are vendor-reported results, not a live
matched arm. Exact prompts, dataset checksum and ingestion recipe are not public;
its LoCoMo category totals do not reconcile. Match disclosed model/reasoning and
scope, but never claim exact one-to-one reproduction or paired significance.
Use a short two-row table in the final answer. No automatic new release or main
merge is authorized by this new benchmark request.

The result authentication tool is `benchmarks.integrations.analyze_gpt54_comparison`.
Run it only when both full arms are complete. It verifies source/dependency and
amendment hashes, every request/response/context/result, exact cohort coverage,
recomputed statistics, and closed LoCoMo pack file hashes. It creates the final
comparison report and verification JSON exclusively. Its tamper-rejection test
passes, and its changed files pass Ruff. It has already verified LongMemEval
individually without publishing the two-arm report.

Then run `benchmarks.integrations.analyze_gpt54_evidence` for explicitly post-hoc
annotation retention diagnostics. It reads closed DuckDB packs read-only; no
labels influenced execution. LongMemEval's70 misses currently divide into46
with some annotated turns absent,18 with all annotated turns retained, and6
abstention failures. This is diagnostic evidence, not a causal or semantic
entailment test. The tool requires both arms' final verification before writing.

Convenience monitoring: `python3 /tmp/prme-gpt54-status.py` prints completion,
source progress, terminal failures and cost without partial answer scores.

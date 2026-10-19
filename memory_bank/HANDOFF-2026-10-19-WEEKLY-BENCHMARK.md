# Runbook: the full benchmark on a schedule (2026-10-19)

The Mac mini builds LoCoMo and LongMemEval-S with the current defaults, runs the
offline evidence gate and compares the result with the last stable release and
with the previous run, twice a week. The owner approved the machine's use on
2026-10-19 (issue dwamianm/prism-monorepo#272). Everything is in
`scripts/full-benchmark/`; the older manual flow is
`HANDOFF-2026-10-16-FULL-BENCHMARK.md` and still works.

## The release rule

- An alpha or beta needs the fast tiers: the fast gate, the fast cycle and, where
  it applies, the mid tier. It does not wait for a full run.
- A stable release needs the latest scheduled run to show **no loss** against the
  last stable release (the verdict's first line, below).
- "Latest" means a completed run of the release commit, or of an earlier commit
  with no change under `src/`, `benchmarks/`, `pyproject.toml` or `uv.lock`
  between it and the release commit (`status-weekly.sh` counts those files). When
  the newest run does not cover the release commit, start one by hand with
  `weekly-run.sh --force` (about 15 hours) or wait for the next trigger.
- The answer track is not scheduled. It runs on demand, as before, and it still
  decides a default change (`CLAUDE.md`).

## What runs, and when

A LaunchAgent for the Mac mini's user (`com.prism.weekly-benchmark`) starts
`weekly-run.sh` on **Monday and Thursday at 00:30 local time** (America/Chicago:
05:30 UTC until the clocks change on 2026-11-01, 06:30 UTC after). The first
scheduled run is Thursday 2026-10-22. The job has no `RunAtLoad` and no
`KeepAlive`: installing it or logging in never starts a run. It needs the Mac
user to be logged in at the console (a LaunchAgent lives in the user's session),
and the Mac never sleeps (`pmset sleep 0`); each run also holds `caffeinate`.

A run:

1. Fetches `origin/main` and makes a fresh detached worktree at that commit,
   `~/Sites/prism-weekly-<sha>`, then `uv sync --frozen`. It never touches the
   other worktrees or data folders on the machine.
2. Builds LoCoMo (10 packs) and LongMemEval-S (500 packs) through `ingest()` with
   the defaults and `deepseek-v4.1-flash:cloud` through the local Ollama, by
   `run-builds.sh` with `BUILDS=defaults`. Every build shares one extraction
   cache (`cache/extraction`), so a turn or batch whose request is unchanged
   replays without a model call. No paid API is called: the scripts unset the
   key variables, and a run refuses to start if the clone's `.env` holds one.
3. Runs the offline evidence gate over each build.
4. Compares each gate report with the last stable release's (`gate-compare`) and
   with the previous scheduled run's, and writes `report.md` and `report.json`.
5. Deletes the packs and worktrees of runs beyond the newest 3. Reports stay.

Which reference is "the last stable release": **v0.15.0**, through the batched
defaults builds of the full batching run (`full-defaults-*-deepseek-d810ee55`).
Batching at 2,048 tokens is what v0.15.0 ships; the per-turn builds are the
variant. Commit `d810ee55` is v0.15.0 plus tests and docs: `git diff v0.15.0
d810ee55 -- src pyproject.toml uv.lock` is empty. The two gate reports are copied
to `reference/stable/` the first time they are needed.

## When a trigger does nothing

Each case writes one line to `state/schedule.log`:

- **Another run is going.** `state/weekly.lock` holds the pid of a live
  `weekly-run.sh`. A lock whose pid is gone is cleared. A build or gate that
  someone started by hand (an `extracted_packs build` or `product_packing gate`
  process) also counts.
- **`origin/main` has not changed** since the last completed run.
- **Less than 120 GB is free.** The packs of runs beyond the newest 2 are deleted
  first to make room; if that is not enough the run refuses and says how much is
  free. A run's packs take about 33 GB (LongMemEval-S 32 GB), so 3 runs and the
  120 GB reserve fit in the 390 GB that is free today.
- **Ollama does not answer, the model is missing or does not answer.** Run
  `ollama signin` or start the app. A different Ollama version or model identity
  only warns, and the report records both.

An unfinished run of the current `origin/main` commit (the Mac restarted, or a
build gave up) is resumed by the next trigger instead of started again: every
finished step is skipped, and a build resumes from the turns it holds.

## Pauses

An Ollama usage limit pauses a build. The driver waits 15 minutes and starts it
again, up to 24 times per build (6 hours), and the build resumes from the turns
it holds. If a build still gives up, the run is marked incomplete, with no
verdict, and the next trigger resumes it. If a run is still going when the next
trigger fires, the trigger skips with a log line.

## How long it takes

Recorded builds on this Mac mini (17 packs at a time on LongMemEval-S):

| Step | Cold cache | Source |
|---|---|---|
| LoCoMo build | 12 to 13 min | `d810ee55` 13.1 min, `793118e3` 12.0 min |
| LoCoMo gate | 9 min | 9.0 and 9.1 min |
| LongMemEval-S build | 14.8 to 14.9 h | 14.9 h and 14.8 h, each with one 15 minute retry wait |
| LongMemEval-S gate | 5 to 6 min | 5.5 and 5.2 min |
| Whole run | about 15.3 h | `793118e3`: 2026-10-18 15:29 to 2026-10-19 06:43 UTC |

**Warm cache** (a build whose extraction requests are all in the cache; an
estimate until a scheduled run records one): LoCoMo about 15 minutes and
LongMemEval-S about 9 hours (range 6 to 12), so about 9.5 hours in all. Replay
still has to ingest, embed and write every turn, so it saves roughly 40% of the
build, not all of it. The basis is the older all-hit builds in
`data/extracted-packs-v1`. LoCoMo: 5,882 turns replayed in 976 to 1,071 s with 10
packs at a time. LongMemEval-S (`ingest-refs-sentences-lme-deepseek`, 246,716
replayed turns): 489,999 pack-seconds against 813,222 for the cold batched
build. Divided by 17 packs at a time that is 8.0 hours, and the cold build's wall
time ran 12% above its own pack-seconds divided by 17 (14.9 hours against 13.3),
which gives about 9. That old build took 12.6 hours of wall time, beside another
build on the same machine.
The first run starts with an empty shared cache, so it is cold. A later run is
warm only when the extraction prompt, the batching and the options that add
earlier memories to a request are unchanged. The report's "From the cache" and
"Build time" columns record the real figure; replace this paragraph's estimate
with the first warm run's.

**Seeding the cache.** An extraction cache entry is keyed by the model and the
full request, so it is valid for any identical request, whichever run made it.
To start the first scheduled run warm, copy a finished manual run's caches into
the shared folder (the copy only reads the old folder; `-n` never overwrites):

```sh
P=~/Sites/prism/data/extracted-packs-v1; C=~/Sites/prism-weekly-scheduler/cache/extraction
cp -Rn $P/full-defaults-lme-deepseek-<sha8>/extraction-cache/. $C/
cp -Rn $P/full-defaults-locomo-deepseek-<sha8>/extraction-cache/. $C/
```

On 2026-10-19 a manual run of `bf8bd8d1` (the 0.16.0a3 release commit) was building
on the Mac mini, so its caches are the natural seed once it ends. A seed helps
only while the extraction request is unchanged since that commit.

Even a cold run and 6 hours of pauses (about 21 hours) end long before the next
trigger, which comes 72 hours (Monday to Thursday) or 96 hours (Thursday to
Monday) after the last.

## Reading a verdict

`runs/<id>/report.md` starts with two lines:

- **Against the last stable release (v0.15.0): no loss.** or **loss.** followed
  by the measures that lost, or **not compared** with the reason (gate-compare
  refused the pair, or a gate is missing). This line is the release rule's.
- Against the previous scheduled run: the same, for trend.

A measure lost when its paired 95% interval (gate-compare's, question level)
lies wholly below zero, the rule in `CLAUDE.md`. A drop whose interval reaches
zero is noise. The verdict covers five measures per benchmark: all evidence
packed, all evidence packed with memory text, evidence recall, all evidence
among the candidates and projected accuracy. The per-category rows are listed
but are not part of the verdict (30 to 130 questions each). Retrieval time is
not judged, because it depends on the machine's load.

## Looking at it

On the Mac mini (`ssh` as the benchmark user):

```sh
~/Sites/prism-weekly-scheduler/bin/status-weekly.sh          # job, next run, lock, last verdict, disk
~/Sites/prism-weekly-scheduler/bin/status-weekly.sh --sizes  # also the packs' size (slow)
tail -f ~/Sites/prism-weekly-scheduler/runs/<id>/driver.log  # a run in progress
```

`launchctl print gui/$(id -u)/com.prism.weekly-benchmark` shows the job and its
calendar entries (Weekday 1 and 4, 00:30); it does not print the next fire time,
so `status-weekly.sh` computes it.

## Publishing the results

The Mac mini holds no GitHub credentials. Run this on the MacBook after a run
completes. It copies the small result files over ssh into a branch of a fresh
worktree, checks each file's sha256 against the original, commits, pushes and
opens the pull request in `dwamianm/prism` the way PR #290 did, with
`Part of dwamianm/prism-monorepo#272`:

```sh
export BENCHMARK_HOST=dmac@<the Mac mini's address>
export BENCHMARK_SSH="-i ~/.ssh/<key> -o IdentitiesOnly=yes"
scripts/full-benchmark/publish-results.sh --list   # runs and whether each is published
scripts/full-benchmark/publish-results.sh          # the newest completed run not on main yet
```

The files go to `benchmarks/results/research/weekly/<run id>/`: the report, the
comparisons, the build summaries, the driver log and the gate reports' Markdown.
The gate reports' JSON (2.7 MB a run) and the packs never leave the Mac mini.
**Nothing runs this automatically yet**; the owner or a session runs it by hand.

## Installing, changing and removing it

From a checkout on the MacBook (copies the scripts to
`~/Sites/prism-weekly-scheduler/bin` on the Mac mini, checks them, installs the
job; safe to repeat, and it refuses while a run is going):

```sh
scripts/full-benchmark/deploy-schedule.sh
```

The installed copy is what the job runs, so a change to these scripts on `main`
reaches the schedule only after a deploy. To rehearse without a build, on the
Mac mini: `~/Sites/prism-weekly-scheduler/bin/weekly-run.sh --dry-run`. It does
everything but the builds and gates (fetch, disk and Ollama checks, a worktree
and `uv sync`, the gate-compare and report on copies of the stable reference's
gate reports) and then removes its worktree. Its folder in `dry-runs/` is never
published.

To remove the job (reports, packs and state stay; a run that is going blocks it
unless `--stop-run` is given):

```sh
~/Sites/prism-weekly-scheduler/bin/uninstall-schedule.sh
```

## After a stable release

Make the release's own run the new reference: take the run whose commit is the
release commit (or covers it) and showed no loss, then on the Mac mini

```sh
R=~/Sites/prism-weekly-scheduler; ID=<run id>
cp $R/runs/$ID/gate-weekly-locomo-$ID.json $R/reference/stable/gate-locomo.json
cp $R/runs/$ID/gate-weekly-lme-$ID.json $R/reference/stable/gate-longmemeval.json
printf 'NAME=v0.16.0\nCOMMIT=<full sha>\n' > $R/reference/stable/reference.env
```

## Layout on the Mac mini (`~/Sites/prism-weekly-scheduler`)

`bin/` the installed scripts; `state/` the lock, `last-completed.env`,
`schedule.log`, `launchd.out` and `launchd.err`; `runs/<id>/` one folder per run
(`YYYYMMDD-HHMMSS-<sha8>`, kept forever: `run.env`, `driver.log`, gate reports,
summaries, comparisons, `report.md`, `report.json`); `dry-runs/`; `reference/stable/`;
`packs/` and `cache/extraction/`. A run's builds are labeled `weekly-locomo-<id>`
and `weekly-lme-<id>`.

## Not done yet

Two unattended runs have not completed, and no results pull request has been
opened from a real run (issue acceptance criteria 2 and 3). The first scheduled
run is Thursday 2026-10-22 at 00:30 local time.

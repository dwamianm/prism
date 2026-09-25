# Measuring retrieval quality

## Latest complete GPT-5.4 defaults (2026-09-23)

| Benchmark | Complete result | Effective context ceiling |
|---|---:|---:|
| LongMemEval-S | **430/500 (86.0%)** | 3,996 tokens |
| LoCoMo, categories 1–4 | **985/1,540 (64.0%)** | 3,996 tokens |

Both use `gpt-5.4-2026-03-05`, medium reasoning for reader and judge, and default
retrieval over stored conversation turns. All 2,040 questions completed; all
4,080 benchmark model calls succeeded on their first attempt. The full
LongMemEval cohort includes preference and abstention questions. LoCoMo excludes
all 446 adversarial questions prospectively and uses a disclosed semantic yes/no
judge, not upstream token-F1. These scoped registrations differ from the older
adapter defaults documented below.

See the [registered protocol](benchmarks/results/research/2026-09-23/GPT54-COMPARISON-PROTOCOL.md),
[complete results and verification](benchmarks/results/research/2026-09-23/GPT54-DEFAULT-BENCHMARK-COMPARISON.md),
and [evidence-retention diagnostics](benchmarks/results/research/2026-09-23/GPT54-EVIDENCE-DIAGNOSTICS.md).
Zep's published figures are external references with different methods, not a
paired live arm. The earlier DeepSeek 87.4% LongMemEval result remains separate.
All source/answer records are retained locally under `data/gpt54-comparison-v1/`;
public reports contain their checksums. The completed run must not be rerun or
overwritten as part of verification.

These GPT-5.4 numbers stay the published reference. New answer runs use the
[DeepSeek answer track](#deepseek-answer-track-through-ollama), which is reported
separately and is not comparable with them.

## Offline evidence gate: the first gate for retrieval changes

Run this gate on every retrieval, packing or representation change before any
paid answer run. Spend reader and judge money only on changes that move it, and
put its before and after numbers in the pull request.

The gate replays the public `retrieve()` for all 1,540 LoCoMo and 500
LongMemEval-S questions over copies of the saved 2026-09-23 memory packs, at
each question's recorded reference time. It then measures the context that the
product renderer actually produced. It makes no reader, judge or paid API calls;
only the local query embedding runs, from locally cached model files. A full run
takes about 10 minutes on a laptop.

```sh
# Current code and defaults
uv run python -m benchmarks.diagnostics.product_packing gate --output /tmp/gate-before.json
# The change, switched on through its configuration option
uv run python -m benchmarks.diagnostics.product_packing gate \
  --set packing.token_budget=8192 --output /tmp/gate-after.json
uv run python -m benchmarks.diagnostics.product_packing gate-compare \
  /tmp/gate-before.json /tmp/gate-after.json --output /tmp/gate-comparison.json
```

Each command writes the JSON file named by `--output` and a Markdown summary
beside it (same name, `.md`). For each benchmark and category, the report gives:

- records per context;
- the memory-text share of context tokens;
- packed records without memory text (REFERENCE and KEY_VALUE fallbacks, and
  records whose text is blank);
- the share of questions with all annotated evidence in the context, and with
  its text;
- the rank distribution of annotated evidence;
- a projected accuracy.

The comparison pairs the two runs question by question. It reports wins, losses
and ties with a paired bootstrap interval. It rejects reports whose questions,
datasets, archive, tokenizer or projection constants differ. Its intervals
resample questions, and LoCoMo's questions come from only 10 conversations, so
treat them as narrow.

With current defaults the gate reproduces every saved context (same
`context_sha256`; the report lists any mismatch) and these baselines:

| | LoCoMo | LongMemEval-S |
|---|---:|---:|
| Records per context | 25.2 | 23.9 |
| Memory-text share of context tokens | 29% | 32% |
| All annotated evidence packed | 45/282 multi-hop (16.0%) | 403/470 (85.7%) |

**The projected accuracy is a planning estimate, not an answer score.** Each
question takes the saved GPT-5.4 run's accuracy on questions whose annotated
evidence was all packed, or partly missing (section 1 of the
[2026-09-23 audit](memory_bank/AUDIT-2026-09-23-BENCHMARK-GAP.md)). LoCoMo uses
its per-category rates. LongMemEval-S uses its pooled rates, because several of
its categories have only a handful of questions with evidence missing, so its
category projections are pooled estimates. Questions that retrieval cannot move
(no resolvable annotation, or abstention) keep their category's measured rate.
At the saved run's evidence states the projection gives 985/1,540 and 430/500 by
construction. The audit's re-pack simulator, using the same LoCoMo rates,
reproduced the real packed sets with mean Jaccard 0.83 and projected 63.3%
against 64.0% measured. The projection ignores distractor effects and gaps in
the annotations. All 2,040 questions have already been examined, so this is a
development gate: publication claims still need fresh or held-out data.

### What the gate can and cannot measure

The gate replays saved packs, so it measures changes to retrieval, ranking,
packing and rendering. A change that acts when memories are stored (extraction,
speaker handling, write-time facts or cards) needs new packs before this gate
can see it. When `--set` overrides leave every context identical to the saved
run, the summary says so; check the setting names before concluding that a
change does nothing.

`--set` takes dotted configuration keys with JSON values, and it rejects unknown
keys. It cannot change storage paths, the embedding model, extraction, query
reformulation, temporal relations, the organizer, or the API and MCP settings.
Environment variables and `.env` files are ignored. The organizer's opportunistic
maintenance is off during replay: it promotes records by wall-clock age, which
would make a replay depend on the day it runs. In the saved run the records were
minutes old, so maintenance changed nothing there.

### Inputs

All inputs are local and gitignored. Keep every one of them:

- the archive `data/gpt54-comparison-v1/` in the main checkout
  (`/Users/dwamianm/Sites/prism`); `--archive` points elsewhere, and worktrees
  use the main checkout's archive by default;
- the LoCoMo packs that `locomo/prepared.json` names, in
  `/Users/dwamianm/Sites/prism-locomo-baseline-2026-09-22/data/`;
- the 2026-09-22 LongMemEval-S control captures and packs, in
  `/Users/dwamianm/Sites/prism-opt-in-study-2026-09-22/data/opt-in-study/opt-in-successor-v2/baseline/`;
- both datasets under the main checkout's `data/benchmarks/`.

Removing either of those two checkouts deletes the gate's baseline, and the
packs cannot be rebuilt byte for byte. Before replaying, the gate checks the
dataset checksums, the archive's manifest of saved contexts, each LongMemEval-S
capture, and each pack's tree identity. Saved packs are never opened in place:
each is copied to a temporary directory first, because retrieval writes
receipts.

`--capture-dir` keeps every rendered context and receipt. These contain
benchmark text, so keep them out of published results.

`--plain vector`, `--plain bm25` or `--plain rrf` measures a plain RAG reference
instead of `retrieve()`: the same stored turns, ranked by that index alone (or by
reciprocal rank fusion of both, k=60) and packed in rank order as plain lines.
It accepts only `packing.token_budget` and `packing.overhead_tokens` overrides,
and its reports compare with PRME reports through `gate-compare`.

## Baselines for the GPT-5.4 comparison

The 2026-09-23 comparison has no reference points, so it cannot show how much of
the gap belongs to the memory system. `benchmarks.integrations.gpt54_baselines`
adds baseline arms. Each one reuses the registered reader, judge, prompts, model
settings and questions, and checks them against the
[registration](benchmarks/results/research/2026-09-23/gpt54-comparison-v1-registration.json)
before it makes any call. The frozen harness itself is unchanged.

| Arm | Benchmarks | Context the reader sees |
|---|---|---|
| `full-context` | LoCoMo | The whole conversation: a date header per session, one `Speaker: text` line per turn, image captions kept. 12,612 to 24,081 tokens per conversation, 20,619 on average. |
| `plain-vector`, `plain-bm25`, `plain-rrf` | LoCoMo, LongMemEval-S | The saved packs' raw turns, ranked by one index or by RRF (k=60) of both, packed in rank order as `(date) speaker: text` lines up to 3,996 tokens. No PRME scoring, expansion, filters or renderer. |
| `prme`, `prme@<commit>`, `prme-<name>` | LoCoMo, LongMemEval-S | PRME's own `retrieve()`, replayed over the saved packs by the evidence gate and rendered as the product renders it, up to 3,996 tokens: the current defaults (`prme@<commit>` is a later baseline of them, filed under its commit), or a named variant with the settings it changes. Its preparation counts how many contexts still match the saved 2026-09-23 run exactly. These arms run on the [DeepSeek answer track](#deepseek-answer-track-through-ollama) only. |

The plain arms use the same stored text, embedding model and indexes as PRME, so
they isolate PRME's retrieval and rendering rather than reproduce a published
RAG system. LoCoMo turns are stored with their date and speaker in the text,
which mainly affects BM25.

On the evidence gate at 3,996 tokens, RRF is the best plain method, so
`plain-rrf` is the arm to run on both benchmarks. The reader-format row uses
`--set 'packing.context_format="reader"' --set 'packing.multipath_ordering="score"'`.

| Run | LoCoMo all evidence packed | LoCoMo multi-hop | LoCoMo projected | LongMemEval-S all evidence packed | LongMemEval-S projected |
|---|---:|---:|---:|---:|---:|
| PRME defaults (JSON, balanced) | 983/1,536 (64.0%) | 45/282 (16.0%) | 64.0% | 403/470 (85.7%) | 86.0% |
| PRME reader format, score order | 1,202/1,536 (78.3%) | 103/282 (36.5%) | 73.7% | 396/470 (84.3%) | 85.1% |
| Plain vector | 1,232/1,536 (80.2%) | 149/282 (52.8%) | 74.8% | 403/470 (85.7%) | 86.0% |
| Plain BM25 | 1,112/1,536 (72.4%) | 101/282 (35.8%) | 69.7% | 357/470 (76.0%) | 80.1% |
| Plain RRF | 1,283/1,536 (83.5%) | 153/282 (54.3%) | 77.3% | 403/470 (85.7%) | 86.0% |

Paired question by question, plain RRF packs all LoCoMo evidence for more
questions than PRME defaults (+19.5 pp, 95% interval +17.3 to +21.7), the PRME
reader format (+5.3 pp, +3.6 to +7.0), plain vector (+3.3 pp, +1.8 to +4.9) and
plain BM25 (+11.1 pp, +9.4 to +12.8). On LongMemEval-S it ties PRME defaults and
plain vector (403/470 each, with 20 and 29 questions won and lost each way) and
beats plain BM25 (+9.8 pp, +7.2 to +12.8). The projected accuracies are planning
estimates from the gate, not answer scores.

The [reader ordering record](benchmarks/results/research/2026-09-24/READER-PACKING-ORDER-GATE-V1.md)
compares score order with `balanced` under the reader format at 4K (3,996
tokens) and 8K, under the weighted score and under rank fusion with session
decay 0.6, by category.

```sh
# No model calls. prepare needs a clean, committed tree.
uv run python -m benchmarks.integrations.gpt54_baselines prepare full-context
uv run python -m benchmarks.integrations.gpt54_baselines prepare plain-rrf --benchmark locomo
uv run python -m benchmarks.integrations.gpt54_baselines estimate plain-rrf --benchmark locomo
# Paid reader and judge calls: the owner runs this after approving the spend.
uv run python -m benchmarks.integrations.gpt54_baselines run plain-rrf --benchmark locomo \
  --max-usd <approved cap>
```

`prepare` writes under the main checkout's `data/gpt54-baselines-v1/`, shared by
every worktree, and never overwrites a prepared arm. For a plain arm it runs the
evidence gate with the same ranking, so the gate report next to the contexts
describes exactly what the reader will see. An arm that has made paid calls
cannot be prepared again.

`estimate` scales each question's recorded 2026-09-23 reader and judge usage to
the arm's context. The ledger reserves each request's worst case before sending
it, so the cap also needs room for the requests in flight:

| Arm | Estimated cost | Minimum cap |
|---|---:|---:|
| LoCoMo full context | $44.92 ($8.72 if every later question in a conversation reads the shared prefix from the provider's cache, which is not guaranteed) | $46.98 |
| LoCoMo plain RRF | $12.11 | $13.28 |
| LongMemEval-S plain RRF | $4.46 | $5.69 |

`run` asks for confirmation at an interactive terminal and refuses to start
without one, so no unattended process can spend. `--max-usd` is the arm's
approved cap, which its ledger enforces across runs; a later run may raise it,
and the ledger records the change, but never lower it. `run` also needs the
saved run's reader and judge calibration in the archive, `OPENAI_API_KEY` in the
main checkout's `.env`, and, for LongMemEval-S, the official judge in the
2026-09-22 study checkout.

The first failure stops new questions and keeps its record. A later run asks a
question again only when it received no answer or verdict (a budget stop, a
provider HTTP error, an ambiguous transport failure or an interrupted process).
Truncated or malformed responses and invalid verdicts are final and leave the
arm incomplete, as the registration's retry policy requires. A complete arm is
written to `benchmarks/results/research/<date>/` with its accuracy, categories,
replaced failures, cost, provider tokens and context budget, and is never rerun.
None of these arms has been run yet:

| Arm | LongMemEval-S | LoCoMo | Context |
|---|---:|---:|---|
| PRME defaults (2026-09-23) | 430/500 (86.0%) | 985/1,540 (64.0%) | 3,996-token ceiling |
| Full context | not in scope | not run yet | whole conversation |
| Plain RRF | not run yet | not run yet | 3,996-token ceiling |

## DeepSeek answer track through Ollama

Paid answer runs are not allowed for the epic #77 work, so answer runs use a
second track: `deepseek-v4.1-flash:cloud` as both reader and judge, through the
local Ollama server's OpenAI-compatible endpoint at `http://127.0.0.1:11434/v1`.
It is `gpt54_baselines` with `--provider ollama`. It uses the same arms,
registered prompts, question sets and official LongMemEval-S judge as the GPT-5.4
track, and it keeps its own contexts, answers and results.

**The two tracks are not comparable.** The GPT-5.4 numbers (LongMemEval-S 86.0%,
LoCoMo 64.0%) stay the published reference. A DeepSeek score is compared only
with another DeepSeek score from the same model identity and settings:

1. Record a DeepSeek baseline for the current defaults (the `prme` arm) on both
   benchmarks, prepared from a commit on `main`.
2. Prepare each change as a named variant of the defaults (`prme-<name>`, with
   the settings it changes), then answer it with `run-pair` together with a
   fresh answer run of the current baseline of the defaults (`--baseline`):
   the one whose own answer run completed most recently for that benchmark
   (#127). The two runs are answered in one session,
   interleaved question by question, and `compare` pairs a variant only with
   the defaults run answered alongside it (#129). The baseline was prepared
   at its own commit, so a variant prepared at a later commit, or on a branch,
   could also pick up any code change between the two commits. `prepare`
   therefore replays the defaults at the variant's commit through the
   evidence gate as well, without captures, keeps that report as
   `defaults-gate.json` next to the variant's own, and records the hash of the
   defaults' text on every question (#139). `run-pair` checks those hashes
   against that report, and refuses a variant whose defaults at its commit
   read different text from the baseline on any question; `compare` refuses
   such a pair. When that happens because a
   change merged to `main` altered the defaults, record a new baseline at a
   commit that includes it (`prme@<commit>` below) and prepare the variant
   again from a commit that descends from it. When the variant's own branch
   alters the defaults, put that change behind its settings. A variant
   prepared before #139, or by a checkout without it, records no such
   hashes, so `run-pair` answers no further pair of it, and `compare`
   refuses its pairs that started after #139 (below). While the variant
   still needs its first pair or its confirmation, prepare it again from a
   checkout with #139, under a new name with the same settings. With the same
   context text it stays the same variant; if the newer code changed its
   text, it is a new variant with its own first pair and confirmation (#143).
3. A default changes only when the default-change rule in the epic #77 work
   rules in `CLAUDE.md` is met. That rule includes a confirmation run: run
   `run-pair` again for the same variant, which starts a new pair with a fresh
   variant run and a fresh defaults run; it must pass the test a second time.
   A variant is what it changes, not its arm name (#130): its settings and
   its context text on every question. Its pairs count together under any
   arm name, but only alongside one baseline and on one context text (#143).
   Its first pair is the first of them to complete, and its confirmation the
   next to complete that started after the first completed and repeats the
   first pair's settings; `compare` refuses any other pair and `run-pair`
   answers none. A code change that alters a variant's context text makes a
   new variant, with its own first pair and confirmation. Other settings on
   the same context text stay in the same count, but only a pair with the
   first pair's settings can confirm it. `compare` lists every arm and pair
   with the variant's settings or its context text, alongside any baseline,
   so the earlier attempts stay in view. `compare` records each first pair and
   confirmation it accepts, and `verdict` reads those records and says whether
   the variant passed (#144); the pull request that flips a default cites it.
   It also needs the [interleaved A/A check](#interleaved-aa-check): one pair
   of the defaults against themselves on both benchmarks. After a default
   changes, or when the model identity changes, record a new baseline with
   `prepare prme` and `run prme` at a newer commit on `main` (see
   `prme@<commit>` below), and answer later variants alongside it, where
   every variant's count starts again from zero.

`:cloud` models are served by Ollama's hosted service, not by this machine. The
prompts leave the machine and count against the Ollama account's usage limits.
LoCoMo and LongMemEval-S are public datasets.

Before running it:

- An Ollama server on `127.0.0.1:11434`, signed in to an Ollama account, with
  `deepseek-v4.1-flash:cloud` pulled. The address and model are fixed.
- If `HTTP_PROXY` is set, exclude `127.0.0.1` in `NO_PROXY`: the model identity
  lookup honors proxy settings, while the answer calls ignore them.
- The same datasets and official LongMemEval-S judge as the GPT-5.4 track.
  `calibrate` uses both benchmarks' prompts, so it needs them even for a LoCoMo
  run.
- For `prepare prme`, a current `origin/main` (run `git fetch` first).
- Where possible, turn off the Ollama app's automatic updates while variant
  pairs are in progress. A new server version needs a new A/A pair on both
  benchmarks before any further variant pair counts (#137, below).

What the track sends and records:

- Every request: the prompt as one user message, temperature 0, seed 20260923
  (the registration's bootstrap seed), the registered token limits (8,192 for
  the reader, 2,048 for the judge), no streaming, and thinking off
  (`reasoning_effort` `none`, as in this repository's earlier DeepSeek runs).
  The reader prompts already ask for the evidence to be explained before the
  answer.
- Every result, private and published: an `answer_model` block with the provider,
  API, endpoint, model, sampling settings, retry settings, the Ollama server's
  model identity (manifest digest, remote host and remote model) and the
  failure policy its answers were given under; the calibration it passed and
  how many attempts that took; the commit and settings that prepared the
  contexts; the provider token counts; a `failure_policy` block that counts
  retries and unscored questions (below); and a cost of $0.
- Each call's request, every HTTP attempt and the answer are written once, so
  the result can be verified again from them, as on the GPT-5.4 track.

Safeguards:

- The endpoint must be `127.0.0.1` or `::1` with the `/v1` path, and no
  credentials are sent, so the track cannot reach a paid API. It never builds
  the OpenAI client and never reads `OPENAI_API_KEY`.
- Only Ollama cloud models are accepted. The OpenAI-compatible endpoint cannot
  set a context size, so a local model could cut long prompts short without
  saying so.
- A cloud model's remote weights are not pinned; the manifest digest is the
  strongest identity Ollama gives, and seeded sampling on a hosted service is
  not guaranteed to repeat. The identity is checked before and after every
  calibration and run, and a change during a run publishes nothing.
- Contexts and answers live under the main checkout's
  `data/ollama-answers-v1/ollama-deepseek-v4.1-flash-cloud/`, apart from the
  GPT-5.4 track. Published results are named
  `ollama-deepseek-v4.1-flash-cloud-<arm>-<benchmark>-result.json`.
- `calibrate` runs the registered authored calibration: two authored cases with
  each benchmark's prompts, where the judge must accept the reader's answer and
  the reference and reject a wrong answer. Every attempt is kept, and `run` and
  `run-pair` refuse to start until the same model identity, with the same
  settings, has passed.
- An arm's first answer binds it to that reader and judge; a run with another
  model or other settings is refused, so answers are never mixed. Each arm keeps
  an append-only run log outside its folder, which every result summarizes.
  A named variant's run log also records every preparation of it with its
  identity: the settings whose values differ from the defaults at that
  commit, by dotted name as the configuration reads them, and the hash of
  its context text on every question (#130). So `--set x=true` and
  `--set x=True` are the same settings, an override that repeats a default
  changes nothing, and `prepare` refuses a variant whose settings are all
  defaults. `prepare` says when another arm was already prepared with the
  same settings and context text, which makes it the same variant; on the
  same context text under other settings, whose pairs count with this one's
  although only a pair with the first pair's settings can confirm it; or with
  the same settings on other context text, which makes it another variant
  whose pairs `compare` lists beside this one's (#143).
- Every start, resume and finish records the live Ollama server version in the
  run log, and every result lists the server versions its answers were given
  under (`server_versions`). `compare` refuses results whose recorded server
  versions differ, within one run or between the two (#129).
- A named variant is answered only with `run-pair`; `run` refuses it. A plain
  or full-context arm can be paired with the defaults the same way; answered
  on its own, it is a reference score that `compare` pairs with nothing. The
  pair's `--baseline` must be a defaults baseline with a complete answer run of
  its own (`run prme`), which records it, and it must be the current baseline:
  the one whose own complete answer run finished last on that benchmark. So
  once a newer baseline is recorded, no pair is answered alongside an older
  one, not even an A/A pair, and a pair whose baseline stops being current
  while it is answered is kept but not published (#127).
- A pair's answers live under `pairs/<baseline>/<arm>/<benchmark>/pair-<N>/`,
  with a `before` folder (the defaults) and an `after` folder. Each baseline
  and arm has one append-only pair run log under `runs/pairs/`, whose events
  carry the pair number. The two sides share one queue in registered question
  order, each question's `after` side first, through the registration's four
  concurrent requests. `run-pair` resumes the latest pair while it can still
  finish. It starts the next pair instead after a complete pair, a final
  failure, a new preparation of either arm, an answer model or settings change,
  a different Ollama server version from the one the pair started under, or a
  pair started under another failure policy (read from its run log, so the
  reason is the same for a pair folder moved aside). Run `run-pair` only from
  a checkout that includes #132: the private data is shared by every
  worktree, and older code gives up a pair started under the amendment and
  answers the next one under the registered rules. An
  unfinished pair it gives up gets an `abandoned` event with the reason, and
  every pair stays on record: each result lists every other pair of that arm
  and how it ended (`earlier_pairs`), alongside this baseline or an earlier
  one, since a new baseline numbers the arm's pairs from 1 again, and
  `pairs_started` counts them all (#130). Each start of a variant's pair
  records the pair's id and the variant's identity in the pair's run log.
  Once the run log records a pair complete, it stays complete, even when its
  folder is moved aside. `run-pair` refuses to answer a variant whose first
  pair and confirmation alongside the baseline are already complete, under
  any of its arm names, and a pair on the context text of a first pair
  answered under other settings, which could confirm nothing (#143). A
  server version that
  changes while a pair is answered is caught at its finish, and the pair is
  kept but not published. A complete pair with a side that `compare` will
  refuse under the 1% limit is published, and its `finished` event and every
  later pair's `earlier_pairs` mark it as complete but invalid. A `--sample`
  after a complete pair opens the next
  pair, whose full run reuses the sample's answers. Each side is published as its own result,
  named
  `ollama-deepseek-v4.1-flash-cloud-<baseline>-vs-<arm>-<benchmark>-pair-<N>-<side>-result.json`,
  and carries a `pair` block that `compare` checks. Its `run_log` summarizes
  the pair's run log and the run log of the arm whose contexts it read.
- Every A/A pair `run-pair` publishes is added to the track's A/A record,
  `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-aa-checks.jsonl`,
  one line per pair in the order they finished (#137). Each line records the
  conditions the pair was answered under (the answer model with its identity
  and settings, the Ollama server versions, the failure policy and its
  amendment, and the context budget and tokenizer), the paths and digests of
  its published results, whether `compare` accepted it and its interval, and
  whether it is the first accepted A/A pair on its benchmark under those
  conditions (`first`), which is the A/A check the default-change rule reads
  for them. Every start of a pair records its id, which ties the record to
  the pair's run log. A pair whose run log records it complete but invalid
  (#132) is recorded as refused; any other refusal by `compare` records
  nothing and is reported. `run-pair` starts an A/A pair only when the record
  already lists every complete A/A pair on its benchmark, and a variant's
  pair only when the record holds an A/A check under the current model
  identity, answer settings, failure policy, Ollama server version and
  budget on both benchmarks, since `compare` would refuse the pair otherwise
  and it would still count as the variant's first pair or confirmation.
  `record-aa-check` adds an A/A pair published before the record existed, or
  one `run-pair` published but could not add (it then says why and prints the
  command). It refuses a pair the run logs do not show complete or record
  under another pair id, one already recorded, one whose results are not
  published under this checkout's `benchmarks/results/research/`, one that
  finished before a pair the record already lists on its benchmark, and any
  pair while the record leaves out a complete A/A pair that finished before
  it. The record belongs to the checkout, while the run logs are shared by
  every worktree, so answer A/A pairs from the checkout that keeps the
  record, or copy a pair's published results into it before recording the
  pair. Commit each new line with the pair's published results: the record
  is tracked, so until then the tree is not clean and `prepare` refuses to
  run. The two A/A pairs of 2026-09-24 were added with `record-aa-check`.
- Every pair the `compare` command accepts as a variant's first pair or
  confirmation, or as an A/A pair, is recorded for the verdict step (#144): a
  `compared` event in the pair's run log, and a line in the track's verdict
  record,
  `benchmarks/results/research/ollama-deepseek-v4.1-flash-cloud-pair-verdicts.jsonl`.
  The line records the benchmark, the role (`first`, `confirmation` or `aa`),
  the baseline and the arm, the pair's id and when it started and finished,
  the variant's settings and context text hash with the first pair it counts
  with, the difference, `interval_95` (for LoCoMo also the conversation-level
  and question-level intervals), whether the interval excludes zero, the A/A
  check a variant's pair relied on, compare's warnings, and the paths and
  digests of both published results. It is checked against those results
  before it is written, and the pair must be on record as complete in its
  run log. The record keeps one line per pair: comparing a pair again adds an
  event to its run log but no line, and a line with other values for the pair
  is refused. `compare` records only results published under this checkout's
  `benchmarks/results/research/`: for results anywhere else it prints the
  comparison and then fails, so compare the published copies. On a machine
  without the track's run logs it compares and records nothing. A reference
  arm's pair and a repeat answered as two runs on their own are not recorded,
  and the `aa` lines are for reference, since the margin reads the A/A
  record. The record is tracked from the start, so a new line leaves the tree
  dirty and `prepare` refuses to run until it is committed with the pair's
  published results, as for the A/A record.
- `verdict prme --variant <name> --provider ollama` reads the verdict record
  for the variant on both benchmarks and prints `pass`, `fail` or
  `incomplete`, with each pair's numbers. On each benchmark the variant's
  pairs count alongside the current baseline on its context text (#143). With
  the track's run logs on the machine, the current baseline is theirs, the
  context text that of the arm's latest preparation, the recorded first pair
  and confirmation must be the ones the run logs count, the A/A record must
  list every complete A/A pair, and compare's current warnings about each
  pair are repeated; the output then reports `checked_against_run_logs:
  true`. Elsewhere the baseline is that of the record's last first pair or
  confirmation on the benchmark, with a warning. A first pair or confirmation
  passes with a gain on at least one benchmark whose 95% interval excludes
  zero and no loss on either whose interval excludes zero, and a loss decides
  it as soon as it is recorded. Where an accepted A/A pair under the pair's
  conditions excluded zero, on either benchmark, the gain must also be larger
  than the largest absolute A/A difference in the A/A record on its
  benchmark, or the #118 repeat's if that is larger; each pair's `margin`
  says whether that applied. The variant passes when both pass, and fails
  when either fails. A pair `compare` refused was never recorded, so it
  counts as neither, and a pair alongside an earlier baseline or on other
  context text does not count. The pairs must record the same settings on
  both benchmarks, and a first pair and its confirmation the same model
  identity and answer settings. Every line is checked against the published
  results it names, and its numbers, and each accepted A/A line's, are
  recomputed from them, so an edited line is refused; so is one recorded
  before a change to the paired bootstrap code, which says so. The pull
  request that flips a default cites its output.
- The `prme` arm prepares the shipped defaults, so `prepare` refuses it unless
  the checked-out commit is on `main`. A variant can be prepared from a branch.
  A variant's preparation replays the evidence gate twice, once with its
  settings and once with the defaults at the same commit (#139), so it takes
  about twice as long as the defaults'.
- Each commit has at most one defaults baseline. The first is the `prme` arm.
  Once it has a complete run, `prepare prme` at another commit on `main` files
  a new baseline as `prme@<commit>`, where `<commit>` is the first 8 characters
  of the checked-out commit. It has its own contexts, answers, run log and
  published result, and every earlier baseline and its record stay as they
  were. The new arm's run log starts with a `new-baseline` event naming its
  commit, the first baseline's commit and any earlier `prme@<commit>` baseline
  that never finished, and its result reports that event.
- `run prme` answers the baseline of the checked-out commit, so run it from the
  commit that prepared it. While a `prme@<commit>` baseline is prepared and not
  complete, `prepare prme` and `run prme` at any other commit refuse and name
  it: check out its commit to finish it, or move its folder aside to give it
  up, which the next baseline's `new-baseline` event records.
- A commit that already has a complete baseline cannot get another. If the
  model identity changes while the newest baseline is at the checked-out
  commit, record the new baseline at the next commit on `main`.
- A new baseline, the first on a benchmark or a later one, must be prepared at
  a commit that descends from the commit of every complete baseline on either
  benchmark, so the current baseline holds the newest defaults recorded and
  neither benchmark's lags behind the other's. `prepare prme` and `run prme`
  refuse an older commit on `main`, or one git cannot place (run `git fetch`
  first), and `run` refuses to answer such a baseline in full when called
  directly (#127).
- `run` and `run-pair` need no spending cap or terminal confirmation. They use
  the registration's four concurrent requests (a pair's two sides share them);
  if the Ollama account's usage limit stops a run with HTTP 429, run it again
  later and it asks only the questions that got no answer.
- Answers follow the registered retry rules described for the GPT-5.4 arms
  above, as amended for this track on 2026-09-24 (#132). The amendment is
  recorded in
  `benchmarks/results/research/2026-09-24/ollama-failure-policy-amendment.json`,
  which `run` and `run-pair` check against the registration before asking
  anything. It pins the policy text and the values that score under it (the
  verdict pattern, the retry file names and the 1% limit), so changing either
  needs a new amendment. It applies to standalone runs and to both sides of
  every pair alike, and the GPT-5.4 track keeps the registered rules:
  - A reader answer that ends for any reason other than `stop` (in practice, a
    looping answer that reaches the 8,192-token limit) is sent once more as the
    same request. If the second answer also ends early, the question is scored
    incorrect without calling the judge and recorded as `truncated`.
  - A verdict is accepted when what is left after removing leading and
    trailing characters other than the letters A to Z reads `yes` or `no`, in
    any case, so `姫Yes` counts as yes. Otherwise (an empty verdict included),
    or when the judge's response ends early, the judge is called once more,
    and if that verdict is not accepted either, the question is scored
    incorrect and recorded as `verdict_unresolved`. "Letters" means A to Z,
    because the stray characters seen so far were CJK characters, which
    Unicode counts as letters.
  - At most one retry per reader call and one per judge call. Each retry is
    kept next to the call it repeats (`reader-retry.json`, `judge-retry.json`),
    and each result row records its `outcome` (`judged`, `truncated` or
    `verdict_unresolved`), the retries' digests and whether the verdict was
    accepted only after stray characters were removed.
  - Every result's `failure_policy` block names the amendment and counts the
    reader and judge retries, the verdicts accepted after removing stray
    characters, and the `truncated` and `verdict_unresolved` questions.
    `compare` refuses a result in which more than 1% of the questions are
    `truncated` or `verdict_unresolved`, so a pair with such a side is invalid.
    Every `finished` event on the track records the unscored count, and a
    complete result over the limit is still published but marked `invalid`
    there, with a warning.
  - The authored calibration is not amended: it still needs every answer to
    end normally and every verdict to read exactly `yes` or `no`.
- Any other final failure (for example an empty answer or a response from
  another model) leaves the arm incomplete, and it publishes nothing. To start
  that arm over, move its folder aside (or remove it) and prepare it again. The
  run log outside the folder keeps every earlier run, and the result reports
  how many runs and preparations there were. An arm with a complete run, alone
  or in a pair, is never prepared again. An arm that already has answers under
  the registered rules is never answered under the amendment; prepare it again
  instead, so no arm mixes two policies. Its recorded answers still verify
  under the rules they were given under.

```sh
# No model calls. prepare needs a clean, committed tree.
uv run python -m benchmarks.integrations.gpt54_baselines prepare prme --benchmark locomo --provider ollama
uv run python -m benchmarks.integrations.gpt54_baselines prepare prme --benchmark locomo --provider ollama \
  --variant rrf --set 'scoring.fusion="rrf"'
# DeepSeek reader and judge calls through Ollama. No API cost.
uv run python -m benchmarks.integrations.gpt54_baselines calibrate --provider ollama
uv run python -m benchmarks.integrations.gpt54_baselines run prme --benchmark locomo --provider ollama --sample 2
uv run python -m benchmarks.integrations.gpt54_baselines run prme --benchmark locomo --provider ollama
# A variant and a fresh run of the defaults, interleaved question by question.
uv run python -m benchmarks.integrations.gpt54_baselines run-pair prme --benchmark locomo --provider ollama \
  --variant rrf --baseline prme@46647825
# The A/A check: the baseline answered against itself.
uv run python -m benchmarks.integrations.gpt54_baselines run-pair prme --benchmark locomo --provider ollama \
  --baseline prme@46647825
# No model calls: the paired difference and its 95% interval. A variant's first pair or confirmation, or an A/A
# pair, is also recorded in the verdict record (#144).
uv run python -m benchmarks.integrations.gpt54_baselines compare \
  --before <pair's published before result> --after <same pair's published after result>
# No model calls: whether a variant passed the default-change rule, from the pairs compare recorded (#144).
uv run python -m benchmarks.integrations.gpt54_baselines verdict prme --variant rrf --provider ollama
# No model calls: add an A/A pair published before the A/A record existed (run-pair adds the others).
uv run python -m benchmarks.integrations.gpt54_baselines record-aa-check \
  --before <A/A pair's before result> --after <same pair's after result>
# A repeat: an earlier baseline and a later one that read the same contexts.
uv run python -m benchmarks.integrations.gpt54_baselines compare \
  --before <earlier baseline result> --after <later baseline result>
```

`--sample N` asks only the first N questions of each category, in registered
order, as a smoke check, with `run` or `run-pair`. Its result is labeled as a
sample, reports only how many answers the judge accepted, with no accuracy or
interval, and is published with a `-sample-N` suffix. A later full run (of the
same pair, for `run-pair`) reuses its answers.

`compare` refuses results that are incomplete, are samples, cover different
questions, are the same answer run, or were answered by different model
identities or settings (an Ollama server upgrade alone does not change the
identity). It also refuses results answered under different failure policies,
a result in which more than 1% of the questions are `truncated` or
`verdict_unresolved`, results that record different amendments, and a
DeepSeek result answered under the registered rules after the amendment was
registered, which only a checkout without #132 produces (#132). Its
`failure_policy` block reports both sides' counts. On the DeepSeek track it
also refuses any pairing other than the
two sides of one `run-pair` pair, with the defaults as `--before`, or a repeat
(below), and it refuses results whose recorded Ollama server versions differ
(#129). Unless the two results are a repeat, it also refuses a pair whose
before side is not the current baseline when `compare` runs, or did not read
that baseline's own contexts, so a pair answered before a newer baseline
completed no longer counts. It reads the track's run logs for that, so compare
pairs on the machine that keeps their answers. A variant's pair must be the
variant's first pair or its confirmation (#130). `compare` reads every variant
preparation and pair in the track's run logs for that. The pairs that count
together are those alongside the pair's baseline that read its context text,
under any arm name, in the order they completed (#143). The first to complete
is the first pair, and the next to complete that started after the first
completed and has the first pair's settings is the confirmation; a pair given
up, not published, or complete but invalid under the 1% limit counts as
neither. Settings are compared only when both pairs recorded them, so for a
pair started before #130 the context text decides alone. `compare` refuses
any other pair, such as one on the first pair's context text under other
settings, and a pair the run logs do not show as complete, with the pair id
and identity its start recorded. A pair alongside a new baseline, or on other
context text, is not a confirmation of an earlier pair: it starts a count of
its own. Its `variant` block names the pair's `role`, the `first` pair and
the `confirmation` (with the commit and server version each was answered
with), every arm with the variant's settings or its context text, alongside
any baseline, with each preparation and pair (`arms`), the variants that
change any of the same settings to other values (`related`), the variant
pairs that record no identity (`unknown`), the pairs with the variant's
settings or its context text that started before this one finished and never
completed for a reason other than a final failure (`dropped`), the complete
pairs alongside this baseline with the variant's settings or its context text
that differ in the other, with what differs (`other_identities`), and the
complete pairs alongside other baselines, with whether each of those
baselines prepared the same defaults' context text as this one
(`other_baselines`). It warns about dropped pairs, about unknown pairs that
completed earlier, about other identities, about pairs alongside other
baselines, separately when such a baseline prepared the same defaults' text
(so the defaults did not change and those pairs ran the same test), and when
related variants have complete pairs. Its `baseline` block
lists every
complete baseline in the order their own runs finished and names the current
one (#127). A pair can therefore be compared again only while its baseline is
current: keep `compare`'s output with the pull request that relies on it,
before a newer baseline is recorded. The default-change rule reads only the 4K
budget, so `run-pair` and `compare` refuse the defaults or a variant prepared
with any context budget other than 3,996 tokens, counted by the registered
`cl100k_base` tokenizer; the plain and full-context reference arms are paired
with the defaults at any budget (#125). A variant that changes the budget can still be prepared, so the
evidence gate can measure it. A variant's pair must also have been answered
under the model identity, answer settings, failure policy, Ollama server
version and context budget of an A/A check in the track's A/A record, on both
benchmarks, so after an Ollama update `compare` refuses every variant pair
answered under the new version until an A/A pair under it has been answered
and accepted on both (#137). The A/A check is the first A/A pair under those
conditions that completed and that `compare` accepted; a later one never
replaces it. `compare` reads the A/A record in the checkout it runs from and
checks it against the track's run logs and the published results: the record
must list every A/A pair the run logs show complete, and nothing else, and
each line must name its pair's published results with their digests and the
conditions they record. Its `aa_check` block names the
check on each benchmark and lists every other A/A pair under the same
conditions (`other_pairs`), with a warning when there are any, and another
when any of them excludes zero, since the rule's extra margin then applies.
The plain and full-context reference arms need no A/A check. Each row published since #125 carries two
hashes: `context_sha256` covers the whole capture file, including the retrieval
receipt's random request id, so two preparations of the same contexts never
share one, and `context_text_sha256` covers the context text alone.
`compare`'s `contexts` block reports how many questions the two sides asked on
different context text (`differing`) and what shows it (`shown_by`): the rows'
text hashes, or, for results published before them, both results reading one
preparation or both reproducing every saved context. Both are `null`, with a
warning, when nothing shows it. A variant's rows also carry
`defaults_text_sha256`, the hash of the defaults' text replayed at the
variant's commit (#139), and the `contexts` block's `changed_by_other_code`
counts the questions on which those defaults read other text than the before
side: what changed between the two preparations, such as code on `main` or on
the variant's branch, the dependencies or the saved run. `compare` refuses a
variant's pair with any, since the pair would credit that change to the
variant, so at 0 every question in `differing` differs by the variant's
settings alone. The count is `null` for any other arm. It is also `null` for
the `prme-reader-rrf` pairs, which started before #139 at a commit whose
defaults read the baseline's text, and `compare` accepts those with a warning;
it refuses any variant pair without the count that started at or after
2026-09-24 18:00 UTC, which came from a preparation or checkout without #139.
The warning about contexts prepared from different commits gives the
`differing` count, and is left out when every context text is the same or when
the defaults at the variant's commit read the before side's text on every
question. `compare` reports the paired accuracy difference with a 95% interval, per
category as well, and the questions gained and lost. LoCoMo intervals resample
its 10 conversations, keeping each conversation's questions together;
LongMemEval-S intervals resample questions, because each question has its own
history. The default-change rule reads `interval_95`. A percentile bootstrap
over only 10 conversations covers less than 95% (for a variant with no effect
it excluded zero about 9% of the time instead of 5%), so for LoCoMo
`interval_95` spans the conversation-level interval
(`interval_95_conversations`) and the question-level one
(`interval_95_questions`), and a LoCoMo difference excludes zero only when
both do. A category with fewer than two conversations has no
conversation-level interval, and its `interval_95` is empty. When both results are
baselines of the defaults with the same inputs,
`compare` reports them as a repeat: how many verdicts changed, whether the
interval excludes zero, and whether the two were answered as one interleaved
pair. The two sides of an A/A pair read the same prepared contexts. Two
baselines answered on their own have the same inputs when they have the same
budget, the same context text on every question, and they used the same code
for sending, checking and judging the calls and the same Ollama server
version; that sequential repeat is still accepted so the #118 measurement can
be checked. The rows' text hashes show the same text. Results published before
rows carried them (#125), such as the #118 repeat, show it only when both
preparations reproduce the saved 2026-09-23 context on every question, which
holds only while the defaults still do.

| DeepSeek run | LongMemEval-S | LoCoMo | Context |
|---|---:|---:|---|
| Baseline, `prme` defaults at `97c9402f` (2026-09-24) ([LoCoMo](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme-locomo-result.json), [LongMemEval-S](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme-longmemeval-result.json)) | **423/500 (84.6%)**, 95% interval 81.4% to 87.6% | **1,014/1,540 (65.8%)**, 95% interval 63.4% to 68.2% | 3,996-token ceiling |
| Repeat of the defaults, `prme@46647825` (2026-09-24) ([LoCoMo](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-locomo-result.json), [LongMemEval-S](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-longmemeval-result.json)) | **434/500 (86.8%)**, 95% interval 83.8% to 89.6% | **1,007/1,540 (65.4%)**, 95% interval 63.0% to 67.7% | 3,996-token ceiling |
| Smoke check, first 2 per category ([LoCoMo](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme-locomo-sample-2-result.json), [LongMemEval-S](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme-longmemeval-sample-2-result.json)) | 12 answered, 9 accepted (not a score) | 8 answered, 8 accepted (not a score) | 3,996-token ceiling |

The first baseline was the reference for DeepSeek paired runs until its
repeat, `prme@46647825`, completed; the repeat is now the most recent complete
baseline, so later variants are answered alongside it (step 2), and `run-pair`
and `compare` refuse the first baseline as the before side of any pair other
than a repeat of the defaults (#127). The repeat counts as a baseline because
it holds the defaults prepared at a newer commit on `main`. Its own answer run
is never the before side of a variant's comparison, because `compare` pairs a
variant only with the fresh defaults run answered alongside it (#129).
`compare` refuses results
answered by another model identity or other settings. Both first-baseline arms
were prepared from `main` at `97c9402f`,
and every context matches the saved 2026-09-23 run (all 1,540 LoCoMo and 500
LongMemEval-S questions). The smoke checks used separate local folders, so the
baseline reused none of their answers. The model passed calibration on its
first attempt, and all 4,080 reader and judge calls in the two results returned
HTTP 200 on their first attempt. Each row was checked against its recorded
calls when the result was written, and repeating that check over the kept
records reproduces both results exactly. The intervals resample questions;
LoCoMo's questions come from 10 conversations, and its conversation-level
interval is 63.5% to 68.4%.

This baseline does not replace the earlier DeepSeek 437/500 (87.4%)
LongMemEval-S result. That run used the same model under a different harness
and settings (among them a 64-token judge limit), so the two are not paired.

| Benchmark | Category | Accepted |
|---|---|---:|
| LongMemEval-S | `single-session-user` | 68/70 (97.1%) |
| LongMemEval-S | `single-session-assistant` | 53/56 (94.6%) |
| LongMemEval-S | `single-session-preference` | 27/30 (90.0%) |
| LongMemEval-S | `knowledge-update` | 69/78 (88.5%) |
| LongMemEval-S | `temporal-reasoning` | 113/133 (85.0%) |
| LongMemEval-S | `multi-session` | 93/133 (69.9%) |
| LoCoMo | `single-hop` | 644/841 (76.6%) |
| LoCoMo | `temporal` | 229/321 (71.3%) |
| LoCoMo | `open-domain` | 40/96 (41.7%) |
| LoCoMo | `multi-hop` | 101/282 (35.8%) |

The first LongMemEval-S baseline run stopped at 292 of 500 questions. On one
temporal-reasoning question the reader repeated itself until it reached the
8,192-token limit, and the registered retry policy treats a truncated answer as
final (#124). The arm was prepared again from the same commit, with identical
context text, and answered in full; that question then got an ordinary answer,
which the judge rejected. The result's run log counts both runs and the second
preparation. The first run's records are kept locally under
`data/ollama-answers-v1/discarded-attempts/`. On the 292 questions both runs
answered, 8 verdicts differed (6 accepted only in the first run, 2 only in the
second). That was an incidental observation; the
[run-to-run floor](#run-to-run-floor) below is the measurement.

The same smoke sample, answered twice on identical contexts, gave differently
worded answers for 18 of 20 questions and one flipped verdict, so seeded runs of
the hosted model do not repeat exactly (#118).

### Run-to-run floor

Two answer runs of the same defaults with the same inputs show how far the
paired test moves when nothing changes (#118). The repeat, `prme@46647825`,
was prepared from `main` at `46647825` and answered about two hours after the
first baseline, with the same model identity (manifest digest `e04da138`), the
same Ollama server version (0.34.3) and the same settings. No `src/` file
changed between the two commits, both preparations reproduce every saved
2026-09-23 context, and the code that sends and judges the calls is the same,
so `compare` reports the pair as a repeat with no warnings. The repeat used the
same passed calibration as the first baseline, all 4,080 of its reader and
judge calls returned HTTP 200 on the first attempt, and repeating the row checks
over the kept records reproduces both results exactly.

| Benchmark | First baseline (`prme`) | Repeat (`prme@46647825`) | Paired difference, 95% interval | Changed verdicts |
|---|---:|---:|---:|---:|
| LongMemEval-S | 423/500 (84.6%) | 434/500 (86.8%) | **+2.2 points, +0.4 to +4.2** | 23 of 500 (17 gained, 6 lost) |
| LoCoMo | 1,014/1,540 (65.8%) | 1,007/1,540 (65.4%) | -0.45 points, -1.43 to +0.52 (spanning -1.36 to +0.39 resampling conversations and -1.43 to +0.52 resampling questions) | 61 of 1,540 (27 gained, 34 lost) |

**The floor does not hold.** The LongMemEval-S interval excludes zero, so under
the default-change rule in `CLAUDE.md` the paired test was not trustworthy as it
stood. #129 revised it: each variant is now answered alongside its own fresh
run of the defaults, LoCoMo intervals resample conversations, and server
versions are recorded and checked. Under the old test, a variant paired
with the first LongMemEval-S baseline would have shown a 2.2-point gain whose
interval excludes zero without changing anything. The data cannot tell chance
(a 95% interval excludes zero about 1 time in 20 when nothing changes) from the
hosted model changing behind the same identity. Either way, every variant was
paired with one baseline run, and that run's own draw moved every comparison
made against it: on the 292 LongMemEval-S questions that three runs answered
(the stopped first run, the first baseline and the repeat), the judge accepted
243, 239 and 248. The [interleaved A/A check](#interleaved-aa-check) of the
revised test has since been answered, and it holds on both benchmarks.

By category, the LongMemEval-S repeat gained on `knowledge-update` (7 gained,
1 lost) and `multi-session` (8 gained, none lost) and lost on
`temporal-reasoning` (4 lost, none gained). No LoCoMo category's interval
excludes zero, resampling either questions or conversations. LoCoMo's conversation-level interval for the repeat is 63.5% to
67.5%.

To check the numbers, pair the two published results of each benchmark with
`compare` (the first baseline as `--before`, the repeat as `--after`). A test
pins the changed verdicts, the intervals and whether each interval excludes
zero. Since #129, `compare` also resamples LoCoMo's 10 conversations, which
gives -1.36 to +0.39 points for this pair, narrower than the question-level
interval; the LoCoMo `interval_95` spans both.

### Interleaved A/A check

The revised test (#129) is checked by answering the defaults against
themselves as one interleaved pair on each benchmark:

```sh
uv run python -m benchmarks.integrations.gpt54_baselines run-pair prme --benchmark longmemeval --provider ollama \
  --baseline prme@46647825
uv run python -m benchmarks.integrations.gpt54_baselines run-pair prme --benchmark locomo --provider ollama \
  --baseline prme@46647825
```

Both sides read the same prepared contexts, so they send identical requests,
and `compare` reports the pair as an interleaved repeat. The check holds when
the interval (for LoCoMo, the one spanning the conversation-level and
question-level intervals) includes zero on both benchmarks. If either excludes
zero, a variant's gain must also be larger than the largest absolute A/A
difference measured so far on that benchmark, the #118 repeat included (2.2
points on LongMemEval-S and 0.45 points on LoCoMo so far).

**It holds.** On 2026-09-24, one interleaved pair of `prme@46647825` against
itself completed on each benchmark under the DeepSeek track's amended failure
policy (#132), with the same model identity (manifest digest `e04da138`),
Ollama server version (0.34.3) and settings as the #118 runs, from a clean
tree at `5f89121c`. The two pairs were answered at the same time: LongMemEval-S
pair 5 took 14 minutes and LoCoMo pair 3 took 31.

| Benchmark | Before side | After side | Paired difference, 95% interval | Changed verdicts |
|---|---:|---:|---:|---:|
| LongMemEval-S, pair 5 ([before](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-vs-prme@46647825-longmemeval-pair-5-before-result.json), [after](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-vs-prme@46647825-longmemeval-pair-5-after-result.json)) | 432/500 (86.4%) | 431/500 (86.2%) | -0.2 points, -1.4 to +1.0 | 9 of 500 (4 gained, 5 lost) |
| LoCoMo, pair 3 ([before](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-vs-prme@46647825-locomo-pair-3-before-result.json), [after](benchmarks/results/research/2026-09-24/ollama-deepseek-v4.1-flash-cloud-prme@46647825-vs-prme@46647825-locomo-pair-3-after-result.json)) | 1,018/1,540 (66.1%) | 1,012/1,540 (65.7%) | -0.39 points, -1.30 to +0.52 (spanning -0.93 to +0.19 resampling conversations and -1.30 to +0.52 resampling questions) | 50 of 1,540 (22 gained, 28 lost) |

Both intervals include zero, so the default-change rule in `CLAUDE.md` allows
the revised test without the extra margin, for model identity `e04da138`,
Ollama server version 0.34.3, the current answer settings and the amended
failure policy. It does not carry over to anything else: when any of these
changes, a new A/A pair is needed on both benchmarks before a variant pair
counts. Both pairs are in the A/A record, and `compare` refuses a variant pair
answered under any other conditions (#137). No
category's interval excludes zero on either benchmark.

All 8,160 reader and judge calls of the two pairs returned HTTP 200 on their
first attempt. Neither side of either pair needed a reader retry, a judge retry
or a repaired verdict, so no question was scored `truncated` or
`verdict_unresolved`, and `compare` accepts both pairs with no warnings.
`gpt4_7abb270c`, which had run to the token limit in 4 of its 11 earlier reader
answers, ended normally on both sides. The two sides of an interleaved pair
disagreed less than the two sequential #118 runs did: 9 of 500 LongMemEval-S
verdicts (1.8%) against 23 (4.6%), and 50 of 1,540 LoCoMo verdicts (3.2%)
against 61 (4.0%). One pair per benchmark cannot show whether that comes from
the interleaving or from chance. The answers themselves still differed: in the
private answer records, the reader text was identical on only 9 of 500
LongMemEval-S questions and 195 of 1,540 LoCoMo questions.

To check the numbers, pair the two published sides of each pair with `compare`
(the `before` result as `--before`). Two tests pin both sides' counts, answer
settings, model identity, server version and failure-policy counts, the
given-up earlier pairs, the intervals (per category too) and the changed
verdicts.

Earlier that day, four LongMemEval-S pairs and two LoCoMo pairs of
`prme@46647825` against itself were started under the registered retry policy,
with the same model identity, server version and settings. Each one stopped at
a final failure, which the registered retry policy never asks again, so none
published a result:

| Pair | Answered by both sides | What stopped it |
|---|---:|---|
| LongMemEval-S 1 | 264 of 500 | Both sides' reader answers to `gpt4_7abb270c` ran to the 8,192-token limit |
| LongMemEval-S 2 | 267 of 500 | The after side's reader answer to `gpt4_7abb270c` ran to the limit |
| LongMemEval-S 3 | 261 of 500 | The before side's reader answer to `gpt4_7f6b06db` ran to the limit |
| LongMemEval-S 4 | 314 of 500 | The before side's verdict on `gpt4_385a5000` was `Yes` with a stray character in front |
| LoCoMo 1 | 61 of 1,540 | The before side's verdict on `conv-26-q0060` had stray characters before `<answer>no</answer>` |
| LoCoMo 2 | 62 of 1,540 | The after side's verdict on `conv-26-q0062` had a stray character and a multiple-choice layout |

By then `gpt4_7abb270c` had run to the limit in 4 of its 11 reader answers,
including the first #118 run (#124), and verdicts with stray characters
appeared 3 times in these 2,470 judge calls but never in the 4,080 of the two
published runs of the defaults. A pair has twice as many answers for one final
failure to stop, so under the registered policy a pair rarely completed.

The DeepSeek track's failure policy was amended for this (#132, see the
safeguards above). None of the six answers would stop a pair under the
amendment. The looping reader answers of LongMemEval-S pairs 1 to 3 would be
sent once more, and scored as `truncated` if the retry looped too. The verdict
with a stray `姫` in front (pair 4) would count as yes. The two LoCoMo
verdicts, in a tagged or multiple-choice layout, are not repaired, so the
judge would be called once more, and the question scored as
`verdict_unresolved` if that verdict failed too. The six pairs were given
up on record and their folders moved aside to
`data/ollama-answers-v1/discarded-attempts/ollama-deepseek-v4.1-flash-cloud/pairs/`,
with the same layout below it. Their run logs stayed in place, so the next
`run-pair` of each benchmark gave up the last of them (LongMemEval-S pair 4
and LoCoMo pair 2) as started under another failure policy and started the
fresh pairs above (LongMemEval-S pair 5, LoCoMo pair 3), whose results list
every earlier pair as given up (`earlier_pairs`). The amendment records the
digests of both pair run logs as they stood when it was registered. No pair
mixes the two policies.

The stopped attempts had already answered one question about the design. The
two sides of an A/A pair send identical requests back to back, which could have
made them agree more than two runs hours apart would. Their answers did not
repeat: on the 1,229 questions both sides answered, the reader text was
identical on 46 (3.7%) and the verdicts differed on 30 (2.4%), against 4.6% and
4.0% of verdicts for the two #118 runs, two hours apart.

## Earlier registered memory-utility comparison

The first registered held-out current-product answer comparison is complete.
On 149 deterministically scored LongMemEval-V2 web-small questions, PRME scored
80/149 (53.69%) versus 10/149 (6.71%) for the same local Qwen 9B reader without
memory. The paired difference was +46.98 points with a question-bootstrap 95%
interval of +37.58 to +55.70 points. The fail-closed comparator accepted every
row, official score, source/configuration binding, saved pack identity, and the
zero-memory contract. See the [complete report](benchmarks/results/research/2026-09-14/LONGMEMEVAL-V2-WEB-UNSEEN-DETERMINISTIC-V1.md).

That run used one reader, one saved memory artifact, no competing memory system,
and a mean 43,195 PRME memory-context tokens. It establishes memory utility for
the named cohort, not market leadership. Historical JSON files without a
complete report and artifact chain remain research artifacts and are not
directly comparable with the current harness. See the [August audit](memory_bank/AUDIT-2026-08-04.md)
for the older README table correction.

The September cleanup removes LoCoMo `observation` ingestion, copied answer
examples, and harness-only query reformulation/entity fan-out. Both LLM adapters
now use one public `retrieve()` call per question. Optional expansion belongs
to the product configuration. These are measurement corrections, not evidence
of improved answer accuracy.

## Contract for the next baseline

Complete full-history development runs from September 12, including the initial
regression and its intent correction, are preserved with per-question evidence
and paired comparisons in the [development report](benchmarks/results/evidence/2026-09-12/README.md).
These measure support retrieval, not end-to-end answer quality. The completed
[held-out report](benchmarks/results/evidence/2026-09-12/heldout/README.md) covers
381 questions with zero errors. Its paired comparisons do not establish an
advantage over vector/RRF baselines. The original-version run also completed:
2,048-token support recall changed from 85.12% to 85.27%, with a paired interval
including zero and a changed evaluation clock. A held-out improvement is not
established; cross-product answer-quality evaluations remain open.

| Layer | Report |
|---|---|
| Candidate retrieval | Evidence recall@k, MRR, nDCG@k |
| Context packing | Supporting-evidence recall at fixed token budgets; unsupported or conflicting context |
| End-to-end answering | Accuracy by category, abstention, repeated-run spread, infrastructure errors |

Record the source commit, dataset hash/split, selected question IDs, complete
engine configuration, embedding version, ingestion mode, generation and judge
models, prompts, token budget, scoring threshold, dependency versions, and elapsed
time. Tune on a development set; reserve held-out questions for final evaluation.

Report sample coverage explicitly. Adapters currently default to subsets;
LongMemEval excludes single-session-preference and LoCoMo excludes category 5.
Keyword containment is a diagnostic, not official benchmark accuracy. LLM
generation and judging can vary at temperature zero. Cached verdicts reduce
judge variation; they do not make generation deterministic.

JSON and terminal reports distinguish `total_queries`, `scored_queries`,
`error_count`, and `coverage` for the selected questions. Query failures retain
their question text and a null score, so retry selection includes them. The
legacy `judge_error` field now covers ingestion/retrieval exceptions as well as
generation/judging failures. Accuracy and category scores exclude these errors;
aggregate scores weight only measured questions. Always report coverage with
accuracy.

A whole-benchmark failure sets `benchmark_error` and `complete=false`; its
question count may be unknown. `complete` means that the selected evaluation
finished without errors, not that the full published dataset was evaluated.
The CLI exits nonzero if any benchmark in any run is incomplete, even when
other questions or later runs score well. It also retains the existing failure
exit for a nonempty benchmark with a zero score.

## Available commands

For host-specific retrieval latency, exact repeatability, and tenant isolation
through the public product path, run:

```bash
uv run python -m benchmarks.operational_eval \
  --sizes 10 50 200 \
  --latency-samples 100 \
  --determinism-samples 100 \
  --isolation-samples 10000 \
  --owners 5 \
  --output /tmp/prme-operational.json
```

The runner fixes the retrieval clock, uses exact vector search, disables
maintenance, records implementation/runtime/feature provenance, writes an
incomplete artifact before measurement, and exits nonzero on any repeatability
or owner-isolation failure. Sizes are eligible objects per owner; the default
five-owner corpus therefore holds 50, 250, and 1,000 total objects. Its latency
is specific to the measured host and its synthetic corpus; it is neither a
relevance score nor an answer-quality result.

For chronological conflict-memory replay with a local model, see the
[MemConflict adapter](benchmarks/integrations/MEMCONFLICT.md). It separates source
dialogues from evaluation labels, reports malformed-message omissions, and
compares PRME context, BM25 context and empty memory. Its raw answers are unjudged
diagnostics; it does not report official accuracy.

For 100K-to-10M conversation histories across ten memory abilities, see the
[BEAM integration](benchmarks/integrations/BEAM.md). It runs behind the pinned
official harness without exposing rubrics or answers to PRME and provides raw
source and full product-extraction profiles. The first validated scored raw
development run completed one 100K conversation and all ten abilities at 12/20
(60.0%), with a 0.49833 mean rubric score and no structural validation errors.
The [report](benchmarks/results/research/2026-09-15/BEAM-100K-RAW-SCORED.md)
records the complete claim boundary and the three rejected precursor trials.
This small cloud-model cohort identifies abstention, broad summarization,
temporal coverage, and contradiction coverage as gaps; it is not an official-scale
or matched cross-system result.

For controlled evidence retrieval without a generation or judge API, use:

```bash
uv sync --dev --extra evaluation
uv run python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_s_cleaned.json \
  --variant s --split dev --limit 5 \
  --budgets 2048 4096 8192 --output /tmp/prme-evidence-dev.json
```

Download the full-history S file from the
[official cleaned dataset](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/tree/main).
The existing download script fetches **oracle** histories, which contain only
supporting sessions. Explicitly label those runs `--variant oracle`; they are
diagnostics and do not test retrieval through full-history distractors.

This evaluator compares public PRME retrieval with BM25, vector search,
reciprocal rank fusion (constant 60), recency, and empty memory on identical
raw conversation turns. It records source commit and worktree fingerprint,
dataset hash, selected IDs, configuration, dependency versions, errors and
category coverage. Answers, evidence flags, and answer-bearing session IDs
never enter the memory pack. The raw-turn profile uses `store()` with NOTE
nodes and disables QA pairing and opportunistic maintenance. It does not
measure LLM extraction.

The deterministic `dev`/`test` partition is PRME's own 20/80 split by hashed
question ID, keeping abstention variants together. It is not an official split.
Keep the seed fixed, tune only on `dev`, and remove `--limit` for complete split
coverage. Run from a clean source commit with no concurrent source edits for
publication. Five questions are a smoke test, not a quality claim.

Metrics include evidence recall@k, MRR, nDCG, and evidence retained at actual
token budgets using a shared whole-turn packer and a named tiktoken encoding.
The packer includes source headers and separators in the count and never
truncates a turn. This isolates ranking; it does **not** evaluate the product
context formatter. Unlabeled/abstention questions have null evidence metrics;
answering and abstention accuracy need a separate judged evaluation. Timings
use sequential warm shared indexes and must not be presented as independent
cold-start performance. Interrupted runs retain a partial JSON report.

The evaluator passes the dataset's question date as `reference_time` by default,
so relative dates use the conversation's clock. `--clock wall` reproduces the
older behavior for a controlled ablation. Each response records the actual
clock; neither option applies a historical knowledge cutoff.

Use `--concurrency 4` to evaluate independent questions concurrently, each in
its own memory pack. Reports retain frozen question order and per-question
errors. Concurrency is recorded; timing under concurrent workloads is not a
standalone latency measurement and must not be compared as such.

Compare two completed runs on exactly the same selected questions:

```bash
uv run python -m benchmarks.compare_evidence /tmp/before.json /tmp/after.json \
  --output /tmp/comparison.json
```

The comparison rejects errors, missing/duplicate questions, different source
corpora or labels, and mismatched datasets/budgets. It reports paired changes,
wins/ties/losses, and a seeded query-bootstrap interval overall and by category.
Shared histories can make queries dependent, so these intervals are descriptive
and do not establish population-wide superiority. Development results still
need a frozen held-out confirmation. Timing is deliberately not compared.

To measure the real structured-extraction path, run the same source-only
selection once with raw notes and once with synchronous `ingest()`. The worker
writes the selected IDs, source/configuration provenance, and immutable Ollama
model digest before its first extraction call. Every extraction job must reach
its durable completion boundary; the report also records source materialization
coverage and node types. Use the oracle dataset for a bounded extraction study
and label it accordingly: it does not test retrieval through long-history
distractors.

```bash
uv run python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_oracle.json \
  --variant oracle --split dev --limit 12 --seed prme-ingestion-v1 \
  --ingestion-profile raw --output /tmp/prme-ingestion-raw.json
uv run python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_oracle.json \
  --variant oracle --split dev --limit 12 --seed prme-ingestion-v1 \
  --ingestion-profile extracted --extraction-provider ollama \
  --extraction-model prme-qwen3.5:9b-8k \
  --extraction-base-url http://127.0.0.1:11434/v1 \
  --output /tmp/prme-ingestion-extracted.json
uv run python -m benchmarks.compare_evidence \
  /tmp/prme-ingestion-raw.json /tmp/prme-ingestion-extracted.json \
  --allow-profile-change --output /tmp/prme-ingestion-comparison.json
```

This comparison expands retrieved node provenance back to neutral source-turn
IDs and then applies the shared source packer. It measures whether extraction
preserves retrievable source lineage. It does not prove that an extracted node's
rendered text contains the answer; that requires the separate generated-answer
and judge layer.

```bash
uv sync --dev
uv run pytest tests/ -q
uv run python -m benchmarks all --json /tmp/prme-synthetic.json
uv run python scripts/download_benchmarks.py --all
uv run python -m benchmarks all-real --json /tmp/prme-real-keyword.json
```

FastEmbed may download a model on first use. LLM diagnostics require explicitly
selected models and credentials for their providers:

```bash
uv run python -m benchmarks all-real --llm \
  --llm-provider "$ANSWER_PROVIDER" --llm-model "$ANSWER_MODEL" \
  --judge-provider "$JUDGE_PROVIDER" --judge-model "$JUDGE_MODEL" \
  --judge-cache /tmp/prme-verdicts.json --runs 3 \
  --json /tmp/prme-real-llm.json
```

This diagnostic command does not yet automate the full publication contract.
Use a fresh verdict cache after changing judge prompts. Never present a merge
of retry-only results and a baseline as a single run.

## Remaining publication work

Tracked in [#64](https://github.com/dwamianm/prism/issues/64):

- Extend the evidence evaluator's provenance contract to generated-answer runs.
- Run and publish a registered `ingest()` versus raw-store source-lineage study,
  then add generated-answer scoring over the extracted product contexts.
- Standardize context budgets and preparation across adapters. LoCoMo still uses
  supplied image captions, omits short turns, and builds knowledge profiles only
  in its keyword path. Document or ablate these before claiming a raw-conversation baseline.
- Require an explicit judge for publication and report category-level coverage.
- Freeze the evaluation split and run repeated evaluations with recorded configuration.

Scope isolation, temporal eligibility, provenance, and deterministic exact
retrieval remain correctness gates regardless of answer-score improvements.


## Precision and causal simulation diagnostics

The [PrecisionMemBench report](benchmarks/results/precision/2026-09-12/README.md)
preserves failures and discloses fixture-supplied behavior. Candidate recall alone
misses irrelevant-memory pollution. Score-floor sweeps on this small synthetic
suite are development calibration, not held-out validation or vendor comparisons.

Run all causal simulation checks, retaining failures, with:

```sh
uv run --no-sync python -m scripts.run_simulations --output /tmp/prme-simulations.json
```

Use repeatable `--scenario NAME` to narrow diagnosis. Every selected checkpoint
must pass for exit status zero; incomplete/empty runs and scenario errors fail.
The previous 80% success threshold has been removed. These keyword/ranking tests
are regression diagnostics and do not establish semantic answer accuracy.


[Local reader diagnostics](benchmarks/results/reader/2026-09-12/README.md)
preserve a failed header clarification and a controlled lifecycle-key ablation.
They explain a source-wording fix; they are not judged accuracy measurements.

[Recovery and developer workflow checks](benchmarks/results/recovery/2026-09-12/README.md)
cover abrupt exits, saved extraction reuse, installed-package behavior, and vector
startup cost. They preserve an incomplete local-model trial and distinguish
component timings from total startup latency. These reliability checks do not
establish semantic answer accuracy or competitive leadership.


[Extraction classification probes](benchmarks/results/extraction/2026-09-12/README.md)
exercise twelve authored source statements through live local-model ingestion.
They distinguish claim kind from epistemic status and check every materialized
claim. A prompt/schema clarification failed to establish a quality gain and was
reverted. The raw trials and corrected namesake evaluator are retained; these
are development diagnostics, not held-out answer accuracy or competitive scores.


[Evidence formatting fidelity](benchmarks/results/fidelity/2026-09-12/README.md)
records provenance preservation, removal of unsupported reasoning directives,
and identity-based context deduplication. Paired local-reader counterexamples
are authored development diagnostics, not independent accuracy measurements.

## Product packing replay

This replay reorders frozen candidates, so it cannot see ranking or
candidate-generation changes. Use the [offline evidence gate](#offline-evidence-gate-the-first-gate-for-retrieval-changes)
first for those.

Capture the actual public retrieval response while running a development
source-evidence evaluation, then replay those candidates offline:

```sh
python -m benchmarks.retrieval_eval \
  --dataset data/benchmarks/longmemeval/longmemeval_s_cleaned.json \
  --variant s --split dev --clock question --concurrency 4 \
  --capture-candidates /tmp/prme-dev-candidates --output /tmp/prme-dev.json
python -m benchmarks.diagnostics.product_packing \
  --input /tmp/prme-dev.json --snapshots /tmp/prme-dev-candidates \
  --output /tmp/prme-product-packing.json
```

Use a fresh snapshot directory for each run. Snapshots contain benchmark source
text and full candidate metadata; keep them separate from published summaries.
Their hashes are retained in the report. The benchmark supervisor records the
worker's actual process exit and rejects stale output as completion evidence.

The offline comparator first reproduces every saved product context and token
count exactly. It then compares the current density ordering with composite-score
ordering **within the multi-path tier only**, using all the same candidates,
priorities, representations, provenance labels, timestamps and budgets. It uses
the product's configured tokenizer and reserved overhead, which can differ from
the shared whole-turn evaluator's protocol. Full source text must be present in
a content-bearing JSON representation to receive support credit; reference-only
and key-value pointers receive none. Unlabeled questions remain unscored.

This exploratory command accepts only the development split. It reports paired
support-retention changes and complete-evidence retention, not generated-answer
accuracy. It does not change production packing defaults. Run it separately from
retrieval: its comparator substitution is intentionally confined to a sequential
offline process.

The [product packing development report](benchmarks/results/packing/2026-09-12/README.md)
retains the initial capture/replay check and clearly separates it from the broader
run. The [LongMemEval-V2 assessment](benchmarks/integrations/LONGMEMEVAL_V2.md)
documents the pinned official adapter, registered runner, and remaining
multimodal and comparative work. The [MemoryAgentBench integration](benchmarks/integrations/MEMORYAGENTBENCH.md)
adds outcome-free input registration and fail-closed result verification across
accurate retrieval, test-time learning, long-range understanding, and conflict
resolution. Its real-data ingress path passes, but it has no promoted task score
yet. V1 source recall is not a substitute for either benchmark's task coverage.

Use repeatable `--question-id ID` to reproduce a specific evidence-evaluation
failure within its original split. It cannot be combined with `--limit`; unknown
or out-of-split IDs are rejected. The selected IDs and full dataset hash remain
in the report. A targeted retry is a separate diagnostic, not a replacement for
an incomplete full run or evidence of complete-split coverage.

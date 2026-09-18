# LongMemEval-V2 web-small development cohort

Date: 2026-09-14  
Status: completed development diagnosis and targeted recovery; not a benchmark estimate

## Frozen cohort

The cohort selected the first released web question for each of the seven
question types after excluding the earlier `00aa905a` pipeline smoke. Selection
and input hashes were frozen before any answers were read. All seven questions
share the official 100-trajectory web-small haystack.

PRME used local FastEmbed `BAAI/bge-small-en-v1.5`, a 32,768-token internal
budget, a 100-result limit, and up to eight source screenshots. The reader was
Ollama 0.34.0 `qwen3.5:9b` at digest
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`,
with thinking enabled, a 20,000-token completion cap, and one request at a time.
No question, answer, category, evaluation function, or construction metadata
entered either memory pack.

## Initial result

The official harness generated all seven answers in 26m12s, then the configured
GPT-5.2 judge returned HTTP 429 `insufficient_quota` on its first abstention
check. The upstream harness writes reader outputs only after each score, so that
failure discarded the completed generations. A checkpointed retry used the
already frozen prompt rows and saved every answer before scoring.

| Measurement | Schema 1 |
|---|---:|
| Questions | 7 |
| Context truncations | 0 |
| PRME query latency | 0.246–0.751s |
| Reader prompt tokens | 310,489 |
| Reader completion tokens | 36,548 |
| Official deterministic score | 1/3 |
| Official LLM-judge score | unavailable (HTTP 429) |
| Local 35B substitute judge | 0/4 |
| Development-only combined diagnostic | 1/7 |

The local substitute used the official judge prompts with the already-installed
`qwen3.5:35b-a3b` model. It is not an official score and cannot be compared with
the leaderboard.

## Failure diagnosis and repair

The failed dynamic answer was a reader failure: its exact reference phrase was
already present in schema 1's packed context, but the 9B reader returned
`UNKNOWN`.

The failed procedure answer was a retrieval failure. The supporting states
ranked 148th or lower and none entered packed context. PRME's main lexical path
normalized BM25 correctly, but aggregation and entity-focused fan-out paths
passed raw, unbounded Tantivy scores into composite ranking. Commit `477c7fb`
normalizes every supplementary result set before merging. That moved the best
supporting state to rank 34, though its large raw accessibility tree still lost
at packing time.

Adapter schema 2 in `4d86b4f` adds a compact, ordered procedure trace beside the
complete raw state evidence. It repeats domain, environment, goal, and source
identity on every chunk, labels agent thoughts as unverified, and disables
generic adjacent-session expansion because the trace already supplies coherent
sequence context.

For the failed procedure query, the new trace ranked 7th, entered packed context,
and contained all three observed transitions needed after the stated start
state. For the dynamic query, the relevant trace ranked 1st and the exact banner
remained present.

## Targeted recovery

The same two failed deterministic questions were rerun through the unmodified
official harness with the same reader settings. This is a development
confirmation on seen failures, not a held-out score.

| Measurement | Schema 2 recovery |
|---|---:|
| Official deterministic score | 2/2 |
| Context truncations | 0 |
| Average PRME query latency | 0.610s |
| Dynamic context tokens | 43,556 |
| Procedure context tokens | 40,208 (44,037 before) |
| Reader prompt/completion tokens | 84,946 / 25,038 |
| Reader generation wall time | 14m37s |

The dynamic answer changed from wrong to correct, and the procedure answer
changed from `one` to the correct `three` after the ordered trace entered
context.

## Reader development profile and resumability

A separate Ollama development profile sent the documented OpenAI-compatible
`reasoning_effort: none` setting through PRME's checkpointed launcher. Merely
passing the upstream `--reader-disable-thinking` flag did not affect the local
model because the upstream special case checks for the exact
`Qwen/Qwen3.5-9B` model string. This profile is a development-speed result on the
same two seen failures, not a leaderboard reader result.

| Measurement | Thinking profile | Ollama no-reasoning profile |
|---|---:|---:|
| Official deterministic score | 2/2 | 2/2 |
| Reader prompt tokens | 84,946 | 84,950 |
| Reader completion tokens | 25,038 | 1,253 |
| Reader generation wall time | 14m37s | 1m48s |
| Completion reduction | — | 95.0% |
| Wall-time reduction | — | 87.7% |

The dynamic answer remained `Message is added to queue` and the procedure
answer remained `three`. Neither context was truncated. The checkpointed
launcher fsynced each response before scoring and retained the original prompt
rows. An exact replay made no reader calls, preserved both scores, and completed
prompt reuse plus scoring in 5.06 seconds. This closes the failure mode that
discarded the original seven completed generations after the external judge
returned HTTP 429.

## Pack cost and lifecycle

| Measurement | Schema 1 | Schema 2 | Delta |
|---|---:|---:|---:|
| Events/nodes | 9,477 | 9,713 | +236 |
| Saved pack size | 738,028 KiB | 748,032 KiB | +10,004 KiB |
| Indexing wall time | 18m54s | 19m39s | +45s |

All 100 schema-2 trajectories completed, covering 1,737 source states. Event and
node counts match. The first schema-2 save uncovered a temporary-directory
shutdown error after the saved copy was already complete; the adapter had
reopened an unused source client. The final implementation leaves it closed and
reopens lazily. A separate one-trajectory official save run then exited without
late vector writes or missing-directory errors.

The two short correct answers consumed 13,773 and 11,265 completion tokens under
the original thinking profile. The no-reasoning development profile establishes
a practical local iteration path, while a publishable LongMemEval-V2 result
still requires the prescribed reader settings, full web and enterprise sets,
matched baselines, complete failure accounting, and access to the released LLM
judge where specified.

Machine-readable protocol and results are in
`longmemeval-v2-web-development-v1-registration.json` and
`longmemeval-v2-web-development-v1-results.json` in this directory.

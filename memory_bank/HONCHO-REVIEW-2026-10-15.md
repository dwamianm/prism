# Honcho review (2026-10-15)

The owner asked what PRME can learn from Honcho, the open-source memory service
from Plastic Labs. This note covers Honcho v3.3.0 at commit
[`ed17042`](https://github.com/plastic-labs/honcho/tree/ed1704265e609364da6bf291bb3369bc9654f8dc)
(2026-10-14), its published run files in
[plastic-labs/honcho-benchmarks](https://github.com/plastic-labs/honcho-benchmarks/tree/20c497bff02ff8737268be6d91c197767dc7bac0/a1d689b)
(December 2025, from Honcho commit `a1d689b`) and two Plastic Labs blog posts.
PRME was at `main` `58caddb1` (package version 0.14.2). Following the
calibration rule in `docs/RESEARCH-AGENDA.md`, every Honcho number below is
quoted with its conditions and is not compared directly with PRME's.

The dated competitive landscape snapshot
(`docs/COMPETITIVE-LANDSCAPE-2026-09-17.md`) does not cover Honcho. Its revision
policy keeps that file unchanged, so this review is a separate note.

## What Honcho is

- **Service.** FastAPI over PostgreSQL with pgvector, plus Redis. Users and
  agents are both "peers", and peers share "sessions" of messages.
- **Deriver.** A background worker extracts facts ("conclusions") from batches
  of messages with one structured-output model call per batch. Facts are
  stored per (observer, observed) pair of peers.
- **Dialectic.** The chat endpoint is a tool-using agent that answers questions
  from memory. It searches facts and raw messages (semantic search, exact text
  search, date ranges and reasoning chains) for up to a configured number of
  rounds, at one of five reasoning levels.
- **Dreamer.** Scheduled offline passes write deductions, knowledge updates,
  contradictions and patterns that link to the facts they rest on. They also
  delete outdated facts and update a short "peer card".
- **Summaries.** Each session gets a short summary every 20 messages and a long
  one every 60.

## Published results

| Benchmark | Reported | Setup | Reading |
|---|---|---|---|
| LongMemEval-S | 452/500 (90.4%) | Official GPT-4o judge prompt. Claude Haiku 4.5 dialectic with up to 20 tool rounds and a mean of 11,622 context tokens per question (median 5.3% of the history). gemini-2.5-flash-lite extraction in 16,384-token batches. Dreaming off. No session history in the prompt. Haiku 4.5 with the full history scored 313/500. | A memory-retrieval result, with a different reader, judge and budget from the DeepSeek track (453/500 at 3,996 tokens). |
| LoCoMo | 89.9% (mean of per-conversation scores); 1,378/1,540 (89.5%) per question | GPT-4o-mini judge with a lenient prompt. Haiku 4.5 dialectic with the conversation in its prompt. 128-token extraction batches. Dreaming on. Haiku 4.5 with the full conversation and the same judge scored 1,280/1,540 (83.1%). | Not a memory-retrieval result. See below. |
| BEAM 100K, 500K, 1M, 10M | 0.630, 0.649, 0.631 and 0.406 mean rubric score | Their harness and rubric grading. Dreaming on at 100K only. The runner asks without a session, so no session history is in the prompt. Haiku 4.5 alone scored 0.533 at 100K. | PRME has one-conversation development runs only (`benchmarks/results/research/2026-09-15/`). |

Why the LoCoMo number does not measure memory retrieval:

- The run set the dialectic's `SESSION_HISTORY_MAX_TOKENS` to 32,768, and the
  runner passed the session with every question, so the agent's prompt held the
  session's messages up to that size. The largest LoCoMo conversation is 21,574
  tokens, so every question saw its whole conversation. 528 of the 1,540
  answers mention "session history".
- The judge must pass any answer that contains the gold answer's text. It may
  also pass an answer that departs from a gold answer it finds unsupported by
  the evidence.
- The category names are shifted. Under PRME's mapping, their "multi_hop" group
  is category 2, the temporal "When did..." questions. Per question, their
  results are multi-hop 237/282 (84.0%), temporal 283/321 (88.2%), open-domain
  74/96 (77.1%) and single-hop 784/841 (93.2%).

Plastic Labs also reported an extraction model: Qwen3-8B fine-tuned on about
10,000 curated extraction traces. With the same answer model and judge, their
LoCoMo result was 86.9% with it as the extractor, against 69.6% with the base
Qwen3-8B and 80.0% with Claude 4 Sonnet. Extraction quality alone moved that
score by 17 points.

## What PRME can use

1. **Batched extraction** (#254). Honcho extracts a batch of
   speaker-tagged turns per model call. PRME makes one call per turn, which puts
   a full LongMemEval-S `ingest()` build near 247,000 calls.
2. **Updates and contradictions found by meaning** (#222 and #223, with the
   evidence in
   [this comment](https://github.com/dwamianm/prism/issues/223#issuecomment-6195851665)).
   Honcho's largest BEAM gain is on contradiction resolution. PRME already has
   the `CONTESTED` state,
   `CONTRADICTS` edges and conflict annotations, but its detector matches
   predicates exactly.
3. **Paraphrased duplicate claims** (#255). Honcho merges near-identical
   facts at cosine distance 0.05. PRME's store rule is that similarity cannot
   establish equivalence, so this ticket only measures how much context such
   duplicates take, with a decision rule fixed in advance.

Smaller ideas, not filed:

- A candidate source for a date window. Event-time filters apply after the
  500 + 500 candidate cut, which matters only in stores much larger than
  LoCoMo or LongMemEval-S, such as BEAM 1M or 10M.
- Organizer triggers on new records and idle time (Honcho waits for 50 new
  facts and 60 idle minutes, at most once every 8 hours). These are worth
  adding only together with a job that calls a model.
- A capped identity card (at most 40 typed lines: identity, attribute,
  relationship and instruction) as an opt-in variant. PRME's earlier entity
  profiles lost 1.8 points on a 152-question run.

## What PRME should not copy

- Deleting outdated facts. PRME is append-only and supersedes instead.
- A model-driven tool loop inside retrieval. It would make retrieval
  nondeterministic. A host agent can already loop over PRME through the MCP
  server, which exposes time filters, scans and aggregation.
- The benchmark setup above: history in the answer prompt, a lenient judge and
  settings tuned per benchmark.
- Surprisal-based sampling for consolidation. Honcho ships it turned off.

## Sources

- [Honcho repository at `ed17042`](https://github.com/plastic-labs/honcho/tree/ed1704265e609364da6bf291bb3369bc9654f8dc)
- [Published run files](https://github.com/plastic-labs/honcho-benchmarks/tree/20c497bff02ff8737268be6d91c197767dc7bac0/a1d689b)
- [LoCoMo runner at the benchmark commit](https://github.com/plastic-labs/honcho/blob/a1d689bdbb0e3d7786710625e7130e3db37f6108/tests/bench/locomo.py)
- [LoCoMo judge prompt and category names at the benchmark commit](https://github.com/plastic-labs/honcho/blob/a1d689bdbb0e3d7786710625e7130e3db37f6108/tests/bench/locomo_common.py)
- [Dialectic session history at the benchmark commit](https://github.com/plastic-labs/honcho/blob/a1d689bdbb0e3d7786710625e7130e3db37f6108/src/dialectic/core.py)
- [Benchmarking Honcho](https://plasticlabs.ai/blog/research/Benchmarking-Honcho)
- [Introducing Neuromancer XR](https://plasticlabs.ai/blog/research/Introducing-Neuromancer-XR)
- [Honcho evals page](https://honcho.dev/evals/)

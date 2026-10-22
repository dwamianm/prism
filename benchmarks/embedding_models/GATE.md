# The embedding model gate

Part of dwamianm/prism#366. This file is committed before any model other than
the default has been run on the store pack, the padded stores or any tier, so the
numbers cannot move it. It changes no default: a default flips only on the release
steward's decision from the report.

## The hypothesis

The default embedding model, `BAAI/bge-small-en-v1.5` (384 dimensions), is the
weakest link for meaning search, and a larger registered local model raises the rank
of the memory that answers a question, across question types, including after the
store is crowded with look-alike memories. The graph experiments (#351) did not pass
their gates, so the model that makes the vectors is the next lever.

## Models and recipes

`bge-small` (the default, the reference), `minilm` (`all-MiniLM-L6-v2`), `bge-base`,
`bge-large`, `mxbai` (`mxbai-embed-large-v1`) and `nomic` (`nomic-embed-text-v1.5`),
all as the engine registers them. `models.py` has the table. A model is used under the
recipe its authors specify, because a model used without its prefix is not a fair
test of it:

- BGE and mxbai take an instruction before the question only. Each is measured as
  `plain` (what the engine does today) and `+q` (with the instruction), and the two
  share their stored vectors, so only the questions are embedded twice.
- nomic needs `search_query: ` before a question and `search_document: ` before a stored
  text. `nomic` is that recipe; `nomic-bare` (no prefixes) is measured once on the store
  pack to show what a missing prefix costs.
- minilm has no prefixes.

The engine's provider adds no prefix, so this experiment adds an option for it
(`embedding.query_prefix` and `embedding.document_prefix`, off by default), and its
model version names the recipe, so vectors made under one recipe are refused under
another. The scale harness applies the same prefixes in its embedding cache, so one padded
store serves both question recipes; the store pack runs through the engine's option
as well, and the two must give the same ranks.

## What is measured

1. **The store pack** (the fast cycle store stage: 75 memories, 17 questions) and the three
   spellings of the cities question (`japan-cities` and its two variants): the answer's rank,
   the top 3 results and the mean reciprocal rank (MRR), per recipe.
2. **The scale harness** of `benchmarks/graph_oracle` (PR #364), baseline retrieval only, no
   graph: the 204 frozen questions (53 original, 86 `blind-v1`, 65 `blind-v2`; nothing about
   the questions or their answers changes) over the 189-memory core, over the core padded
   with look-alike memories to 2,000 and to 10,000, and over the core padded with
   conversation turns to 2,000. Answers in the top 3, top 5, rank 1 and MRR, by question type
   and pooled. "Blind" below means the 151 questions of `blind-v1` and `blind-v2`.
3. **Cost** on one ONNX thread (the one-CPU case): embedding time per text (median and 95th
   percentile over short memory sentences), time to embed the 10,000 memory store, the
   PostgreSQL pgvector table and HNSW index size per 10,000 memories, the model download,
   the loaded model's memory, and what a `store()` call and a `retrieve()` call cost.
4. **The tiers**, for the best one or two recipes that are eligible (below): the defect probes,
   chat-v1, the corrections probe, the fast cycle, the offline evidence gate and the mid
   tier, each once against `bge-small`.

## The pass rule

A recipe is recommended for the release steward to consider only when every line holds.

1. **Blind top 3.** The pooled blind top 3 at 2,000 look-alike memories is at least 4 points
   above `bge-small`'s: at least 7 more of the 151 blind questions (6 is 3.97 points).
2. **No type loses.** At 2,000 look-alike memories and at 2,000 conversation memories, no
   question type (the seven types of `SCALE.md`, pooled over the 204 questions) has more
   than 1 fewer answer in the top 3 than `bge-small`.
3. **Probes.** Defect probes 7 of 7 answers in the top 3. chat-v1 answers in the top 3 not
   below 14 and in the top 5 not below 15.
4. **Fast cycle.** `fast_cycle run` exits 0 against the committed baseline. If a gated measure
   is worse, it is listed with its numbers and its cause. It is not a regression only when the
   cause is the embedding itself (a different set of similar memories, so a different entity or
   edge count) and no answer rank, answers in the top 3 or context, or stale measure got worse.
5. **Offline gate and mid tier.** No paired 95% interval of a measure where higher is better lies
   entirely below zero, in either comparison against `bge-small`.
6. **Speed.** The median embedding time per text on one thread is at most 8 times
   `bge-small`'s.

A recipe that fails any line is not recommended, whatever it gains elsewhere. The report
states the cost to a deployment with one CPU plainly (CPU per `store()`, the model in the
image, vector storage), so the owner can weigh it; cost is a criterion only through line 6.

## Which recipes get the tiers

The tiers run for the one or two recipes with the highest pooled blind top 3 at 2,000 look-alike
memories among those that meet line 6 (ties: higher blind MRR, then the cheaper one), counting
`bge-small+q` as a candidate because it costs nothing. If no eligible recipe meets line 1, the
verdict is already "no recommendation"; the tiers still run for the single best eligible recipe so
the report says what the model did elsewhere, with the saved-pack offline gate included only if
re-embedding its packs takes under three hours.

## Decisions made for the run, and why

- **Threads.** Everything is measured with `embedding.threads=1`, the one-CPU deployment's
  setting. Padded store runs go several at a time, so their embedding seconds are an upper
  bound; the per-text figures come from a separate run with nothing else of mine running.
- **Quantization.** The registered `bge-small` and `bge-base` files are 8-bit quantized, the
  others full precision. The registry is what a user gets, so that is what is measured, and the
  report says so beside the numbers.
- **Batching.** The provider embeds one text per ONNX call (a determinism choice of the
  engine). The cost figures use it, so they are the cost of the engine as it is, and a bulk
  rebuild could go faster in batches.
- **No stored vectors move.** A stored vector under another model or recipe is refused by the
  engine, so every store in the experiment is built fresh under its recipe.

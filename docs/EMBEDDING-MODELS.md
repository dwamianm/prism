# Switching embedding models on an existing store

A stored vector means something only under the model that made it, and models differ in dimension, so vectors from
two models cannot be compared. A store is tied to its model that way, and that part is inherent. What used to be
missing was a path to move a store to another model: the engine records the model name and version of every vector,
and its design rule says every derived index can be rebuilt, but only a DuckDB pack had a rebuild
(`prme rebuild`), and a PostgreSQL store, with its fixed `vector(N)` column, had none.

`enable_multi_model_embeddings` (off by default, `PRME_ENABLE_MULTI_MODEL_EMBEDDINGS=true`) adds that path. It ships
behind the option, changes no default and does not choose a model: whether any model should replace the default is
the question of the [embedding model experiment](../BENCHMARKS.md#embedding-models-does-a-larger-local-model-improve-meaning-search-dwamianmprism366). Part of dwamianm/prism#368.

With the option off nothing here is constructed, vectors are stored as they always were, and the configuration dump
is unchanged.

## What the option changes

Vectors are kept by node, model name and model version, with one index per model. One model is **active**
(`embedding.model_name`, as before): searches read only its index, and new writes go to it. The models in
`embedding.additional_models` are written on every store as well (dual write), so a new model can be built while the
store keeps serving the active one.

| | DuckDB pack | PostgreSQL |
|---|---|---|
| Where the vectors are | `vector_metadata` and `vector_payloads`, which already held a row per vector with its model, version and dimension, now with one row per node and model | a `node_embeddings` table: `node_id`, `model_name`, `model_version`, `dimension` and an untyped `vector`, keyed by the first three |
| The index of a model | one USearch file per model, named in the `embedding_models` table; the pack's `vectors.usearch` stays the first model's file | one partial HNSW index per model on a dimension cast, for example `((embedding::vector(768)) vector_cosine_ops) WHERE model_name = '...' AND model_version = '...'` |
| What happens to the existing vectors | the model they came from keeps `vectors.usearch`, with no copy and no re-embedding | they are copied once into `node_embeddings` (a SQL copy of the stored vector, never through a model); `nodes.embedding` is left as it is |
| Lifecycle | retiring a node removes its vector from every model's rows, and a model's file drops the stale key when it next opens | the rows of a retired node are deleted for every model |

**Why a partial index per model on PostgreSQL, and not a table per dimension.** pgvector indexes a `vector` column
without a declared dimension only through an expression index with a cast, and a partial index is chosen only by a
query that repeats its predicate. That gives each model its own graph: two models of the same dimension (bge-small and
MiniLM are both 384) never share an index, and a search for one never filters the other's rows after the index scan,
which would under-return. A table per dimension would keep the plain typed column and index, but it puts every model of
a dimension into one index, and it needs a new table for every new dimension. The partial index needs pgvector 0.5 or
later and indexes up to 2,000 dimensions; a larger model still stores and searches exactly. Exact search, the default
(`vector_exact_search`), never uses these indexes, and approximate search spells the model out as literals so the
planner can.

Single-file packs (`pack_path`) are not supported with the option in this release: their vector snapshot is one row of
the pack, and the option raises an error when combined with it.

## Moving a store to another model

1. **Turn the option on** for every process that serves the store (`enable_multi_model_embeddings=True`). The first
   open adopts the existing vectors as the first model's index (see below). Nothing is re-embedded.
2. **Name the new model as an additional model**: `embedding.additional_models=["BAAI/bge-base-en-v1.5"]`
   (`PRME_EMBEDDING__ADDITIONAL_MODELS`, a list or comma-separated). From then on every memory stored, ingested,
   re-derived or consolidated gets a vector under both models. A write to the additional model that fails is logged and
   does not fail the write; the next step fills it.
3. **Build the new model's vectors for the memories already stored**, with the local model, in the background:

   ```python
   preview = await engine.reembed("BAAI/bge-base-en-v1.5", dry_run=True)    # counts, writes nothing
   result = await engine.reembed("BAAI/bge-base-en-v1.5", batch_size=64, pause_ms=50)
   print(result.total, result.done, result.remaining, result.failed)
   ```

   or `prme reembed ./memory.duckdb --model BAAI/bge-base-en-v1.5 --pause-ms 50` (`--postgres` takes the store
   `PRME_DATABASE_URL` names), `POST /v1/reembed` and the `memory_reembed` MCP tool for one owner's memories.
   The job takes the active memories without a vector under the model in id order, embeds a batch, stores each vector
   with the model's name, version and dimension, and sleeps `pause_ms` between batches. It calls no model service, writes
   only vectors, and can run while the store is read and written. Its result is the progress record: `total`, `done`,
   `remaining`, `failed`, and a `cursor`. A stopped run needs nothing to resume: the vectors it stored are its progress, and
   the next call continues at the first memory still without one (`cursor` only skips memories that failed). A memory
   stored while the job runs is taken in the same sweep when its id is ahead of the cursor, and by a second sweep over
   the ids behind it before the call ends; with the model in `additional_models` the write stores its own vector, so
   there is nothing to catch up. A memory that was retired meanwhile, or that a concurrent write already gave a vector,
   is skipped, not duplicated. `await engine.embedding_status()` and `prme embeddings status` list every model, its
   vectors and whether it covers every memory.
4. **Switch** when `remaining` is 0: set `embedding.model_name` to the new model and move the old model into
   `additional_models` (the active model cannot also be listed there), then reopen. Searches read the new model's index.
   The old model's index stays, and new writes keep reaching it, which keeps going back possible. Nothing is deleted by
   the switch.
5. **Drop the old model** when you no longer need to go back: `await engine.drop_embeddings("BAAI/bge-small-en-v1.5")`
   or `prme embeddings drop --model BAAI/bge-small-en-v1.5`. The active model cannot be dropped.

Setting `embedding.model_name` first, without the steps above, is safe: see the next section. `reembed()` with no
model builds the active model's vectors, which is how a store opened that way is brought up to date.

## What the engine reports instead of mixing models

A store whose vectors were all made by another model than the configured one has no vector for the configured model.
With the option on, the engine does not mix them:

- A write (`store`, `ingest`, `ingest_fast`, `reingest`, ...) is refused before it stores anything, with an
  `EmbeddingModelMismatchError` (a subclass of `EmbeddingVersionMismatchError`) that names the stored and the
  configured model with their versions and dimensions. This holds for a model of the same dimension and for a new
  version of the same model name.
- A search returns `embedding_mismatch` and the failed `VECTOR` channel in its metadata, as before, and the lexical and
  graph results still come back.
- The retrieval metadata and the receipt carry `embedding_incomplete`:

  ```json
  {"model": "BAAI/bge-base-en-v1.5", "version": "fastembed-0.7.4", "dimension": 768,
   "nodes_total": 1200, "nodes_without_vector": 1200,
   "stored_models": [{"model": "BAAI/bge-small-en-v1.5", "version": "fastembed-0.7.4", "dimension": 384, "vectors": 1200}]}
  ```

A store whose active model has some vectors but not all (a job that was stopped, or a switch made too early) is not
refused: writes go through under the active model, a search reads the vectors that exist, and `embedding_incomplete`
says how many memories are missing. It is left out of metadata and receipts when nothing is missing, so responses of an
engine without the option keep their bytes. The count is made on every retrieval with the option on.

`reembed()` itself is never refused by this check: it is the way out.

## Adopting an existing store

- **DuckDB.** The pack already records each vector's model, version and dimension. Opening it with the option on registers
  those models, gives the one with most vectors the pack's `vectors.usearch`, and builds a file for any other from the
  vectors' durable payloads (a pack that silently mixed two models of one dimension under the old code is split this
  way). It re-embeds nothing, and it is idempotent.
- **PostgreSQL.** The first open with the option on creates `node_embeddings` and copies the vectors of `nodes.embedding`
  into it, each under the model and version its row recorded and the dimension read from the vector, in batches of
  1,000 that each commit. It is idempotent (`ON CONFLICT DO NOTHING`), resumable (a crash leaves the batches already
  copied, and the next open finishes), and safe on a store being read: other processes that open the store wait on an
  advisory lock until the copy is done, and a process with the option off keeps reading `nodes.embedding`, which is
  untouched. A marker row (`embedding_store_state`) says when the copy finished, so a later open costs one query. A vector
  whose row recorded no model has unknown provenance, which the engine never guesses from today's configuration; it is
  left out, counted in the log and in the marker, reported by `embedding_incomplete`, and filled by `reembed()`.
  The model's partial HNSW index is created when the model is first opened (`CREATE INDEX CONCURRENTLY` when it
  already has rows, and an index a failed build left invalid is rebuilt).

The old column is kept, so a store can go back to the option off. Going back has a limit: with the option off the engine
reads `nodes.embedding`, which holds only what it held when the option was first turned on, and the engine logs a warning
at open that says so. A DuckDB pack that holds more than one model's vectors refuses to open with the option off, with an
error that says to open it with the option or to drop the others first.

## `prme rebuild` and PostgreSQL

`prme rebuild` (`rebuild_indexes()`) rebuilds a DuckDB pack's derived indexes from its graph and stays DuckDB only. It
reads a local pack, so the command line cannot reach a PostgreSQL store with it, and the engine method raises an error on
PostgreSQL that names `reembed`. With the option on, `rebuild_indexes()` on DuckDB re-embeds every open model. On
PostgreSQL there are no derived index files, and what a rebuild is used for there, moving to another model, is `reembed`.

## What a hosted service must do

A service that runs the engine for its users switches the model once, for the whole store, without a gap:

1. Build the image with the files of both models (the registered local models are downloaded into the fastembed cache; the
   engine makes no network call to load them), and give the job its own CPU budget (`embedding.threads`, `pause_ms`) so
   it does not take the serving processes' CPU.
2. Roll out `enable_multi_model_embeddings` with the new model in `embedding.additional_models` to every process that
   writes. New memories are then written under both models.
3. Run the one-time job, store-wide (`reembed(model)` with no owner, or `prme reembed --postgres`), until `remaining` is
   0. It is resumable, so a restarted worker simply calls it again, and its result is the progress to show.
4. Switch: roll out `embedding.model_name` set to the new model. Keep the old model in `additional_models` for as long as a
   rollback is wanted, then `drop_embeddings` it.

## Limits

- Additional models and the job's own models are embedded as they are, without `embedding.query_prefix` and
  `embedding.document_prefix`, which belong to the active model. A model that needs prefixes can be made active (set the
  prefixes with `embedding.model_name`; the job then builds it with them), not additional.
- The job covers the whole store by default; `user_id` limits it to one owner, which is what the HTTP route and the MCP
  tool do. A store is complete when the whole-store job reports `remaining` 0.
- PostgreSQL keeps a copy of the first model's vectors in `nodes.embedding` until you remove them; the option does not
  delete it.
- A store served by a process with the option off while another has it on is not supported: the first writes only the
  old column.
- Single-file packs are not supported with the option.

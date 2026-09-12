# Custom embedding providers

Pass `embedding_provider=` to `MemoryEngine.create()`, `MemoryEngine.open()` or
`MemoryClient()`. A provider needs stable `model_name`, `model_version`, and
positive integer `dimension` attributes, plus async `embed(texts)` returning one
float vector per input in the same order. Its metadata determines the effective
embedding configuration and the new index dimension; the caller's config object
is not modified. Storage rejects incompatible existing vectors rather than
silently treating different model spaces as comparable.

Optionally implement async `embed_query(text)` returning one float vector.
Both local and PostgreSQL search use it; ingestion, recovery preparation and
rebuilds continue to use `embed(texts)` for documents. Providers without the
optional method retain the same encoding and cache behavior for documents and
queries. A query encoder failure remains a failed vector path; PRME does not
silently substitute document encoding.

The distinction matters for models with different query/document recipes.
[FastEmbed's retrieval guide](https://qdrant.github.io/fastembed/qdrant/Retrieval_with_FastEmbed/)
describes its separate methods. The
[BGE model card](https://huggingface.co/BAAI/bge-small-en-v1.5) describes query
instructions for short-query passage retrieval. This runnable example makes
that instruction explicit using the existing local model:

```python
from prme import CachedEmbeddingProvider, MemoryClient
from prme.storage.embedding import FastEmbedProvider


class BGEWithQueryInstruction(FastEmbedProvider):
    @property
    def model_version(self) -> str:
        # Identify the document AND query recipe. Changing either requires
        # a new version, even when the underlying weights stay the same.
        return super().model_version + ":bge-query-instruction-v1"

    async def embed_query(self, text: str) -> list[float]:
        instruction = "Represent this sentence for searching relevant passages: "
        return (await self.embed([instruction + text]))[0]


provider = CachedEmbeddingProvider(BGEWithQueryInstruction())
with MemoryClient("./custom_embedding_memory", embedding_provider=provider) as memory:
    memory.store("Aurora needs approval before deployment.", user_id="alice")
    response = memory.retrieve("Aurora deployment policy", user_id="alice")
    print(response.bundle.render())
```

This demonstrates the interface; it is not a benchmark-validated replacement for
the default encoding. Existing defaults are unchanged. `EmbeddingProvider` and
`QueryEmbeddingProvider` are importable protocols for annotations; inheritance
is optional. `CachedEmbeddingProvider` is an optional LRU wrapper. It separates
query/document entries when the underlying provider distinguishes them, preserves
legacy shared caching otherwise, and returns independently owned vector lists.
Provider metadata must remain stable for its lifetime; create a new provider and
cache when switching models or preprocessing. Query cache errors do not store
failed values.

Supply the provider on every reopen. Its Python implementation, credentials and
model resources are not serialized into the memory pack. Saved numerical vectors
and identity metadata remain portable. Reusing a saved effective config whose
provider is `custom` without supplying the object raises an explicit error.
When changing encoding versions, rebuild indexes with the intended provider;
changing dimensions can require backend/index migration and is not automatic.
CLI commands cannot recreate an arbitrary Python provider; use the Python APIs
for model-dependent operations on these packs.

The provider remains caller-owned: PRME neither closes its HTTP clients nor
unloads its models. For `MemoryClient`, async methods execute on the client's
worker loop. Create loop-bound resources lazily there, or use the async engine
when sharing an existing async client. Never reuse an HTTP client bound to a
different event loop. Built-in factory behavior is unchanged when no provider is
supplied.

Retrieval receipts report the actual provider/model/version/dimension and mark
the use of `embed_query`. A version must cover weights, normalization, truncation,
prefixes and both encoding tasks. Those are provider-reported identities, not
independent verification that a remote service still uses the same weights.

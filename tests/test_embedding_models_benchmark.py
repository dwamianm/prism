"""The embedding experiment's recipes and its cache apply prefixes as GATE.md says (dwamianm/prism#366)."""

from __future__ import annotations

import pytest

from benchmarks.embedding_models.models import MODELS, recipes
from benchmarks.embedding_models.scale import RecipeCache
from prme.config import EmbeddingConfig


def test_every_recipe_is_a_valid_embedding_configuration_for_its_model():
    found = recipes()

    assert {"bge-small", "bge-small+q", "nomic", "nomic-bare", "minilm"} <= set(found)
    for label, recipe in found.items():
        config = EmbeddingConfig(_env_file=None, **recipe.embedding())
        assert (config.model_name, config.dimension, config.threads) == (recipe.model.name, recipe.model.dimension, 1), label
    # The engine's own use is plain; nomic's only correct recipe has both prefixes.
    assert found["bge-small"].embedding().get("query_prefix") is None
    assert found["nomic"].embedding()["document_prefix"] == "search_document: "
    assert "document_prefix" not in found["nomic-bare"].embedding()
    assert MODELS["nomic"].query_prefix == "search_query: "


class _Provider:
    model_name, model_version, dimension = "m", "v", 2

    def __init__(self):
        self.seen: list[str] = []

    async def embed(self, texts):
        self.seen += texts
        return [[float(len(text)), 1.0] for text in texts]

    async def embed_query(self, text):
        self.seen.append("query:" + text)
        return [0.0, 1.0]


@pytest.mark.asyncio
async def test_recipe_cache_prefixes_documents_once_and_questions_per_pass():
    provider = _Provider()
    cache = RecipeCache(None, "D: ").wrap(provider)

    await cache.embed(["one", "two", "one"])
    await cache.embed(["one"])
    cache.query_prefix = "Q: "
    await cache.embed_query("one")
    await cache.embed_query("one")
    cache.query_prefix = ""
    await cache.embed_query("one")

    # A stored text is embedded once with its prefix; a question is cached per prefix and never gets the document prefix.
    assert provider.seen == ["D: one", "D: two", "Q: one", "one"]
    assert cache.computed == 2 and cache.hits == 2


@pytest.mark.asyncio
async def test_recipe_cache_can_leave_the_prefixes_to_the_engines_provider():
    provider = _Provider()
    cache = RecipeCache(None, None, delegate_query=True).wrap(provider)

    assert await cache.embed_query("one") == [0.0, 1.0]
    assert provider.seen == ["query:one"]

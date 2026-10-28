"""The embedding models and recipes of the experiment (dwamianm/prism#366, ``GATE.md``).

A *model* is a registered local FastEmbed model. A *recipe* is a model plus the prefixes put around its text: the
engine's own recipe adds nothing (``plain``), and the recipe the model's authors specify adds a query instruction
(BGE and mxbai: before the question only) or both task prefixes (nomic). The document side is fixed by the model, so
the plain and instructed recipes of one model share their stored vectors and differ only in how a question is embedded.
"""
from __future__ import annotations

from dataclasses import dataclass

INSTRUCTION = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    dimension: int
    query_prefix: str | None = None  # the author's recipe for a question
    document_prefix: str | None = None  # the author's recipe for a stored text
    note: str = ""


MODELS: dict[str, Model] = {model.key: model for model in (
    Model("bge-small", "BAAI/bge-small-en-v1.5", 384, INSTRUCTION, None, "the default; the registered file is the 8-bit quantized one"),
    Model("minilm", "sentence-transformers/all-MiniLM-L6-v2", 384, None, None, "no prefixes"),
    Model("bge-base", "BAAI/bge-base-en-v1.5", 768, INSTRUCTION, None, "the registered file is the 8-bit quantized one"),
    Model("bge-large", "BAAI/bge-large-en-v1.5", 1024, INSTRUCTION, None, "full precision"),
    Model("mxbai", "mixedbread-ai/mxbai-embed-large-v1", 1024, INSTRUCTION, None, "full precision"),
    Model("nomic", "nomic-ai/nomic-embed-text-v1.5", 768, "search_query: ", "search_document: ", "full precision; prefixes required"),
)}
DEFAULT = "bge-small"


@dataclass(frozen=True)
class Recipe:
    """A model with the question and stored-text prefixes it is used with."""

    label: str
    model: Model
    query_prefix: str | None
    document_prefix: str | None

    def embedding(self, threads: int | None = 1) -> dict:
        """The ``EmbeddingConfig`` fields that make this recipe through the engine's own option."""
        values: dict = {"model_name": self.model.name, "dimension": self.model.dimension, "threads": threads}
        if self.query_prefix:
            values["query_prefix"] = self.query_prefix
        if self.document_prefix:
            values["document_prefix"] = self.document_prefix
        return values


def recipes() -> dict[str, Recipe]:
    """Every recipe the report names: ``key`` is the engine's plain use, ``key+q`` the author's query instruction.

    nomic has one correct recipe (both prefixes), so ``nomic`` is that one and ``nomic-bare`` is the plain use, which
    the report measures once on the store pack to show what a missing prefix costs.
    """
    found: dict[str, Recipe] = {}
    for key, model in MODELS.items():
        if model.document_prefix:
            found[key] = Recipe(key, model, model.query_prefix, model.document_prefix)
            found[f"{key}-bare"] = Recipe(f"{key}-bare", model, None, None)
        else:
            found[key] = Recipe(key, model, None, None)
            if model.query_prefix:
                found[f"{key}+q"] = Recipe(f"{key}+q", model, model.query_prefix, None)
    return found


def document_group(recipe: Recipe) -> tuple[str, str | None]:
    """Recipes with the same group store the same document vectors."""
    return recipe.model.key, recipe.document_prefix

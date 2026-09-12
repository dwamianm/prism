"""Request descriptors for controlled full-retrieval comparisons.

Reported model names/versions and source-file hashes are observations, not a
guarantee that a remote provider's model weights are pinned or reproducible.
"""
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import platform

from pydantic import BaseModel, ConfigDict, JsonValue
from typing import Literal


class RetrievalExecution(BaseModel):
    """Extensible raw JSON maps preserve canonical bytes on future reads.

    Add observations inside the maps; adding default model fields would change
    the checksum of existing receipts and requires a new receipt schema.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    parameters: dict[str, JsonValue]
    features: dict[str, JsonValue]


def _name(value) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def _reported(value) -> JsonValue:
    return value if isinstance(value, (str, int, float, bool)) or value is None else None


def reranker_identity(reranker) -> dict[str, JsonValue]:
    return {"enabled": reranker is not None, "provider": _name(reranker),
            "model": _reported(getattr(reranker, "_model_name", None))}


def feature_identity(vector_index, lexical_index, reranker) -> dict[str, JsonValue]:
    """Capture non-secret implementation/model observations at pipeline creation."""
    provider = getattr(vector_index, "_provider", None)
    from prme.storage.embedding import has_query_encoder
    query_features = {"query_encoding": "embed_query"} if has_query_encoder(provider) else {}
    packages: dict[str, JsonValue] = {}
    for package in ("prme", "duckdb", "usearch", "tantivy", "fastembed", "onnxruntime", "sentence-transformers"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    sources: dict[str, JsonValue] = {}
    for name in ("scoring", "ranking_adjustments", "query_analysis", "scope", "candidates", "filtering",
                 "session_context", "reranker", "packing"):
        try:
            sources[name] = hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
        except OSError:
            sources[name] = None
    return {"python": platform.python_version(), "packages": packages, "source_files_sha256": sources,
            "embedding": {"provider": _name(provider), "model": _reported(getattr(provider, "model_name", None)),
                "version": _reported(getattr(provider, "model_version", None)),
                "dimension": _reported(getattr(provider, "dimension", None)), **query_features},
            "reranker": reranker_identity(reranker),
            "vector_backend": _name(vector_index),
            "vector_search": {"exact": _reported(getattr(vector_index, "_exact_search", None))},
            "lexical_backend": _name(lexical_index)}

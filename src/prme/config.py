"""PRME configuration management.

Type-safe configuration using pydantic-settings with support for
environment variables (PRME_ prefix), .env files, and direct arguments.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class EmbeddingConfig(BaseSettings):
    """Configuration for the embedding provider."""

    provider: str = Field(
        default="fastembed", description="Embedding provider name"
    )
    model_name: str = Field(
        default="BAAI/bge-small-en-v1.5", description="Embedding model identifier"
    )
    dimension: int = Field(
        default=384, description="Embedding vector dimension"
    )

    model_config = {
        "env_prefix": "PRME_EMBEDDING_",
    }


class PRMEConfig(BaseSettings):
    """Root configuration for PRME.

    Loads from environment variables with PRME_ prefix,
    .env files, and direct arguments. Nested configs use
    double-underscore delimiter (e.g., PRME_EMBEDDING__DIMENSION=384).
    """

    db_path: str = Field(
        default="./memory.duckdb", description="Path to DuckDB database file"
    )
    vector_path: str = Field(
        default="./vectors.usearch", description="Path to USearch vector index"
    )
    lexical_path: str = Field(
        default="./lexical_index", description="Path to tantivy lexical index directory"
    )
    embedding: EmbeddingConfig = Field(
        default_factory=EmbeddingConfig,
        description="Embedding provider configuration",
    )

    model_config = {
        "env_prefix": "PRME_",
        "env_nested_delimiter": "__",
    }

"""PRME configuration management.

Type-safe configuration using pydantic-settings with support for
environment variables (PRME_ prefix), .env files, and direct arguments.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class ExtractionConfig(BaseSettings):
    """Configuration for the LLM extraction provider.

    Controls which LLM provider and model is used for structured
    extraction of entities, facts, and relationships from conversation text.
    """

    provider: str = Field(
        default="openai",
        description="Extraction provider: 'openai', 'anthropic', or 'ollama'",
    )
    model: str = Field(
        default="gpt-4o-mini",
        description="Model identifier for the selected extraction provider",
    )
    max_retries: int = Field(
        default=3,
        description="Instructor retry count for schema validation failures",
    )
    timeout: float = Field(
        default=30.0,
        description="Seconds per extraction call",
    )

    model_config = {
        "env_prefix": "PRME_EXTRACTION_",
    }


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
    api_key: str | None = Field(
        default=None,
        description="API key for API-based embedding providers (e.g., OpenAI)",
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
    extraction: ExtractionConfig = Field(
        default_factory=ExtractionConfig,
        description="LLM extraction provider configuration",
    )
    write_queue_size: int = Field(
        default=1000,
        description="Max pending write queue items",
    )

    model_config = {
        "env_prefix": "PRME_",
        "env_nested_delimiter": "__",
    }

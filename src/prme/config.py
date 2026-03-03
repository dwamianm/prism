"""PRME configuration management.

Type-safe configuration using pydantic-settings with support for
environment variables (PRME_ prefix), .env files, and direct arguments.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from prme.retrieval.config import PackingConfig, ScoringWeights


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

    database_url: str | None = Field(
        default=None,
        description="PostgreSQL connection string. When set, all storage uses PostgreSQL.",
    )
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

    # Retrieval scoring and packing config (RFC-0005, RFC-0006)
    scoring: ScoringWeights = Field(
        default_factory=ScoringWeights,
        description="Scoring weights for composite retrieval formula (RFC-0005 S7)",
    )
    packing: PackingConfig = Field(
        default_factory=PackingConfig,
        description="Context packing configuration (RFC-0006)",
    )

    # [HYPOTHESIS] parameter overrides
    epistemic_weights: dict[str, float] = Field(
        default={
            "observed": 1.0,
            "asserted": 0.9,
            "inferred": 0.7,
            "hypothetical": 0.3,
            "conditional": 0.5,
            "deprecated": 0.1,
            "unverified": 0.5,
        },
        description=(
            "Epistemic multiplier values for composite score formula "
            "(RFC-0005 S7) [HYPOTHESIS]. Keys are EpistemicType values."
        ),
    )
    unverified_confidence_threshold: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description=(
            "Confidence threshold for UNVERIFIED nodes in DEFAULT retrieval "
            "mode (RFC-0003 S8) [HYPOTHESIS]"
        ),
    )
    confidence_overrides: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Override specific confidence matrix cells. "
            "Keys: 'epistemic_type:source_type' (e.g., 'observed:user_stated'). "
            "Values: float 0.0-1.0. Merges into default matrix at startup."
        ),
    )

    @model_validator(mode="after")
    def _validate_confidence_overrides(self) -> PRMEConfig:
        """Validate confidence_overrides key format and value range."""
        for key, value in self.confidence_overrides.items():
            if not (0.0 <= value <= 1.0):
                raise ValueError(
                    f"confidence_overrides['{key}'] = {value} not in [0.0, 1.0]"
                )
            parts = key.split(":")
            if len(parts) != 2:
                raise ValueError(
                    f"confidence_overrides key '{key}' must be "
                    f"'epistemic_type:source_type' format"
                )
        return self

    @property
    def backend(self) -> str:
        """Return 'postgres' when database_url is set, else 'duckdb'."""
        return "postgres" if self.database_url else "duckdb"

    model_config = {
        "env_prefix": "PRME_",
        "env_nested_delimiter": "__",
    }

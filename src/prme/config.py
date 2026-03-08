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


class OrganizerConfig(BaseSettings):
    """Configuration for self-organizing memory (RFC-0015)."""

    opportunistic_enabled: bool = Field(
        default=True,
        description="Enable opportunistic maintenance during retrieve/ingest",
    )
    opportunistic_cooldown: int = Field(
        default=3600,
        description="Minimum seconds between opportunistic maintenance passes",
    )
    opportunistic_budget_ms: int = Field(
        default=200,
        description="Max milliseconds per opportunistic maintenance pass",
    )
    opportunistic_batch_size: int = Field(
        default=50,
        description="Max nodes processed per job per opportunistic pass",
    )
    default_organize_budget_ms: int = Field(
        default=5000,
        description="Default time budget for explicit organize() calls",
    )
    promotion_age_days: float = Field(
        default=7.0,
        description="Min age in days before auto-promotion [HYPOTHESIS]",
    )
    promotion_evidence_count: int = Field(
        default=1,
        description="Min evidence refs for auto-promotion. Default 1 matches "
        "store() which creates exactly 1 evidence ref per node. "
        "Higher values require evidence accumulation via reinforcement. "
        "[HYPOTHESIS]",
    )
    archive_salience_threshold: float = Field(
        default=0.10,
        ge=0.0, le=1.0,
        description="Salience below this + low confidence triggers DEPRECATED (RFC-0007 S6)",
    )
    archive_confidence_threshold: float = Field(
        default=0.40,
        ge=0.0, le=1.0,
        description="Confidence threshold paired with archive_salience_threshold",
    )
    force_archive_salience_threshold: float = Field(
        default=0.05,
        ge=0.0, le=1.0,
        description="Salience below this triggers ARCHIVED regardless of confidence",
    )
    deprecate_confidence_threshold: float = Field(
        default=0.15,
        ge=0.0, le=1.0,
        description="Confidence below this triggers DEPRECATED at any salience",
    )
    summarization_daily_min_events: int = Field(
        default=5,
        description="Minimum events per day to trigger a daily summary",
    )
    summarization_weekly_min_summaries: int = Field(
        default=3,
        description="Minimum daily summaries needed for weekly rollup",
    )
    summarization_monthly_min_summaries: int = Field(
        default=2,
        description="Minimum weekly summaries needed for monthly rollup",
    )
    summarization_max_items_per_summary: int = Field(
        default=10,
        description="Maximum items to include per summary",
    )

    model_config = {
        "env_prefix": "PRME_ORGANIZER_",
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
    organizer: OrganizerConfig = Field(
        default_factory=OrganizerConfig,
        description="Self-organizing memory configuration (RFC-0015)",
    )
    enable_store_supersedence: bool = Field(
        default=False,
        description=(
            "When True, store() checks new content for contradiction signals "
            "(migration/replacement language) and marks matching existing nodes "
            "as superseded. Requires vector index to find similar nodes. "
            "Default False for backward compatibility."
        ),
    )
    reinforce_similarity_threshold: float | None = Field(
        default=None,
        description=(
            "When set, store() checks for existing similar nodes via vector search. "
            "If similarity >= threshold, reinforces the existing node instead of "
            "creating a duplicate. None (default) disables this behavior."
        ),
    )
    enable_surprise_gating: bool = Field(
        default=False,
        description=(
            "When True, store() computes a novelty score for incoming content "
            "by comparing against existing memory via vector similarity. "
            "Novel content gets boosted salience; redundant content gets "
            "reduced salience. Default False for backward compatibility."
        ),
    )
    novelty_high_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description=(
            "Novelty score above which content receives a salience boost. "
            "[HYPOTHESIS]"
        ),
    )
    novelty_low_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description=(
            "Novelty score below which content receives a salience penalty. "
            "[HYPOTHESIS]"
        ),
    )
    novelty_salience_boost: float = Field(
        default=0.15,
        ge=0.0,
        le=0.5,
        description=(
            "Salience boost applied to highly novel content. [HYPOTHESIS]"
        ),
    )
    novelty_salience_penalty: float = Field(
        default=0.10,
        ge=0.0,
        le=0.5,
        description=(
            "Salience penalty applied to redundant content. [HYPOTHESIS]"
        ),
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

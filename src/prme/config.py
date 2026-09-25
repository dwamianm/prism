"""PRME configuration management.

Type-safe configuration using pydantic-settings with support for
environment variables (PRME_ prefix), .env files, and direct arguments.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings

from prme.retrieval.config import (
    DEFAULT_PACKING_SETTINGS,
    DEFAULT_SCORING_SETTINGS,
    RANK_FUSION_ONLY_SETTINGS,
    PackingConfig,
    ScoringWeights,
    default_packing_config,
    default_scoring_weights,
)
from prme.retrieval.temporal_relations import TemporalRelationConfig


class _ProjectSettings(BaseSettings):
    """Read project settings without exporting secrets into process globals."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "forbid"}

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        def scoped_dotenv():
            # Shared .env files contain provider secrets and other applications'
            # settings. Ignore those here while retaining constructor typo errors.
            return {key: value for key, value in dotenv_settings().items() if key in settings_cls.model_fields}

        return init_settings, env_settings, scoped_dotenv, file_secret_settings


class ExtractionConfig(_ProjectSettings):
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
        description="Instructor retry count for response-schema validation failures",
    )
    timeout: float = Field(
        default=30.0,
        gt=0,
        allow_inf_nan=False,
        description="Seconds per extraction call, and per opt-in query reformulation call",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        allow_inf_nan=False,
        description=(
            "Sampling temperature for structured extraction. Zero favors "
            "repeatable schema-constrained output; increase only after "
            "benchmarking extraction quality for the selected provider."
        ),
    )
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = Field(
        default=None,
        description=(
            "Optional provider reasoning level for structured extraction. "
            "Ollama defaults to 'none' so reasoning traces cannot consume the "
            "schema response budget; other providers keep their own default."
        ),
    )
    lease_seconds: float = Field(
        default=300.0, gt=0, allow_inf_nan=False,
        description="Durable extraction lease; active workers renew it and commit rechecks ownership",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Optional extraction credential; overrides provider environment variables. "
            "Opt-in query reformulation uses it too"
        ),
    )
    base_url: str | None = Field(
        default=None,
        description=(
            "Optional extraction endpoint; overrides provider environment variables. "
            "Opt-in query reformulation uses it too"
        ),
    )

    @model_validator(mode="after")
    def default_ollama_reasoning_effort(self) -> ExtractionConfig:
        if self.provider.strip().casefold() == "ollama" and self.reasoning_effort is None:
            self.reasoning_effort = "none"
        return self

    model_config = {
        "env_prefix": "PRME_EXTRACTION_",
    }


class EmbeddingConfig(_ProjectSettings):
    """Configuration for the embedding provider."""

    _DEFAULT_FASTEMBED_MODEL: ClassVar[str] = "BAAI/bge-small-en-v1.5"
    _OPENAI_MODELS: ClassVar[dict[str, int]] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    provider: str = Field(
        default="fastembed", description="Embedding provider name"
    )
    model_name: str = Field(
        default=_DEFAULT_FASTEMBED_MODEL, description="Embedding model identifier"
    )
    dimension: int = Field(
        default=384,
        ge=1,
        description=(
            "Embedding vector dimension. When omitted, registered FastEmbed and "
            "OpenAI model dimensions are inferred without loading model weights."
        ),
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="API key for API-based embedding providers (e.g., OpenAI)",
    )

    @model_validator(mode="before")
    @classmethod
    def infer_builtin_model_dimension(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        provider = str(data.get("provider", "fastembed")).strip().casefold()
        if provider == "openai" and "model_name" not in data:
            data["model_name"] = "text-embedding-3-small"
        model_name = str(data.get("model_name", cls._DEFAULT_FASTEMBED_MODEL))
        if data.get("dimension") is not None:
            return data
        if provider == "openai":
            dimension = cls._OPENAI_MODELS.get(model_name)
            if dimension is None:
                raise ValueError(
                    f"OpenAI embedding model {model_name!r} has no known dimension; "
                    "set dimension explicitly"
                )
        elif provider == "fastembed":
            from prme.storage.embedding import fastembed_model_dimension

            dimension = fastembed_model_dimension(model_name)
        else:
            raise ValueError(
                f"Embedding provider {provider!r} cannot infer a dimension; "
                "set dimension explicitly"
            )
        data["dimension"] = dimension
        return data

    model_config = {
        "env_prefix": "PRME_EMBEDDING_",
    }


def _validate_user_keys(keys: dict[str, SecretStr]) -> None:
    values = [key.get_secret_value() for key in keys.values()]
    if any(not user.strip() for user in keys) or any(not key.strip() for key in values):
        raise ValueError("User IDs and bearer credentials must not be empty")
    if len(values) != len(set(values)):
        raise ValueError("Each user must have a distinct bearer credential")


class APIConfig(_ProjectSettings):
    """Configuration for the HTTP API server (security hardening, issue #34)."""

    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "API key for bearer-token authentication. When set, every "
            "endpoint except /v1/health requires "
            "'Authorization: Bearer <api_key>'. None (default) disables "
            "authentication — only safe for single-user localhost use."
        ),
    )
    user_keys: dict[str, SecretStr] = Field(
        default_factory=dict,
        description="User IDs mapped to distinct bearer credentials. Binds every HTTP operation "
                    "to the authenticated user. Cannot be combined with the legacy global api_key.",
    )

    @model_validator(mode="after")
    def validate_user_keys(self):
        if self.user_keys and self.api_key is not None:
            raise ValueError("Configure user_keys or the global api_key, not both")
        _validate_user_keys(self.user_keys)
        return self

    @property
    def auth_enabled(self) -> bool:
        """Whether requests need a bearer token (a global key or per-user keys).

        The HTTP router and the server's bind check both use this, so they
        cannot disagree about when the API is unauthenticated.
        """
        return self.api_key is not None or bool(self.user_keys)

    cors_origins: list[str] = Field(
        default_factory=list,
        description=(
            "Allowed CORS origins. Empty (default) disables CORS entirely. "
            "Pin explicit origins (e.g. ['http://localhost:3000']) for "
            "browser clients. Avoid '*' — credentials are never allowed "
            "with a wildcard origin."
        ),
    )
    cors_allow_credentials: bool = Field(
        default=False,
        description=(
            "Allow credentialed CORS requests. Only honored when "
            "cors_origins pins explicit origins (no '*')."
        ),
    )

    model_config = {
        "env_prefix": "PRME_API_",
    }


class MCPConfig(_ProjectSettings):
    """MCP identity: a fixed owner for stdio, or distinct credentials for HTTP."""

    user_id: str | None = None
    user_keys: dict[str, SecretStr] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_identity(self):
        if self.user_id is not None and not self.user_id.strip():
            raise ValueError("MCP user_id must not be empty")
        if self.user_id is not None and self.user_keys:
            raise ValueError("Use a fixed MCP user_id or per-user HTTP keys, not both")
        # Same distinct, nonempty credential contract as the HTTP API.
        _validate_user_keys(self.user_keys)
        return self

    model_config = {"env_prefix": "PRME_MCP_"}


class OrganizerConfig(_ProjectSettings):
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
    dedup_similarity_threshold: float = Field(
        default=0.92,
        ge=0.0,
        le=1.0,
        description="Minimum vector similarity for duplicate detection (issue #11) [HYPOTHESIS]",
    )
    alias_similarity_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Minimum vector similarity for alias detection (issue #11) [HYPOTHESIS]",
    )

    # Consolidation pipeline (issue #22)
    consolidation_min_cluster_size: int = Field(
        default=3,
        ge=2,
        description="Minimum memories in a cluster for consolidation [HYPOTHESIS]",
    )
    consolidation_similarity_threshold: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Vector cosine similarity threshold for clustering [HYPOTHESIS]",
    )
    consolidation_preserve_recent_days: int = Field(
        default=7,
        ge=0,
        description="Don't archive memories newer than this many days [HYPOTHESIS]",
    )
    consolidation_min_confidence_preserve: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Don't archive memories with confidence >= this value [HYPOTHESIS]",
    )
    default_ttl_days: dict[str, int | None] = Field(
        default={
            "entity": None,
            "fact": None,
            "event": 365,
            "decision": 180,
            "preference": None,
            "task": 90,
            "summary": 365,
            "note": 90,
        },
        description=(
            "Default TTL in days per NodeType. None means no expiry. "
            "Applied at store() time when ttl_days is not explicitly set. "
            "(RFC-0007 S9, issue #12)"
        ),
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


class PRMEConfig(_ProjectSettings):
    """Root configuration for PRME.

    Loads from environment variables with PRME_ prefix,
    .env files, and direct arguments. Nested configs use
    double-underscore delimiter (e.g., PRME_EMBEDDING__DIMENSION=384).
    """

    database_url: SecretStr | None = Field(
        default=None,
        description="PostgreSQL connection string. When set, all storage uses PostgreSQL.",
    )
    namespace_id: UUID | None = Field(
        default=None,
        description="Expected physical local-pack identity. New packs bind this ID; existing packs "
                    "must already match. PostgreSQL workspaces set their bound identity internally. "
                    "This does not filter shared tables or grant access.",
    )
    db_path: str = Field(
        default="./memory.duckdb", description="Path to DuckDB database file"
    )
    duckdb_threads: int | None = Field(
        default=None, ge=1,
        description="Optional DuckDB worker-thread count per open database. None preserves "
                    "DuckDB's default. Concurrent engines for the same file must use the same "
                    "setting. Does not limit embedding/index threads or apply to PostgreSQL.",
    )

    @field_validator("duckdb_threads", mode="before")
    @classmethod
    def validate_duckdb_threads(cls, value):
        if isinstance(value, bool):
            raise ValueError("duckdb_threads must be a positive integer or None")
        return value

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
        default_factory=default_scoring_weights,
        description=(
            "Retrieval scoring (RFC-0005 S7). Defaults to reciprocal rank fusion "
            "(fusion='rrf', rrf_k=60) with a 0.25 current-state recency boost "
            "(rrf_recency_boost) and an event-time tie-break (rrf_tie_break); "
            "set fusion='weighted' for the weighted composite formula. "
            "PRME_SCORING__* environment variables and .env entries keep those "
            "defaults for the settings they leave out, and a weighted fusion "
            "they set drops the rank fusion ones. Scoring passed in code, as a "
            "ScoringWeights object or a dict, follows ScoringWeights, whose "
            "fusion defaults to 'weighted', so a saved configuration reloads as "
            "it was written."
        ),
    )
    packing: PackingConfig = Field(
        default_factory=default_packing_config,
        description=(
            "Context packing configuration (RFC-0006). Defaults to the reader "
            "context format, score ordering and a 0.6 rank fusion session "
            "decay. PRME_PACKING__* environment variables and .env entries keep "
            "those unless they set them. Packing passed in code, as a "
            "PackingConfig object or a dict, follows PackingConfig's own "
            "defaults (auditable, balanced, no rank fusion session decay), so a "
            "saved configuration reloads as it was written."
        ),
    )
    temporal_relation: TemporalRelationConfig = Field(
        default_factory=TemporalRelationConfig,
        description=(
            "Opt-in evidence-bound temporal arithmetic. Disabled by default; "
            "when enabled, the configured resolver and independent gate may "
            "make network calls during temporal retrieval."
        ),
    )
    organizer: OrganizerConfig = Field(
        default_factory=OrganizerConfig,
        description="Self-organizing memory configuration (RFC-0015)",
    )
    mcp: MCPConfig = Field(default_factory=MCPConfig)

    api: APIConfig = Field(
        default_factory=APIConfig,
        description="HTTP API server configuration (auth, CORS)",
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
    enable_qa_pairing: bool = Field(
        default=False,
        description=(
            "[HYPOTHESIS] When True, store() creates best-effort merged Q-A "
            "nodes for consecutive session turns from different roles or "
            "speakers. The "
            "heuristic is in-process, is not part of durable store recovery, "
            "and has not improved a registered quality benchmark. Default "
            "False; requires session_id."
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

    # Neural reranking (cross-encoder)
    enable_reranker: bool = Field(
        default=False,
        description="Enable cross-encoder neural reranking after composite scoring. Requires: pip install prme[reranker]",
    )
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="HuggingFace cross-encoder model for neural reranking.",
    )
    reranker_top_k: int = Field(
        default=100,
        description="Number of top candidates to rerank (controls latency vs quality).",
    )

    reranker_policy: Literal["legacy", "score_envelope", "anchored_score_envelope"] = Field(
        default="legacy",
        description=(
            "Experimental neural ordering policy, used only with enable_reranker. "
            "Envelope policies retain the original scored prefix's score scale; "
            "anchored_score_envelope first prioritizes its original ordinary "
            "multi-path anchor. These are rankings, not relevance probabilities."
        ),
    )

    # Multi-query reformulation (issue #43)
    enable_query_reformulation: bool = Field(
        default=False,
        description=(
            "When True, retrieve() asks an LLM for alternative phrasings of "
            "the query, runs each as an additional retrieval pass, and merges "
            "the results (deduplicated by node id) with the original query's "
            "results. Improves recall on tangential/keyword-mismatched facts "
            "at the cost of one LLM call plus N extra retrievals per query. "
            "Uses the extraction provider, model, endpoint, credential and "
            "timeout. Default False; with this off, "
            "retrieve() makes no LLM calls (RFC-0005 S3)."
        ),
    )
    query_reformulation_merge_policy: Literal["new_only", "max_signals"] = Field(
        default="new_only",
        description=(
            "Experimental alternate-query merge policy, used only with "
            "enable_query_reformulation. max_signals unions distinct backend paths "
            "and takes maximum component signals for identical source snapshots. "
            "Alternate-query backend failures abort that retrieval under this policy."
        ),
    )
    query_reformulation_count: int = Field(
        default=2,
        ge=1,
        le=5,
        description=(
            "Number of alternative queries to generate per retrieve() call "
            "when enable_query_reformulation is True. [HYPOTHESIS]"
        ),
    )

    # Temporal parsing cost control (issue #61)
    temporal_languages: list[str] = Field(
        default=["en"],
        description=(
            "Languages used to parse date expressions out of query text. "
            "dateparser costs ~70ms per query when it has to detect the "
            "language itself versus ~0.3ms with a single language pinned, and "
            "that detection was 85% of retrieval latency. Set to an empty list "
            "to restore dateparser's auto-detection for multilingual queries "
            "at that cost."
        ),
    )

    # Dual-stream ingestion (issue #25)
    materialization_queue_size: int = Field(
        default=500,
        ge=1,
        description=(
            "Maximum number of durable pending events read in one materialization "
            "batch. Additional work remains on disk; acknowledged events are never dropped."
        ),
    )
    materialization_budget_ms: int = Field(
        default=100,
        description=(
            "Time budget (ms) per materialization drain pass during "
            "retrieve() or organize(). Controls how much deferred graph "
            "work is processed per call."
        ),
    )

    # Index write-path batching (issue #39)
    vector_save_interval: int = Field(
        default=64,
        ge=1,
        description=(
            "Number of vector inserts between full USearch index saves to "
            "disk. The index is rewritten in full on each save, so saving on "
            "every insert makes ingestion O(N^2) in write volume. Any "
            "pending writes are always flushed on close(). Set to 1 to save "
            "after every insert (legacy behavior)."
        ),
    )
    vector_exact_search: bool = Field(
        default=True,
        description=(
            "When True, vector search uses brute-force exact cosine nearest "
            "neighbors instead of the approximate HNSW graph traversal. HNSW "
            "construction is order- and thread-dependent, so two rebuilds "
            "from the same event log can return different neighbor sets, "
            "violating the 'identical log + config -> identical retrieval' "
            "determinism claim. Exact search is order-independent and makes "
            "retrieval reproducible. Applies to both backends. PostgreSQL "
            "materializes eligible rows before ordering, so filtered ANN "
            "candidates cannot hide matching memories. Exact cost grows with "
            "eligible corpus size; benchmark your workload. Set False to allow "
            "approximate search, which may miss eligible neighbors."
        ),
    )
    lexical_commit_interval: int = Field(
        default=64,
        ge=1,
        description=(
            "Number of lexical documents added between tantivy commits. "
            "Per-document commits cause segment explosion and merge churn. "
            "Documents are buffered in a long-lived writer and committed "
            "once this many are pending (or lexical_commit_max_delay_s "
            "elapses). Pending documents are always committed on close(). "
            "Set to 1 to commit after every document (legacy behavior). "
            "Note: buffered documents are not searchable until committed."
        ),
    )
    lexical_commit_max_delay_s: float = Field(
        default=2.0,
        ge=0.0,
        description=(
            "Maximum seconds a lexical document may sit uncommitted in the "
            "writer buffer before a commit is forced, bounding search "
            "staleness when fewer than lexical_commit_interval documents "
            "arrive. The bound is evaluated when the next document is "
            "indexed (there is no background timer); search() always "
            "flushes pending writes first, so a query never misses its own "
            "writes regardless of this value. 0 disables the time bound "
            "(commit only on interval/search/close)."
        ),
    )
    max_concurrent_extractions: int = Field(
        default=8,
        ge=1,
        description=(
            "Maximum number of concurrent background LLM extraction tasks "
            "in the ingestion pipeline. Bounds memory and outbound LLM "
            "request fan-out when ingesting large batches (ingest_batch of "
            "N would otherwise launch N concurrent extractions)."
        ),
    )

    # Per-namespace weight profiles (issue #24)
    namespace_weights: dict[str, ScoringWeights] = Field(
        default_factory=dict,
        description=(
            "Optional per-namespace scoring weight overrides. Keys are "
            "namespace strings (e.g., 'project-x', 'personal'). When a "
            "retrieve() call includes a namespace, the corresponding "
            "weights are used instead of the global scoring weights."
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

    # Encryption at rest (RFC-0014 S10, issue #14)
    encryption_enabled: bool = Field(
        default=False,
        description=(
            "Master toggle for encryption at rest. When True and "
            "encryption_key is set, memory pack files are encrypted "
            "on close() and decrypted on create(). Default False "
            "for backward compatibility."
        ),
    )
    encryption_key: SecretStr | None = Field(
        default=None,
        description=(
            "Encryption passphrase (or raw key) for at-rest encryption. "
            "Used with PBKDF2-HMAC-SHA256 to derive a Fernet key. Prefix "
            "with 'raw_key:' to supply a raw Fernet key explicitly or "
            "'passphrase:' to force derivation; an unprefixed value is "
            "auto-detected for backward compatibility. None disables "
            "encryption regardless of encryption_enabled."
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
        url = self.database_url
        return "postgres" if url and url.get_secret_value() else "duckdb"

    model_config = {
        "env_prefix": "PRME_",
        "env_nested_delimiter": "__",
    }

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        init, *others = super().settings_customise_sources(
            settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings,
        )

        def environment() -> dict[str, Any]:
            # ScoringWeights and PackingConfig read a missing setting as their
            # own historical default, because saved receipts and configurations
            # rely on that. Environment, .env and secret settings that name
            # only some scoring or packing values keep the product defaults
            # instead; values passed in code are left as written. The sources
            # are merged first, highest priority last, so a default never
            # replaces a value that a lower-priority source sets.
            merged: dict[str, Any] = {}
            for source in reversed(others):
                merged = _merged(merged, source())
            return with_product_retrieval_defaults(merged)

        return (init, environment)


def _merged(low: dict[str, Any], high: dict[str, Any]) -> dict[str, Any]:
    """``low`` updated with ``high``, merging nested dicts key by key."""
    result = dict(low)
    for key, value in high.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merged(result[key], value)
        else:
            result[key] = value
    return result


def with_product_retrieval_defaults(values: dict[str, Any]) -> dict[str, Any]:
    """Fill scoring and packing settings given as dicts with the product defaults they leave out.

    PRMEConfig applies this to its environment and .env settings. Callers that
    build a configuration from partial settings, such as the evidence gate's
    ``--set`` overrides, apply it so their settings change only what they name.
    Scoring that sets a fusion other than rank fusion takes none of the rank
    fusion defaults, which weighted scoring would drop with a warning.
    """
    filled = dict(values)
    scoring = filled.get("scoring")
    if isinstance(scoring, dict):
        defaults = DEFAULT_SCORING_SETTINGS
        if scoring.get("fusion", defaults["fusion"]) != "rrf":
            defaults = {key: value for key, value in defaults.items() if key not in RANK_FUSION_ONLY_SETTINGS}
        filled["scoring"] = {**defaults, **scoring}
    packing = filled.get("packing")
    if isinstance(packing, dict):
        filled["packing"] = {**DEFAULT_PACKING_SETTINGS, **packing}
    return filled

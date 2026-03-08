"""Integration tests for surprise-gated storage (issue #20).

Validates that the novelty scoring pipeline integrates correctly with
MemoryEngine.store(), including salience adjustment, metadata enrichment,
non-fatal failure handling, and config parameter propagation. Each test
creates a fresh temporary database via MemoryEngine.create().
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from prme.config import PRMEConfig
from prme.ingestion.novelty import NoveltyResult, NoveltyScorer
from prme.storage.engine import MemoryEngine
from prme.types import NodeType, Scope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_surprise_") as d:
        yield d


@pytest.fixture
def base_config(tmp_dir):
    """Config with surprise gating DISABLED (default)."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
    )


@pytest.fixture
def surprise_config(tmp_dir):
    """Config with surprise gating ENABLED."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
        enable_surprise_gating=True,
    )


async def create_engine(config: PRMEConfig) -> MemoryEngine:
    """Create a MemoryEngine from config."""
    return await MemoryEngine.create(config)


async def _store_and_get_node(
    engine: MemoryEngine,
    content: str,
    user_id: str = "test-user",
):
    """Store content and return the created MemoryNode."""
    await engine.store(
        content,
        user_id=user_id,
        node_type=NodeType.FACT,
        scope=Scope.PERSONAL,
    )
    nodes = await engine.query_nodes(user_id=user_id, limit=100)
    for n in nodes:
        if n.content == content:
            return n
    raise RuntimeError(f"Could not find stored node with content {content!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSurpriseGatingDisabled:
    """Verify default behavior: surprise gating off = no overhead."""

    @pytest.mark.asyncio
    async def test_store_does_not_compute_novelty_when_disabled(self, base_config):
        """When enable_surprise_gating=False, store() should not call _compute_novelty."""
        engine = await create_engine(base_config)
        try:
            with patch.object(
                engine, "_compute_novelty", new_callable=AsyncMock
            ) as mock_novelty:
                await engine.store(
                    "Python is a programming language",
                    user_id="test-user",
                    node_type=NodeType.FACT,
                    scope=Scope.PERSONAL,
                )
                mock_novelty.assert_not_called()
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_default_salience_when_disabled(self, base_config):
        """Nodes should have default salience_base=0.5 when gating is off."""
        engine = await create_engine(base_config)
        try:
            node = await _store_and_get_node(
                engine, "Default salience test content"
            )
            assert node.salience_base == pytest.approx(0.5, abs=1e-6)
            assert node.metadata is None or "novelty_score" not in (
                node.metadata or {}
            )
        finally:
            await engine.close()


class TestNovelContentBoosted:
    """Verify that novel content receives salience boost."""

    @pytest.mark.asyncio
    async def test_novel_content_gets_boosted_salience(self, surprise_config):
        """First content stored in empty store should be fully novel (score=1.0)
        and receive boosted salience."""
        engine = await create_engine(surprise_config)
        try:
            node = await _store_and_get_node(
                engine, "Quantum computing uses qubits for parallel computation"
            )
            # In an empty store, no neighbors exist so novelty = 1.0
            assert node.metadata is not None
            assert node.metadata["novelty_score"] == pytest.approx(1.0, abs=0.01)
            assert node.metadata["max_similarity"] == pytest.approx(0.0, abs=0.01)
            # salience_base should be boosted: 0.5 + 0.15 = 0.65
            assert node.salience_base == pytest.approx(0.65, abs=0.01)
        finally:
            await engine.close()


class TestRedundantContentPenalized:
    """Verify that redundant content receives salience penalty."""

    @pytest.mark.asyncio
    async def test_redundant_content_gets_reduced_salience(self, surprise_config):
        """Storing very similar content twice should penalize the second entry."""
        engine = await create_engine(surprise_config)
        try:
            # Store first content (novel)
            first_node = await _store_and_get_node(
                engine, "I prefer using Python for data science projects"
            )
            assert first_node.salience_base == pytest.approx(0.65, abs=0.01)

            # Store nearly identical content
            second_node = await _store_and_get_node(
                engine, "I prefer using Python for data science projects"
            )
            # Second should have low novelty and reduced salience
            assert second_node.metadata is not None
            novelty = second_node.metadata["novelty_score"]
            # High similarity to existing content -> low novelty
            assert novelty < 0.5
            # Salience should be penalized (below default 0.5)
            assert second_node.salience_base < 0.5
        finally:
            await engine.close()


class TestNoveltyMetadataStored:
    """Verify novelty metadata is persisted on nodes."""

    @pytest.mark.asyncio
    async def test_metadata_contains_novelty_fields(self, surprise_config):
        """Stored nodes should carry novelty_score and max_similarity in metadata."""
        engine = await create_engine(surprise_config)
        try:
            node = await _store_and_get_node(
                engine, "Functional programming emphasizes immutability"
            )
            assert node.metadata is not None
            assert "novelty_score" in node.metadata
            assert "max_similarity" in node.metadata
            assert isinstance(node.metadata["novelty_score"], float)
            assert isinstance(node.metadata["max_similarity"], float)
            assert 0.0 <= node.metadata["novelty_score"] <= 1.0
            assert 0.0 <= node.metadata["max_similarity"] <= 1.0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_nearest_node_id_in_metadata_when_neighbors_exist(
        self, surprise_config
    ):
        """When neighbors exist, nearest_node_id should be in metadata."""
        engine = await create_engine(surprise_config)
        try:
            # First store creates an entry
            await engine.store(
                "Machine learning models need training data",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            # Second store should find the first as a neighbor
            second_node = await _store_and_get_node(
                engine, "Machine learning models need training data"
            )
            assert second_node.metadata is not None
            assert "nearest_node_id" in second_node.metadata
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_existing_metadata_preserved_with_novelty(self, surprise_config):
        """User-provided metadata should be merged with novelty metadata."""
        engine = await create_engine(surprise_config)
        try:
            await engine.store(
                "Rust has zero-cost abstractions",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
                metadata={"source": "documentation", "version": 2},
            )
            nodes = await engine.query_nodes(user_id="test-user", limit=100)
            node = next(
                n for n in nodes if n.content == "Rust has zero-cost abstractions"
            )
            assert node.metadata is not None
            # User metadata preserved
            assert node.metadata["source"] == "documentation"
            assert node.metadata["version"] == 2
            # Novelty metadata added
            assert "novelty_score" in node.metadata
            assert "max_similarity" in node.metadata
        finally:
            await engine.close()


class TestFailureIsNonFatal:
    """Verify that novelty scoring failures do not break store()."""

    @pytest.mark.asyncio
    async def test_store_succeeds_when_novelty_scoring_raises(self, surprise_config):
        """If _compute_novelty raises, store() should still succeed with default salience."""
        engine = await create_engine(surprise_config)
        try:
            # Patch _compute_novelty to raise an exception
            with patch.object(
                engine,
                "_compute_novelty",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Vector index exploded"),
            ):
                event_id = await engine.store(
                    "Content stored despite novelty failure",
                    user_id="test-user",
                    node_type=NodeType.FACT,
                    scope=Scope.PERSONAL,
                )
                assert event_id is not None

                # Node should exist with default salience
                node = await _store_and_get_node(
                    engine, "Content stored despite novelty failure"
                )
                assert node.salience_base == pytest.approx(0.5, abs=1e-6)
                # No novelty metadata should be present
                assert node.metadata is None or "novelty_score" not in (
                    node.metadata or {}
                )
        finally:
            await engine.close()


class TestConfigParametersRespected:
    """Verify that custom config values are propagated to NoveltyScorer."""

    @pytest.mark.asyncio
    async def test_custom_boost_value_applied(self, tmp_dir):
        """Custom novelty_salience_boost should be used for adjustment."""
        lexical_path = Path(tmp_dir) / "lexical_custom"
        lexical_path.mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory_custom.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors_custom.usearch"),
            lexical_path=str(lexical_path),
            enable_surprise_gating=True,
            novelty_salience_boost=0.25,
        )
        engine = await create_engine(config)
        try:
            # Novel content (empty store) should get custom boost
            node = await _store_and_get_node(
                engine, "Completely unique content for custom boost test"
            )
            # salience_base = 0.5 + 0.25 = 0.75
            assert node.salience_base == pytest.approx(0.75, abs=0.01)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_custom_penalty_value_applied(self, tmp_dir):
        """Custom novelty_salience_penalty should be used for redundant content."""
        lexical_path = Path(tmp_dir) / "lexical_penalty"
        lexical_path.mkdir(exist_ok=True)
        config = PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory_penalty.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors_penalty.usearch"),
            lexical_path=str(lexical_path),
            enable_surprise_gating=True,
            novelty_salience_penalty=0.20,
        )
        engine = await create_engine(config)
        try:
            # Store first
            await engine.store(
                "Exact duplicate penalty test content",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            # Store exact duplicate
            second_node = await _store_and_get_node(
                engine, "Exact duplicate penalty test content"
            )
            # If novelty is low enough (redundant), salience_base = 0.5 - 0.20 = 0.30
            if second_node.metadata and second_node.metadata.get(
                "novelty_score", 1.0
            ) <= config.novelty_low_threshold:
                assert second_node.salience_base == pytest.approx(0.30, abs=0.01)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_config_defaults_match_expected_values(self):
        """Default config values for surprise gating should match spec."""
        config = PRMEConfig()
        assert config.enable_surprise_gating is False
        assert config.novelty_high_threshold == pytest.approx(0.7)
        assert config.novelty_low_threshold == pytest.approx(0.3)
        assert config.novelty_salience_boost == pytest.approx(0.15)
        assert config.novelty_salience_penalty == pytest.approx(0.10)


class TestNoveltyScorer:
    """Unit tests for the NoveltyScorer class itself."""

    @pytest.mark.asyncio
    async def test_empty_index_returns_max_novelty(self):
        """When vector search returns no results, novelty should be 1.0."""
        mock_index = AsyncMock()
        mock_index.search = AsyncMock(return_value=[])

        scorer = NoveltyScorer()
        result = await scorer.score("test content", "user-1", mock_index)

        assert result.novelty_score == pytest.approx(1.0)
        assert result.max_similarity == pytest.approx(0.0)
        assert result.nearest_node_id is None
        assert result.salience_adjustment > 0  # boost

    @pytest.mark.asyncio
    async def test_high_similarity_returns_low_novelty(self):
        """When max similarity is high, novelty should be low."""
        mock_index = AsyncMock()
        mock_index.search = AsyncMock(
            return_value=[
                {"node_id": "node-1", "score": 0.95},
                {"node_id": "node-2", "score": 0.80},
            ]
        )

        scorer = NoveltyScorer()
        result = await scorer.score("test content", "user-1", mock_index)

        assert result.novelty_score == pytest.approx(0.05, abs=0.01)
        assert result.max_similarity == pytest.approx(0.95)
        assert result.nearest_node_id == "node-1"
        assert result.salience_adjustment < 0  # penalty

    @pytest.mark.asyncio
    async def test_moderate_similarity_no_adjustment(self):
        """Novelty in the middle zone should have zero adjustment."""
        mock_index = AsyncMock()
        mock_index.search = AsyncMock(
            return_value=[{"node_id": "node-1", "score": 0.5}]
        )

        scorer = NoveltyScorer()
        result = await scorer.score("test content", "user-1", mock_index)

        assert result.novelty_score == pytest.approx(0.5)
        assert result.salience_adjustment == pytest.approx(0.0)

    @pytest.mark.asyncio
    async def test_exclude_node_id_filtered(self):
        """The exclude_node_id should be filtered from results."""
        mock_index = AsyncMock()
        mock_index.search = AsyncMock(
            return_value=[
                {"node_id": "self-node", "score": 1.0},
                {"node_id": "other-node", "score": 0.3},
            ]
        )

        scorer = NoveltyScorer()
        result = await scorer.score(
            "test content",
            "user-1",
            mock_index,
            exclude_node_id="self-node",
        )

        # With self-node excluded, only "other-node" at 0.3 remains
        assert result.max_similarity == pytest.approx(0.3)
        assert result.nearest_node_id == "other-node"

    @pytest.mark.asyncio
    async def test_custom_thresholds_respected(self):
        """Custom threshold values should shift boost/penalty boundaries."""
        mock_index = AsyncMock()
        # Similarity = 0.4 -> novelty = 0.6
        mock_index.search = AsyncMock(
            return_value=[{"node_id": "node-1", "score": 0.4}]
        )

        # With default thresholds (high=0.7, low=0.3), novelty=0.6 is neutral
        scorer_default = NoveltyScorer()
        result_default = await scorer_default.score(
            "test content", "user-1", mock_index
        )
        assert result_default.salience_adjustment == pytest.approx(0.0)

        # With lowered high_threshold=0.5, novelty=0.6 should get a boost
        scorer_custom = NoveltyScorer(high_novelty_threshold=0.5)
        result_custom = await scorer_custom.score(
            "test content", "user-1", mock_index
        )
        assert result_custom.salience_adjustment > 0

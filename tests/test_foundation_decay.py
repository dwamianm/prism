"""Tests for RFC-0015 foundation layer: DecayProfile, OrganizerConfig, MemoryNode decay fields,
DuckDB schema migration, and round-trip persistence.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import duckdb
import pytest
from pydantic import ValidationError

from prme.config import OrganizerConfig, PRMEConfig
from prme.models.nodes import MemoryNode
from prme.storage.duckpgq_graph import DuckPGQGraphStore
from prme.storage.schema import initialize_database
from prme.types import (
    DECAY_LAMBDAS,
    DEFAULT_DECAY_PROFILE_MAPPING,
    DecayProfile,
    EpistemicType,
    NodeType,
    Scope,
)


# ---------------------------------------------------------------------------
# DecayProfile enum
# ---------------------------------------------------------------------------


class TestDecayProfile:
    """Tests for the DecayProfile enum and related mappings."""

    def test_enum_values(self):
        """DecayProfile has all five expected values."""
        assert DecayProfile.PERMANENT == "permanent"
        assert DecayProfile.SLOW == "slow"
        assert DecayProfile.MEDIUM == "medium"
        assert DecayProfile.FAST == "fast"
        assert DecayProfile.RAPID == "rapid"
        assert len(DecayProfile) == 5

    def test_decay_lambdas_mapping(self):
        """DECAY_LAMBDAS maps every DecayProfile to a float coefficient."""
        for profile in DecayProfile:
            assert profile in DECAY_LAMBDAS
            assert isinstance(DECAY_LAMBDAS[profile], float)

    def test_decay_lambdas_values(self):
        """DECAY_LAMBDAS has the expected coefficients."""
        assert DECAY_LAMBDAS[DecayProfile.PERMANENT] == 0.000
        assert DECAY_LAMBDAS[DecayProfile.SLOW] == 0.005
        assert DECAY_LAMBDAS[DecayProfile.MEDIUM] == 0.020
        assert DECAY_LAMBDAS[DecayProfile.FAST] == 0.070
        assert DECAY_LAMBDAS[DecayProfile.RAPID] == 0.200

    def test_permanent_has_zero_decay(self):
        """PERMANENT profile has lambda=0 (no decay)."""
        assert DECAY_LAMBDAS[DecayProfile.PERMANENT] == 0.0

    def test_lambdas_monotonically_increasing(self):
        """Lambda values increase from PERMANENT through RAPID."""
        order = [
            DecayProfile.PERMANENT,
            DecayProfile.SLOW,
            DecayProfile.MEDIUM,
            DecayProfile.FAST,
            DecayProfile.RAPID,
        ]
        for i in range(len(order) - 1):
            assert DECAY_LAMBDAS[order[i]] < DECAY_LAMBDAS[order[i + 1]]


class TestDefaultDecayProfileMapping:
    """Tests for DEFAULT_DECAY_PROFILE_MAPPING."""

    def test_covers_all_epistemic_types(self):
        """DEFAULT_DECAY_PROFILE_MAPPING covers every EpistemicType member."""
        for et in EpistemicType:
            assert et in DEFAULT_DECAY_PROFILE_MAPPING, (
                f"Missing mapping for EpistemicType.{et.name}"
            )

    def test_mapping_values_are_decay_profiles(self):
        """Every mapping value is a valid DecayProfile."""
        for et, dp in DEFAULT_DECAY_PROFILE_MAPPING.items():
            assert isinstance(dp, DecayProfile)

    def test_observed_maps_to_slow(self):
        """OBSERVED epistemic type maps to SLOW decay profile."""
        assert DEFAULT_DECAY_PROFILE_MAPPING[EpistemicType.OBSERVED] == DecayProfile.SLOW

    def test_deprecated_maps_to_permanent(self):
        """DEPRECATED epistemic type maps to PERMANENT (never decays further)."""
        assert DEFAULT_DECAY_PROFILE_MAPPING[EpistemicType.DEPRECATED] == DecayProfile.PERMANENT

    def test_hypothetical_maps_to_rapid(self):
        """HYPOTHETICAL epistemic type maps to RAPID decay."""
        assert DEFAULT_DECAY_PROFILE_MAPPING[EpistemicType.HYPOTHETICAL] == DecayProfile.RAPID


# ---------------------------------------------------------------------------
# OrganizerConfig
# ---------------------------------------------------------------------------


class TestOrganizerConfig:
    """Tests for OrganizerConfig defaults and validation."""

    def test_default_construction(self):
        """OrganizerConfig() with all defaults constructs successfully."""
        c = OrganizerConfig()
        assert isinstance(c, OrganizerConfig)

    def test_default_values(self):
        """OrganizerConfig has expected default values."""
        c = OrganizerConfig()
        assert c.opportunistic_enabled is True
        assert c.opportunistic_cooldown == 3600
        assert c.opportunistic_budget_ms == 200
        assert c.opportunistic_batch_size == 50
        assert c.default_organize_budget_ms == 5000
        assert c.promotion_age_days == 7.0
        assert c.promotion_evidence_count == 1
        assert c.archive_salience_threshold == 0.10
        assert c.archive_confidence_threshold == 0.40
        assert c.force_archive_salience_threshold == 0.05
        assert c.deprecate_confidence_threshold == 0.15

    def test_custom_values(self):
        """OrganizerConfig accepts custom values."""
        c = OrganizerConfig(
            opportunistic_enabled=False,
            opportunistic_cooldown=1800,
            promotion_age_days=14.0,
            archive_salience_threshold=0.20,
        )
        assert c.opportunistic_enabled is False
        assert c.opportunistic_cooldown == 1800
        assert c.promotion_age_days == 14.0
        assert c.archive_salience_threshold == 0.20

    def test_salience_threshold_validation(self):
        """archive_salience_threshold rejects values outside [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            OrganizerConfig(archive_salience_threshold=1.5)
        with pytest.raises(ValidationError):
            OrganizerConfig(archive_salience_threshold=-0.1)

    def test_confidence_threshold_validation(self):
        """archive_confidence_threshold rejects values outside [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            OrganizerConfig(archive_confidence_threshold=2.0)

    def test_force_archive_threshold_validation(self):
        """force_archive_salience_threshold rejects values outside [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            OrganizerConfig(force_archive_salience_threshold=-0.01)

    def test_deprecate_threshold_validation(self):
        """deprecate_confidence_threshold rejects values outside [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            OrganizerConfig(deprecate_confidence_threshold=1.01)

    def test_integrates_into_prme_config(self):
        """OrganizerConfig is accessible as PRMEConfig.organizer."""
        c = PRMEConfig()
        assert isinstance(c.organizer, OrganizerConfig)
        assert c.organizer.opportunistic_enabled is True

    def test_env_var_prefix(self, monkeypatch):
        """OrganizerConfig loads from PRME_ORGANIZER_ prefixed env vars."""
        monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_ENABLED", "false")
        monkeypatch.setenv("PRME_ORGANIZER_OPPORTUNISTIC_COOLDOWN", "7200")
        c = OrganizerConfig()
        assert c.opportunistic_enabled is False
        assert c.opportunistic_cooldown == 7200


# ---------------------------------------------------------------------------
# MemoryNode decay fields
# ---------------------------------------------------------------------------


class TestMemoryNodeDecayFields:
    """Tests for decay-related fields on MemoryNode."""

    def test_default_decay_fields(self):
        """MemoryNode has correct default values for decay fields."""
        node = MemoryNode(
            node_type=NodeType.FACT,
            user_id="test-user",
            content="test content",
        )
        assert node.decay_profile == DecayProfile.MEDIUM
        assert isinstance(node.last_reinforced_at, datetime)
        assert node.last_reinforced_at.tzinfo is not None
        assert node.reinforcement_boost == 0.0
        assert node.salience_base == 0.5
        assert node.confidence_base == 0.5
        assert node.pinned is False

    def test_custom_decay_fields(self):
        """MemoryNode accepts custom decay field values."""
        now = datetime.now(timezone.utc)
        node = MemoryNode(
            node_type=NodeType.FACT,
            user_id="test-user",
            content="test content",
            decay_profile=DecayProfile.SLOW,
            last_reinforced_at=now,
            reinforcement_boost=0.3,
            salience_base=0.8,
            confidence_base=0.9,
            pinned=True,
        )
        assert node.decay_profile == DecayProfile.SLOW
        assert node.last_reinforced_at == now
        assert node.reinforcement_boost == 0.3
        assert node.salience_base == 0.8
        assert node.confidence_base == 0.9
        assert node.pinned is True

    def test_salience_base_validation_range(self):
        """salience_base rejects values outside [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            MemoryNode(
                node_type=NodeType.FACT,
                user_id="test-user",
                content="test",
                salience_base=1.5,
            )
        with pytest.raises(ValidationError):
            MemoryNode(
                node_type=NodeType.FACT,
                user_id="test-user",
                content="test",
                salience_base=-0.1,
            )

    def test_confidence_base_validation_range(self):
        """confidence_base rejects values outside [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            MemoryNode(
                node_type=NodeType.FACT,
                user_id="test-user",
                content="test",
                confidence_base=1.1,
            )
        with pytest.raises(ValidationError):
            MemoryNode(
                node_type=NodeType.FACT,
                user_id="test-user",
                content="test",
                confidence_base=-0.01,
            )

    def test_reinforcement_boost_non_negative(self):
        """reinforcement_boost rejects negative values."""
        with pytest.raises(ValidationError):
            MemoryNode(
                node_type=NodeType.FACT,
                user_id="test-user",
                content="test",
                reinforcement_boost=-0.5,
            )

    def test_salience_base_boundary_values(self):
        """salience_base accepts boundary values 0.0 and 1.0."""
        node_low = MemoryNode(
            node_type=NodeType.FACT,
            user_id="test-user",
            content="test",
            salience_base=0.0,
        )
        assert node_low.salience_base == 0.0

        node_high = MemoryNode(
            node_type=NodeType.FACT,
            user_id="test-user",
            content="test",
            salience_base=1.0,
        )
        assert node_high.salience_base == 1.0


# ---------------------------------------------------------------------------
# DuckDB schema migration
# ---------------------------------------------------------------------------


class TestDuckDBSchemaMigration:
    """Tests for DuckDB schema migration adding decay fields."""

    def test_initialize_creates_decay_columns(self, tmp_path):
        """initialize_database() creates nodes table with decay columns."""
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        initialize_database(conn)

        # Check that all 6 new columns exist
        columns = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'nodes'"
        ).fetchall()
        column_names = {row[0] for row in columns}

        assert "decay_profile" in column_names
        assert "last_reinforced_at" in column_names
        assert "reinforcement_boost" in column_names
        assert "salience_base" in column_names
        assert "confidence_base" in column_names
        assert "pinned" in column_names
        conn.close()

    def test_migration_idempotent(self, tmp_path):
        """Running initialize_database() twice does not fail."""
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        initialize_database(conn)
        # Second call should be a no-op (idempotent)
        initialize_database(conn)

        columns = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'nodes' AND column_name = 'decay_profile'"
        ).fetchall()
        assert len(columns) == 1
        conn.close()

    def test_migration_on_existing_db_without_decay(self, tmp_path):
        """Migration adds decay columns to an existing DB lacking them."""
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        # Create old-style schema without decay columns
        conn.execute("""
            CREATE TABLE nodes (
                id UUID PRIMARY KEY,
                node_type VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                session_id VARCHAR,
                scope VARCHAR NOT NULL DEFAULT 'personal',
                content TEXT NOT NULL,
                metadata JSON,
                confidence FLOAT DEFAULT 0.5,
                salience FLOAT DEFAULT 0.5,
                lifecycle_state VARCHAR NOT NULL DEFAULT 'tentative',
                valid_from TIMESTAMPTZ DEFAULT current_timestamp,
                valid_to TIMESTAMPTZ,
                superseded_by UUID,
                evidence_refs JSON,
                created_at TIMESTAMPTZ DEFAULT current_timestamp,
                updated_at TIMESTAMPTZ DEFAULT current_timestamp,
                epistemic_type VARCHAR NOT NULL DEFAULT 'asserted',
                source_type VARCHAR NOT NULL DEFAULT 'user_stated'
            )
        """)
        # Create edges and other required tables
        conn.execute("""
            CREATE TABLE edges (
                id UUID PRIMARY KEY,
                source_id UUID NOT NULL,
                target_id UUID NOT NULL,
                edge_type VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                confidence FLOAT DEFAULT 0.5,
                valid_from TIMESTAMPTZ DEFAULT current_timestamp,
                valid_to TIMESTAMPTZ,
                provenance_event_id UUID,
                metadata JSON,
                created_at TIMESTAMPTZ DEFAULT current_timestamp
            )
        """)
        conn.execute("""
            CREATE TABLE events (
                id UUID PRIMARY KEY,
                timestamp TIMESTAMPTZ NOT NULL,
                role VARCHAR NOT NULL,
                content TEXT NOT NULL,
                content_hash VARCHAR NOT NULL,
                user_id VARCHAR NOT NULL,
                session_id VARCHAR,
                scope VARCHAR NOT NULL DEFAULT 'personal',
                metadata JSON,
                created_at TIMESTAMPTZ DEFAULT current_timestamp
            )
        """)
        conn.execute("""
            CREATE TABLE operations (
                id VARCHAR PRIMARY KEY,
                op_type VARCHAR NOT NULL,
                target_id VARCHAR,
                payload JSON,
                actor_id VARCHAR,
                namespace_id VARCHAR,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        # Verify no decay columns before migration
        columns_before = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'nodes' AND column_name = 'decay_profile'"
        ).fetchall()
        assert len(columns_before) == 0

        # Run initialization (which triggers migration)
        initialize_database(conn)

        # Verify columns were added
        columns_after = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'nodes' AND column_name = 'decay_profile'"
        ).fetchall()
        assert len(columns_after) == 1
        conn.close()


# ---------------------------------------------------------------------------
# DuckDB round-trip persistence
# ---------------------------------------------------------------------------


class TestDuckDBRoundTrip:
    """Tests for round-trip node persistence with decay fields via DuckDB graph store."""

    @pytest.mark.asyncio
    async def test_round_trip_default_decay_fields(self, tmp_path):
        """Create node with default decay fields, read back, verify match."""
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        initialize_database(conn)
        store = DuckPGQGraphStore(conn, asyncio.Lock())

        node = MemoryNode(
            node_type=NodeType.FACT,
            user_id="test-user",
            content="Python is great",
        )

        node_id = await store.create_node(node)
        retrieved = await store.get_node(node_id)

        assert retrieved is not None
        assert retrieved.decay_profile == DecayProfile.MEDIUM
        assert retrieved.reinforcement_boost == 0.0
        assert retrieved.salience_base == 0.5
        assert retrieved.confidence_base == 0.5
        assert retrieved.pinned is False
        assert isinstance(retrieved.last_reinforced_at, datetime)
        conn.close()

    @pytest.mark.asyncio
    async def test_round_trip_custom_decay_fields(self, tmp_path):
        """Create node with custom decay fields, read back, verify match."""
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        initialize_database(conn)
        store = DuckPGQGraphStore(conn, asyncio.Lock())

        now = datetime.now(timezone.utc)
        node = MemoryNode(
            node_type=NodeType.ENTITY,
            user_id="test-user",
            content="Important entity",
            decay_profile=DecayProfile.PERMANENT,
            last_reinforced_at=now,
            reinforcement_boost=0.42,
            salience_base=0.95,
            confidence_base=0.88,
            pinned=True,
        )

        node_id = await store.create_node(node)
        retrieved = await store.get_node(node_id)

        assert retrieved is not None
        assert retrieved.decay_profile == DecayProfile.PERMANENT
        assert retrieved.reinforcement_boost == pytest.approx(0.42, abs=1e-6)
        assert retrieved.salience_base == pytest.approx(0.95, abs=1e-6)
        assert retrieved.confidence_base == pytest.approx(0.88, abs=1e-6)
        assert retrieved.pinned is True
        conn.close()

    @pytest.mark.asyncio
    async def test_round_trip_all_decay_profiles(self, tmp_path):
        """Round-trip every DecayProfile value to verify enum serialization."""
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        initialize_database(conn)
        store = DuckPGQGraphStore(conn, asyncio.Lock())

        for profile in DecayProfile:
            node = MemoryNode(
                node_type=NodeType.NOTE,
                user_id="test-user",
                content=f"Node with {profile.value} decay",
                decay_profile=profile,
            )
            node_id = await store.create_node(node)
            retrieved = await store.get_node(node_id)
            assert retrieved is not None
            assert retrieved.decay_profile == profile, (
                f"Expected {profile}, got {retrieved.decay_profile}"
            )
        conn.close()

    @pytest.mark.asyncio
    async def test_round_trip_preserves_other_fields(self, tmp_path):
        """Decay field changes do not affect other MemoryNode fields."""
        conn = duckdb.connect(str(tmp_path / "test.duckdb"))
        initialize_database(conn)
        store = DuckPGQGraphStore(conn, asyncio.Lock())

        node = MemoryNode(
            node_type=NodeType.DECISION,
            user_id="alice",
            content="Use PostgreSQL for production",
            confidence=0.9,
            salience=0.8,
            scope=Scope.PROJECT,
            decay_profile=DecayProfile.SLOW,
            salience_base=0.75,
            confidence_base=0.85,
        )

        node_id = await store.create_node(node)
        retrieved = await store.get_node(node_id)

        assert retrieved is not None
        assert retrieved.node_type == NodeType.DECISION
        assert retrieved.user_id == "alice"
        assert retrieved.content == "Use PostgreSQL for production"
        assert retrieved.confidence == pytest.approx(0.9, abs=1e-6)
        assert retrieved.salience == pytest.approx(0.8, abs=1e-6)
        assert retrieved.scope == Scope.PROJECT
        assert retrieved.decay_profile == DecayProfile.SLOW
        assert retrieved.salience_base == pytest.approx(0.75, abs=1e-6)
        assert retrieved.confidence_base == pytest.approx(0.85, abs=1e-6)
        conn.close()


# ---------------------------------------------------------------------------
# Export verification
# ---------------------------------------------------------------------------


class TestExports:
    """Tests that new types are exported from the prme package."""

    def test_decay_profile_exported(self):
        """DecayProfile is importable from prme."""
        from prme import DecayProfile as DP
        assert DP.MEDIUM == "medium"

    def test_decay_lambdas_exported(self):
        """DECAY_LAMBDAS is importable from prme."""
        from prme import DECAY_LAMBDAS as DL
        assert isinstance(DL, dict)
        assert len(DL) == 5

    def test_default_decay_profile_mapping_exported(self):
        """DEFAULT_DECAY_PROFILE_MAPPING is importable from prme."""
        from prme import DEFAULT_DECAY_PROFILE_MAPPING as DDPM
        assert isinstance(DDPM, dict)
        assert len(DDPM) == len(EpistemicType)

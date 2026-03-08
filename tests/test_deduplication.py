"""Tests for deduplication and entity alias resolution (issue #11).

Validates:
- Finding exact duplicate nodes
- Finding semantic duplicates via vector similarity
- Merge logic (correct node kept, edges transferred, SUPERSEDES created)
- Alias detection (abbreviations, case variations, semantic)
- Config thresholds
- Budget enforcement
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from prme import LifecycleState, MemoryEngine, NodeType, PRMEConfig, Scope
from prme.config import OrganizerConfig
from prme.models.edges import MemoryEdge
from prme.models.nodes import MemoryNode
from prme.organizer.alias_resolution import (
    AliasCandidate,
    _is_abbreviation_match,
    _is_case_variation,
    find_aliases,
    resolve_aliases,
)
from prme.organizer.deduplication import (
    DuplicateCandidate,
    _pick_canonical,
    find_duplicates,
    merge_duplicates,
)
from prme.organizer.models import JobResult
from prme.types import DecayProfile, EdgeType, EpistemicType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_dedup_") as d:
        yield d


@pytest.fixture
def config(tmp_dir):
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
    )


async def create_engine(config: PRMEConfig) -> MemoryEngine:
    """Create a MemoryEngine from config."""
    return await MemoryEngine.create(config)


# ---------------------------------------------------------------------------
# Unit tests: _pick_canonical
# ---------------------------------------------------------------------------


class TestPickCanonical:
    """Test canonical node selection logic."""

    def _make_node(
        self,
        confidence_base: float = 0.5,
        evidence_count: int = 1,
        age_days: int = 0,
    ) -> MemoryNode:
        now = datetime.now(timezone.utc)
        return MemoryNode(
            user_id="test-user",
            node_type=NodeType.FACT,
            content="some content",
            confidence_base=confidence_base,
            evidence_refs=[uuid4() for _ in range(evidence_count)],
            created_at=now - timedelta(days=age_days),
            decay_profile=DecayProfile.MEDIUM,
        )

    def test_higher_confidence_wins(self):
        a = self._make_node(confidence_base=0.8)
        b = self._make_node(confidence_base=0.5)
        canonical, duplicate = _pick_canonical(a, b)
        assert canonical.confidence_base == 0.8
        assert duplicate.confidence_base == 0.5

    def test_more_evidence_wins(self):
        a = self._make_node(confidence_base=0.5, evidence_count=3)
        b = self._make_node(confidence_base=0.5, evidence_count=1)
        canonical, duplicate = _pick_canonical(a, b)
        assert len(canonical.evidence_refs) == 3
        assert len(duplicate.evidence_refs) == 1

    def test_older_wins_when_tied(self):
        a = self._make_node(confidence_base=0.5, evidence_count=1, age_days=10)
        b = self._make_node(confidence_base=0.5, evidence_count=1, age_days=2)
        canonical, duplicate = _pick_canonical(a, b)
        assert canonical.created_at < duplicate.created_at


# ---------------------------------------------------------------------------
# Unit tests: alias string matching
# ---------------------------------------------------------------------------


class TestAliasStringMatching:
    """Test abbreviation and case variation detection."""

    def test_known_abbreviation_js(self):
        assert _is_abbreviation_match("JavaScript", "JS") is True
        assert _is_abbreviation_match("JS", "JavaScript") is True

    def test_known_abbreviation_python(self):
        assert _is_abbreviation_match("Python", "py") is True

    def test_known_abbreviation_kubernetes(self):
        assert _is_abbreviation_match("Kubernetes", "k8s") is True

    def test_known_abbreviation_aws(self):
        assert _is_abbreviation_match("Amazon Web Services", "AWS") is True

    def test_unknown_abbreviation(self):
        assert _is_abbreviation_match("Foo", "Bar") is False

    def test_case_variation(self):
        assert _is_case_variation("PostgreSQL", "postgresql") is True
        assert _is_case_variation("javascript", "JavaScript") is True

    def test_case_variation_exact_same(self):
        # Exact same string is NOT a case variation
        assert _is_case_variation("Python", "Python") is False

    def test_different_strings_not_case_variation(self):
        assert _is_case_variation("Python", "JavaScript") is False


# ---------------------------------------------------------------------------
# Integration tests: find_duplicates
# ---------------------------------------------------------------------------


class TestFindDuplicates:
    """Test duplicate detection via engine."""

    @pytest.mark.asyncio
    async def test_find_exact_duplicates(self, config):
        engine = await create_engine(config)
        try:
            # Store identical content twice
            await engine.store(
                "Python is a great programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Python is a great programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            org_config = OrganizerConfig()
            duplicates = await find_duplicates(engine, org_config)

            assert len(duplicates) >= 1
            # At least one pair should be exact
            exact = [d for d in duplicates if d.match_type == "exact"]
            assert len(exact) >= 1
            assert exact[0].similarity == 1.0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_find_semantic_duplicates(self, config):
        engine = await create_engine(config)
        try:
            # Store semantically similar content
            await engine.store(
                "Python is a great programming language for data science",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Python is an excellent programming language for data science work",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            org_config = OrganizerConfig(dedup_similarity_threshold=0.85)
            duplicates = await find_duplicates(engine, org_config)

            # Should find at least one pair (exact or semantic)
            assert len(duplicates) >= 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_no_duplicates_different_content(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "Python is a programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "The weather in Tokyo is rainy today",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            org_config = OrganizerConfig()
            duplicates = await find_duplicates(engine, org_config)

            # Should not find semantic duplicates between unrelated content
            semantic = [d for d in duplicates if d.match_type == "semantic"]
            assert len(semantic) == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_budget_enforcement(self, config):
        engine = await create_engine(config)
        try:
            # Store several nodes
            for i in range(5):
                await engine.store(
                    f"Fact number {i} about something unique {i * 1000}",
                    user_id="test-user",
                    node_type=NodeType.FACT,
                )

            org_config = OrganizerConfig()
            # Very tight budget (0.01 ms) -- should exit early
            duplicates = await find_duplicates(
                engine, org_config, budget_ms=0.01,
            )

            # Just verify it doesn't crash -- may or may not find duplicates
            assert isinstance(duplicates, list)
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Integration tests: merge_duplicates
# ---------------------------------------------------------------------------


class TestMergeDuplicates:
    """Test duplicate merging logic."""

    @pytest.mark.asyncio
    async def test_merge_creates_supersedes_edge(self, config):
        engine = await create_engine(config)
        try:
            # Store duplicate content
            eid1 = await engine.store(
                "JavaScript is used for web development",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            eid2 = await engine.store(
                "JavaScript is used for web development",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            # Find the node IDs
            nodes = await engine.query_nodes(
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            js_nodes = [n for n in nodes if "JavaScript" in n.content]
            assert len(js_nodes) == 2

            # Create a manual duplicate candidate
            dup = DuplicateCandidate(
                str(js_nodes[0].id), str(js_nodes[1].id), 1.0, "exact"
            )

            merged = await merge_duplicates(engine, [dup])
            assert merged == 1

            # Verify SUPERSEDES edge exists
            edges = await engine._graph_store.get_edges(
                edge_type=EdgeType.SUPERSEDES,
            )
            supersede_edges = [
                e for e in edges
                if str(e.source_id) in (str(js_nodes[0].id), str(js_nodes[1].id))
                and str(e.target_id) in (str(js_nodes[0].id), str(js_nodes[1].id))
            ]
            assert len(supersede_edges) >= 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_merge_archives_duplicate(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "Rust is a systems programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Rust is a systems programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            nodes = await engine.query_nodes(
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            rust_nodes = [n for n in nodes if "Rust" in n.content]
            assert len(rust_nodes) == 2

            dup = DuplicateCandidate(
                str(rust_nodes[0].id), str(rust_nodes[1].id), 1.0, "exact"
            )
            merged = await merge_duplicates(engine, [dup])
            assert merged == 1

            # One should still be active, one should be superseded
            active_nodes = await engine.query_nodes(
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            active_rust = [n for n in active_nodes if "Rust" in n.content]
            assert len(active_rust) == 1

            # Check the superseded node
            superseded_nodes = await engine.query_nodes(
                lifecycle_states=[LifecycleState.SUPERSEDED],
            )
            superseded_rust = [n for n in superseded_nodes if "Rust" in n.content]
            assert len(superseded_rust) == 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_merge_transfers_evidence(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "Go is created by Google",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Go is created by Google",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            nodes = await engine.query_nodes(
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            go_nodes = [n for n in nodes if "Go" in n.content]
            assert len(go_nodes) == 2

            # Each node should have 1 evidence ref initially
            assert len(go_nodes[0].evidence_refs) >= 1
            assert len(go_nodes[1].evidence_refs) >= 1

            dup = DuplicateCandidate(
                str(go_nodes[0].id), str(go_nodes[1].id), 1.0, "exact"
            )
            await merge_duplicates(engine, [dup])

            # The canonical (surviving) node should have evidence from both
            active_nodes = await engine.query_nodes(
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            active_go = [n for n in active_nodes if "Go" in n.content]
            assert len(active_go) == 1
            # Should have at least 2 evidence refs (one from each original node)
            assert len(active_go[0].evidence_refs) >= 2
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_correct_node_kept(self, config):
        """Higher confidence node should be kept as canonical."""
        engine = await create_engine(config)
        try:
            # Store the same content twice
            await engine.store(
                "Docker containers are lightweight",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Docker containers are lightweight",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            nodes = await engine.query_nodes(
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            docker_nodes = [n for n in nodes if "Docker" in n.content]
            assert len(docker_nodes) == 2

            # Boost one node's confidence
            high_conf_id = str(docker_nodes[0].id)
            await engine._graph_store.update_node(
                high_conf_id, confidence_base=0.9
            )

            dup = DuplicateCandidate(
                str(docker_nodes[0].id),
                str(docker_nodes[1].id),
                1.0,
                "exact",
            )
            await merge_duplicates(engine, [dup])

            # The high-confidence node should survive
            active = await engine.query_nodes(
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            active_docker = [n for n in active if "Docker" in n.content]
            assert len(active_docker) == 1
            assert str(active_docker[0].id) == high_conf_id
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Integration tests: find_aliases
# ---------------------------------------------------------------------------


class TestFindAliases:
    """Test alias detection for entity nodes."""

    @pytest.mark.asyncio
    async def test_find_abbreviation_alias(self, config):
        engine = await create_engine(config)
        try:
            # Store entities with known alias relationship
            await engine.store(
                "JavaScript",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )
            await engine.store(
                "JS",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )

            org_config = OrganizerConfig()
            aliases = await find_aliases(engine, org_config)

            abbreviation_aliases = [
                a for a in aliases if a.alias_type == "abbreviation"
            ]
            assert len(abbreviation_aliases) >= 1
            assert abbreviation_aliases[0].confidence == 0.95
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_find_case_variation_alias(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "PostgreSQL",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )
            await engine.store(
                "postgresql",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )

            org_config = OrganizerConfig()
            aliases = await find_aliases(engine, org_config)

            # Should find case variation or abbreviation
            found = [
                a for a in aliases
                if a.alias_type in ("case_variation", "abbreviation")
            ]
            assert len(found) >= 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_no_alias_for_different_entities(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "Python",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )
            await engine.store(
                "Kubernetes",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )

            org_config = OrganizerConfig()
            aliases = await find_aliases(engine, org_config)

            # Should not find abbreviation or case variation aliases
            string_aliases = [
                a for a in aliases
                if a.alias_type in ("abbreviation", "case_variation")
            ]
            assert len(string_aliases) == 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Integration tests: resolve_aliases
# ---------------------------------------------------------------------------


class TestResolveAliases:
    """Test alias resolution logic."""

    @pytest.mark.asyncio
    async def test_high_confidence_alias_merged(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "JavaScript",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )
            await engine.store(
                "JS",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )

            nodes = await engine.query_nodes(
                node_type=NodeType.ENTITY,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            entity_nodes = [n for n in nodes if n.content in ("JavaScript", "JS")]
            assert len(entity_nodes) == 2

            # High confidence alias (abbreviation = 0.95 >= 0.90 merge threshold)
            alias = AliasCandidate(
                str(entity_nodes[0].id),
                str(entity_nodes[1].id),
                "abbreviation",
                0.95,
            )
            resolved = await resolve_aliases(engine, [alias])
            assert resolved == 1

            # One entity should be superseded
            active = await engine.query_nodes(
                node_type=NodeType.ENTITY,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            active_entities = [n for n in active if n.content in ("JavaScript", "JS")]
            assert len(active_entities) == 1
            # The longer name (JavaScript) should be kept
            assert active_entities[0].content == "JavaScript"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_low_confidence_alias_linked(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "React",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )
            await engine.store(
                "ReactJS",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )

            nodes = await engine.query_nodes(
                node_type=NodeType.ENTITY,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            react_nodes = [n for n in nodes if "React" in n.content]
            assert len(react_nodes) == 2

            # Low confidence alias (below merge threshold)
            alias = AliasCandidate(
                str(react_nodes[0].id),
                str(react_nodes[1].id),
                "semantic",
                0.87,
            )
            resolved = await resolve_aliases(engine, [alias])
            assert resolved == 1

            # Both entities should still be active
            active = await engine.query_nodes(
                node_type=NodeType.ENTITY,
                lifecycle_states=[LifecycleState.TENTATIVE, LifecycleState.STABLE],
            )
            active_react = [n for n in active if "React" in n.content]
            assert len(active_react) == 2

            # A RELATES_TO edge should exist
            edges = await engine._graph_store.get_edges(
                edge_type=EdgeType.RELATES_TO,
            )
            alias_edges = [
                e for e in edges
                if e.metadata and e.metadata.get("relation") == "alias"
            ]
            assert len(alias_edges) >= 1
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Integration tests: config thresholds
# ---------------------------------------------------------------------------


class TestConfigThresholds:
    """Test that config thresholds are respected."""

    @pytest.mark.asyncio
    async def test_high_threshold_reduces_duplicates(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "Python is widely used in data science and machine learning",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Python is popular in data science and ML applications",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            # Very strict threshold
            strict_config = OrganizerConfig(dedup_similarity_threshold=0.99)
            strict_dups = await find_duplicates(engine, strict_config)

            # More lenient threshold
            lenient_config = OrganizerConfig(dedup_similarity_threshold=0.80)
            lenient_dups = await find_duplicates(engine, lenient_config)

            # Strict should find fewer or equal duplicates than lenient
            assert len(strict_dups) <= len(lenient_dups)
        finally:
            await engine.close()

    def test_default_config_values(self):
        org_config = OrganizerConfig()
        assert org_config.dedup_similarity_threshold == 0.92
        assert org_config.alias_similarity_threshold == 0.85


# ---------------------------------------------------------------------------
# Integration tests: full job pipeline via organize()
# ---------------------------------------------------------------------------


class TestOrganizeJobPipeline:
    """Test deduplicate and alias_resolve jobs via engine.organize()."""

    @pytest.mark.asyncio
    async def test_deduplicate_job_runs(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "TypeScript extends JavaScript with types",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "TypeScript extends JavaScript with types",
                user_id="test-user",
                node_type=NodeType.FACT,
            )

            result = await engine.organize(
                jobs=["deduplicate"],
                budget_ms=10000,
            )

            assert "deduplicate" in result.jobs_run
            job_result = result.per_job["deduplicate"]
            assert isinstance(job_result, JobResult)
            assert job_result.nodes_modified >= 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_alias_resolve_job_runs(self, config):
        engine = await create_engine(config)
        try:
            await engine.store(
                "JavaScript",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )
            await engine.store(
                "JS",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )

            result = await engine.organize(
                jobs=["alias_resolve"],
                budget_ms=10000,
            )

            assert "alias_resolve" in result.jobs_run
            job_result = result.per_job["alias_resolve"]
            assert isinstance(job_result, JobResult)
            # Should find and resolve at least the JS/JavaScript alias
            assert job_result.nodes_processed >= 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_both_jobs_in_organize(self, config):
        engine = await create_engine(config)
        try:
            # Store duplicates and aliases
            await engine.store(
                "Go is a compiled language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Go is a compiled language",
                user_id="test-user",
                node_type=NodeType.FACT,
            )
            await engine.store(
                "Kubernetes",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )
            await engine.store(
                "k8s",
                user_id="test-user",
                node_type=NodeType.ENTITY,
            )

            result = await engine.organize(
                jobs=["deduplicate", "alias_resolve"],
                budget_ms=10000,
            )

            assert "deduplicate" in result.jobs_run
            assert "alias_resolve" in result.jobs_run
        finally:
            await engine.close()

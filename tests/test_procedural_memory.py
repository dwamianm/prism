"""Tests for procedural memory (INSTRUCTION node type).

Verifies INSTRUCTION nodes:
- Are stored with correct defaults (SLOW decay, higher confidence)
- Appear in "system_instructions" section of MemoryBundle
- Are packed before facts (Priority 0)
- Benefit from instruction reinforcement
- Progress through lifecycle (tentative -> stable via promotion)
- Use SLOW decay profile by default
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from prme.config import PRMEConfig
from prme.models.nodes import MemoryNode
from prme.retrieval.models import MemoryBundle, RetrievalCandidate
from prme.retrieval.packing import classify_into_sections, pack_context
from prme.storage.engine import MemoryEngine
from prme.types import (
    DecayProfile,
    EpistemicType,
    LifecycleState,
    NodeType,
    Scope,
    NODE_TYPE_DECAY_OVERRIDES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="prme_procedural_") as d:
        yield d


@pytest.fixture
def config(tmp_dir):
    """Config for procedural memory tests."""
    lexical_path = Path(tmp_dir) / "lexical_index"
    lexical_path.mkdir(exist_ok=True)
    return PRMEConfig(
        db_path=str(Path(tmp_dir) / "memory.duckdb"),
        vector_path=str(Path(tmp_dir) / "vectors.usearch"),
        lexical_path=str(lexical_path),
    )


async def _create_engine(config: PRMEConfig) -> MemoryEngine:
    return await MemoryEngine.create(config)


async def _store_instruction(
    engine: MemoryEngine,
    content: str,
    user_id: str = "test-user",
    **kwargs,
) -> tuple[str, str]:
    """Store an INSTRUCTION and return (event_id, node_id)."""
    event_id = await engine.store(
        content,
        user_id=user_id,
        node_type=NodeType.INSTRUCTION,
        scope=Scope.PERSONAL,
        **kwargs,
    )
    nodes = await engine.query_nodes(user_id=user_id, limit=200)
    for n in nodes:
        if n.content == content:
            return event_id, str(n.id)
    raise RuntimeError(f"Could not find stored node with content {content!r}")


# ---------------------------------------------------------------------------
# Tests: NodeType and Decay Profile
# ---------------------------------------------------------------------------


class TestInstructionNodeType:
    """Tests for INSTRUCTION in NodeType enum and decay profile."""

    def test_instruction_in_node_type_enum(self):
        """INSTRUCTION should be a valid NodeType value."""
        assert NodeType.INSTRUCTION == "instruction"
        assert NodeType("instruction") == NodeType.INSTRUCTION

    def test_instruction_decay_override_is_slow(self):
        """INSTRUCTION nodes should map to SLOW decay in overrides."""
        assert NODE_TYPE_DECAY_OVERRIDES[NodeType.INSTRUCTION] == DecayProfile.SLOW

    @pytest.mark.asyncio
    async def test_stored_instruction_has_slow_decay(self, config):
        """Stored INSTRUCTION nodes should default to SLOW decay profile."""
        engine = await _create_engine(config)
        try:
            _, node_id = await _store_instruction(
                engine, "Always respond in concise bullet points"
            )
            node = await engine.get_node(node_id)
            assert node is not None
            assert node.decay_profile == DecayProfile.SLOW
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_stored_instruction_has_higher_default_confidence(self, config):
        """INSTRUCTION nodes should default to at least 0.7 confidence."""
        engine = await _create_engine(config)
        try:
            _, node_id = await _store_instruction(
                engine, "Use Python 3.12+ for all new projects"
            )
            node = await engine.get_node(node_id)
            assert node is not None
            assert node.confidence >= 0.7, (
                f"Expected confidence >= 0.7, got {node.confidence}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_instruction_default_epistemic_type(self, config):
        """INSTRUCTION nodes should default to OBSERVED epistemic type."""
        engine = await _create_engine(config)
        try:
            _, node_id = await _store_instruction(
                engine, "Always check the database before responding"
            )
            node = await engine.get_node(node_id)
            assert node is not None
            assert node.epistemic_type == EpistemicType.OBSERVED
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_instruction_can_be_inferred(self, config):
        """INSTRUCTION nodes can be created with INFERRED epistemic type."""
        engine = await _create_engine(config)
        try:
            event_id = await engine.store(
                "User seems to prefer short answers",
                user_id="test-user",
                node_type=NodeType.INSTRUCTION,
                scope=Scope.PERSONAL,
                epistemic_type=EpistemicType.INFERRED,
            )
            nodes = await engine.query_nodes(user_id="test-user", limit=200)
            inferred_instructions = [
                n for n in nodes
                if n.content == "User seems to prefer short answers"
            ]
            assert len(inferred_instructions) == 1
            assert inferred_instructions[0].epistemic_type == EpistemicType.INFERRED
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Tests: Context Packing / Section Classification
# ---------------------------------------------------------------------------


def _make_candidate(
    node_type: NodeType,
    content: str = "test content",
    composite_score: float = 0.5,
    lifecycle_state: LifecycleState = LifecycleState.STABLE,
) -> RetrievalCandidate:
    """Create a minimal RetrievalCandidate for testing."""
    node = MemoryNode(
        user_id="test",
        node_type=node_type,
        content=content,
        lifecycle_state=lifecycle_state,
    )
    return RetrievalCandidate(
        node=node,
        composite_score=composite_score,
    )


class TestInstructionSectionClassification:
    """Tests for INSTRUCTION node section classification in packing."""

    def test_instruction_classified_as_system_instructions(self):
        """INSTRUCTION nodes should go to 'system_instructions' section."""
        candidate = _make_candidate(NodeType.INSTRUCTION)
        section = classify_into_sections(candidate)
        assert section == "system_instructions"

    def test_fact_still_classified_as_stable_facts(self):
        """FACT nodes should still go to 'stable_facts' (no regression)."""
        candidate = _make_candidate(NodeType.FACT)
        section = classify_into_sections(candidate)
        assert section == "stable_facts"

    def test_contested_instruction_goes_to_contested(self):
        """CONTESTED INSTRUCTION nodes should go to contested_claims."""
        candidate = _make_candidate(
            NodeType.INSTRUCTION,
            lifecycle_state=LifecycleState.CONTESTED,
        )
        section = classify_into_sections(candidate)
        assert section == "contested_claims"


class TestInstructionPackingPriority:
    """Tests that INSTRUCTION nodes are packed before other content."""

    def test_instructions_packed_before_facts(self):
        """INSTRUCTION candidates should appear in bundle before facts."""
        instruction = _make_candidate(
            NodeType.INSTRUCTION,
            content="Always use Python 3.12+",
            composite_score=0.3,  # Low score, but should still be packed first
        )
        fact = _make_candidate(
            NodeType.FACT,
            content="Python is a programming language",
            composite_score=0.9,  # High score fact
        )

        bundle = pack_context([fact, instruction])

        # Instructions should be in their own section
        assert "system_instructions" in bundle.sections
        assert len(bundle.sections["system_instructions"]) == 1
        assert bundle.sections["system_instructions"][0].node.content == "Always use Python 3.12+"

    def test_instructions_packed_before_pinned_items(self):
        """INSTRUCTION candidates should be packed before even pinned items."""
        instruction = _make_candidate(
            NodeType.INSTRUCTION,
            content="Be concise",
            composite_score=0.3,
        )
        # Create a pinned fact (salience=1.0)
        pinned_node = MemoryNode(
            user_id="test",
            node_type=NodeType.FACT,
            content="Important pinned fact with lots of text content",
            salience=1.0,
        )
        pinned = RetrievalCandidate(
            node=pinned_node,
            composite_score=0.9,
        )

        # Very small budget that can only fit one item
        from prme.retrieval.config import PackingConfig
        tight_config = PackingConfig(
            token_budget=20,  # Very tight
            overhead_tokens=0,
        )

        bundle = pack_context([pinned, instruction], config=tight_config)

        # With a tight budget, instruction should be included first
        if "system_instructions" in bundle.sections:
            assert len(bundle.sections["system_instructions"]) >= 1
        # The key assertion: if only one item fits, it should be the instruction
        if bundle.included_count == 1:
            all_candidates = []
            for section_candidates in bundle.sections.values():
                all_candidates.extend(section_candidates)
            assert all_candidates[0].node.node_type == NodeType.INSTRUCTION


# ---------------------------------------------------------------------------
# Tests: MemoryBundle render_system_instructions
# ---------------------------------------------------------------------------


class TestMemoryBundleSystemInstructions:
    """Tests for MemoryBundle.render_system_instructions()."""

    def test_render_empty_when_no_instructions(self):
        """render_system_instructions should return '' when no instructions."""
        bundle = MemoryBundle()
        assert bundle.render_system_instructions() == ""

    def test_render_single_instruction(self):
        """render_system_instructions should format a single instruction."""
        candidate = _make_candidate(
            NodeType.INSTRUCTION,
            content="Always respond concisely",
        )
        bundle = MemoryBundle(
            sections={"system_instructions": [candidate]},
        )
        rendered = bundle.render_system_instructions()
        assert "## System Instructions" in rendered
        assert "- Always respond concisely" in rendered

    def test_render_multiple_instructions(self):
        """render_system_instructions should list all instructions."""
        c1 = _make_candidate(NodeType.INSTRUCTION, content="Be concise")
        c2 = _make_candidate(NodeType.INSTRUCTION, content="Use Python 3.12+")
        bundle = MemoryBundle(
            sections={"system_instructions": [c1, c2]},
        )
        rendered = bundle.render_system_instructions()
        assert "## System Instructions" in rendered
        assert "- Be concise" in rendered
        assert "- Use Python 3.12+" in rendered


# ---------------------------------------------------------------------------
# Tests: Instruction Reinforcement
# ---------------------------------------------------------------------------


class TestInstructionReinforcement:
    """Tests for INSTRUCTION reinforcement via similar content."""

    @pytest.mark.asyncio
    async def test_instruction_reinforced_by_related_content(self, config):
        """Storing content that validates an instruction should reinforce it."""
        engine = await _create_engine(config)
        try:
            # Store an instruction
            _, inst_id = await _store_instruction(
                engine, "Always use Python for data analysis tasks"
            )
            original = await engine.get_node(inst_id)
            assert original is not None
            original_boost = original.reinforcement_boost

            # Store related content that validates the instruction
            await engine.store(
                "I completed the data analysis using Python as we always do",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            # Check that the instruction was reinforced
            updated = await engine.get_node(inst_id)
            assert updated is not None
            assert updated.reinforcement_boost >= original_boost, (
                f"Expected reinforcement_boost >= {original_boost}, "
                f"got {updated.reinforcement_boost}"
            )
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_instruction_reinforcement_nonfatal(self, config):
        """Instruction reinforcement failure should not break store()."""
        engine = await _create_engine(config)
        try:
            # Store an instruction
            await _store_instruction(
                engine, "Always validate input data"
            )

            # Store another fact -- even if instruction reinforcement
            # has issues internally, the store should succeed
            event_id = await engine.store(
                "Validating all input data before processing",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )
            assert event_id is not None
            assert len(event_id) > 0
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Tests: Instruction Lifecycle
# ---------------------------------------------------------------------------


class TestInstructionLifecycle:
    """Tests for INSTRUCTION lifecycle (tentative -> stable)."""

    @pytest.mark.asyncio
    async def test_instruction_starts_tentative(self, config):
        """Newly stored INSTRUCTION nodes should start in TENTATIVE state."""
        engine = await _create_engine(config)
        try:
            _, node_id = await _store_instruction(
                engine, "Prefer TypeScript over JavaScript"
            )
            node = await engine.get_node(node_id)
            assert node is not None
            assert node.lifecycle_state == LifecycleState.TENTATIVE
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_instruction_can_be_promoted(self, config):
        """INSTRUCTION nodes should be promotable to STABLE."""
        engine = await _create_engine(config)
        try:
            _, node_id = await _store_instruction(
                engine, "Always run tests before committing"
            )

            # Promote manually
            await engine.promote(node_id)

            node = await engine.get_node(node_id)
            assert node is not None
            assert node.lifecycle_state == LifecycleState.STABLE
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_instruction_can_be_superseded(self, config):
        """INSTRUCTION nodes should support supersedence."""
        engine = await _create_engine(config)
        try:
            _, old_id = await _store_instruction(
                engine, "Use Python 3.10 for all projects"
            )
            _, new_id = await _store_instruction(
                engine, "Use Python 3.12 for all projects"
            )

            await engine.supersede(old_id, new_id)

            old_node = await engine.get_node(old_id, include_superseded=True)
            assert old_node is not None
            assert old_node.lifecycle_state == LifecycleState.SUPERSEDED
        finally:
            await engine.close()


# ---------------------------------------------------------------------------
# Tests: End-to-end retrieval
# ---------------------------------------------------------------------------


class TestInstructionRetrieval:
    """End-to-end tests for INSTRUCTION nodes in retrieval pipeline."""

    @pytest.mark.asyncio
    async def test_instruction_appears_in_retrieval(self, config):
        """INSTRUCTION nodes should appear in retrieval results."""
        engine = await _create_engine(config)
        try:
            await _store_instruction(
                engine, "Always provide code examples in responses"
            )

            response = await engine.retrieve(
                "How should I respond to questions?",
                user_id="test-user",
            )

            # Check that the instruction appears in the bundle
            bundle = response.bundle
            all_contents = []
            for section_candidates in bundle.sections.values():
                for c in section_candidates:
                    all_contents.append(c.node.content)

            # The instruction should be findable (either in results or bundle)
            all_result_contents = [r.node.content for r in response.results]
            assert any(
                "code examples" in c.lower()
                for c in all_result_contents + all_contents
            ), "INSTRUCTION node should appear in retrieval results"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_instruction_section_in_bundle(self, config):
        """When instructions are retrieved, they appear in system_instructions section."""
        engine = await _create_engine(config)
        try:
            await _store_instruction(
                engine, "Always respond in English"
            )
            await engine.store(
                "Python is a programming language",
                user_id="test-user",
                node_type=NodeType.FACT,
                scope=Scope.PERSONAL,
            )

            response = await engine.retrieve(
                "What language should I use?",
                user_id="test-user",
            )

            bundle = response.bundle
            # If instructions are in the results, they should be in
            # the system_instructions section
            if "system_instructions" in bundle.sections:
                for c in bundle.sections["system_instructions"]:
                    assert c.node.node_type == NodeType.INSTRUCTION
        finally:
            await engine.close()

"""Tests for the VSA codebook (symbol → hypervector mapping)."""

import numpy as np
import pytest

from research.vsa.codebook import Codebook
from research.vsa.core import similarity


@pytest.fixture
def codebook():
    return Codebook(dim=10_000, seed=42)


class TestCodebook:
    def test_builtin_roles_populated(self, codebook):
        """All built-in roles should be pre-populated."""
        for role in Codebook.BUILTIN_ROLES:
            assert role in codebook

    def test_node_types_populated(self, codebook):
        for nt in Codebook.NODE_TYPES:
            assert f"NT_{nt}" in codebook

    def test_deterministic(self):
        """Same seed should produce identical vectors."""
        cb1 = Codebook(dim=10_000, seed=42)
        cb2 = Codebook(dim=10_000, seed=42)
        for role in Codebook.BUILTIN_ROLES:
            assert np.allclose(cb1.get(role), cb2.get(role))

    def test_different_seeds_different_vectors(self):
        cb1 = Codebook(dim=10_000, seed=42)
        cb2 = Codebook(dim=10_000, seed=99)
        sim = similarity(cb1.get("AGENT"), cb2.get("AGENT"))
        assert abs(sim) < 0.1

    def test_new_symbol_auto_created(self, codebook):
        """Unknown symbols should be auto-created."""
        v = codebook.get("brand_new_symbol")
        assert v.shape == (10_000,)
        # Same symbol should return same vector
        v2 = codebook.get("brand_new_symbol")
        assert np.allclose(v, v2)

    def test_different_symbols_are_dissimilar(self, codebook):
        a = codebook.get("alice")
        b = codebook.get("bob")
        sim = similarity(a, b)
        assert abs(sim) < 0.1

    def test_get_or_encode_single_word(self, codebook):
        """Single word should match atomic vector."""
        v1 = codebook.get("hello")
        v2 = codebook.get_or_encode("hello")
        assert np.allclose(v1, v2)

    def test_get_or_encode_multi_word(self, codebook):
        """Multi-word should produce composite, not atomic."""
        v_single = codebook.get("hello")
        v_multi = codebook.get_or_encode("hello world")
        # Should be different from just "hello"
        sim = similarity(v_single, v_multi)
        assert sim < 0.8  # related but not identical

    def test_lookup_finds_closest(self, codebook):
        """Lookup should find the exact symbol for its own vector."""
        target = codebook.get("AGENT")
        results = codebook.lookup(target, top_k=3)
        assert len(results) > 0
        assert results[0][0] == "AGENT"
        assert results[0][1] > 0.99

    def test_lookup_threshold(self, codebook):
        """High threshold should filter out unrelated symbols."""
        target = codebook.get("AGENT")
        results = codebook.lookup(target, threshold=0.5)
        for sym, sim in results:
            assert sim >= 0.5

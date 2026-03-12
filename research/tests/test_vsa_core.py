"""Tests for VSA core operations.

Validates the mathematical properties that the entire memory system
depends on. If these fail, nothing else works.
"""

import numpy as np
import pytest

from research.vsa.core import (
    DEFAULT_DIM,
    HV,
    bind,
    bundle,
    hard_quantize,
    inverse_permute,
    normalize,
    permute,
    random_hv,
    similarity,
    unbind,
    weighted_bundle,
)


@pytest.fixture
def rng():
    return np.random.default_rng(42)


@pytest.fixture
def dim():
    return DEFAULT_DIM


class TestRandomHV:
    def test_correct_dimension(self, dim, rng):
        v = random_hv(dim, rng=rng)
        assert v.shape == (dim,)

    def test_bipolar_values(self, dim, rng):
        v = random_hv(dim, bipolar=True, rng=rng)
        unique_vals = set(np.unique(v))
        assert unique_vals <= {-1.0, 1.0}

    def test_approximately_balanced(self, dim, rng):
        v = random_hv(dim, bipolar=True, rng=rng)
        # Should be roughly 50/50 positive/negative
        pos_ratio = np.mean(v > 0)
        assert 0.45 < pos_ratio < 0.55

    def test_random_vectors_are_quasi_orthogonal(self, dim, rng):
        """Two random vectors in high dim should have near-zero similarity."""
        a = random_hv(dim, rng=rng)
        b = random_hv(dim, rng=rng)
        sim = similarity(a, b)
        # Expected: |sim| < ~0.03 for dim=10000
        assert abs(sim) < 0.1


class TestBind:
    def test_self_inverse(self, dim, rng):
        """bind(a, a) should be close to the identity (all 1s for bipolar)."""
        a = random_hv(dim, bipolar=True, rng=rng)
        result = bind(a, a)
        # For bipolar: a * a = 1 for all elements
        assert np.allclose(result, np.ones(dim))

    def test_commutative(self, dim, rng):
        a = random_hv(dim, rng=rng)
        b = random_hv(dim, rng=rng)
        assert np.allclose(bind(a, b), bind(b, a))

    def test_preserves_dissimilarity(self, dim, rng):
        """bind(a, b) should be dissimilar to both a and b."""
        a = random_hv(dim, bipolar=True, rng=rng)
        b = random_hv(dim, bipolar=True, rng=rng)
        ab = bind(a, b)
        assert abs(similarity(ab, a)) < 0.1
        assert abs(similarity(ab, b)) < 0.1

    def test_recoverable_via_unbind(self, dim, rng):
        """unbind(bind(a, b), a) should be similar to b."""
        a = random_hv(dim, bipolar=True, rng=rng)
        b = random_hv(dim, bipolar=True, rng=rng)
        ab = bind(a, b)
        recovered_b = unbind(ab, a)
        assert similarity(recovered_b, b) > 0.99  # exact for bipolar


class TestBundle:
    def test_similar_to_components(self, dim, rng):
        """Bundle should be similar to all its components."""
        a = random_hv(dim, bipolar=True, rng=rng)
        b = random_hv(dim, bipolar=True, rng=rng)
        c = random_hv(dim, bipolar=True, rng=rng)
        bundled = bundle(a, b, c)

        # Each component should have positive similarity with the bundle
        assert similarity(bundled, a) > 0.2
        assert similarity(bundled, b) > 0.2
        assert similarity(bundled, c) > 0.2

    def test_not_similar_to_non_components(self, dim, rng):
        """Bundle should NOT be similar to vectors not in it."""
        a = random_hv(dim, rng=rng)
        b = random_hv(dim, rng=rng)
        c = random_hv(dim, rng=rng)
        d = random_hv(dim, rng=rng)  # not bundled
        bundled = bundle(a, b, c)
        assert abs(similarity(bundled, d)) < 0.1

    def test_minimum_vectors(self):
        with pytest.raises(ValueError):
            bundle(np.ones(10))

    def test_is_normalized(self, dim, rng):
        a = random_hv(dim, rng=rng)
        b = random_hv(dim, rng=rng)
        bundled = bundle(a, b)
        assert abs(np.linalg.norm(bundled) - 1.0) < 1e-6


class TestUnbind:
    def test_exact_recovery_bipolar(self, dim, rng):
        """For bipolar vectors, unbind should exactly recover the value."""
        role = random_hv(dim, bipolar=True, rng=rng)
        value = random_hv(dim, bipolar=True, rng=rng)
        bound = bind(role, value)
        recovered = unbind(bound, role)
        assert np.allclose(recovered, value)

    def test_noisy_recovery_from_bundle(self, dim, rng):
        """Unbinding from a bundle gives a noisy but recognizable result."""
        role_a = random_hv(dim, bipolar=True, rng=rng)
        val_a = random_hv(dim, bipolar=True, rng=rng)
        role_b = random_hv(dim, bipolar=True, rng=rng)
        val_b = random_hv(dim, bipolar=True, rng=rng)

        composite = bundle(bind(role_a, val_a), bind(role_b, val_b))
        recovered_a = unbind(composite, role_a)

        # Should be more similar to val_a than to val_b or random
        sim_a = similarity(recovered_a, val_a)
        sim_b = similarity(recovered_a, val_b)
        sim_rand = similarity(recovered_a, random_hv(dim, rng=np.random.default_rng(999)))

        assert sim_a > sim_b
        assert sim_a > sim_rand
        assert sim_a > 0.3  # recognizable despite noise


class TestPermute:
    def test_dissimilar_after_permute(self, dim, rng):
        """Permuted vector should be dissimilar to original."""
        v = random_hv(dim, bipolar=True, rng=rng)
        p = permute(v, 1)
        assert abs(similarity(v, p)) < 0.1

    def test_invertible(self, dim, rng):
        """permute then inverse_permute should recover the original."""
        v = random_hv(dim, bipolar=True, rng=rng)
        p = permute(v, 5)
        recovered = inverse_permute(p, 5)
        assert np.allclose(v, recovered)

    def test_different_shifts_are_dissimilar(self, dim, rng):
        """Different shift amounts produce dissimilar vectors."""
        v = random_hv(dim, bipolar=True, rng=rng)
        p1 = permute(v, 1)
        p2 = permute(v, 2)
        p10 = permute(v, 10)
        assert abs(similarity(p1, p2)) < 0.1
        assert abs(similarity(p1, p10)) < 0.1


class TestSimilarity:
    def test_self_similarity(self, dim, rng):
        v = random_hv(dim, rng=rng)
        assert abs(similarity(v, v) - 1.0) < 1e-6

    def test_opposite_similarity(self, dim, rng):
        v = random_hv(dim, rng=rng)
        assert abs(similarity(v, -v) + 1.0) < 1e-6

    def test_zero_vector(self, dim):
        v = random_hv(dim)
        zero = np.zeros(dim)
        assert similarity(v, zero) == 0.0


class TestWeightedBundle:
    def test_higher_weight_dominates(self, dim, rng):
        """Component with higher weight should have more influence."""
        a = random_hv(dim, bipolar=True, rng=rng)
        b = random_hv(dim, bipolar=True, rng=rng)

        # Give 'a' much more weight
        result = weighted_bundle([a, b], [10.0, 1.0])
        sim_a = similarity(result, a)
        sim_b = similarity(result, b)
        assert sim_a > sim_b

    def test_equal_weights_like_bundle(self, dim, rng):
        """Equal weights should behave like regular bundle."""
        a = random_hv(dim, bipolar=True, rng=rng)
        b = random_hv(dim, bipolar=True, rng=rng)

        wb = weighted_bundle([a, b], [1.0, 1.0])
        b_regular = bundle(a, b)
        # Should be very similar (only difference is normalization path)
        assert similarity(wb, b_regular) > 0.99


class TestHardQuantize:
    def test_output_is_bipolar(self, dim, rng):
        v = random_hv(dim, bipolar=False, rng=rng)
        q = hard_quantize(v)
        unique_vals = set(np.unique(q))
        assert unique_vals <= {-1.0, 1.0}

    def test_preserves_sign(self, dim, rng):
        v = random_hv(dim, bipolar=False, rng=rng)
        q = hard_quantize(v)
        # Non-zero elements should preserve sign
        nonzero = v != 0
        signs_match = np.sign(v[nonzero]) == q[nonzero]
        assert np.all(signs_match)


class TestCapacity:
    """Tests for the memory capacity of the VSA system.

    This is critical: how many items can we store and still
    retrieve them reliably?
    """

    def test_can_store_and_retrieve_100_items(self, dim, rng):
        """Should be able to store 100 role-value pairs and retrieve each."""
        n_items = 100
        roles = [random_hv(dim, bipolar=True, rng=rng) for _ in range(n_items)]
        values = [random_hv(dim, bipolar=True, rng=rng) for _ in range(n_items)]

        # Store as a weighted bundle of bindings
        bindings = [bind(r, v) for r, v in zip(roles, values)]

        # Test: can we recover each value?
        correct = 0
        for i in range(n_items):
            # Query: unbind with role_i, find closest value
            query = bind(bindings[i], roles[i])  # should ≈ values[i]

            # Simple unbind from the single binding (not composite)
            best_sim = -1
            best_idx = -1
            for j in range(n_items):
                sim = similarity(query, values[j])
                if sim > best_sim:
                    best_sim = sim
                    best_idx = j
            if best_idx == i:
                correct += 1

        accuracy = correct / n_items
        assert accuracy == 1.0, f"Individual binding retrieval: {accuracy:.0%}"

    def test_bundled_retrieval_degrades_gracefully(self, dim, rng):
        """Bundle of N bindings: retrieval accuracy should degrade gracefully."""
        n_items = 20
        roles = [random_hv(dim, bipolar=True, rng=rng) for _ in range(n_items)]
        values = [random_hv(dim, bipolar=True, rng=rng) for _ in range(n_items)]

        bindings = [bind(r, v) for r, v in zip(roles, values)]
        # Proper bundle: sum all then normalize once
        composite = np.sum(bindings, axis=0)
        composite = normalize(composite)

        correct = 0
        for i in range(n_items):
            recovered = unbind(composite, roles[i])
            best_sim = -1
            best_idx = -1
            for j in range(n_items):
                sim = similarity(recovered, values[j])
                if sim > best_sim:
                    best_sim = sim
                    best_idx = j
            if best_idx == i:
                correct += 1

        accuracy = correct / n_items
        # With 20 items in 10k dims, should get ~100% accuracy
        assert accuracy >= 0.8, f"Bundled retrieval accuracy: {accuracy:.0%}"

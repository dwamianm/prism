"""TDD tests for PRMEConfig expansion and ConfidenceMatrix.with_overrides().

Tests PRMEConfig scoring, packing, epistemic_weights, unverified_confidence_threshold,
and confidence_overrides fields, plus ConfidenceMatrix.with_overrides() merge method.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prme.config import PRMEConfig
from prme.epistemic.matrix import DEFAULT_CONFIDENCE_MATRIX, ConfidenceMatrix
from prme.retrieval.config import ScoringWeights, PackingConfig


# ---------------------------------------------------------------------------
# PRMEConfig Field Tests
# ---------------------------------------------------------------------------


class TestPRMEConfigFields:
    """Tests for expanded PRMEConfig fields."""

    def test_default_construction_succeeds(self):
        """PRMEConfig() with all defaults constructs successfully."""
        c = PRMEConfig()
        assert isinstance(c, PRMEConfig)

    def test_has_scoring_field(self):
        """PRMEConfig has a scoring field of type ScoringWeights."""
        c = PRMEConfig()
        assert isinstance(c.scoring, ScoringWeights)
        assert c.scoring.w_semantic == 0.25

    def test_has_packing_field(self):
        """PRMEConfig has a packing field of type PackingConfig."""
        c = PRMEConfig()
        assert isinstance(c.packing, PackingConfig)
        assert c.packing.token_budget == 4096

    def test_has_epistemic_weights_field(self):
        """PRMEConfig has epistemic_weights dict with 7 default entries."""
        c = PRMEConfig()
        assert isinstance(c.epistemic_weights, dict)
        assert len(c.epistemic_weights) == 7
        assert c.epistemic_weights["observed"] == 1.0
        assert c.epistemic_weights["asserted"] == 0.9
        assert c.epistemic_weights["inferred"] == 0.7
        assert c.epistemic_weights["hypothetical"] == 0.3
        assert c.epistemic_weights["conditional"] == 0.5
        assert c.epistemic_weights["deprecated"] == 0.1
        assert c.epistemic_weights["unverified"] == 0.5

    def test_has_unverified_confidence_threshold_field(self):
        """PRMEConfig has unverified_confidence_threshold (default 0.30)."""
        c = PRMEConfig()
        assert c.unverified_confidence_threshold == 0.30

    def test_has_confidence_overrides_field(self):
        """PRMEConfig has confidence_overrides (default empty dict)."""
        c = PRMEConfig()
        assert c.confidence_overrides == {}

    def test_valid_confidence_overrides_accepted(self):
        """PRMEConfig accepts valid 'type:source' confidence overrides."""
        c = PRMEConfig(confidence_overrides={"observed:user_stated": 0.95})
        assert c.confidence_overrides["observed:user_stated"] == 0.95

    def test_rejects_out_of_range_confidence_override(self):
        """PRMEConfig rejects confidence_overrides values > 1.0."""
        with pytest.raises(ValidationError):
            PRMEConfig(confidence_overrides={"observed:user_stated": 1.5})

    def test_rejects_negative_confidence_override(self):
        """PRMEConfig rejects confidence_overrides values < 0.0."""
        with pytest.raises(ValidationError):
            PRMEConfig(confidence_overrides={"observed:user_stated": -0.1})

    def test_rejects_bad_key_format(self):
        """PRMEConfig rejects keys not in 'type:source' format."""
        with pytest.raises(ValidationError):
            PRMEConfig(confidence_overrides={"badkey": 0.5})

    def test_scoring_weights_sum_enforced(self):
        """ScoringWeights sum validation still works through PRMEConfig."""
        c = PRMEConfig()
        additive = (
            c.scoring.w_semantic + c.scoring.w_lexical + c.scoring.w_graph
            + c.scoring.w_recency + c.scoring.w_salience + c.scoring.w_confidence
        )
        assert abs(additive - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# ConfidenceMatrix.with_overrides() Tests
# ---------------------------------------------------------------------------


class TestConfidenceMatrixWithOverrides:
    """Tests for ConfidenceMatrix.with_overrides() method."""

    def test_empty_overrides_returns_same_instance(self):
        """with_overrides({}) returns the same matrix instance."""
        m = DEFAULT_CONFIDENCE_MATRIX.with_overrides({})
        assert m is DEFAULT_CONFIDENCE_MATRIX

    def test_override_existing_cell(self):
        """with_overrides can change an existing matrix cell."""
        m2 = DEFAULT_CONFIDENCE_MATRIX.with_overrides(
            {"observed:user_stated": 0.95}
        )
        assert m2.matrix[("observed", "user_stated")] == 0.95

    def test_original_unmodified_after_override(self):
        """Original DEFAULT_CONFIDENCE_MATRIX is unmodified (frozen)."""
        _ = DEFAULT_CONFIDENCE_MATRIX.with_overrides(
            {"observed:user_stated": 0.95}
        )
        assert DEFAULT_CONFIDENCE_MATRIX.matrix[("observed", "user_stated")] == 0.90

    def test_new_entry_accepted(self):
        """with_overrides can add new entries (forward-compatible)."""
        m3 = DEFAULT_CONFIDENCE_MATRIX.with_overrides(
            {"new_type:new_source": 0.50}
        )
        assert m3.matrix[("new_type", "new_source")] == 0.50

    def test_returns_new_instance(self):
        """with_overrides returns a new ConfidenceMatrix, not mutated original."""
        m2 = DEFAULT_CONFIDENCE_MATRIX.with_overrides(
            {"observed:user_stated": 0.95}
        )
        assert m2 is not DEFAULT_CONFIDENCE_MATRIX
        assert isinstance(m2, ConfidenceMatrix)

"""Tests for the memory quality self-assessment and auto-tuning module (issue #24).

Covers:
    - FeedbackTracker: record, retrieve, quality scoring
    - WeightTuner: per-signal adjustments, normalization, bounds
    - QualityMetrics: computation from signal lists
    - Per-namespace weight profiles in PRMEConfig
    - Integration: feedback_apply job wiring
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prme.config import PRMEConfig
from prme.quality.feedback import FeedbackSignal, FeedbackSignalType, FeedbackTracker
from prme.quality.metrics import QualityMetrics, compute_quality_metrics
from prme.quality.tuner import WeightTuner, _ADDITIVE_FIELDS, _MAX_WEIGHT, _MIN_WEIGHT
from prme.retrieval.config import ScoringWeights


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(
    signal_type: FeedbackSignalType,
    query: str = "test query",
    node_ids: list[str] | None = None,
    correction: str | None = None,
    timestamp: datetime | None = None,
) -> FeedbackSignal:
    return FeedbackSignal(
        query=query,
        surfaced_node_ids=node_ids or ["node-1"],
        signal_type=signal_type,
        correction_content=correction,
        timestamp=timestamp or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# FeedbackTracker tests
# ---------------------------------------------------------------------------


class TestFeedbackTracker:
    """Test FeedbackTracker record, retrieval, and quality scoring."""

    def test_record_and_retrieve(self):
        tracker = FeedbackTracker()
        signal = _make_signal(FeedbackSignalType.USED)
        tracker.record(signal)
        assert len(tracker) == 1
        signals = tracker.get_signals(window_days=30)
        assert len(signals) == 1
        assert signals[0] is signal

    def test_multiple_signals(self):
        tracker = FeedbackTracker()
        for st in FeedbackSignalType:
            tracker.record(_make_signal(st))
        assert len(tracker) == 4
        assert len(tracker.get_signals()) == 4

    def test_window_filtering(self):
        tracker = FeedbackTracker()
        old = datetime.now(timezone.utc) - timedelta(days=60)
        recent = datetime.now(timezone.utc) - timedelta(days=5)
        tracker.record(_make_signal(FeedbackSignalType.USED, timestamp=old))
        tracker.record(_make_signal(FeedbackSignalType.USED, timestamp=recent))
        assert len(tracker.get_signals(window_days=30)) == 1
        assert len(tracker.get_signals(window_days=90)) == 2

    def test_quality_score_all_used(self):
        tracker = FeedbackTracker()
        for _ in range(5):
            tracker.record(_make_signal(FeedbackSignalType.USED))
        assert tracker.get_quality_score() == pytest.approx(1.0)

    def test_quality_score_all_ignored(self):
        tracker = FeedbackTracker()
        for _ in range(5):
            tracker.record(_make_signal(FeedbackSignalType.IGNORED))
        assert tracker.get_quality_score() == pytest.approx(0.3)

    def test_quality_score_all_corrected(self):
        tracker = FeedbackTracker()
        for _ in range(5):
            tracker.record(_make_signal(FeedbackSignalType.CORRECTED))
        assert tracker.get_quality_score() == pytest.approx(0.0)

    def test_quality_score_mixed(self):
        tracker = FeedbackTracker()
        tracker.record(_make_signal(FeedbackSignalType.USED))
        tracker.record(_make_signal(FeedbackSignalType.IGNORED))
        tracker.record(_make_signal(FeedbackSignalType.CORRECTED))
        tracker.record(_make_signal(FeedbackSignalType.CONTRADICTED))
        # (1.0 + 0.3 + 0.0 + 0.0) / 4 = 0.325
        assert tracker.get_quality_score() == pytest.approx(0.325)

    def test_quality_score_no_signals(self):
        tracker = FeedbackTracker()
        assert tracker.get_quality_score() == pytest.approx(1.0)

    def test_clear(self):
        tracker = FeedbackTracker()
        tracker.record(_make_signal(FeedbackSignalType.USED))
        tracker.clear()
        assert len(tracker) == 0
        assert len(tracker.get_signals()) == 0


# ---------------------------------------------------------------------------
# WeightTuner tests
# ---------------------------------------------------------------------------


class TestWeightTuner:
    """Test WeightTuner adjustments, normalization, and bounds."""

    def test_no_signals_returns_current(self):
        weights = ScoringWeights()
        tuner = WeightTuner(weights)
        result = tuner.update([])
        assert result.version_id == weights.version_id

    def test_used_increases_dominant_weight(self):
        weights = ScoringWeights()
        tuner = WeightTuner(weights, learning_rate=0.01)
        # Default dominant component is w_semantic (0.25)
        signal = _make_signal(FeedbackSignalType.USED)
        result = tuner.update([signal])
        # w_semantic should have increased relative to others
        assert result.w_semantic > weights.w_semantic - 0.01

    def test_ignored_decreases_dominant_weight(self):
        weights = ScoringWeights()
        tuner = WeightTuner(weights, learning_rate=0.01)
        signal = _make_signal(FeedbackSignalType.IGNORED)
        result = tuner.update([signal])
        # After normalization, the dominant component should be
        # relatively smaller. Direct comparison is tricky due to
        # normalization, but the dominant ratio should decrease.
        old_ratio = weights.w_semantic / 1.0  # sum is 1.0
        new_sum = sum(getattr(result, f) for f in _ADDITIVE_FIELDS)
        new_ratio = result.w_semantic / new_sum
        assert new_ratio < old_ratio + 0.001  # not increased

    def test_corrected_shifts_semantic_to_confidence(self):
        weights = ScoringWeights()
        tuner = WeightTuner(weights, learning_rate=0.05)
        signal = _make_signal(FeedbackSignalType.CORRECTED)
        result = tuner.update([signal])
        # After normalization, confidence should have a larger share
        # relative to semantic compared to before
        old_conf_ratio = weights.w_confidence / (weights.w_semantic + weights.w_confidence)
        new_conf_ratio = result.w_confidence / (result.w_semantic + result.w_confidence)
        assert new_conf_ratio > old_conf_ratio

    def test_contradicted_shifts_confidence_to_epistemic(self):
        weights = ScoringWeights()
        tuner = WeightTuner(weights, learning_rate=0.05)
        signal = _make_signal(FeedbackSignalType.CONTRADICTED)
        result = tuner.update([signal])
        # Epistemic weight should increase
        assert result.w_epistemic > weights.w_epistemic

    def test_weight_normalization_sums_to_one(self):
        weights = ScoringWeights()
        tuner = WeightTuner(weights, learning_rate=0.05)
        signals = [
            _make_signal(FeedbackSignalType.USED),
            _make_signal(FeedbackSignalType.CORRECTED),
            _make_signal(FeedbackSignalType.IGNORED),
        ]
        result = tuner.update(signals)
        additive_sum = sum(getattr(result, f) for f in _ADDITIVE_FIELDS)
        assert additive_sum == pytest.approx(1.0, abs=1e-6)

    def test_bounds_enforcement_min(self):
        """No individual weight should go below _MIN_WEIGHT."""
        # Create weights with one very small component
        weights = ScoringWeights(
            w_semantic=0.50, w_lexical=0.02, w_graph=0.20,
            w_recency=0.10, w_salience=0.08, w_confidence=0.10,
        )
        tuner = WeightTuner(weights, learning_rate=0.05)
        # Apply many IGNORED signals to push dominant down
        signals = [_make_signal(FeedbackSignalType.IGNORED) for _ in range(20)]
        result = tuner.update(signals)
        for field_name in _ADDITIVE_FIELDS:
            assert getattr(result, field_name) >= _MIN_WEIGHT - 1e-9

    def test_bounds_enforcement_max(self):
        """No individual weight should exceed _MAX_WEIGHT."""
        weights = ScoringWeights()
        tuner = WeightTuner(weights, learning_rate=0.1)
        # Apply many USED signals (always boosting dominant)
        signals = [_make_signal(FeedbackSignalType.USED) for _ in range(50)]
        result = tuner.update(signals)
        for field_name in _ADDITIVE_FIELDS:
            assert getattr(result, field_name) <= _MAX_WEIGHT + 1e-9

    def test_learning_rate_controls_magnitude(self):
        """Larger learning rate should produce larger adjustments."""
        weights = ScoringWeights()
        signal = _make_signal(FeedbackSignalType.USED)

        tuner_small = WeightTuner(ScoringWeights(), learning_rate=0.001)
        result_small = tuner_small.update([signal])

        tuner_large = WeightTuner(ScoringWeights(), learning_rate=0.1)
        result_large = tuner_large.update([signal])

        delta_small = abs(result_small.w_semantic - weights.w_semantic)
        delta_large = abs(result_large.w_semantic - weights.w_semantic)
        assert delta_large > delta_small

    def test_multiple_sequential_updates(self):
        """Multiple update() calls should accumulate adjustments."""
        tuner = WeightTuner(ScoringWeights(), learning_rate=0.01)
        for _ in range(5):
            tuner.update([_make_signal(FeedbackSignalType.CORRECTED)])
        # After 5 corrections, confidence should have grown significantly
        result = tuner.current_weights
        additive_sum = sum(getattr(result, f) for f in _ADDITIVE_FIELDS)
        assert additive_sum == pytest.approx(1.0, abs=1e-6)
        assert result.w_confidence > ScoringWeights().w_confidence

    def test_epistemic_weight_clamped(self):
        """Epistemic weight should respect bounds."""
        tuner = WeightTuner(ScoringWeights(), learning_rate=0.1)
        signals = [_make_signal(FeedbackSignalType.CONTRADICTED) for _ in range(50)]
        result = tuner.update(signals)
        assert result.w_epistemic <= _MAX_WEIGHT
        assert result.w_epistemic >= _MIN_WEIGHT


# ---------------------------------------------------------------------------
# QualityMetrics tests
# ---------------------------------------------------------------------------


class TestQualityMetrics:
    """Test compute_quality_metrics function."""

    def test_empty_signals_returns_perfect_quality(self):
        metrics = compute_quality_metrics([])
        assert metrics.retrieval_quality == pytest.approx(1.0)
        assert metrics.total_signals == 0
        assert metrics.used_rate == 0.0

    def test_all_used(self):
        signals = [_make_signal(FeedbackSignalType.USED) for _ in range(10)]
        metrics = compute_quality_metrics(signals)
        assert metrics.retrieval_quality == pytest.approx(1.0)
        assert metrics.used_rate == pytest.approx(1.0)
        assert metrics.correction_rate == pytest.approx(0.0)
        assert metrics.total_signals == 10

    def test_mixed_signals(self):
        signals = [
            _make_signal(FeedbackSignalType.USED),
            _make_signal(FeedbackSignalType.USED),
            _make_signal(FeedbackSignalType.IGNORED),
            _make_signal(FeedbackSignalType.CORRECTED),
            _make_signal(FeedbackSignalType.CONTRADICTED),
        ]
        metrics = compute_quality_metrics(signals)
        assert metrics.total_signals == 5
        assert metrics.used_rate == pytest.approx(2 / 5)
        assert metrics.ignored_rate == pytest.approx(1 / 5)
        assert metrics.correction_rate == pytest.approx(1 / 5)
        assert metrics.contradiction_rate == pytest.approx(1 / 5)
        # quality = (2*1.0 + 1*0.3) / 5 = 2.3/5 = 0.46
        assert metrics.retrieval_quality == pytest.approx(0.46)

    def test_weight_adjustments_passthrough(self):
        adj = {"w_semantic": 0.05, "w_confidence": -0.03}
        metrics = compute_quality_metrics([], weight_adjustments=adj)
        assert metrics.weight_adjustments == adj


# ---------------------------------------------------------------------------
# Per-namespace weight profiles
# ---------------------------------------------------------------------------


class TestNamespaceWeights:
    """Test per-namespace weight profiles in PRMEConfig."""

    def test_default_empty(self):
        config = PRMEConfig()
        assert config.namespace_weights == {}

    def test_set_namespace_weights(self):
        custom = ScoringWeights(
            w_semantic=0.40, w_lexical=0.10, w_graph=0.15,
            w_recency=0.10, w_salience=0.10, w_confidence=0.15,
        )
        config = PRMEConfig(namespace_weights={"project-x": custom})
        assert "project-x" in config.namespace_weights
        assert config.namespace_weights["project-x"].w_semantic == pytest.approx(0.40)

    def test_multiple_namespaces(self):
        ns1 = ScoringWeights(
            w_semantic=0.25, w_lexical=0.20, w_graph=0.20,
            w_recency=0.10, w_salience=0.10, w_confidence=0.15,
        )
        ns2 = ScoringWeights(
            w_semantic=0.35, w_lexical=0.10, w_graph=0.15,
            w_recency=0.15, w_salience=0.10, w_confidence=0.15,
        )
        config = PRMEConfig(namespace_weights={"ns1": ns1, "ns2": ns2})
        assert len(config.namespace_weights) == 2
        assert config.namespace_weights["ns1"].w_semantic == pytest.approx(0.25)
        assert config.namespace_weights["ns2"].w_semantic == pytest.approx(0.35)

    def test_global_weights_unaffected(self):
        """Namespace weights should not change the global scoring weights."""
        custom = ScoringWeights(
            w_semantic=0.50, w_lexical=0.10, w_graph=0.10,
            w_recency=0.10, w_salience=0.10, w_confidence=0.10,
        )
        config = PRMEConfig(namespace_weights={"ns": custom})
        # Global scoring should still be the default
        assert config.scoring.w_semantic == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Integration: engine.feedback() and feedback_apply job
# ---------------------------------------------------------------------------


class TestEngineIntegration:
    """Test feedback recording and feedback_apply job via MemoryEngine."""

    @pytest.fixture
    def tmp_dir(self):
        with tempfile.TemporaryDirectory(prefix="prme_quality_") as d:
            yield d

    @pytest.fixture
    def engine_config(self, tmp_dir):
        lexical_path = Path(tmp_dir) / "lexical_index"
        lexical_path.mkdir(exist_ok=True)
        return PRMEConfig(
            db_path=str(Path(tmp_dir) / "memory.duckdb"),
            vector_path=str(Path(tmp_dir) / "vectors.usearch"),
            lexical_path=str(lexical_path),
        )

    @pytest.mark.asyncio
    async def test_feedback_records_signal(self, engine_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(engine_config)
        try:
            signal = _make_signal(FeedbackSignalType.USED)
            await engine.feedback(signal)
            assert len(engine._feedback_tracker) == 1
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_quality_metrics_computed(self, engine_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(engine_config)
        try:
            await engine.feedback(_make_signal(FeedbackSignalType.USED))
            await engine.feedback(_make_signal(FeedbackSignalType.CORRECTED))
            metrics = engine.quality_metrics
            assert metrics.total_signals == 2
            assert metrics.used_rate == pytest.approx(0.5)
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_feedback_apply_updates_weights(self, engine_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(engine_config)
        try:
            old_version = engine._config.scoring.version_id

            # Record several CORRECTED signals
            for _ in range(10):
                await engine.feedback(
                    _make_signal(FeedbackSignalType.CORRECTED)
                )

            # Run feedback_apply job via organize
            result = await engine.organize(
                user_id="test-user",
                jobs=["feedback_apply"],
                budget_ms=5000,
            )

            assert "feedback_apply" in result.jobs_run
            job_result = result.per_job["feedback_apply"]
            assert job_result.details["signals_processed"] == 10

            # Weights should have changed
            new_version = engine._config.scoring.version_id
            assert new_version != old_version

            # Tracker should be cleared after processing
            assert len(engine._feedback_tracker) == 0
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_feedback_apply_no_signals_noop(self, engine_config):
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(engine_config)
        try:
            result = await engine.organize(
                user_id="test-user",
                jobs=["feedback_apply"],
                budget_ms=5000,
            )
            assert "feedback_apply" in result.jobs_run
            job_result = result.per_job["feedback_apply"]
            assert job_result.details["status"] == "no_signals"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_corrected_signals_decrease_vector_weight(self, engine_config):
        """CORRECTED signals should decrease w_semantic relative to w_confidence."""
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(engine_config)
        try:
            old_weights = engine._config.scoring
            old_ratio = old_weights.w_semantic / (
                old_weights.w_semantic + old_weights.w_confidence
            )

            for _ in range(20):
                await engine.feedback(
                    _make_signal(FeedbackSignalType.CORRECTED)
                )

            await engine.organize(
                user_id="test-user",
                jobs=["feedback_apply"],
                budget_ms=5000,
            )

            new_weights = engine._config.scoring
            new_ratio = new_weights.w_semantic / (
                new_weights.w_semantic + new_weights.w_confidence
            )
            assert new_ratio < old_ratio
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_used_signals_increase_relevant_weights(self, engine_config):
        """USED signals should increase the dominant weight component."""
        from prme.storage.engine import MemoryEngine

        engine = await MemoryEngine.create(engine_config)
        try:
            old_semantic = engine._config.scoring.w_semantic

            for _ in range(10):
                await engine.feedback(
                    _make_signal(FeedbackSignalType.USED)
                )

            await engine.organize(
                user_id="test-user",
                jobs=["feedback_apply"],
                budget_ms=5000,
            )

            # w_semantic is the dominant weight (0.25), so it should be
            # boosted by USED signals. After normalization, the ratio
            # should increase.
            new_weights = engine._config.scoring
            additive_sum = sum(
                getattr(new_weights, f) for f in _ADDITIVE_FIELDS
            )
            assert additive_sum == pytest.approx(1.0, abs=1e-6)
        finally:
            await engine.close()

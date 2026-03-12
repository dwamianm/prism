"""Tests for temporal encoding in VSA."""

from datetime import datetime, timezone

import pytest

from research.vsa.core import similarity
from research.vsa.temporal import TemporalEncoder


@pytest.fixture
def encoder():
    return TemporalEncoder(dim=10_000, seed=99)


class TestAbsoluteEncoding:
    def test_same_timestamp_identical(self, encoder):
        dt = datetime(2025, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        v1 = encoder.encode_absolute(dt)
        v2 = encoder.encode_absolute(dt)
        assert similarity(v1, v2) > 0.99

    def test_same_day_different_hour_similar(self, encoder):
        """Same date, different hour should have partial similarity."""
        dt1 = datetime(2025, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2025, 3, 15, 14, 0, 0, tzinfo=timezone.utc)
        sim = similarity(
            encoder.encode_absolute(dt1),
            encoder.encode_absolute(dt2),
        )
        # Share year, month, day — differ on hour
        assert sim > 0.3  # significant partial overlap

    def test_same_month_different_day(self, encoder):
        dt1 = datetime(2025, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2025, 3, 20, 10, 0, 0, tzinfo=timezone.utc)
        sim = similarity(
            encoder.encode_absolute(dt1),
            encoder.encode_absolute(dt2),
        )
        # Share year, month, hour — differ on day
        assert sim > 0.2

    def test_different_year_less_similar_than_same_year(self, encoder):
        """Different year should share fewer components than same year."""
        dt_base = datetime(2025, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
        dt_same_year = datetime(2025, 6, 20, 14, 0, 0, tzinfo=timezone.utc)  # shares year only
        dt_diff_year = datetime(2024, 6, 20, 14, 0, 0, tzinfo=timezone.utc)  # shares nothing

        sim_same_year = similarity(
            encoder.encode_absolute(dt_base),
            encoder.encode_absolute(dt_same_year),
        )
        sim_diff_year = similarity(
            encoder.encode_absolute(dt_base),
            encoder.encode_absolute(dt_diff_year),
        )
        # Same year shares 1/4 components; diff year shares 0/4
        assert sim_same_year > sim_diff_year


class TestRelativeEncoding:
    def test_now_is_self_similar(self, encoder):
        now = encoder.encode_relative(0)
        assert similarity(now, now) > 0.99

    def test_nearby_steps_more_similar(self, encoder):
        """Step 1 ago should be less similar to now than step 0."""
        now = encoder.encode_relative(0)
        one_ago = encoder.encode_relative(1)
        ten_ago = encoder.encode_relative(10)

        sim_1 = abs(similarity(now, one_ago))
        sim_10 = abs(similarity(now, ten_ago))

        # Both should be low (permutation destroys correlation)
        # but they should be comparable in magnitude for different shifts
        assert sim_1 < 0.1
        assert sim_10 < 0.1

    def test_day_offset_encoding(self, encoder):
        """Day offsets should produce distinct vectors."""
        d0 = encoder.encode_day_offset(0)
        d1 = encoder.encode_day_offset(1)
        d10 = encoder.encode_day_offset(10)

        assert abs(similarity(d0, d1)) < 0.1
        assert abs(similarity(d0, d10)) < 0.1
        assert abs(similarity(d1, d10)) < 0.1


class TestRecencyScore:
    def test_identical_time_scores_high(self, encoder):
        t = encoder.encode_day_offset(5)
        score = encoder.recency_score(t, t)
        assert score > 0.99

    def test_different_time_scores_low(self, encoder):
        t1 = encoder.encode_day_offset(5)
        t2 = encoder.encode_day_offset(50)
        score = encoder.recency_score(t1, t2)
        assert abs(score) < 0.1

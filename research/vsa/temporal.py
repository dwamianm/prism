"""Temporal encoding for VSA memory.

Encodes time as hypervectors that can be bound to memories, enabling
temporal queries like "what happened recently" or "what was true in March".

Two encoding strategies:
1. **Absolute**: Each timestamp gets a unique vector via date component binding
2. **Relative**: Time deltas encoded as permutation chains (closer = more similar)

The key insight: time vectors for nearby moments should have higher similarity
than distant moments, creating a natural temporal gradient for retrieval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

from research.vsa.core import HV, DEFAULT_DIM, bind, bundle, permute, similarity, random_hv


class TemporalEncoder:
    """Encodes temporal information as hypervectors.

    Uses a hierarchical binding approach:
    - Year, month, day, hour each get their own "slot" vectors
    - A timestamp = bind(YEAR_SLOT, year_val) + bind(MONTH_SLOT, month_val) + ...
    - Nearby timestamps share components, so they have natural similarity

    For relative time, uses permutation chains:
    - "now" = base vector
    - "1 step ago" = permute(base, 1)
    - "2 steps ago" = permute(base, 2)
    - Similarity decreases with distance (permutation destroys correlation)
    """

    def __init__(self, dim: int = DEFAULT_DIM, seed: int = 99):
        """Initialize temporal encoder.

        Args:
            dim: Hypervector dimensionality.
            seed: RNG seed for reproducible slot vectors.
        """
        self.dim = dim
        self._rng = np.random.default_rng(seed)

        # Slot vectors for hierarchical time encoding
        self._year_slot = random_hv(dim, bipolar=True, rng=self._rng)
        self._month_slot = random_hv(dim, bipolar=True, rng=self._rng)
        self._day_slot = random_hv(dim, bipolar=True, rng=self._rng)
        self._hour_slot = random_hv(dim, bipolar=True, rng=self._rng)

        # Value vectors for each possible value (lazy-generated)
        self._year_vecs: dict[int, HV] = {}
        self._month_vecs: dict[int, HV] = {}
        self._day_vecs: dict[int, HV] = {}
        self._hour_vecs: dict[int, HV] = {}

        # Base vector for relative time encoding
        self._time_base = random_hv(dim, bipolar=True, rng=self._rng)

        # Pre-generate month and day vectors (small finite sets)
        for m in range(1, 13):
            self._month_vecs[m] = random_hv(dim, bipolar=True, rng=self._rng)
        for d in range(1, 32):
            self._day_vecs[d] = random_hv(dim, bipolar=True, rng=self._rng)
        for h in range(24):
            self._hour_vecs[h] = random_hv(dim, bipolar=True, rng=self._rng)

    def _get_year_vec(self, year: int) -> HV:
        """Get or create the vector for a year value."""
        if year not in self._year_vecs:
            # Use a year-specific seed for determinism
            year_rng = np.random.default_rng(self._rng.integers(0, 2**31) + year)
            self._year_vecs[year] = random_hv(self.dim, bipolar=True, rng=year_rng)
        return self._year_vecs[year]

    def encode_absolute(self, dt: datetime) -> HV:
        """Encode an absolute timestamp as a hypervector.

        The encoding binds each time component with its slot vector,
        then bundles them. Timestamps sharing components (same month,
        same day) will have partial similarity.

        Args:
            dt: Datetime to encode.

        Returns:
            Hypervector representing the timestamp.
        """
        year_bound = bind(self._year_slot, self._get_year_vec(dt.year))
        month_bound = bind(self._month_slot, self._month_vecs[dt.month])
        day_bound = bind(self._day_slot, self._day_vecs[dt.day])
        hour_bound = bind(self._hour_slot, self._hour_vecs[dt.hour])

        return bundle(year_bound, month_bound, day_bound, hour_bound)

    def encode_relative(self, steps_ago: int) -> HV:
        """Encode a relative time position via permutation.

        "Now" (steps_ago=0) is the base vector. Each step back
        applies one permutation, making it progressively less similar
        to "now". This creates a natural recency gradient.

        The similarity between "now" and "k steps ago" decays
        approximately as 1/sqrt(dim) for large k (practically zero
        after ~dim/10 steps).

        Args:
            steps_ago: Number of time steps in the past (0 = now).

        Returns:
            Hypervector for this relative time position.
        """
        return permute(self._time_base, steps_ago)

    def encode_day_offset(self, day: int, reference_day: int = 0) -> HV:
        """Encode a day number relative to a reference point.

        Useful for simulation scenarios where events happen on
        numbered days. Uses permutation from a base, so nearby
        days have higher similarity.

        Args:
            day: The day number to encode.
            reference_day: The reference day (default 0).

        Returns:
            Hypervector for this day offset.
        """
        offset = day - reference_day
        return permute(self._time_base, offset)

    def recency_score(self, memory_time: HV, query_time: HV) -> float:
        """Compute recency-based similarity between two time vectors.

        Higher values mean the memory is more temporally relevant
        to the query time.

        Args:
            memory_time: Time vector of the stored memory.
            query_time: Time vector of the query.

        Returns:
            Similarity score in [-1, 1].
        """
        return similarity(memory_time, query_time)

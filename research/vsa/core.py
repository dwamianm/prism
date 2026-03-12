"""Core VSA operations for MAP (Multiply-Add-Permute) architecture.

Implements the fundamental algebraic operations on hyperdimensional vectors:
- bind: associative binding via element-wise multiplication
- bundle: superposition via element-wise addition + normalization
- unbind: inverse binding (same as bind for bipolar vectors)
- similarity: cosine similarity for retrieval
- permute: cyclic shift for sequence/order encoding

All operations work on numpy arrays of shape (d,) where d is the
hypervector dimension (default 10,000).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# Type alias for hyperdimensional vectors
HV = NDArray[np.float64]

# Default dimension — 10,000 is standard for MAP architecture.
# Higher dims = better capacity but more compute. 10k is the sweet spot
# for ~1000 stored items with reliable retrieval.
DEFAULT_DIM = 10_000


def random_hv(dim: int = DEFAULT_DIM, bipolar: bool = True, rng: np.random.Generator | None = None) -> HV:
    """Generate a random hypervector.

    Args:
        dim: Vector dimensionality.
        bipolar: If True, elements are {-1, +1}. If False, standard normal.
        rng: Optional random generator for reproducibility.

    Returns:
        Random hypervector of shape (dim,).
    """
    if rng is None:
        rng = np.random.default_rng()

    if bipolar:
        return rng.choice([-1.0, 1.0], size=dim).astype(np.float64)
    else:
        return rng.standard_normal(dim).astype(np.float64)


def bind(a: HV, b: HV) -> HV:
    """Bind two hypervectors via element-wise multiplication.

    Binding creates a new vector that is dissimilar to both inputs.
    Used to associate a role with a filler: bind(AGENT, alice) means
    "the agent is Alice".

    Properties:
    - Commutative: bind(a, b) == bind(b, a)
    - Self-inverse for bipolar: bind(a, a) ≈ identity
    - Distributes over bundle: bind(a, bundle(b, c)) ≈ bundle(bind(a,b), bind(a,c))

    Args:
        a: First hypervector.
        b: Second hypervector.

    Returns:
        Bound hypervector (element-wise product).
    """
    return a * b


def bundle(*vectors: HV) -> HV:
    """Bundle (superpose) multiple hypervectors via element-wise addition.

    Bundling creates a vector similar to all inputs — a "set" or "bag"
    of the component vectors. Used to compose structured memories:
    bundle(bind(AGENT, alice), bind(ACTION, run)) = a structured event.

    The result is normalized to unit length for consistent similarity scores.

    Args:
        *vectors: Two or more hypervectors to bundle.

    Returns:
        Bundled and normalized hypervector.

    Raises:
        ValueError: If fewer than 2 vectors provided.
    """
    if len(vectors) < 2:
        raise ValueError("bundle requires at least 2 vectors")

    result = np.sum(vectors, axis=0)
    return normalize(result)


def unbind(composite: HV, key: HV) -> HV:
    """Unbind a key from a composite to recover the associated value.

    For bipolar vectors, unbind is the same as bind (since x * x = 1).
    Given composite = bind(key, value), unbind(composite, key) ≈ value.

    In practice with bundled composites, the result is a noisy version
    of the target — you compare it against known vectors to find the
    best match.

    Args:
        composite: The composite hypervector.
        key: The key to unbind.

    Returns:
        Approximate recovered value vector.
    """
    return bind(composite, key)


def similarity(a: HV, b: HV) -> float:
    """Compute cosine similarity between two hypervectors.

    Returns a value in [-1, 1] where:
    - 1.0 means identical
    - 0.0 means orthogonal (unrelated)
    - -1.0 means opposite

    For random high-dimensional vectors, expected similarity ≈ 0
    with standard deviation ≈ 1/sqrt(dim).

    Args:
        a: First hypervector.
        b: Second hypervector.

    Returns:
        Cosine similarity score.
    """
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(np.dot(a, b) / (norm_a * norm_b))


def permute(v: HV, shifts: int = 1) -> HV:
    """Cyclically permute a hypervector.

    Permutation creates a vector dissimilar to the original but
    deterministically recoverable. Used for:
    - Sequence encoding: permute(v, i) for position i
    - Temporal encoding: permute(v, t) for time step t

    Args:
        v: Hypervector to permute.
        shifts: Number of positions to shift (positive = right).

    Returns:
        Permuted hypervector.
    """
    return np.roll(v, shifts)


def inverse_permute(v: HV, shifts: int = 1) -> HV:
    """Inverse of permute — shifts in the opposite direction.

    Args:
        v: Hypervector to un-permute.
        shifts: Number of positions that were originally shifted.

    Returns:
        Un-permuted hypervector.
    """
    return np.roll(v, -shifts)


def normalize(v: HV) -> HV:
    """Normalize a hypervector to unit length.

    Args:
        v: Hypervector to normalize.

    Returns:
        Unit-length hypervector, or zero vector if input is zero.
    """
    norm = np.linalg.norm(v)
    if norm == 0:
        return v
    return v / norm


def hard_quantize(v: HV) -> HV:
    """Quantize a real-valued vector to bipolar {-1, +1}.

    Used after bundling to snap back to the bipolar domain.
    This implements majority-rule: positive → +1, negative → -1.
    Zeros are randomly assigned.

    Args:
        v: Real-valued hypervector.

    Returns:
        Bipolar hypervector.
    """
    result = np.sign(v)
    # Handle zeros by random assignment
    zeros = result == 0
    if np.any(zeros):
        result[zeros] = np.random.default_rng().choice([-1.0, 1.0], size=int(np.sum(zeros)))
    return result


def weighted_bundle(vectors: list[HV], weights: list[float]) -> HV:
    """Bundle vectors with different weights.

    Allows some components to dominate the composite. Useful for
    recency-weighted memory where newer items get higher weight.

    Args:
        vectors: List of hypervectors.
        weights: Corresponding weights (higher = more influence).

    Returns:
        Weighted, bundled, and normalized hypervector.

    Raises:
        ValueError: If vectors and weights have different lengths.
    """
    if len(vectors) != len(weights):
        raise ValueError("vectors and weights must have same length")
    if len(vectors) == 0:
        raise ValueError("need at least 1 vector")

    result = np.zeros_like(vectors[0])
    for v, w in zip(vectors, weights):
        result += w * v
    return normalize(result)

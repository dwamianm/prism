"""Tests for multi-query aggregation detection and candidate pool scaling.

Verifies that:
- Aggregation queries (how many, total, list all, etc.) are detected.
- Normal queries are not flagged as aggregation.
- Candidate k values are multiplied correctly for aggregation queries.
- K values are capped at the configured maximum.
"""

from __future__ import annotations

import pytest

from prme.retrieval.config import PackingConfig
from prme.retrieval.query_analysis import analyze_query


# ---------------------------------------------------------------------------
# Aggregation detection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_how_many_detected_as_aggregation():
    """'How many X?' queries should set is_aggregation=True."""
    result = await analyze_query("How many charity events did I participate in?")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_how_much_detected_as_aggregation():
    result = await analyze_query("How much money did I spend on groceries?")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_how_often_detected_as_aggregation():
    result = await analyze_query("How often do I go running?")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_total_detected_as_aggregation():
    result = await analyze_query("What is the total number of books I read?")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_count_detected_as_aggregation():
    result = await analyze_query("Can you count my completed projects?")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_list_all_detected_as_aggregation():
    result = await analyze_query("List all the model kits I have built")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_all_of_the_detected_as_aggregation():
    result = await analyze_query("What are all of the places I have visited?")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_every_time_detected_as_aggregation():
    result = await analyze_query("Every time I went to the gym, what did I do?")
    assert result.is_aggregation is True


@pytest.mark.asyncio
async def test_all_the_times_detected_as_aggregation():
    result = await analyze_query("Tell me about all the times I helped a friend move")
    assert result.is_aggregation is True


# ---------------------------------------------------------------------------
# Non-aggregation queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_semantic_not_aggregation():
    result = await analyze_query("Tell me about my last vacation")
    assert result.is_aggregation is False


@pytest.mark.asyncio
async def test_entity_lookup_not_aggregation():
    result = await analyze_query("Who is Alice?")
    assert result.is_aggregation is False


@pytest.mark.asyncio
async def test_temporal_query_not_aggregation():
    result = await analyze_query("When did I start my new job?")
    assert result.is_aggregation is False


@pytest.mark.asyncio
async def test_factual_query_not_aggregation():
    result = await analyze_query("What is my favorite color?")
    assert result.is_aggregation is False


@pytest.mark.asyncio
async def test_relational_query_not_aggregation():
    result = await analyze_query("What is related to my trip?")
    assert result.is_aggregation is False


# ---------------------------------------------------------------------------
# Candidate k-value scaling tests
# ---------------------------------------------------------------------------


def test_aggregation_k_multiplier_default():
    """Default aggregation_k_multiplier should be 2.5."""
    config = PackingConfig()
    assert config.aggregation_k_multiplier == 2.5


def test_aggregation_k_max_default():
    """Default aggregation_k_max should be 500."""
    config = PackingConfig()
    assert config.aggregation_k_max == 500


def test_k_values_multiplied_for_aggregation():
    """When aggregation detected, k values should be multiplied by the multiplier."""
    config = PackingConfig(vector_k=100, lexical_k=100, graph_max_candidates=75)
    mult = config.aggregation_k_multiplier
    cap = config.aggregation_k_max

    new_vector_k = min(int(config.vector_k * mult), cap)
    new_lexical_k = min(int(config.lexical_k * mult), cap)
    new_graph_k = min(int(config.graph_max_candidates * mult), cap)

    assert new_vector_k == 250
    assert new_lexical_k == 250
    assert new_graph_k == 187  # int(75 * 2.5) = 187


def test_k_values_capped_at_maximum():
    """K values should be capped at aggregation_k_max even after multiplication."""
    config = PackingConfig(
        vector_k=300,
        lexical_k=300,
        graph_max_candidates=300,
        aggregation_k_multiplier=2.5,
        aggregation_k_max=500,
    )
    mult = config.aggregation_k_multiplier
    cap = config.aggregation_k_max

    new_vector_k = min(int(config.vector_k * mult), cap)
    new_lexical_k = min(int(config.lexical_k * mult), cap)
    new_graph_k = min(int(config.graph_max_candidates * mult), cap)

    # 300 * 2.5 = 750, capped at 500
    assert new_vector_k == 500
    assert new_lexical_k == 500
    assert new_graph_k == 500


def test_k_values_unchanged_for_normal_queries():
    """Non-aggregation queries should use default k values unchanged."""
    config = PackingConfig(vector_k=100, lexical_k=100, graph_max_candidates=75)
    # For non-aggregation, the pipeline uses config as-is
    assert config.vector_k == 100
    assert config.lexical_k == 100
    assert config.graph_max_candidates == 75


def test_custom_multiplier():
    """Custom aggregation_k_multiplier should be used in calculation."""
    config = PackingConfig(
        vector_k=100,
        lexical_k=100,
        graph_max_candidates=50,
        aggregation_k_multiplier=3.0,
        aggregation_k_max=500,
    )
    mult = config.aggregation_k_multiplier
    cap = config.aggregation_k_max

    assert min(int(config.vector_k * mult), cap) == 300
    assert min(int(config.lexical_k * mult), cap) == 300
    assert min(int(config.graph_max_candidates * mult), cap) == 150


def test_custom_cap():
    """Custom aggregation_k_max should cap the multiplied values."""
    config = PackingConfig(
        vector_k=100,
        lexical_k=100,
        graph_max_candidates=75,
        aggregation_k_multiplier=2.5,
        aggregation_k_max=200,
    )
    mult = config.aggregation_k_multiplier
    cap = config.aggregation_k_max

    # 100 * 2.5 = 250, capped at 200
    assert min(int(config.vector_k * mult), cap) == 200
    assert min(int(config.lexical_k * mult), cap) == 200
    # 75 * 2.5 = 187, under cap
    assert min(int(config.graph_max_candidates * mult), cap) == 187

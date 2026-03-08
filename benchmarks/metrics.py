"""Scoring utilities for the PRME benchmark suite.

Provides keyword-matching, exclusion scoring, and category aggregation
functions used by all benchmark adapters to evaluate retrieval results
against ground truth.
"""

from __future__ import annotations


def keyword_match_score(expected_keywords: list[str], text: str) -> float:
    """Fraction of expected keywords found (case-insensitive) in *text*.

    Args:
        expected_keywords: Keywords that should appear in the text.
        text: The text to search.

    Returns:
        Score in [0, 1]. Returns 1.0 when *expected_keywords* is empty.
    """
    if not expected_keywords:
        return 1.0
    text_lower = text.lower()
    found = sum(1 for kw in expected_keywords if kw.lower() in text_lower)
    return found / len(expected_keywords)


def exclusion_score(excluded_keywords: list[str], text: str) -> float:
    """Penalize for the presence of excluded keywords.

    Args:
        excluded_keywords: Keywords that should NOT appear.
        text: The text to check.

    Returns:
        1.0 if none found, 0.0 if all found. Linear interpolation otherwise.
    """
    if not excluded_keywords:
        return 1.0
    text_lower = text.lower()
    found = sum(1 for kw in excluded_keywords if kw.lower() in text_lower)
    return 1.0 - (found / len(excluded_keywords))


def category_scores(
    results: list[tuple[str, float]],
) -> dict[str, float]:
    """Compute average score per category from (category, score) pairs.

    Args:
        results: List of (category_name, score) tuples.

    Returns:
        Dictionary mapping category name to mean score.
    """
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for cat, score in results:
        totals[cat] = totals.get(cat, 0.0) + score
        counts[cat] = counts.get(cat, 0) + 1
    return {
        cat: totals[cat] / counts[cat]
        for cat in sorted(totals)
    }

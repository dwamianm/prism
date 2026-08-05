"""Tests for query analysis Stage 1, focused on temporal extraction.

Covers the dateparser cost guards from issue #61: the language pin and the
cue pre-gate must cut the call without losing any temporal signal the
pipeline previously produced.
"""

from __future__ import annotations

import pytest

from prme.retrieval import query_analysis
from prme.retrieval.query_analysis import (
    DEFAULT_TEMPORAL_LANGUAGES,
    _extract_temporal_signals,
    _has_temporal_cue,
    analyze_query,
)
from prme.types import QueryIntent


# ---------------------------------------------------------------------------
# Pre-gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "what did we decide last week",
        "the meeting on March 5 2026",
        "two weeks ago we shipped the parser",
        "what happened yesterday",
        "the day after tomorrow",
        "notes from 2026-03-05",
        "call me Friday",
        "budget for Q3 in 3 months",
    ],
)
def test_pre_gate_passes_temporal_queries(query):
    assert _has_temporal_cue(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "what is my dentist's name",
        "roadmap for project falcon",
        "who works on the platform team",
        "how many times was Sweden mentioned",
        "",
    ],
)
def test_pre_gate_rejects_non_temporal_queries(query):
    assert _has_temporal_cue(query) is False


def test_pre_gate_skips_dateparser_entirely(monkeypatch):
    """A query with no date-like token must not reach dateparser at all."""
    import dateparser.search

    calls: list[str] = []

    def _spy(text, **kwargs):
        calls.append(text)
        return None

    monkeypatch.setattr(dateparser.search, "search_dates", _spy)

    assert _extract_temporal_signals("roadmap for project falcon") == []
    assert calls == []

    _extract_temporal_signals("roadmap for project falcon last week")
    assert calls == ["roadmap for project falcon last week"]


# ---------------------------------------------------------------------------
# Language pin
# ---------------------------------------------------------------------------


def test_languages_are_pinned_by_default(monkeypatch):
    import dateparser.search

    seen: dict = {}

    def _spy(text, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(dateparser.search, "search_dates", _spy)

    _extract_temporal_signals("what did we decide last week")
    assert seen["languages"] == list(DEFAULT_TEMPORAL_LANGUAGES)


def test_empty_languages_restores_auto_detection(monkeypatch):
    import dateparser.search

    seen: dict = {}

    def _spy(text, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(dateparser.search, "search_dates", _spy)

    _extract_temporal_signals("what did we decide last week", languages=[])
    assert seen["languages"] is None


# ---------------------------------------------------------------------------
# Signals still resolve
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "what did we decide last week",
        "the meeting on March 5 2026",
        "what happened yesterday",
        "two weeks ago we shipped the parser",
    ],
)
def test_temporal_signals_still_extracted(query):
    signals = _extract_temporal_signals(query)
    assert signals, f"expected a temporal signal for {query!r}"
    assert all(s["resolved"] is not None for s in signals)


def test_single_word_false_positives_still_filtered():
    # "me" parses as Monday and "may" as May in dateparser; both must be
    # dropped, and neither should survive the pre-gate change.
    assert _extract_temporal_signals("tell me about the parser") == []


async def test_analyze_query_resolves_window_from_text():
    analysis = await analyze_query("what did we decide last week")
    assert analysis.intent == QueryIntent.TEMPORAL
    assert analysis.time_from is not None
    assert analysis.time_to is not None


async def test_analyze_query_without_temporal_cue():
    analysis = await analyze_query("roadmap for project falcon")
    assert analysis.temporal_signals == []
    assert analysis.time_from is None
    assert analysis.time_to is None


async def test_explicit_window_survives_the_pre_gate():
    """Explicit time_from/time_to must apply even when the text has no cue."""
    from datetime import datetime, timezone

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 1, tzinfo=timezone.utc)
    analysis = await analyze_query(
        "roadmap for project falcon", time_from=start, time_to=end
    )
    assert analysis.time_from == start
    assert analysis.time_to == end


async def test_analyze_query_forwards_languages(monkeypatch):
    seen: dict = {}

    def _spy(query, languages=DEFAULT_TEMPORAL_LANGUAGES):
        seen["languages"] = languages
        return []

    monkeypatch.setattr(query_analysis, "_extract_temporal_signals", _spy)

    await analyze_query("what happened last week", languages=["fr"])
    assert seen["languages"] == ["fr"]

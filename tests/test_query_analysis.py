"""Tests for query analysis Stage 1, focused on temporal extraction.

Covers the dateparser cost guards from issue #61: the language pin and the
cue pre-gate must cut the call without losing any temporal signal the
pipeline previously produced. Also covers the opt-in temporal-first intent
order from issue #85.
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

    def _spy(query, languages=DEFAULT_TEMPORAL_LANGUAGES, reference_time=None):
        seen["languages"] = languages
        return []

    monkeypatch.setattr(query_analysis, "_extract_temporal_signals", _spy)

    await analyze_query("what happened last week", languages=["fr"])
    assert seen["languages"] == ["fr"]


async def test_relative_dates_use_request_clock():
    from datetime import datetime, timedelta, timezone

    base = datetime(2024, 5, 10, 12, tzinfo=timezone(timedelta(hours=-5)))
    analysis = await analyze_query("What happened two days ago?", reference_time=base)
    assert analysis.time_from == datetime(2024, 5, 8, 17, tzinfo=timezone.utc)
    assert analysis.time_to == analysis.time_from
    later = await analyze_query("What happened two days ago?", reference_time=base + timedelta(days=30))
    assert later.time_from == analysis.time_from + timedelta(days=30)


async def test_reference_clock_requires_timezone():
    from datetime import datetime

    with pytest.raises(ValueError, match="reference_time must include a timezone"):
        await analyze_query("What happened yesterday?", reference_time=datetime(2024, 5, 10))


async def test_concurrent_query_clocks_are_independent():
    import asyncio
    from datetime import datetime, timedelta, timezone

    clocks = [datetime(2020 + i, 5, 10, 12, tzinfo=timezone.utc) for i in range(12)]
    results = await asyncio.gather(*[
        analyze_query("What happened two days ago?", reference_time=clock) for clock in clocks
    ])
    assert [r.time_from for r in results] == [c - timedelta(days=2) for c in clocks]


# ---------------------------------------------------------------------------
# Intent order (issue #85)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,entities",
    [
        # A person, a place and an organization.
        ("When did Caroline go to the LGBTQ support group?", ["Caroline"]),
        ("When did I last visit Paris?", ["Paris"]),
        ("When did I start working at Google?", ["Google"]),
        # A date with no temporal keyword, and a month name after a preposition.
        ("What did Melanie paint in July 2023?", ["Melanie", "July"]),
        ("What did Caroline do in June?", ["Caroline", "June"]),
        # The issue's proposal checks temporal intent before the who / what is /
        # what are prefix too, so these change although they name nobody.
        ("Who did I meet before the parade?", []),
        ("What are my plans for tomorrow?", []),
    ],
)
async def test_temporal_first_classifies_named_temporal_questions_as_temporal(query, entities):
    from datetime import datetime, timezone

    clock = datetime(2023, 6, 1, 12, tzinfo=timezone.utc)
    default = await analyze_query(query, reference_time=clock)
    temporal_first = await analyze_query(query, reference_time=clock, intent_order="temporal_first")
    assert default.intent == QueryIntent.ENTITY_LOOKUP
    assert temporal_first.intent == QueryIntent.TEMPORAL
    # Entity handling does not depend on the intent.
    assert default.entities == temporal_first.entities == entities
    assert (default.time_from, default.time_to) == (temporal_first.time_from, temporal_first.time_to)


@pytest.mark.parametrize(
    "query",
    [
        "When did I start my new job?",
        "what did we decide last week",
        "What happened two days ago?",
        "How long ago did we move to the new office?",
        "what did we discuss on 2026-03-05",
    ],
)
async def test_temporal_first_leaves_questions_already_classified_temporal_alone(query):
    default = await analyze_query(query)
    temporal_first = await analyze_query(query, intent_order="temporal_first")
    assert default.intent == temporal_first.intent == QueryIntent.TEMPORAL


@pytest.mark.parametrize(
    "query,intent",
    [
        ("Who is Alice?", QueryIntent.ENTITY_LOOKUP),
        ("What does Caroline like?", QueryIntent.ENTITY_LOOKUP),
        ("What are my hobbies?", QueryIntent.ENTITY_LOOKUP),
        ("Which projects are related to billing?", QueryIntent.RELATIONAL),
        ("how do I reset my password", QueryIntent.FACTUAL),
        ("roadmap for project falcon", QueryIntent.SEMANTIC),
    ],
)
async def test_temporal_first_leaves_questions_without_temporal_wording_alone(query, intent):
    default = await analyze_query(query)
    temporal_first = await analyze_query(query, intent_order="temporal_first")
    assert default.intent == temporal_first.intent == intent


@pytest.mark.parametrize(
    "query,intent",
    [
        ("Who is June dating?", QueryIntent.ENTITY_LOOKUP),
        ("What does April like to cook?", QueryIntent.ENTITY_LOOKUP),
        ("What does Sun Microsystems make?", QueryIntent.ENTITY_LOOKUP),
        # Temporal wording still decides, but the name gives no date window.
        ("When did June start dating Alex?", QueryIntent.TEMPORAL),
    ],
)
async def test_temporal_first_reads_a_month_or_weekday_name_as_a_name(query, intent):
    analysis = await analyze_query(query, intent_order="temporal_first")
    assert analysis.intent == intent
    assert analysis.temporal_signals == []
    assert analysis.time_from is None and analysis.time_to is None
    # The entity-first order resolves the same word to a date, which only
    # temporal intent would use.
    default = await analyze_query(query)
    assert default.intent == QueryIntent.ENTITY_LOOKUP and default.time_from is not None


async def test_temporal_first_changes_the_current_state_path_and_guidance():
    from prme.retrieval.context_formatter import _detect_context_type
    from prme.retrieval.scoring import _is_current_state_query

    # A present-tense question is current-state until temporal intent takes it off the path.
    present = "Who is Caroline dating since the breakup?"
    assert _is_current_state_query(await analyze_query(present)) is True
    assert _is_current_state_query(await analyze_query(present, intent_order="temporal_first")) is False
    # Explicit current wording keeps it on the path under either order.
    current = "Who is Caroline dating now, since the breakup?"
    assert _is_current_state_query(await analyze_query(current, intent_order="temporal_first")) is True
    # Temporal guidance for a question whose wording does not already select it.
    dated = "What did Melanie paint in July 2023?"
    assert _detect_context_type(dated, await analyze_query(dated)) == "default"
    assert _detect_context_type(dated, await analyze_query(dated, intent_order="temporal_first")) == "temporal"
    # "When" selects it under either order.
    when = "When did Caroline go to the support group?"
    assert _detect_context_type(when, await analyze_query(when)) == "temporal"


async def test_unknown_intent_order_is_refused():
    with pytest.raises(ValueError, match="Unknown query intent order"):
        await analyze_query("When did Caroline go?", intent_order="temporal")

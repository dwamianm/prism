"""Tests for prme.retrieval.context_formatter."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from prme.models.nodes import MemoryNode
from prme.retrieval.context_formatter import (
    compute_time_offsets,
    format_days_ago,
    format_for_llm,
)
from prme.retrieval.models import RetrievalCandidate
from prme.types import NodeType, Scope


def _make_candidate(
    content: str,
    event_time: datetime | None = None,
    created_at: datetime | None = None,
    score: float = 0.5,
    node_type: NodeType = NodeType.EVENT,
) -> RetrievalCandidate:
    """Create a minimal RetrievalCandidate for testing."""
    now = created_at or datetime(2023, 6, 1, tzinfo=timezone.utc)
    node = MemoryNode(
        user_id="test",
        node_type=node_type,
        scope=Scope.PERSONAL,
        content=content,
        created_at=now,
        event_time=event_time,
    )
    return RetrievalCandidate(node=node, composite_score=score)


# ---------------------------------------------------------------------------
# format_days_ago
# ---------------------------------------------------------------------------


class TestFormatDaysAgo:
    def test_today(self):
        dt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        assert format_days_ago(dt, dt) == "today"

    def test_yesterday(self):
        qdt = datetime(2023, 7, 2, tzinfo=timezone.utc)
        edt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        assert format_days_ago(edt, qdt) == "yesterday"

    def test_days(self):
        qdt = datetime(2023, 7, 7, tzinfo=timezone.utc)
        edt = datetime(2023, 7, 3, tzinfo=timezone.utc)
        assert format_days_ago(edt, qdt) == "4 days ago"

    def test_weeks(self):
        qdt = datetime(2023, 7, 15, tzinfo=timezone.utc)
        edt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        assert "2 weeks ago" in format_days_ago(edt, qdt)
        assert "14 days" in format_days_ago(edt, qdt)

    def test_months(self):
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        edt = datetime(2023, 4, 1, tzinfo=timezone.utc)
        result = format_days_ago(edt, qdt)
        assert "months ago" in result

    def test_future(self):
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        edt = datetime(2023, 7, 5, tzinfo=timezone.utc)
        assert format_days_ago(edt, qdt) == "in 4 days"


# ---------------------------------------------------------------------------
# compute_time_offsets
# ---------------------------------------------------------------------------


class TestComputeTimeOffsets:
    def test_weeks_ago(self):
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        result = compute_time_offsets("What happened two weeks ago?", qdt)
        assert "COMPUTED:" in result
        assert "2023-06-17" in result

    def test_months_ago(self):
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        result = compute_time_offsets("What did I do a month ago?", qdt)
        assert "COMPUTED:" in result
        assert "2023-06-01" in result

    def test_last_friday(self):
        # July 1, 2023 is a Saturday. Last Friday = June 30
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        result = compute_time_offsets("What artist did I listen to last Friday?", qdt)
        assert "COMPUTED:" in result
        assert "2023-06-30" in result

    def test_past_weekend(self):
        qdt = datetime(2023, 7, 5, tzinfo=timezone.utc)  # Wednesday
        result = compute_time_offsets("What did I fix the past weekend?", qdt)
        assert "COMPUTED:" in result

    def test_no_offset(self):
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        result = compute_time_offsets("What is my current budget?", qdt)
        assert result == ""

    def test_numeric_weeks(self):
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        result = compute_time_offsets("What happened 4 weeks ago?", qdt)
        assert "COMPUTED:" in result
        assert "28 days" in result


# ---------------------------------------------------------------------------
# format_for_llm
# ---------------------------------------------------------------------------


class TestFormatForLlm:
    def test_empty_results(self):
        assert format_for_llm([], "test query") == ""

    def test_default_format_includes_dates(self):
        c = _make_candidate(
            "Some fact",
            created_at=datetime(2023, 6, 15, tzinfo=timezone.utc),
        )
        result = format_for_llm([c], "What is the fact?")
        assert "[1]" in result
        assert "2023-06-15" in result
        assert "Some fact" in result

    def test_temporal_sorts_chronologically(self):
        c1 = _make_candidate(
            "First event",
            event_time=datetime(2023, 3, 1, tzinfo=timezone.utc),
        )
        c2 = _make_candidate(
            "Second event",
            event_time=datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        # Pass in reverse order
        result = format_for_llm(
            [c2, c1],
            "What happened first?",
            context_hint="temporal",
            question_date=datetime(2023, 7, 1, tzinfo=timezone.utc),
        )
        # First event should come before second event
        idx1 = result.index("First event")
        idx2 = result.index("Second event")
        assert idx1 < idx2

    def test_temporal_includes_days_ago(self):
        c = _make_candidate(
            "I went jogging",
            event_time=datetime(2023, 6, 17, tzinfo=timezone.utc),
        )
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        result = format_for_llm(
            [c], "What did I do 2 weeks ago?",
            context_hint="temporal",
            question_date=qdt,
        )
        assert "2 weeks ago" in result
        assert "COMPUTED:" in result
        assert "2023-06-17" in result

    def test_temporal_includes_todays_date(self):
        c = _make_candidate("Event")
        qdt = datetime(2023, 7, 1, tzinfo=timezone.utc)
        result = format_for_llm(
            [c], "When?",
            context_hint="temporal",
            question_date=qdt,
        )
        assert "Today's date: 2023-07-01" in result

    def test_knowledge_update_has_latest_markers(self):
        candidates = [
            _make_candidate(
                f"Value {i}",
                event_time=datetime(2023, 1 + i, 1, tzinfo=timezone.utc),
            )
            for i in range(6)
        ]
        result = format_for_llm(
            candidates,
            "What is the current price?",
            context_hint="knowledge_update",
        )
        assert "[MOST RECENT" in result
        assert "chronological order" in result

    def test_knowledge_update_sorts_chronologically(self):
        c_old = _make_candidate(
            "Price is $29",
            event_time=datetime(2023, 1, 1, tzinfo=timezone.utc),
        )
        c_new = _make_candidate(
            "Price is $39",
            event_time=datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        result = format_for_llm(
            [c_new, c_old],
            "What is the price?",
            context_hint="knowledge_update",
        )
        idx_old = result.index("Price is $29")
        idx_new = result.index("Price is $39")
        assert idx_old < idx_new

    def test_auto_detects_temporal(self):
        c = _make_candidate("Event")
        result = format_for_llm(
            [c], "What happened two weeks ago?",
            question_date=datetime(2023, 7, 1, tzinfo=timezone.utc),
        )
        # Should auto-detect temporal and include COMPUTED
        assert "COMPUTED:" in result

    def test_knowledge_update_auto_detected_for_current_state(self):
        """Knowledge-update formatting is auto-detected for current-state queries."""
        candidates = [
            _make_candidate(
                f"Value {i}",
                event_time=datetime(2023, 1 + i, 1, tzinfo=timezone.utc),
            )
            for i in range(6)
        ]
        result = format_for_llm(
            candidates,
            "What is the current status?",
        )
        # Should auto-detect knowledge_update and include [LATEST] markers
        assert "[MOST RECENT" in result
        assert "chronological order" in result

    def test_knowledge_update_not_triggered_for_aggregation(self):
        """Aggregation queries should NOT get knowledge_update formatting."""
        candidates = [
            _make_candidate(
                f"Value {i}",
                event_time=datetime(2023, 1 + i, 1, tzinfo=timezone.utc),
            )
            for i in range(6)
        ]
        result = format_for_llm(
            candidates,
            "How many items do I currently have?",
        )
        # "How many" is aggregation — should get aggregation formatting instead
        assert "AGGREGATION TASK" in result

    def test_knowledge_update_with_explicit_hint(self):
        candidates = [
            _make_candidate(
                f"Value {i}",
                event_time=datetime(2023, 1 + i, 1, tzinfo=timezone.utc),
            )
            for i in range(6)
        ]
        result = format_for_llm(
            candidates,
            "What is the current status?",
            context_hint="knowledge_update",
        )
        assert "[MOST RECENT" in result

    def test_context_hint_overrides_auto_detection(self):
        c = _make_candidate("Event")
        # Query looks temporal, but force default
        result = format_for_llm(
            [c], "What happened yesterday?",
            context_hint="default",
        )
        assert "COMPUTED:" not in result

    def test_max_results_limits_output(self):
        candidates = [
            _make_candidate(f"Fact {i}")
            for i in range(100)
        ]
        result = format_for_llm(candidates, "test", max_results=5)
        assert "[5]" in result
        assert "[6]" not in result

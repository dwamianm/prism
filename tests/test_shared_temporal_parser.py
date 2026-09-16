"""Ingestion and retrieval must not enter shared dateparser state concurrently."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

import pytest

from prme.ingestion.pipeline import IngestionPipeline
from prme.retrieval.query_analysis import _extract_temporal_signals


def resolve(kind, base):
    if kind == "ingestion":
        return datetime.fromisoformat(
            IngestionPipeline._resolve_temporal("yesterday", reference_time=base)
        )
    return _extract_temporal_signals("yesterday", reference_time=base)[0]["resolved"]


@pytest.mark.parametrize("first_kind", ["ingestion", "query"])
def test_mixed_parser_calls_share_one_critical_section(monkeypatch, first_kind):
    import dateparser
    import dateparser.search

    entered, release, second_entered, second_started = (
        threading.Event() for _ in range(4)
    )

    def parser(kind, settings):
        if kind == first_kind:
            entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
        return settings["RELATIVE_BASE"] - timedelta(days=1)

    monkeypatch.setattr(
        dateparser, "parse", lambda text, *, settings: parser("ingestion", settings)
    )
    monkeypatch.setattr(
        dateparser.search,
        "search_dates",
        lambda text, *, languages, settings: [("yesterday", parser("query", settings))],
    )
    first_base = datetime(2020, 1, 10, tzinfo=timezone.utc)
    second_base = datetime(2040, 1, 10, tzinfo=timezone.utc)
    second_kind = "query" if first_kind == "ingestion" else "ingestion"

    def second():
        second_started.set()
        return resolve(second_kind, second_base)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(resolve, first_kind, first_base)
        try:
            assert entered.wait(5)
            second_future = executor.submit(second)
            assert second_started.wait(5)
            assert not second_entered.wait(0.15), (
                "Independent product paths entered shared parser state together"
            )
        finally:
            release.set()
        assert first_future.result(timeout=5) == first_base - timedelta(days=1)
        assert second_future.result(timeout=5) == second_base - timedelta(days=1)
        assert second_entered.is_set()


def test_real_mixed_parsing_keeps_each_requests_reference_clock():
    inputs = [
        (kind, datetime(year, 1, 10, 12, tzinfo=timezone.utc))
        for _ in range(12)
        for kind, year in [("query", 2020), ("ingestion", 2040)]
    ]
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(resolve, kind, base) for kind, base in inputs]
        for future, (_, base) in zip(futures, inputs):
            assert future.result(timeout=15) == base - timedelta(days=1)

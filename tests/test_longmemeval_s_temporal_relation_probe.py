from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from benchmarks.diagnostics import longmemeval_s_temporal_relation_probe as probe


FIRST_ID = UUID("00000000-0000-0000-0000-000000000001")
SECOND_ID = UUID("00000000-0000-0000-0000-000000000002")


def _records() -> dict[UUID, probe.EvidenceRecord]:
    return {
        FIRST_ID: probe.EvidenceRecord(
            id=FIRST_ID,
            event_time=datetime(2024, 3, 4, 10, tzinfo=timezone.utc),
            text="I repotted the plant today.",
        ),
        SECOND_ID: probe.EvidenceRecord(
            id=SECOND_ID,
            event_time=datetime(2024, 3, 18, 10, tzinfo=timezone.utc),
            text="I gave Pat cuttings today.",
        ),
    }


def test_computes_calendar_difference_from_verbatim_event_time_evidence() -> None:
    resolution = probe.RawResolution(
        operation="elapsed_between",
        operands=[
            probe.RawOperand(
                name="repotted plant",
                evidence_id=FIRST_ID,
                quote="I repotted the plant today",
                time_expression="event_time",
                time_basis="event_time",
            ),
            probe.RawOperand(
                name="gave cuttings",
                evidence_id=SECOND_ID,
                quote="I gave Pat cuttings today",
                time_expression="event_time",
                time_basis="event_time",
            ),
        ],
    )

    relation, errors = probe.compute_relation(
        resolution,
        _records(),
        datetime(2024, 3, 20, tzinfo=timezone.utc),
    )

    assert errors == ()
    assert relation is not None
    assert relation.value == "14 days"
    assert str(FIRST_ID) in relation.guidance
    assert "inferred, not stored truth" in relation.guidance


def test_rejects_invented_quote_before_computation() -> None:
    resolution = probe.RawResolution(
        operation="elapsed_since_question",
        operands=[
            probe.RawOperand(
                name="repotted plant",
                evidence_id=FIRST_ID,
                quote="I planted the tree today",
                time_expression="event_time",
                time_basis="event_time",
            )
        ],
    )

    relation, errors = probe.compute_relation(
        resolution,
        _records(),
        datetime(2024, 3, 20, tzinfo=timezone.utc),
    )

    assert relation is None
    assert errors == ("operand[0] quote is not verbatim",)


def test_explicit_day_is_parsed_relative_to_record_year() -> None:
    record = probe.EvidenceRecord(
        id=FIRST_ID,
        event_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
        text="The phone arrived on February 20th.",
    )
    resolution = probe.RawResolution(
        operation="absolute_date",
        operands=[
            probe.RawOperand(
                name="phone arrived",
                evidence_id=FIRST_ID,
                quote="arrived on February 20th",
                time_expression="February 20th",
                time_basis="text_expression",
            )
        ],
    )

    relation, errors = probe.compute_relation(
        resolution,
        {FIRST_ID: record},
        datetime(2024, 3, 20, tzinfo=timezone.utc),
    )

    assert errors == ()
    assert relation is not None
    assert relation.value == "2024-02-20"


def test_month_only_date_is_rejected_as_false_day_precision() -> None:
    record = probe.EvidenceRecord(
        id=FIRST_ID,
        event_time=datetime(2024, 3, 15, tzinfo=timezone.utc),
        text="I booked it last month.",
    )
    resolution = probe.RawResolution(
        operation="absolute_date",
        operands=[
            probe.RawOperand(
                name="booking",
                evidence_id=FIRST_ID,
                quote="booked it last month",
                time_expression="last month",
                time_basis="text_expression",
            )
        ],
    )

    relation, errors = probe.compute_relation(
        resolution,
        {FIRST_ID: record},
        datetime(2024, 3, 20, tzinfo=timezone.utc),
    )

    assert relation is None
    assert errors == ("operand[0] text date lacks day precision",)


def test_word_duration_is_parsed_without_float_coercion() -> None:
    assert probe._parse_duration("two weeks") == (2.0, "week")

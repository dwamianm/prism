"""Prevent silent denominator changes in later MemoryArena travel studies."""

import copy

import pytest

from benchmarks.diagnostics.memoryarena_travel_audit import (
    SLOTS,
    score_strict,
    validate_coverage,
)


def person(identity, plan=None):
    return {"person_idx": identity, "plan": plan}


def test_complete_cohort_keeps_explicit_failures():
    validate_coverage({11: [1, 2], 22: [1]}, [
        {"id": 22, "persons": [person(1)]},
        {"id": 11, "persons": [person(2), person(1, [])]},
    ])


@pytest.mark.parametrize("rows", [
    [],
    [{"id": 11, "persons": [person(1)]}],
    [{"id": 11, "persons": [person(1), person(1)]}],
    [{"id": 11, "persons": [person(1), person(3)]}],
    [{"id": 11, "persons": [person(1), person(2), person(3)]}],
    [{"id": 22, "persons": [person(1), person(2)]}],
    [{"id": 11, "persons": [person(True), person(2)]}],
    [{"id": 11, "persons": [person(1), {"person_idx": 2}]}],
    [{"id": 11, "persons": [person(1), person(2)]}] * 2,
])
def test_changed_or_ambiguous_cohort_is_rejected(rows):
    with pytest.raises(ValueError):
        validate_coverage({11: [1, 2]}, rows)


@pytest.mark.parametrize("expected", [{}, {11: []}, {11: [1, 1]}, {11: [True]}, {True: [1]}])
def test_registration_must_be_explicit_and_unique(expected):
    with pytest.raises(ValueError):
        validate_coverage(expected, [])


def _strict_cohort():
    rows = []
    for group in (11, 22):
        base = [{"day": 1, **{slot: "Old choice" for slot in SLOTS}}]
        answer = [{"day": 1, **{slot: "Cedar Lodge" for slot in SLOTS}}]
        rows.append({
            "id": group,
            "base_person": {"daily_plans": base},
            "answers": [
                {"round_idx": identity, "daily_plans": answer}
                for identity in (1, 2)
            ],
        })
    return rows


def _strict_submission(value="Cedar Lodge"):
    plan = [{"day": 1, **{slot: value for slot in SLOTS}}]
    return [
        {"id": group, "persons": [
            person(1, copy.deepcopy(plan)), person(2, copy.deepcopy(plan))
        ]}
        for group in (11, 22)
    ]


def test_strict_score_accepts_complete_full_string_matches():
    scores = score_strict(_strict_cohort(), _strict_submission("  CEDAR   lodge "))

    assert scores["ps"] == 100
    assert scores["sps"] == 100
    assert scores["sr"] == 100
    assert scores["counts"] == {
        "passed_people": 4,
        "total_people": 4,
        "passed_groups": 2,
        "total_groups": 2,
        "passed_constraint_slots": 24,
        "total_constraint_slots": 24,
    }


def test_strict_score_rejects_prefixes_missing_slots_and_explicit_failures():
    prefix = score_strict(_strict_cohort(), _strict_submission("C"))
    assert prefix["ps"] == prefix["sps"] == prefix["sr"] == 0

    missing = _strict_submission()
    del missing[0]["persons"][0]["plan"][0][SLOTS[0]]
    missing_scores = score_strict(_strict_cohort(), missing)
    assert missing_scores["ps"] == 75
    assert missing_scores["sr"] == 50

    failed = _strict_submission()
    failed[0]["persons"][0]["plan"] = None
    failed_scores = score_strict(_strict_cohort(), failed)
    assert failed_scores["ps"] == 75
    assert failed_scores["sr"] == 50


@pytest.mark.parametrize("plan", [
    [{"day": 1, **{slot: "Cedar Lodge" for slot in SLOTS}},
     {"day": 2, **{slot: "Cedar Lodge" for slot in SLOTS}}],
    [{"day": 2, **{slot: "Cedar Lodge" for slot in SLOTS}}],
    [{"day": True, **{slot: "Cedar Lodge" for slot in SLOTS}}],
])
def test_strict_score_rejects_changed_day_structure(plan):
    rows = _strict_submission()
    rows[0]["persons"][0]["plan"] = plan
    scores = score_strict(_strict_cohort(), rows)
    assert scores["ps"] == 75

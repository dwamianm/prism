"""Prevent silent denominator changes in later MemoryArena travel studies."""

import pytest

from benchmarks.diagnostics.memoryarena_travel_audit import validate_coverage


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

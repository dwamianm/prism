"""A plural first-person claim survives a model that writes I for we, with enable_ingest_claim_fixes (prism-monorepo issue 258).

The batched response in ``fixtures/extraction/trip_batch_haiku45.json`` is what
Claude Haiku 4.5 returned for the four turns of the issue's repro, recorded once.
"""

import json
from pathlib import Path

import pytest

from prme.ingestion.extraction import (
    _VALIDATION_PLURAL,
    _VALIDATION_SOURCE,
    BatchTurn,
    _CitedExtractionResult,
    validate_batch_output,
)
from prme.ingestion.grounding import plural_first_person_form

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "extraction" / "trip_batch_haiku45.json").read_text())
CITIES = "We chose Tokyo, Kyoto, Kanazawa, and Osaka."
DROPPED = "We dropped Hiroshima to save time."


class TestPluralFirstPerson:
    @pytest.mark.parametrize(("value", "text", "expected"), [
        ("I", CITIES, "We"),
        ("i", "we chose Tokyo.", "we"),
        ("me", "Sam called us yesterday.", "us"),
        ("my", "Our trip starts in March.", "Our"),
        ("myself", "We pushed ourselves.", "ourselves"),
        # The turn uses I, or neither form, or the value is another word.
        ("I", "I chose Tokyo, and we agreed.", None),
        ("I", "Jordan chose Tokyo.", None),
        ("Jordan", CITIES, None),
        ("We", CITIES, None),
    ])
    def test_the_counterpart_the_turn_uses(self, value, text, expected):
        assert plural_first_person_form(value, text) == expected

    def test_the_recorded_response_keeps_the_claims_of_the_we_turn(self):
        turns = [BatchTurn(index=t["index"], role=t["role"], content=t["content"]) for t in RECORDED["turns"]]
        facts = {fact.object: fact
                 for fact in validate_batch_output(RECORDED["response"], turns, plural_first_person=True)[3].facts}
        # The model wrote I; the turn says We, and the claim keeps the turn's word.
        assert set(facts) == {"Tokyo, Kyoto, Kanazawa, and Osaka", "Hiroshima to save time"}
        assert {fact.subject for fact in facts.values()} == {"We"}
        # Stored evidence is the whole paragraph, as for any claim.
        assert facts["Hiroshima to save time"].evidence_quote == f"{CITIES} {DROPPED}"

    def test_without_the_option_the_same_response_keeps_nothing_for_that_turn(self):
        turns = [BatchTurn(index=t["index"], role=t["role"], content=t["content"]) for t in RECORDED["turns"]]
        assert validate_batch_output(RECORDED["response"], turns)[3].facts == []

    def test_a_single_turn_check_reads_the_option_from_the_provider_call(self):
        payload = {"entities": [], "facts": [{
            "subject": "I", "predicate": "chose", "object": "Tokyo", "polarity": "positive", "evidence_quote": CITIES}]}
        source = _VALIDATION_SOURCE.set(CITIES)
        try:
            assert _CitedExtractionResult.model_validate(payload).facts == []
            token = _VALIDATION_PLURAL.set(True)
            try:
                [fact] = _CitedExtractionResult.model_validate(payload).facts
            finally:
                _VALIDATION_PLURAL.reset(token)
        finally:
            _VALIDATION_SOURCE.reset(source)
        assert fact.subject == "We"

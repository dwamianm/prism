import pytest

from benchmarks.integrations.gpt54_budget import (
    BudgetExhausted, Ledger, MODEL, response_text, usage_cost,
)


def test_budget_keeps_uncertain_reservations_and_rejects_replay(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json", cap=100)
    ledger.update("first", reserve=70)
    with pytest.raises(BudgetExhausted):
        Ledger(ledger.path, cap=100).update("second", reserve=31)
    with pytest.raises(ValueError):
        ledger.update("first", reserve=1)
    ledger.update("first", actual=20)
    ledger.update("second", reserve=80)
    assert sum(x["charge"] for x in ledger.update()["entries"].values()) == 100


def test_usage_accounts_for_reasoning_output_and_cache():
    body = {"service_tier": "flex", "usage": {
        "input_tokens": 100, "output_tokens": 50,
        "input_tokens_details": {"cached_tokens": 20},
        "output_tokens_details": {"reasoning_tokens": 40},
    }}
    assert usage_cost(body) == 80 * 1250 + 20 * 125 + 50 * 7500
    body["service_tier"] = "default"
    assert usage_cost(body) == 2 * (80 * 1250 + 20 * 125 + 50 * 7500)


@pytest.mark.parametrize("field,value", [("status", "incomplete"), ("model", "gpt-5.4")])
def test_partial_responses_are_not_answers(field, value):
    body = {"model": MODEL, "status": "completed", "output": [
        {"content": [{"type": "output_text", "text": "partial answer"}]}]}
    body[field] = value
    with pytest.raises(ValueError):
        response_text(body)

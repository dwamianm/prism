import json

import pytest

from benchmarks.diagnostics.verify_packing_composition import account


def inputs(representation="full", content="complete source"):
    candidates = [{"node": {"id": "a", "metadata": {"source_turn": "s"},
                             "content": content}}]
    context = json.dumps({"id": "a", "representation": representation, "text": content})
    config = {"tokenizer": "cl100k_base", "token_budget": 1024, "overhead_tokens": 100}
    return context, candidates, config


def test_pointer_does_not_earn_credit_even_with_literal_source():
    context, candidates, config = inputs("reference")
    result = account(context, candidates, config, {"s"})
    assert result["evidence_recall"] == 0
    assert result["pointer_source_ids"] == ["s"]


def test_whole_source_and_unicode_line_separator():
    context, candidates, config = inputs(content="complete\u2028source")
    result = account(context, candidates, config, {"s"})
    assert result["evidence_recall"] == 1
    assert result["all_evidence_retained"] is True


@pytest.mark.parametrize("change", ["duplicate", "unknown", "truncated", "budget", "representation"])
def test_forged_context_is_rejected(change):
    context, candidates, config = inputs()
    if change == "duplicate":
        context += "\n" + context
    elif change == "unknown":
        context = context.replace('"a"', '"other"')
    elif change == "truncated":
        context = context.replace("complete source", "complete")
    elif change == "budget":
        config["token_budget"] = 100
    else:
        context = context.replace('"full"', '"invented"')
    with pytest.raises(ValueError):
        account(context, candidates, config, {"s"})

"""Decision and equal-source controls for the final study; no model calls."""

from copy import deepcopy

import tiktoken

from benchmarks.coding.continuation import ARMS, BUDGET, chunks, decision, notes_context
from benchmarks.coding.continuation_tasks import TASKS


def rows():
    return [
        dict(
            task=t["id"],
            repeat=r,
            arm=a,
            passed=True,
            status="complete",
            input_tokens=100,
            output_tokens=10,
            seconds=1,
        )
        for t in TASKS
        for r in range(2)
        for a in ARMS
    ]


def test_ceiling_is_not_evidence_of_benefit():
    result = decision(rows(), complete=True)
    assert result["reliable_tasks"] == dict(control=4, notes=4, prme=4)
    assert result["decision"] == "shelve_automatic_integration"
    assert not decision(rows()[:-1], complete=True)["complete"]


def test_efficiency_requires_no_losses_or_provider_failures():
    data = rows()
    for row in data:
        if row["arm"] == "prme":
            row["input_tokens"] = 80
    assert decision(data, complete=True)["efficiency_gate"]
    bad = deepcopy(data)
    bad[2]["passed"] = False
    assert decision(bad, complete=True)["decision"] == "shelve_automatic_integration"
    bad = deepcopy(data)
    bad[0]["status"] = "provider_failure"
    assert decision(bad, complete=True)["decision"] == "shelve_automatic_integration"


def test_correctness_needs_two_tasks_over_both_baselines():
    data = rows()
    for row in data:
        if row["arm"] != "prme" and row["task"] in ("correction", "workspace"):
            row["passed"] = False
    assert decision(data, complete=True)["correctness_gate"]
    for row in data:
        if row["arm"] == "prme":
            row["input_tokens"] = 111
    assert decision(data, complete=True)["decision"] == "shelve_automatic_integration"


def test_chunks_preserve_every_source_byte_and_search_stays_within_budget():
    original = ("paragraph café correction.\n" * 30 + "\n") * 4
    records = chunks({"events": {"event-1": {"content": original}}})
    assert "".join(row["text"] for row in records) == original
    assert records[0]["start"] == 1
    result = notes_context(records, "CORRECTION correction")
    assert result == notes_context(list(reversed(records)), "correction")
    assert result["selected"] and result["tokens"] <= BUDGET
    assert (
        len(tiktoken.get_encoding("cl100k_base").encode(result["context"]))
        == result["tokens"]
    )

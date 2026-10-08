"""Authored development contrasts; never a held-out accuracy estimate."""

from __future__ import annotations


def cases() -> list[dict]:
    rows = []

    def add(
        family,
        source,
        claim,
        object_value,
        admit,
        *,
        role="user",
        kind="fact",
        predicate="has",
        quote=None,
    ):
        rows.append(
            {
                "id": f"{family}-{sum(r['family'] == family for r in rows) + 1}",
                "family": family,
                "source": source,
                "role": role,
                "claim": claim,
                "fact": {
                    "subject": "I",
                    "predicate": predicate,
                    "object": object_value,
                    "fact_type": kind,
                    "epistemic_type": "asserted",
                    "polarity": "positive",
                    "evidence_quote": source if quote is None else quote,
                },
                "expected_admit": admit,
            }
        )

    add(
        "health",
        "What does my plan cover for breast cancer treatment?",
        "I have breast cancer.",
        "breast cancer",
        False,
    )
    add(
        "health",
        "I have breast cancer.",
        "I have breast cancer.",
        "breast cancer",
        True,
    )
    add(
        "health",
        "What will having a baby cost under my plan?",
        "I am having a baby.",
        "baby",
        False,
    )
    add("health", "I am having a baby.", "I am having a baby.", "baby", True)
    add(
        "cost",
        "Hypothetically, if I had a $4,000 surgery, how much would I pay?",
        "I had a $4,000 surgery.",
        "$4,000 surgery",
        False,
    )
    add(
        "cost",
        "I had a $4,000 surgery.",
        "I had a $4,000 surgery.",
        "$4,000 surgery",
        True,
    )
    add(
        "cost",
        "Suppose I paid $200 for therapy.",
        "I paid $200 for therapy.",
        "$200 for therapy",
        False,
    )
    add(
        "cost",
        "I paid $200 for therapy.",
        "I paid $200 for therapy.",
        "$200 for therapy",
        True,
    )
    add("name", "My name is Sam.", "My name is Sam.", "Sam", True, predicate="name")
    add(
        "name",
        "Call me Sam.",
        "I prefer to be called Sam.",
        "Sam",
        True,
        kind="preference",
        predicate="prefers_to_be_called",
    )
    add(
        "name",
        "Should I call myself Sam?",
        "I prefer to be called Sam.",
        "Sam",
        False,
        kind="preference",
        predicate="prefers_to_be_called",
    )
    add(
        "name",
        "My brother's name is Sam.",
        "My name is Sam.",
        "Sam",
        False,
        predicate="name",
    )
    add(
        "preference",
        "I prefer short answers.",
        "I prefer short answers.",
        "short answers",
        True,
        kind="preference",
        predicate="prefers",
    )
    add(
        "preference",
        "Please call me Sam, and keep answers short.",
        "I prefer short answers.",
        "short",
        True,
        kind="preference",
        predicate="prefers",
    )
    add(
        "preference",
        "Would I prefer short answers?",
        "I prefer short answers.",
        "short answers",
        False,
        kind="preference",
        predicate="prefers",
    )
    add(
        "preference",
        "My colleague prefers short answers.",
        "I prefer short answers.",
        "short answers",
        False,
        kind="preference",
        predicate="prefers",
    )
    add(
        "quotation",
        'Here is a letter from my doctor: "I have asthma."',
        "I have asthma.",
        "asthma",
        False,
    )
    add("quotation", "I have asthma.", "I have asthma.", "asthma", True)
    add(
        "quotation",
        'Example form: "My name is Sam." This is sample text.',
        "My name is Sam.",
        "Sam",
        False,
        predicate="name",
    )
    add(
        "quotation",
        "My name is Sam. This is my actual name.",
        "My name is Sam.",
        "Sam",
        True,
        predicate="name",
    )
    add("role", "I have asthma.", "I have asthma.", "asthma", False, role="assistant")
    add("role", "I have asthma.", "I have asthma.", "asthma", True)
    add(
        "role",
        "I prefer short answers.",
        "I prefer short answers.",
        "short answers",
        False,
        role="system",
        kind="preference",
        predicate="prefers",
    )
    add(
        "role",
        "I prefer short answers.",
        "I prefer short answers.",
        "short answers",
        True,
        kind="preference",
        predicate="prefers",
    )
    add(
        "attempt",
        "I tried to install Redis, but installation failed.",
        "I installed Redis.",
        "Redis",
        False,
        predicate="installed",
    )
    add(
        "attempt",
        "I installed Redis yesterday.",
        "I installed Redis yesterday.",
        "Redis",
        True,
        predicate="installed",
    )
    add(
        "attempt",
        "I plan to adopt Ruff next month.",
        "I adopted Ruff.",
        "Ruff",
        False,
        predicate="adopted",
    )
    add(
        "attempt",
        "I decided to adopt Ruff.",
        "I decided to adopt Ruff.",
        "Ruff",
        True,
        kind="decision",
        predicate="decided_to_adopt",
    )
    add(
        "polarity",
        "I do not use Redis.",
        "I use Redis.",
        "Redis",
        False,
        predicate="uses",
    )
    add("polarity", "I use Redis.", "I use Redis.", "Redis", True, predicate="uses")
    add(
        "polarity",
        "I no longer live in Oslo.",
        "I live in Oslo.",
        "Oslo",
        False,
        predicate="lives_in",
    )
    add(
        "polarity",
        "I live in Oslo.",
        "I live in Oslo.",
        "Oslo",
        True,
        predicate="lives_in",
    )
    add(
        "mixed",
        "I live in Oslo. What does my plan cover for cancer?",
        "I live in Oslo.",
        "Oslo",
        True,
        predicate="lives_in",
        quote="I live in Oslo.",
    )
    add(
        "mixed",
        "I live in Oslo. What does my plan cover for cancer?",
        "I have cancer.",
        "cancer",
        False,
        quote="What does my plan cover for cancer?",
    )
    add(
        "mixed",
        "If I move to Oslo, my costs will change. I prefer short answers.",
        "I prefer short answers.",
        "short answers",
        True,
        kind="preference",
        predicate="prefers",
        quote="I prefer short answers.",
    )
    add(
        "mixed",
        "If I move to Oslo, my costs will change. I prefer short answers.",
        "I live in Oslo.",
        "Oslo",
        False,
        predicate="lives_in",
        quote="If I move to Oslo, my costs will change.",
    )
    add(
        "evidence",
        "My name is Sam.",
        "My name is Sam.",
        "Sam",
        True,
        predicate="name",
        quote="My name is Sam.",
    )
    add(
        "evidence",
        "My name is Sam.",
        "My name is Sam.",
        "Sam",
        False,
        predicate="name",
        quote="My name is Alex.",
    )
    add(
        "evidence",
        "I use Redis.",
        "I use Redis.",
        "Redis",
        True,
        predicate="uses",
        quote="I use Redis.",
    )
    add(
        "evidence",
        "I use Redis.",
        "I use PostgreSQL.",
        "PostgreSQL",
        False,
        predicate="uses",
        quote="I use PostgreSQL.",
    )
    return rows

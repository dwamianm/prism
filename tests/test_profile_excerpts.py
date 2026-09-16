"""Entity profile excerpts preserve episodes, qualifications and exact budgets."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from prme.models import MemoryNode
from prme.organizer.profiles import build_profile, mentions_entity
from prme.retrieval.tokenization import count_tokens


def source(content, number=1):
    at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return MemoryNode(
        id=UUID(int=number),
        user_id="u",
        node_type="note",
        content=content,
        created_at=at,
        event_time=at,
        valid_from=at,
    )


@pytest.mark.parametrize(
    ("text", "name", "expected"),
    [
        ("Joanna uses SQL", "Ann", False),
        ("ANN uses SQL", "Ann", True),
        ("Ann's database", "Ann", True),
        ("Anna uses SQL", "Ann", False),
        ("Jean-Luc uses SQL", "Jean-Luc", True),
        ("C++ is supported", "C++", True),
        ("Änne likes tea", "Änne", True),
        ("Märianne likes tea", "Änne", False),
    ],
)
def test_literal_complete_names(text, name, expected):
    assert mentions_entity(text, name) is expected


def test_identical_text_in_distinct_episodes_keeps_both_sources():
    sources = [source("Aurora uses SQL.", 1), source("Aurora uses SQL.", 2)]
    text, selected, tokens = build_profile(
        "Aurora", list(reversed(sources)), token_budget=1000, tokenizer="cl100k_base"
    )
    assert [node.id for node in selected] == [node.id for node in sources]
    assert text.count("Aurora uses SQL.") == 2
    assert tokens == count_tokens(text)


def test_equivalent_instants_render_identically_and_backend_order_is_irrelevant():
    original = source("Aurora uses SQL.")
    offset = timezone(timedelta(hours=-5))
    changed = original.model_copy(
        update={
            key: getattr(original, key).astimezone(offset)
            for key in ["created_at", "event_time", "valid_from"]
        }
    )
    first = build_profile(
        "Aurora", [original], token_budget=1000, tokenizer="cl100k_base"
    )
    second = build_profile(
        "Aurora", [changed], token_budget=1000, tokenizer="cl100k_base"
    )
    assert first[0] == second[0] and first[2] == second[2]
    more = [original, source("Aurora uses another tool.", 2)]
    assert (
        build_profile("Aurora", more, token_budget=1000, tokenizer="cl100k_base")[0]
        == build_profile(
            "Aurora", list(reversed(more)), token_budget=1000, tokenizer="cl100k_base"
        )[0]
    )


def test_exact_boundary_never_slices_source_or_treats_special_tokens_as_commands():
    node = source(
        "Aurora uses <|endoftext|> as literal data with an important final exception."
    )
    text, selected, tokens = build_profile(
        "Aurora", [node], token_budget=1000, tokenizer="cl100k_base"
    )
    assert node.content in text and selected == [node]
    assert (
        build_profile("Aurora", [node], token_budget=tokens, tokenizer="cl100k_base")[0]
        == text
    )
    assert (
        build_profile(
            "Aurora", [node], token_budget=tokens - 1, tokenizer="cl100k_base"
        )
        is None
    )
    assert (
        build_profile("Aurora", [node], token_budget=0, tokenizer="cl100k_base") is None
    )

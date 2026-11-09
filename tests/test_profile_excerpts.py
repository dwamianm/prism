"""Entity profile excerpts preserve episodes, qualifications and exact budgets."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

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


def claim(content, number=1):
    """A claim at the same event time as :func:`source`."""
    at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return MemoryNode(
        id=UUID(int=number),
        user_id="u",
        node_type="fact",
        content=content,
        created_at=at,
        event_time=at,
        valid_from=at,
    )


def test_a_turn_beats_its_narrower_claim_whatever_the_ids():
    """Issue #497: one event time, one budget slot, the turn wins."""
    at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    turn_text = "OK. My husband is Theo and my child is Nico."
    for _ in range(50):
        turn = MemoryNode(
            id=uuid4(), user_id="u", node_type="note", content=turn_text,
            created_at=at, event_time=at, valid_from=at,
        )
        narrower = MemoryNode(
            id=uuid4(), user_id="u", node_type="fact", content="Nico is child of Maya Okafor.",
            created_at=at, event_time=at, valid_from=at,
        )
        budget = build_profile(
            "Maya Okafor", [turn], token_budget=10_000, tokenizer="cl100k_base"
        )[2]
        text, selected, _ = build_profile(
            "Maya Okafor", [narrower, turn], token_budget=budget, tokenizer="cl100k_base"
        )
        assert [node.id for node in selected] == [turn.id]
        assert "Theo" in text


def test_equal_event_times_put_a_turn_before_its_claim():
    rich = source("Aurora keeps bees and sells honey.", 2)
    narrow = claim("Aurora keeps bees.", 1)
    for sources in ([narrow, rich], [rich, narrow]):
        _, selected, _ = build_profile(
            "Aurora", sources, token_budget=10_000, tokenizer="cl100k_base"
        )
        assert [node.id for node in selected] == [rich.id, narrow.id]


def test_equal_event_time_sources_with_more_terms_come_first():
    longer = source("Aurora keeps bees, sells honey and teaches beekeeping.", 2)
    shorter = source("Aurora keeps bees.", 1)
    for sources in ([shorter, longer], [longer, shorter]):
        _, selected, _ = build_profile(
            "Aurora", sources, token_budget=10_000, tokenizer="cl100k_base"
        )
        assert [node.id for node in selected] == [longer.id, shorter.id]


def test_equal_event_times_with_equal_term_counts_order_by_text():
    # Same type and term count, so only the text orders them; the text that
    # sorts first carries the higher id.
    later = source("Aurora uses SQL.", 1)
    earlier = source("Aurora uses C#.", 2)
    for sources in ([later, earlier], [earlier, later]):
        _, selected, _ = build_profile(
            "Aurora", sources, token_budget=10_000, tokenizer="cl100k_base"
        )
        assert [node.id for node in selected] == [earlier.id, later.id]


def test_identical_text_at_one_event_time_falls_back_to_the_id():
    first = source("Aurora uses SQL.", 1)
    second = source("Aurora uses SQL.", 2)
    budget = build_profile(
        "Aurora", [first], token_budget=10_000, tokenizer="cl100k_base"
    )[2]
    for sources in ([first, second], [second, first]):
        _, selected, _ = build_profile(
            "Aurora", sources, token_budget=budget, tokenizer="cl100k_base"
        )
        assert [node.id for node in selected] == [first.id]


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

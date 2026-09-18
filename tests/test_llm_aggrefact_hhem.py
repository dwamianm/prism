from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from benchmarks.integrations import run_llm_aggrefact_hhem as subject


def _rows() -> list[dict]:
    return [
        {
            "contamination_identifier": f"case-{label}-{index}",
            "dataset": "d",
            "label": label,
            "doc": "document",
            "claim": "claim",
        }
        for label, count in ((0, 4), (1, 5))
        for index in range(count)
    ]


def test_fresh_selection_is_balanced_disjoint_and_deterministic() -> None:
    rows = _rows()
    observed = frozenset({"case-0-0"})

    first = subject._select_fresh_balanced(rows, observed_ids=observed)
    second = subject._select_fresh_balanced(rows, observed_ids=observed)

    assert first == second
    assert Counter(row["label"] for row in first) == {0: 3, 1: 3}
    assert not observed & {row["contamination_identifier"] for row in first}


def test_dataset_label_counts_are_canonical() -> None:
    rows = _rows()
    rows[-1]["dataset"] = "a"

    result = subject._counts_by_dataset_label(rows)

    assert list(result) == ["a", "d"]
    assert result["a"] == {"1": 1}
    assert result["d"] == {"0": 4, "1": 4}


class _WordTokenizer:
    model_max_length = 512

    def __call__(self, text: str, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(input_ids=text.split())


def test_evidence_windows_cover_every_word_once_with_maximal_prefixes() -> None:
    tokenizer = _WordTokenizer()
    claim = "claim"
    overhead = subject._prompt_token_count(
        tokenizer,
        premise="",
        claim=claim,
    )

    windows = subject._evidence_windows(
        [("E0001", "one two three"), ("E0002", "four\nfive")],
        claim=claim,
        tokenizer=tokenizer,
        max_prompt_tokens=overhead + 2,
    )

    assert [text for text, _tokens in windows] == ["one two", "three four", "five"]
    assert " ".join(text for text, _tokens in windows).split() == [
        "one",
        "two",
        "three",
        "four",
        "five",
    ]
    assert all(tokens <= overhead + 2 for _text, tokens in windows)


def test_cli_deliberately_has_no_test_dataset_argument() -> None:
    parser = subject._parser()
    destinations = {action.dest for action in parser._actions}
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                destinations.update(item.dest for item in subparser._actions)

    assert "dev" in destinations
    assert "failed_registration" in destinations
    assert "test" not in destinations
    assert subject.HHEM_REVISION == "8e4a2e6e96c708cc76c2344f7e4757df2515292c"
    assert subject.FOUNDATION_REVISION == ("7bcac572ce56db69c1ea7c8af255c5d7c9672fc2")

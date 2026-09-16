from __future__ import annotations

import pytest

from benchmarks.integrations import run_llm_aggrefact_bespoke as subject


def test_sentence_fusion_requires_every_claim_sentence() -> None:
    score, sentence_scores = subject._fuse_sentence_probabilities(
        [
            (0, 0, 0.9),
            (0, 1, 0.2),
            (1, 0, 0.4),
            (1, 1, 0.8),
        ],
        claim_sentence_count=2,
    )

    assert sentence_scores == [0.9, 0.8]
    assert score == 0.8


def test_sentence_fusion_rejects_incomplete_inputs() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        subject._fuse_sentence_probabilities(
            [(0, 0, 0.9)],
            claim_sentence_count=2,
        )


class _Tokenizer:
    _tokens = {
        "yes": [10],
        "Yes": [11],
        "YES": [12],
        " yes": [13],
        " Yes": [14],
        "no": [20],
        "No": [21],
        "NO": [22],
        " no": [23],
        " No": [24],
    }

    def __call__(self, text: str, *, add_special_tokens: bool) -> dict[str, list[int]]:
        assert add_special_tokens is False
        return {"input_ids": self._tokens[text]}


def test_answer_token_ids_bind_all_single_token_forms() -> None:
    tokenizer = _Tokenizer()

    assert subject._answer_token_ids(tokenizer, "yes") == [10, 11, 12, 13, 14]
    assert subject._answer_token_ids(tokenizer, "no") == [20, 21, 22, 23, 24]

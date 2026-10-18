"""Token counting shared by memory renderers."""

from functools import lru_cache


@lru_cache(maxsize=8)
def _encoding(name: str):
    import tiktoken

    return tiktoken.get_encoding(name)


def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    """Count literal text with the named encoding, including special-token text."""
    return len(_encoding(encoding).encode(text, disallowed_special=()))


# The tiktoken pre-tokenizer patterns (as tiktoken 0.12 defines them) of gpt2,
# r50k_base, p50k_base and p50k_edit; cl100k_base; and o200k_base and
# o200k_harmony. Under each one, a newline between a line that ends in one of
# LINE_ENDS and a line that starts with one of LINE_STARTS ends a piece, and
# that piece is the same whether the text goes on after the newline or stops
# there: gpt2's family splits the newline off on its own, cl100k_base appends
# it to the punctuation before it, and o200k_base does too unless the next
# line starts with a slash. Byte pair merges never cross a piece, so the count
# of such lines joined by newlines is the sum of each line counted with its
# newline, the last line without one.
_LINE_INDEPENDENT_PATTERNS = frozenset({
    r"""'(?:[sdmt]|ll|ve|re)| ?\p{L}++| ?\p{N}++| ?[^\s\p{L}\p{N}]++|\s++$|\s+(?!\S)|\s""",
    r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s""",
    r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?|[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?|\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n/]*|\s*[\r\n]+|\s+(?!\S)|\s+""",
})
LINE_STARTS = frozenset("[-{")
LINE_ENDS = frozenset('."]}')


@lru_cache(maxsize=8)
def counts_lines_independently(encoding: str) -> bool:
    """Whether the encoding counts newline-joined lines one line at a time.

    True means that for lines which each start with one of ``LINE_STARTS``
    and end with one of ``LINE_ENDS`` (the first line may start with anything),
    the token count of the joined text equals the sum of each line counted
    with its newline, the last line without one. An encoding whose
    pre-tokenizer pattern is not one of those checked here, including one
    that a later tiktoken release defines differently, gets False, so its
    callers count the whole text instead.
    """
    return getattr(_encoding(encoding), "_pat_str", None) in _LINE_INDEPENDENT_PATTERNS

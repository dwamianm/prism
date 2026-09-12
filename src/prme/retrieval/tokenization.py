"""Token counting shared by memory renderers."""

from functools import lru_cache


@lru_cache(maxsize=8)
def _encoding(name: str):
    import tiktoken

    return tiktoken.get_encoding(name)


def count_tokens(text: str, encoding: str = "cl100k_base") -> int:
    """Count literal text with the named encoding, including special-token text."""
    return len(_encoding(encoding).encode(text, disallowed_special=()))

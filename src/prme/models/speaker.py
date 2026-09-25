"""Named conversation speakers carried by stored sources.

A conversation can have more than one human in it. ``store()`` and the ingest
methods accept an optional ``speaker`` name, kept with the source event and its
direct node under a reserved metadata key. The ``participant`` role (see
``prme.epistemic.inference``) marks a human speaker other than the memory's
owner as a first-party source. The name is an unverified caller assertion, not
an identity: PRME does not resolve it to an entity or use it for access.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

SPEAKER_METADATA_KEY = "prme_speaker_v1"

# A speaker is a name, and the reader format repeats it on every line.
MAX_SPEAKER_LENGTH = 200

# Characters that could break a line or reorder how a name displays. These
# categories and code points are fixed across Unicode versions, so a stored name
# is accepted or ignored the same way on every supported Python.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})
_BIDI_CONTROLS = frozenset(
    "؜‎‏‪‫‬‭‮⁦⁧⁨⁩"
)

# One leading parenthesized or bracketed group, such as a date, before the
# speaker's name: "(7:55 pm on 9 June, 2023) Caroline:" or
# "[2023/05/20 (Sat) 02:21] Caroline:".
_LEADING_GROUP_RE = re.compile(r"\s*(?:\[[^\[\]\n]{1,80}\]|\([^()\n]{1,80}\))")


class SpeakerError(ValueError):
    """A speaker name, or metadata carrying the reserved speaker key, was rejected."""


def normalize_speaker(speaker: Any) -> str | None:
    """Return the validated speaker name, or None when there is none.

    Leading and trailing whitespace is removed. The name must then be
    non-empty, at most ``MAX_SPEAKER_LENGTH`` characters, and free of control
    characters, line or paragraph separators and bidirectional controls.
    """
    if speaker is None:
        return None
    if not isinstance(speaker, str):
        raise SpeakerError("speaker must be a string")
    name = speaker.strip()
    if not name:
        raise SpeakerError("speaker must be a non-empty name")
    if len(name) > MAX_SPEAKER_LENGTH:
        raise SpeakerError(f"speaker must be at most {MAX_SPEAKER_LENGTH} characters")
    if any(
        char in _BIDI_CONTROLS or unicodedata.category(char) in _FORBIDDEN_CATEGORIES
        for char in name
    ):
        raise SpeakerError(
            "speaker must not contain control characters, line breaks or bidirectional controls"
        )
    return name


def attach_speaker(metadata: Mapping[str, Any] | None, speaker: str | None) -> Any:
    """Add a validated speaker to source metadata without changing the caller's dict.

    The metadata key is reserved so a speaker always comes from the
    ``speaker`` argument. Without a speaker, the metadata is returned as given
    and later admission checks validate it as before.
    """
    if isinstance(metadata, Mapping) and SPEAKER_METADATA_KEY in metadata:
        raise SpeakerError(f"metadata.{SPEAKER_METADATA_KEY} is reserved; pass speaker instead")
    name = normalize_speaker(speaker)
    if name is None:
        return metadata
    if metadata is not None and not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    result = dict(metadata or {})
    result[SPEAKER_METADATA_KEY] = name
    return result


def metadata_speaker(metadata: Mapping[str, Any] | None) -> str | None:
    """Read a stored speaker name, ignoring a malformed value.

    The key was not reserved before speakers existed, so older metadata can
    hold any value under it; only a valid name is returned.
    """
    if not metadata:
        return None
    value = metadata.get(SPEAKER_METADATA_KEY)
    return _stored_speaker(value) if isinstance(value, str) else None


@lru_cache(maxsize=4096)
def _stored_speaker(value: str) -> str | None:
    try:
        return normalize_speaker(value)
    except SpeakerError:
        return None


def text_states_speaker(text: str, speaker: str) -> bool:
    """Whether the text already begins with the speaker's name and a colon.

    One leading parenthesized or bracketed group, such as a date, may come
    first: ``(7:55 pm on 9 June, 2023) Caroline: ...``. The name is compared
    without regard to case.
    """
    group = _LEADING_GROUP_RE.match(text)
    start = group.end() if group is not None else 0
    body = text[start:start + len(speaker) + 64].lstrip()
    return body[:len(speaker) + 1].casefold() == f"{speaker}:".casefold()


def speaker_labeled(text: str, speaker: str | None) -> str:
    """Prefix ``Speaker: `` unless there is no speaker or the text already names it."""
    if speaker is None or text_states_speaker(text, speaker):
        return text
    return f"{speaker}: {text}"


__all__ = [
    "MAX_SPEAKER_LENGTH",
    "SPEAKER_METADATA_KEY",
    "SpeakerError",
    "attach_speaker",
    "metadata_speaker",
    "normalize_speaker",
    "speaker_labeled",
    "text_states_speaker",
]

"""The description prompt and the quoted-text reading of a description (RFC-0018).

The prompt is part of the observation contract and is versioned: an
observation records the ``describe_prompt_version`` that produced it, and a
new version produces new observations rather than rewriting old ones.
Version 2 asks for a facts-first description: one or two sentences on what
the image is and shows, then one standalone sentence per fact that names
its subject and quotes visible text verbatim. The spike's version 1 asked
for a narrated description ("the slide says ...") and extraction then
recorded the narration as the claim and no whiteboard value at all, so
version 1 is not implemented here.

The source file and frame time are never written into the text; they live
in the event's metadata and ``event_time``, because a "Video frame at 00:03
of ..." prefix was extracted as a fact.
"""

from __future__ import annotations

import re

DESCRIBE_PROMPT_VERSION = 2
"""The prompt version this module implements; recorded on every observation."""

DESCRIBE_PROMPT = """You are the eyes of a personal memory system. {kind_hint}
Write two parts in plain English with no headings, no bullet points and no commentary.
Part 1, one or two sentences: what kind of image this is and what it shows: the place, any people and what they are doing, the objects and the colors.
Part 2, the facts the image states, one sentence per fact. Each sentence must make sense on its own, so name its subject (for example: The store's total is "$8.40".). Quote visible text exactly as written, including numbers, dates, names and prices, inside double quotes. If a value is crossed out and replaced, say which value is crossed out and which is current. Read every value off any chart or table as its own sentence. If the image has no text, state three to five facts about what is visible. Do not guess at anything you cannot see."""

KIND_HINTS = {
    "video_frame": "The image is one frame of a video recording; say that it is a video frame.",
    "photo": "The image is a photo from the person's camera roll; say that it is a photo.",
}
"""Hint for each source kind (``metadata.modality``) that the prompt names."""


def describe_prompt(kind: str, version: int = DESCRIBE_PROMPT_VERSION) -> str:
    """Return the description prompt for a source of *kind* at prompt *version*."""
    if version != DESCRIBE_PROMPT_VERSION:
        raise ValueError(f"Description prompt version {version} is not implemented; use {DESCRIBE_PROMPT_VERSION}")
    try:
        hint = KIND_HINTS[kind]
    except KeyError:
        raise ValueError(f"Unknown source kind {kind!r}; expected one of {sorted(KIND_HINTS)}") from None
    return DESCRIBE_PROMPT.format(kind_hint=hint)


QUOTE_RE = re.compile(r'["\u201c]([^"\u201c\u201d]{1,200})["\u201d]')
"""A quoted value: straight or curly double quotes, which some models prefer."""
_DASHES = re.compile(r"[-‐‑‒–—]+")
_SPACES = re.compile(r"\s+")


def normalize_quote(text: str) -> str:
    """Normalize one quoted value so spelling noise does not read as a change.

    Case, any dash against a space, repeated whitespace and surrounding
    punctuation are ignored: the spike saw the same whiteboard quoted as
    "3-Nov" in one description and "3 Nov" in the next, which kept three
    extra frames. A real change in digits, words or order still differs.
    """
    lowered = _DASHES.sub(" ", text.casefold())
    return _SPACES.sub(" ", lowered).strip(" .,;:!?")


def quoted_text(description: str) -> frozenset[str]:
    """Return the set of normalized values a description quotes verbatim."""
    return frozenset(
        normalized for normalized in (normalize_quote(match) for match in QUOTE_RE.findall(description))
        if normalized
    )

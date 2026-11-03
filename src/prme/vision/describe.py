"""The description prompt and the quoted-text reading of a description (RFC-0018).

The prompt is part of the observation contract and is versioned: an
observation records the ``describe_prompt_version`` that produced it, and a
new version produces new observations rather than rewriting old ones.
Version 2 asks for a facts-first description: one or two sentences on what
the image is and shows, then one standalone sentence per fact that names
its subject and quotes visible text verbatim. Version 3 (the default since
2026-11-02, dwamianm/prism#388) is version 2 with three sentences added to
part 2: when people, hands, raised fingers or repeated objects are clearly
visible, state each count as a numeral, count raised fingers one at a time,
and say when a count is uncertain. Version 2 never asked for counts, and a
frame with both hands raised and all fingers spread was described as "hands
raised, palms facing forward". The spike's version 1 asked for a narrated
description ("the slide says ...") and extraction then recorded the
narration as the claim and no whiteboard value at all, so version 1 is not
implemented here.

The source file and frame time are never written into the text; they live
in the event's metadata and ``event_time``, because a "Video frame at 00:03
of ..." prefix was extracted as a fact.
"""

from __future__ import annotations

import re

DESCRIBE_PROMPT_VERSION = 3
"""The default prompt version for new observations (``vision.describe_prompt_version``)."""

_PROMPT_HEAD = """You are the eyes of a personal memory system. {kind_hint}
Write two parts in plain English with no headings, no bullet points and no commentary.
Part 1, one or two sentences: what kind of image this is and what it shows: the place, any people and what they are doing, the objects and the colors.
Part 2, the facts the image states, one sentence per fact. Each sentence must make sense on its own, so name its subject (for example: The store's total is "$8.40".). Quote visible text exactly as written, including numbers, dates, names and prices, inside double quotes. If a value is crossed out and replaced, say which value is crossed out and which is current. Read every value off any chart or table as its own sentence."""
_PROMPT_TAIL = """ If the image has no text, state three to five facts about what is visible. Do not guess at anything you cannot see."""

COUNT_INSTRUCTION = (
    " When people, hands, raised fingers or repeated objects are clearly visible, state how many there are as a"
    " numeral, one sentence per count, naming what was counted (the people, the raised hands, the raised fingers"
    " on each hand and in total, or the objects). Count raised fingers one at a time; a thumb or finger folded"
    " down against the palm is not raised. If you are not sure of a count, give your best count and say that the"
    " count is uncertain."
)
"""What version 3 adds to version 2, between the chart sentence and the no-text sentence."""

DESCRIBE_PROMPTS = {
    2: _PROMPT_HEAD + _PROMPT_TAIL,
    3: _PROMPT_HEAD + COUNT_INSTRUCTION + _PROMPT_TAIL,
}
"""Every implemented prompt by version, with ``{kind_hint}`` still to fill. Earlier versions stay selectable."""

DESCRIBE_PROMPT = DESCRIBE_PROMPTS[DESCRIBE_PROMPT_VERSION]
"""The default version's prompt."""

KIND_HINTS = {
    "video_frame": "The image is one frame of a video recording; say that it is a video frame.",
    "photo": "The image is a photo from the person's camera roll; say that it is a photo.",
}
"""Hint for each source kind (``metadata.modality``) that the prompt names."""


def describe_prompt(kind: str, version: int = DESCRIBE_PROMPT_VERSION) -> str:
    """Return the description prompt for a source of *kind* at prompt *version*."""
    try:
        template = DESCRIBE_PROMPTS[version]
    except KeyError:
        raise ValueError(
            f"Description prompt version {version} is not implemented; use one of {sorted(DESCRIBE_PROMPTS)}"
        ) from None
    try:
        hint = KIND_HINTS[kind]
    except KeyError:
        raise ValueError(f"Unknown source kind {kind!r}; expected one of {sorted(KIND_HINTS)}") from None
    return template.format(kind_hint=hint)


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


NUMBER_WORDS = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000, "dozen": 12,
}
"""Number words a description may state a count in. "One" is left out: it is as often a pronoun."""
_COUNT_RE = re.compile(r"\b(\d+(?:[.,]\d+)*|" + "|".join(NUMBER_WORDS) + r")\b", re.IGNORECASE)


def stated_counts(description: str) -> frozenset[str]:
    """Return the set of numbers a description states outside quotes, number words as digits.

    The prompt quotes visible text but not what the model counts, so "two
    fingers" and "three fingers" are the same quoted set and, with the pack's
    default embedding, 0.95 to 0.99 similar: without this, the repeat filter
    dropped a held count as a repeat of the previous one (dwamianm/prism#390).
    """
    unquoted = QUOTE_RE.sub(" ", description)
    return frozenset(
        str(NUMBER_WORDS[token.casefold()]) if token.casefold() in NUMBER_WORDS else token.replace(",", "")
        for token in _COUNT_RE.findall(unquoted)
    )

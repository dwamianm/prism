"""Check a fact's self-contained text against the turn it came from (#91).

With ``enable_fact_text_resolution`` the extractor also writes each fact's
supporting sentences as a ``resolved_text`` that stands alone: a pronoun for a
named person becomes the name, and a relative date becomes the date it means.
That text is model output, so it is accepted only when those are its only
changes. Every other word must match a verbatim span of the new turn, so a
resolved text can never carry a negation, condition or claim that the turn
does not.

The span is whole sentences of the new turn, with any qualifying sentence that
follows (the rule ``_supporting_claim_passage`` applies to citations). Each
replaced pronoun must become a name given by the turn's speaker, the turn
itself or an earlier turn in the window, and every other word the replacement
adds must come from one of those turns or be a small function word. Each
replaced relative date must become an absolute date that the date resolver
gives for it from the source time. A turn in the window that supplied a name
is recorded, so the fact can link to every turn it used.

A rejected text is dropped and the fact keeps its source passage as its text,
exactly as without the option.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import difflib
import re

from prme.ingestion.grounding import _mentioned, _supporting_claim_passage
from prme.models.speaker import _LEADING_GROUP_RE

RESOLUTION_METHOD = "reference_resolution_v1"


@dataclass(frozen=True)
class WindowTurn:
    """One earlier turn of the session, as the extractor was shown it."""

    event_id: str
    speaker: str | None
    text: str


@dataclass(frozen=True)
class ResolutionSources:
    """What a resolved text may take its names and dates from."""

    event_id: str
    text: str
    speaker: str | None
    reference_time: datetime | None
    window: Sequence[WindowTurn] = ()


# Comma and quote differences carry no claim, so they are not compared.
_IGNORED_PUNCTUATION = frozenset({",", "'", '"', "‘", "’", "“", "”"})
_TOKEN_RE = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]")

_PERSON_REFERENCES = frozenset("""
i me my mine myself we us our ours ourselves you your yours yourself yourselves
he him his himself she her hers herself they them their theirs themselves
i'm i've i'd i'll we're we've we'd we'll you're you've you'd you'll
he's he'd he'll she's she'd she'll they're they've they'd they'll
""".split())
# The speaker is the only person "I" can be, and never the person "you" is.
_FIRST_PERSON_SINGULAR = frozenset("i me my mine myself i'm i've i'd i'll".split())
_SECOND_PERSON = frozenset(
    "you your yours yourself yourselves you're you've you'd you'll".split()
)
# A verb after a replaced "I", "we" or "you" may change to agree with the name:
# "I love" to "Caroline loves", "I don't" to "Caroline doesn't".
_AGREEMENT = {
    "am": "is", "are": "is", "were": "was", "have": "has", "do": "does",
    "don't": "doesn't", "haven't": "hasn't", "aren't": "isn't", "weren't": "wasn't",
}
# The longest differing segment tried as several replacements.
_MAX_SPLIT_TOKENS = 12
# Words a name replacement may add around the name: "I'm" becomes "Caroline
# is", "we" becomes "Caroline and Melanie".
_NAME_FUNCTION_WORDS = frozenset(
    "is am are was were has have had would will and the a an of s".split()
)
_MONTHS = {
    name: number
    for number, names in enumerate((
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
        ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
        ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
        ("december", "dec"),
    ), 1)
    for name in names
}
_WEEKDAYS = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday".split()
)
# A segment is a relative date only when it holds one of these words, so a
# stray word that dateparser happens to read as a date is not replaced.
_RELATIVE_DATE_CUES = frozenset(
    "yesterday today tonight tomorrow last next this ago past previous coming "
    "week weekend weeks month months year years day days earlier recently".split()
) | _WEEKDAYS
_DATE_FUNCTION_WORDS = frozenset(
    "on in the of at around about during before after prior to earlier previous "
    "preceding following week weekend weeks month months year years day days "
    "a an that which st nd rd th morning afternoon evening night".split()
) | _WEEKDAYS
# Words that turn the source date into a relation: "the week before 8 May 2023".
_DATE_RELATIONS = frozenset(
    "before after prior earlier previous preceding following of during".split()
)

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_WORD = r"([A-Za-z]{3,9})\.?"
_DAY_MONTH_YEAR_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_WORD},?\s+(\d{{4}})\b")
_MONTH_DAY_YEAR_RE = re.compile(rf"\b{_MONTH_WORD}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")
_MONTH_YEAR_RE = re.compile(rf"\b{_MONTH_WORD},?\s+(\d{{4}})\b")
_YEAR_RE = re.compile(r"\b(\d{4})\b")


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int

    @property
    def key(self) -> str:
        return self.text.casefold().replace("’", "'")


def _tokens(text: str, start: int = 0) -> list[_Token]:
    return [
        _Token(match.group(0), match.start(), match.end())
        for match in _TOKEN_RE.finditer(text, start)
        if match.group(0) not in _IGNORED_PUNCTUATION
    ]


def _label_end(text: str, speaker: str | None) -> int:
    """Where a turn's text starts after a leading "(time) Speaker:" label."""
    if speaker is None:
        return 0
    group = _LEADING_GROUP_RE.match(text)
    position = group.end() if group is not None else 0
    while position < len(text) and text[position].isspace():
        position += 1
    label = f"{speaker}:"
    if text[position:position + len(label)].casefold() == label.casefold():
        return position + len(label)
    return 0


def _bare_word(key: str) -> str:
    """A word without its possessive ending: "melanie's" becomes "melanie"."""
    for ending in ("'s", "'"):
        if key.endswith(ending) and len(key) > len(ending):
            return key[: -len(ending)]
    return key


def _capitalized_names(text: str) -> set[str]:
    """Words capitalized where a sentence does not start, read as names."""
    names = set()
    previous = None
    for token in _TOKEN_RE.finditer(text):
        word = token.group(0)
        if (
            previous is not None
            and previous not in {".", "!", "?", ":", "(", "[", "\n"}
            and word[:1].isupper()
            and word.casefold() not in _PERSON_REFERENCES
        ):
            names.add(_bare_word(word.casefold().replace("’", "'")))
        previous = word
    return names


class _Resolver:
    """Classify the differences between a passage span and its resolved text."""

    def __init__(self, sources: ResolutionSources, resolve_date: Callable[..., str | None]) -> None:
        self._sources = sources
        self._resolve_date = resolve_date
        # Where each known word can come from, the new turn first and then the
        # window from its most recent turn back.
        self._origins: list[tuple[dict, set[str], set[str]]] = []
        speaker_words = {token.key for token in _tokens(sources.speaker or "")}
        turn_words = {_bare_word(token.key) for token in _tokens(sources.text)}
        self._origins.append(({"source": "speaker", "event_id": sources.event_id}, speaker_words, speaker_words))
        self._origins.append((
            {"source": "source_turn", "event_id": sources.event_id},
            turn_words, _capitalized_names(sources.text),
        ))
        for turn in reversed(list(sources.window)):
            speaker = {token.key for token in _tokens(turn.speaker or "")}
            self._origins.append(({"source": "window", "event_id": turn.event_id}, speaker, speaker))
            self._origins.append((
                {"source": "window", "event_id": turn.event_id},
                {_bare_word(token.key) for token in _tokens(turn.text)},
                _capitalized_names(turn.text),
            ))

    def classify(self, original: list[_Token], replacement: list[_Token], *,
                 at_sentence_start: bool, passage: str, resolved: str,
                 after_name: bool = False) -> list[dict] | None:
        """Accepted replacements for one differing segment, or None."""
        record = self._one(original, replacement, at_sentence_start=at_sentence_start,
                           passage=passage, resolved=resolved, after_name=after_name)
        if record is not None:
            return [record]
        # Adjacent replacements can arrive as one segment: "Yesterday I" to
        # "On 7 May 2023 Caroline". Try each way of splitting it in two. A
        # longer segment is a rewrite, not a set of replacements, and trying
        # every split of it would take exponential time.
        if len(original) + len(replacement) > _MAX_SPLIT_TOKENS:
            return None
        for left in range(1, len(original)):
            for right in range(1, len(replacement)):
                head = self.classify(original[:left], replacement[:right],
                                     at_sentence_start=at_sentence_start, passage=passage,
                                     resolved=resolved, after_name=after_name)
                if head is None:
                    continue
                tail = self.classify(original[left:], replacement[right:],
                                     at_sentence_start=False, passage=passage, resolved=resolved,
                                     after_name=head[-1]["kind"] == "name")
                if tail is not None:
                    return head + tail
        return None

    def _one(self, original: list[_Token], replacement: list[_Token], *,
             at_sentence_start: bool, passage: str, resolved: str,
             after_name: bool = False) -> dict | None:
        if not replacement:
            return None
        original_keys = [token.key for token in original]
        text = resolved[replacement[0].start:replacement[-1].end]
        source_text = passage[original[0].start:original[-1].end] if original else ""
        if after_name and len(original) == 1 and len(replacement) == 1 and _agrees(
            original[0].key, replacement[0].key
        ):
            return {"kind": "agreement", "original": source_text, "replacement": text, "sources": []}
        if not original:
            # An omitted subject may be supplied only where a sentence starts:
            # "Gonna continue" to "Caroline is gonna continue".
            if not at_sentence_start:
                return None
            sources = self._name_sources(replacement)
            if sources is None:
                return None
            return {"kind": "name", "original": "", "replacement": text, "sources": sources}
        if all(key in _PERSON_REFERENCES for key in original_keys):
            sources = self._name_sources(replacement)
            if sources is None or not self._fits_person(original_keys, replacement):
                return None
            return {"kind": "name", "original": source_text, "replacement": text, "sources": sources}
        if any(key in _PERSON_REFERENCES for key in original_keys):
            return None
        return self._date(original_keys, source_text, text, replacement)

    def _fits_person(self, original_keys: list[str], replacement: list[_Token]) -> bool:
        """Whether the name matches who the pronoun can be, given the speaker."""
        speaker = {token.key for token in _tokens(self._sources.speaker or "")}
        named = {
            _bare_word(token.key) for token in replacement
            if _bare_word(token.key) not in _NAME_FUNCTION_WORDS and token.text[:1].isalnum()
        }
        if any(key in _FIRST_PERSON_SINGULAR for key in original_keys):
            # Without a speaker, "I" has no name to take.
            return bool(speaker) and named <= speaker
        if any(key in _SECOND_PERSON for key in original_keys):
            return not speaker or not named <= speaker
        return True

    def _name_sources(self, replacement: list[_Token]) -> list[dict] | None:
        """Where each added word came from; None if a word has no source or no name is given."""
        words = [_bare_word(token.key) for token in replacement if token.text.isalnum() or "'" in token.key]
        content = [word for word in words if word not in _NAME_FUNCTION_WORDS]
        if not content or any(word in _PERSON_REFERENCES for word in content):
            return None
        named = False
        sources: list[dict] = []
        for word in content:
            origin = next(((where, names) for where, known, names in self._origins if word in known), None)
            if origin is None:
                return None
            where, names = origin
            named = named or word in names
            if where not in sources:
                sources.append(where)
        return sources if named else None

    def _date(self, original_keys: list[str], source_text: str, text: str,
              replacement: list[_Token]) -> dict | None:
        reference = self._sources.reference_time
        if reference is None or not any(key in _RELATIVE_DATE_CUES for key in original_keys):
            return None
        resolved = self._resolve_date(source_text, reference_time=reference)
        if resolved is None:
            return None
        target = datetime.fromisoformat(resolved).date()
        written = _written_date(text)
        if written is None:
            return None
        allowed = _DATE_FUNCTION_WORDS | set(original_keys) | set(_MONTHS)
        for token in replacement:
            key = token.key
            if not token.text.isalnum() or key.isdigit():
                continue
            if key not in allowed and not re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)", key):
                return None
        reference_date = _utc_date(reference)
        if _matches(written, target):
            used = target
        elif _matches(written, reference_date) and any(
            token.key in _DATE_RELATIONS for token in replacement
        ):
            used = reference_date
        else:
            return None
        return {
            "kind": "date", "original": source_text, "replacement": text,
            "date": used.isoformat(),
            "sources": [{"source": "source_time", "reference_time": reference.isoformat()}],
        }


def _agrees(verb: str, agreed: str) -> bool:
    """Whether ``agreed`` is ``verb`` in the third person singular."""
    if _AGREEMENT.get(verb) == agreed:
        return True
    if not verb.isalpha() or verb in _PERSON_REFERENCES:
        return False
    return agreed in {verb + "s", verb + "es"} or (
        verb.endswith("y") and agreed == verb[:-1] + "ies"
    )


def _utc_date(moment: datetime) -> date:
    return moment.astimezone(timezone.utc).date() if moment.tzinfo is not None else moment.date()


def _written_date(text: str) -> tuple[int, int | None, int | None] | None:
    """The one absolute date a replacement writes, at the precision it writes it."""
    for pattern, order in (
        (_ISO_DATE_RE, "ymd"), (_DAY_MONTH_YEAR_RE, "dmy"), (_MONTH_DAY_YEAR_RE, "mdy"),
    ):
        found = pattern.findall(text)
        if len(found) > 1:
            return None
        if found:
            parts = dict(zip(order, found[0]))
            month = _month(parts["m"])
            if month is None:
                return None
            return int(parts["y"]), month, int(parts["d"])
    found = _MONTH_YEAR_RE.findall(text)
    months = [(month_word, year) for month_word, year in found if _month(month_word) is not None]
    if len(months) > 1:
        return None
    if months:
        return int(months[0][1]), _month(months[0][0]), None
    years = _YEAR_RE.findall(text)
    if len(years) == 1:
        return int(years[0]), None, None
    return None


def _month(word: str) -> int | None:
    if word.isdigit():
        number = int(word)
        return number if 1 <= number <= 12 else None
    return _MONTHS.get(word.casefold())


def _matches(written: tuple[int, int | None, int | None], day: date) -> bool:
    year, month, day_of_month = written
    return (
        year == day.year
        and (month is None or month == day.month)
        and (day_of_month is None or day_of_month == day.day)
    )


def _trimmed(text: str) -> str:
    return re.sub(r"^\W+|\W+$", "", text)


def resolve_fact_text(
    resolved_text: str,
    passage: str,
    sources: ResolutionSources,
    *,
    subject: str,
    object_value: str,
    resolve_date: Callable[..., str | None],
) -> tuple[dict | None, str | None]:
    """Return the resolution record for an accepted text, or the reason it was rejected.

    ``passage`` is the fact's grounded evidence passage, a verbatim part of
    ``sources.text``. ``resolve_date(text, reference_time=...)`` returns the ISO
    date a relative reference means, as the ingestion pipeline resolves it.
    """
    body_start = _label_end(passage, sources.speaker)
    original = _tokens(passage, body_start)
    replacement = _tokens(resolved_text)
    if not original or not replacement:
        return None, "empty passage or resolved text"
    matcher = difflib.SequenceMatcher(
        a=[token.key for token in original], b=[token.key for token in replacement], autojunk=False,
    )
    opcodes = matcher.get_opcodes()
    # Passage words before and after what the resolved text covers are other
    # sentences; the sentence check below decides whether leaving them out is
    # allowed.
    while opcodes and opcodes[0][0] == "delete":
        opcodes.pop(0)
    while opcodes and opcodes[-1][0] == "delete":
        opcodes.pop()
    if not any(tag == "equal" for tag, *_ in opcodes):
        return None, "resolved text does not follow the passage"
    candidates = [opcodes]
    tag, a_start, a_end, b_start, b_end = opcodes[0]
    if tag == "replace":
        # A left-out earlier sentence and the first replacement can arrive as
        # one change. Try each point where the covered span could start.
        candidates += [
            [("replace" if split < a_end else "insert", split, a_end, b_start, b_end), *opcodes[1:]]
            for split in range(a_start + 1, a_end + 1)
        ]
    reasons = []
    for candidate in candidates:
        record, reason = _check(candidate, original, replacement, passage, body_start, resolved_text,
                                sources, subject=subject, object_value=object_value, resolve_date=resolve_date)
        if record is not None:
            return record, None
        reasons.append(reason)
    # The reading with no split explains a rejection best.
    return None, reasons[0]


def _check(opcodes, original, replacement, passage, body_start, resolved_text, sources, *,
           subject, object_value, resolve_date) -> tuple[dict | None, str | None]:
    first = opcodes[0][1]
    last = opcodes[-1][2]
    if first >= last:
        return None, "resolved text does not follow the passage"
    span = passage[original[first].start:original[last - 1].end]
    sentences = _supporting_claim_passage(span, passage[body_start:])
    if sentences is None or _trimmed(sentences) != _trimmed(span):
        return None, "resolved text is not whole sentences of the passage"

    resolver = _Resolver(sources, resolve_date)
    replacements: list[dict] = []
    index = 0
    name_end = None  # where the last name replacement ended in the passage
    while index < len(opcodes):
        tag, a_start, a_end, b_start, b_end = opcodes[index]
        if tag == "equal":
            index += 1
            continue
        before = original[a_start - 1].text if a_start > 0 else None
        at_sentence_start = a_start == first or before in {".", "!", "?"}
        # A replacement can share a word with what it replaces, which splits it
        # in two around that word: "last week" to "the week before 8 May
        # 2023". Take in the following changes across short equal runs.
        records = None
        for stop in range(index, min(index + 5, len(opcodes)), 2):
            if stop > index and (opcodes[stop - 1][0] != "equal" or opcodes[stop - 1][2] - opcodes[stop - 1][1] > 2):
                break
            if opcodes[stop][0] == "equal":
                break
            records = resolver.classify(
                original[a_start:opcodes[stop][2]], replacement[b_start:opcodes[stop][4]],
                at_sentence_start=at_sentence_start, passage=passage, resolved=resolved_text,
                # "I really love" to "Caroline really loves": one word between.
                after_name=name_end is not None and a_start - name_end <= 1,
            )
            if records is not None:
                if records[-1]["kind"] == "name":
                    name_end = opcodes[stop][2]
                index = stop + 1
                break
        if records is None:
            if tag == "delete":
                return None, "resolved text leaves out words of the passage"
            changed = passage[original[a_start].start:original[a_end - 1].end] if a_end > a_start else ""
            return None, f"unsupported change to {changed!r}"
        replacements.extend(records)

    added = " ".join(record["replacement"] for record in replacements)
    for label, value in (("subject", subject), ("object", object_value)):
        if not (_mentioned(value, span) or _mentioned(value, added)):
            return None, f"resolved text does not state the fact's {label}"

    window_ids = []
    for record in replacements:
        for where in record["sources"]:
            if where.get("source") == "window" and where["event_id"] not in window_ids:
                window_ids.append(where["event_id"])
    return {
        "method": RESOLUTION_METHOD,
        "original": span,
        "replacements": replacements,
        "window_event_ids": window_ids,
    }, None


__all__ = [
    "RESOLUTION_METHOD",
    "ResolutionSources",
    "WindowTurn",
    "resolve_fact_text",
]

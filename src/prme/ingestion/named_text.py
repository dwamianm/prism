"""Write the owner's name into a claim's text (``enable_named_claim_text``).

A claim's text is the user's own sentence ("My husband is Theo and my child
is Nico."), or a statement built from its subject, predicate and object when
another claim of the message already holds that sentence ("The speaker has
child Nico."). Neither says who. The claim is attached to the person's entity
when the turn's I, me and my bind to a name (``enable_speaker_references``,
``enable_owner_name_carry``), but the text itself, which is what retrieval
indexes, what the reader context shows for a claim and what an application
reads from ``results``, still says "my" or "the speaker".

With the option, a claim from a turn whose first person binds to a name is
stored with the name written in. The rewrite is deterministic and bounded,
so it adds no model call and a recorded extraction replays unchanged:

- ``my`` and ``mine`` become the possessive of the name, ``me`` the name;
- ``I`` becomes the name when what follows it has a known third-person form
  (``I am`` to ``is``, ``I have`` to ``has``, ``I don't`` to ``doesn't``, a
  modal, a past tense) or when the name is one of a coordinated subject
  ("Jordan and I are"), where the verb already agrees;
- when an ``I`` cannot be rewritten safely (a present tense verb the table
  does not know, ``I'd``, ``myself``, an inverted question "Do I need"), the
  sentence is kept whole and the name labels it, as the reader context labels
  a turn: "Maya: I live in Denver with my partner Sam."

Only the plain spellings are the pronoun, so an all-caps word that happens to
read as one is left alone ("I have ME/CFS" keeps ME/CFS, and "Augusta, ME"
is unchanged), and a lowercase i is not read as I, so a turn that writes only
i is left unnamed. A first person inside double quotes, or anywhere in a text
that carries single-quoted speech, is someone else's speech, a set phrase is
not a possessive ("Oh my god", "Oh my,"), "mine" after a determiner or noun is
the noun ("a coal mine"), and a capitalized title or numbered part before the
I makes it a numeral ("World War I"); each of those keeps the sentence whole
under the label too.

A first version gave such a claim its statement instead ("Maya lives in
Denver."). On the chat probe that turned long sentence claims into short
statements that repeat a question's name and verb, and the answers ranked in
the top 3 fell from 14 to 8 of 18 (BENCHMARKS.md, "Named claim text"); the
label keeps the sentence, and the ranks.

Nothing is invented: the name is the one the caller gave as the turn's
``speaker``, the one the turn introduced ("I am Alex Rivera"), or the one an
earlier user turn gave (the owner name carry). A claim whose text names no
one in the first person is left as it is. The evidence quote stays verbatim;
``metadata.named_text`` records the name and the sentence the claim was written from.
"""

from __future__ import annotations

import re

NAMED_TEXT_KEY = "named_text"

# What follows I, and its third-person singular form. A contraction is matched attached to the I.
_AFTER_I = {
    "am": "is", "'m": "is", "was": "was", "have": "has", "'ve": "has", "had": "had", "do": "does",
    "did": "did", "will": "will", "'ll": "will", "would": "would", "can": "can", "could": "could",
    "should": "should", "may": "may", "might": "might", "must": "must", "shall": "shall",
    "cannot": "cannot", "can't": "can't", "won't": "won't", "wouldn't": "wouldn't",
    "couldn't": "couldn't", "shouldn't": "shouldn't", "mustn't": "mustn't", "don't": "doesn't",
    "didn't": "didn't", "haven't": "hasn't", "hadn't": "hadn't", "wasn't": "wasn't",
}
# Past forms that are never a base form, so they read the same after a name.
_PAST = frozenset("""
ate became began bent bled bought bound bred broke brought built came caught chose dealt drank drew dreamt drove
dug fed fell felt fled flew forgot fought found froze gave got grew heard held hid hung kept knelt knew laid led
left lent lit lost made meant met mistook overcame overslept paid rang ran rode rose said sang sank sat saw sent
shook shone shot showed shrank slept slid sold sought spent spoke sprang stole stood struck strove swam swept swung
taught threw told took tore understood undertook went woke won wore wound wrote withdrew
""".split())
# Words ending in "ed" that are a base form, not a past tense.
_BASE_ED = frozenset("bed bleed breed deed embed exceed feed heed need proceed seed shed shred sled speed succeed weed wed"
                     .split())
# Adverbs that may sit between I and its verb.
_ADVERBS = frozenset("""
actually already also always basically currently definitely finally generally honestly just mainly mostly never
normally now occasionally often only personally probably rarely really recently sometimes still truly typically
usually
""".split())
# An interjection before "my" lets a set phrase start ("Oh my god"), but "Oh my phone" is still a possessive.
_INTERJECTIONS = frozenset({"oh"})
# "my god", "my goodness": a set phrase only at the start of a clause ("My god", "Oh, my god").
_MY_SET_PHRASE_AFTER = frozenset({"god", "gosh", "goodness", "lord", "heavens"})
# A capitalized word before I that heads a title or a numbered part makes I a numeral ("World War I", "Part I").
_TITLE_HEADS = frozenset("""
act amendment annex appendix article book chapter class clause episode exhibit figure grade group level paragraph part
phase rank rule scene schedule section stage subsection table title type volume war world
""".split())
# "mine" is the possessive pronoun after a copula, "of" or a coordinator ("the report is mine", "a friend of
# mine"); after a determiner or a noun used attributively it is the noun ("a coal mine").
_MINE_POSSESSIVE_BEFORE = frozenset({"am", "are", "be", "been", "being", "is", "of", "was", "were"})
_DETERMINERS = frozenset({"a", "an", "the", "this", "that", "these", "those", "my", "your", "his", "her", "its",
                          "our", "their", "no", "some", "any", "every", "each", "another"})
# A coordinated subject ("Jordan and I are") keeps its verb; an inverted auxiliary ("Do I need") cannot be rewritten.
# After a coordinator I may also start a clause ("I left and I am tired"), so there the name is written only before a
# verb that reads the same both ways: a plural form, a past tense or a form that does not change after a name. A comma
# is not a coordinator: "Yes, I have" is a clause.
_COORDINATORS = frozenset({"and", "or", "nor"})
_PLURAL_FORMS = frozenset({"are", "were", "aren't", "weren't"})
_AUXILIARIES = frozenset("am are is was were do does did have has had can could will would should may might must shall"
                         .split())

# Case-sensitive on purpose: only the plain spellings are the pronoun. An all-caps word that happens to read as
# one is usually not ("ME/CFS", "Augusta, ME"), and a lowercase i is not read as I.
_FIRST_PERSON_RE = re.compile(
    r"(?<![\w'’‘])(I(?!\.e\.)|[Mm]e|[Mm]y|[Mm]ine|[Mm]yself)(?![\w'’])"
    r"|(?<![\w‘])I(?=['’](?:[mM]|[vV][eE]|[lL][lL]|[dD])\b)")
_CONTRACTION_RE = re.compile(r"['’](m|ve|ll|d)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]*")
_QUOTE_CHARS = frozenset('"“”')
_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'’")
# A single quote that opens speech: not between two letters, and before a letter. Contractions sit between letters.
_SINGLE_QUOTED_SPEECH_RE = re.compile(r"(?<!\w)['‘’](?=[A-Za-z])")


def has_first_person(text: str) -> bool:
    """Whether the text refers to its speaker in the first person singular."""
    return _FIRST_PERSON_RE.search(text) is not None


def possessive(name: str) -> str:
    """The possessive of a name: Maya's, James'."""
    return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"


def _previous_word_span(text: str, end: int) -> tuple[str, int] | None:
    """The word or comma right before ``end`` and where it starts, as written; None at the start of the text.

    Scans backward and copies only the word, so a claim's length does not multiply the work per match.
    """
    cursor = end
    while cursor > 0 and text[cursor - 1].isspace():
        cursor -= 1
    if cursor > 0 and text[cursor - 1] == ",":
        return ",", cursor - 1
    word_end = cursor
    while cursor > 0 and text[cursor - 1] in _WORD_CHARS:
        cursor -= 1
    if cursor == word_end:
        return None
    return text[cursor:word_end], cursor


def _previous_word(text: str, end: int) -> str | None:
    """The word or comma right before ``end``, lowercased."""
    span = _previous_word_span(text, end)
    return span[0].casefold() if span is not None else None


def _next_word(text: str, start: int) -> str | None:
    """The word right after ``start``, lowercased; None when the next thing is not a word."""
    cursor = start
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    match = _WORD_RE.match(text, cursor)
    return match.group(0).casefold() if match else None


def _followed_by_punctuation(text: str, end: int) -> bool:
    cursor = end
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    return cursor >= len(text) or text[cursor] in ".,;:!?…"


def _possessive_mine(text: str, start: int) -> bool:
    """Whether this "mine" is the possessive pronoun, not the noun ("a coal mine")."""
    previous = _previous_word(text, start)
    if previous is None or previous in _MINE_POSSESSIVE_BEFORE or previous in _COORDINATORS:
        return True
    if previous.endswith(("'s", "’s")):
        return True
    if previous in _DETERMINERS:
        return False
    # "a coal mine", "the gold mine": a determiner two words back heads the noun phrase.
    span = _previous_word_span(text, start)
    return _previous_word(text, span[1]) not in _DETERMINERS


def _keep_apostrophe(replacement: str, source: str) -> str:
    """A replacement keeps the apostrophe the user typed."""
    return replacement.replace("'", "’") if "’" in source else replacement


def _past_tense(word: str) -> bool:
    lowered = word.casefold()
    return lowered in _PAST or (lowered.endswith("ed") and len(lowered) >= 4 and lowered not in _BASE_ED)


def named_claim_text(text: str, name: str) -> str:
    """The text with its first person written as ``name``, or labeled with the name when that is not safe."""
    rewritten = name_first_person(text, name)
    return rewritten if rewritten is not None else f"{name}: {text}"


def name_first_person(text: str, name: str) -> str | None:
    """The text with its first person singular written as ``name``, or None when it cannot be done safely.

    Returns the text unchanged when it has no first person singular form.
    """
    out: list[str] = []
    position = 0
    quote_cursor = 0
    inside_double_quotes = False
    single_quoted = _SINGLE_QUOTED_SPEECH_RE.search(text) is not None
    # Two double-quoted spans, or one nested inside another, defeat a parity toggle on the quote marks.
    several_quotes = sum(text.count(char) for char in _QUOTE_CHARS) >= 4
    for match in _FIRST_PERSON_RE.finditer(text):
        word = match.group(0)
        lowered = word.casefold()
        start = match.start()
        # Track the double-quote state in one forward pass instead of rescanning the prefix for every match.
        for index in range(quote_cursor, start):
            if text[index] in _QUOTE_CHARS:
                inside_double_quotes = not inside_double_quotes
        quote_cursor = start
        if single_quoted or several_quotes or inside_double_quotes:
            # Someone else is quoted ("My husband said \"I was fired.\"", "He said 'call me later.'").
            # Writing the name there would put their words in the speaker's mouth, so the sentence is kept
            # whole and the name labels it.
            return None
        out.append(text[position:start])
        position = match.end()
        span = _previous_word_span(text, start)
        previous = span[0].casefold() if span is not None else None
        if lowered in {"my", "mine"}:
            if lowered == "my":
                # "Oh my," is a set phrase, and "My god" or "Oh my god" is one at the start of a clause.
                if _followed_by_punctuation(text, match.end()):
                    return None
                if ((previous is None or previous == "," or previous in _INTERJECTIONS)
                        and _next_word(text, match.end()) in _MY_SET_PHRASE_AFTER):
                    return None
            elif not _possessive_mine(text, start):
                # "a coal mine": the noun, not the possessive pronoun.
                return None
            out.append(possessive(name))
            continue
        if lowered == "me":
            out.append(name)
            continue
        if lowered == "myself":
            return None
        # I: decide what follows.
        if span is not None and span[0][:1].isupper() and previous in _TITLE_HEADS:
            # "World War I": a title or numbered part before the I makes it a numeral, not a subject.
            return None
        contraction = _CONTRACTION_RE.match(text, position)
        if contraction is not None:
            key = f"'{contraction.group(1).casefold()}"
            form = _AFTER_I.get(key)
            # "Jordan and I've" may be a coordinated subject ("have") or a clause ("has").
            if form is None or (previous in _COORDINATORS and key == "'ve"):
                return None
            if contraction.group(1).isupper():
                form = form.upper()
            out.append(f"{name} {form}")
            position = contraction.end()
            continue
        if previous in _AUXILIARIES:
            return None
        rest = text[position:]
        gap = len(rest) - len(rest.lstrip())
        words_after = []
        cursor = position + gap
        verb = None
        while True:
            next_word = _WORD_RE.match(text, cursor)
            if next_word is None:
                break
            candidate = next_word.group(0)
            if candidate.casefold().replace("’", "'") in _ADVERBS and verb is None and not words_after:
                words_after.append((candidate, next_word.end()))
                cursor = next_word.end()
                cursor += len(text[cursor:]) - len(text[cursor:].lstrip())
                continue
            verb = (candidate, next_word.start(), next_word.end())
            break
        if verb is None:
            return None
        verb_word, verb_start, verb_end = verb
        key = verb_word.casefold().replace("’", "'")
        if previous in _COORDINATORS and key != "am":
            if key in _PLURAL_FORMS or _AFTER_I.get(key) == key or (key not in _AFTER_I and _past_tense(verb_word)):
                out.append(name)
                continue
            return None
        if key in _AFTER_I:
            replacement = _keep_apostrophe(_AFTER_I[key], verb_word)
            if verb_word.isupper():
                # "I AM PREGNANT" keeps its emphasis.
                replacement = replacement.upper()
            elif verb_word[:1].isupper():
                replacement = replacement[:1].upper() + replacement[1:]
            out.append(name)
            out.append(text[position:verb_start])
            out.append(replacement)
            position = verb_end
        elif _past_tense(verb_word):
            out.append(name)
        else:
            return None
    out.append(text[position:])
    return "".join(out)

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
dug fed fell felt fled flew forgot fought found froze gave got grew heard held hid hung kept knelt knew laid lay led
left lent lit lost made meant met mistook overcame overslept paid rang ran rode rose said sang sank sat saw sent
shook shone shot showed shrank slept slid sold sought spent spoke sprang stole stood struck strove swam swept swung
taught threw told took tore understood undertook went woke won wore wound wrote withdrew
""".split())
# Words ending in "ed" that are a base form, not a past tense.
_BASE_ED = frozenset("bed bleed breed deed embed exceed feed heed need proceed seed shed shred sled speed succeed wed"
                     .split())
# Adverbs that may sit between I and its verb.
_ADVERBS = frozenset("""
actually already also always basically currently definitely finally generally honestly just mainly mostly never
normally now occasionally often only personally probably rarely really recently sometimes still truly typically
usually
""".split())
# A coordinated subject ("Jordan and I are") keeps its verb; an inverted auxiliary ("Do I need") cannot be rewritten.
# After a coordinator I may also start a clause ("I left and I am tired"), so there the name is written only before a
# verb that reads the same both ways: a plural form, a past tense or a form that does not change after a name. A comma
# is not a coordinator: "Yes, I have" is a clause.
_COORDINATORS = frozenset({"and", "or", "nor"})
_PLURAL_FORMS = frozenset({"are", "were", "aren't", "weren't"})
_AUXILIARIES = frozenset("am are is was were do does did have has had can could will would should may might must shall"
                         .split())

_FIRST_PERSON_RE = re.compile(r"(?<![\w'’])(I(?!\.e\.)|me|my|mine|myself)(?![\w'’])|(?<!\w)I(?=['’](?:m|ve|ll|d)\b)",
                              re.IGNORECASE)
_CONTRACTION_RE = re.compile(r"['’](m|ve|ll|d)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]*")


def has_first_person(text: str) -> bool:
    """Whether the text refers to its speaker in the first person singular."""
    return _FIRST_PERSON_RE.search(text) is not None


def possessive(name: str) -> str:
    """The possessive of a name: Maya's, James'."""
    return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"


def _previous_word(text: str, end: int) -> str | None:
    """The word or comma right before ``end``, lowercased."""
    before = text[:end].rstrip()
    if before.endswith(","):
        return ","
    match = re.search(r"[A-Za-z'’]+$", before)
    return match.group(0).casefold() if match else None


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
    for match in _FIRST_PERSON_RE.finditer(text):
        word = match.group(0)
        lowered = word.casefold()
        out.append(text[position:match.start()])
        position = match.end()
        if lowered in {"my", "mine"}:
            out.append(possessive(name))
            continue
        if lowered == "me":
            out.append(name)
            continue
        if lowered == "myself":
            return None
        # I: decide what follows.
        previous = _previous_word(text, match.start())
        contraction = _CONTRACTION_RE.match(text, position)
        if contraction is not None:
            key = f"'{contraction.group(1).casefold()}"
            form = _AFTER_I.get(key)
            # "Jordan and I've" may be a coordinated subject ("have") or a clause ("has").
            if form is None or (previous in _COORDINATORS and key == "'ve"):
                return None
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
            replacement = _AFTER_I[key]
            if verb_word[:1].isupper():
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

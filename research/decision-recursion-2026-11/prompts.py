"""Every decision prompt, written once. Frozen after the trial in prompt_trials.py (README, "Prompt freeze").

Each template is the ``question`` given to ``decide()``; ``decide()`` adds the lettered options and the line
"Answer with the letter of one option." ``FROZEN_SHA256`` is the hash of the templates below; the experiment
scripts record it in every result file and refuse to run if it changes, so a later edit cannot pass unnoticed.
"""

from __future__ import annotations

import hashlib
import json

TEMPLATES = {
    # Experiment R
    "R1.judge": "Question: {question}\nRecord: {record}\n\nDoes this record answer the question?",
    "R2.pick_record": ("Question: {question}\n\nEach option is a record that partly answers the question. "
                       "Which record names something to look up next?"),
    "R2.pick_word": ("Question: {question}\nRecord: {record}\n\n"
                     "Which word from this record should be searched next to answer the question?"),
    "R3.conflict": ("Question: {question}\nRecord A ({date_a}): {record_a}\nRecord B ({date_b}): {record_b}\n\n"
                    "Do these two records give conflicting answers to the question?"),
    "R3.true_now": "Question: {question}\nNow: {now}\n\nWhich of these is true now?",
    # Experiment W
    "W1.still_true": ("These statements come from one person's chat with an assistant. \"I\" is that person.\n\n"
                      "Old statement ({date_old}): {old}\nNew statement ({date_new}): {new}\n\n"
                      "After the new statement, is the old statement still true?"),
    "W2.dependent": ("These statements come from one person's chat with an assistant. \"I\" is that person.\n\n"
                     "No longer true ({date_retired}): {retired}\nBecause of ({date_new}): {new}\n"
                     "Statement to check ({date_check}): {check}\n\n"
                     "This statement depended on one that is no longer true; is it still true?"),
}

OPTIONS = {
    "R1.judge": ["yes", "partly", "no"],
    "R3.conflict": ["no, both can be true", "yes, they conflict"],
    "W1.still_true": ["still true", "no longer true", "cannot tell"],
    "W2.dependent": ["still true", "no longer true", "cannot tell"],
}

FROZEN_SHA256 = "fef7bf42e2247fd70efcbceaca9b50d78037972c5c43ace2ae1daaf919f33d74"  # frozen 2026-11-10 after one trial


def templates_sha256() -> str:
    return hashlib.sha256(json.dumps({"templates": TEMPLATES, "options": OPTIONS}, sort_keys=True).encode()).hexdigest()


def check_frozen() -> str:
    digest = templates_sha256()
    if FROZEN_SHA256 is not None and digest != FROZEN_SHA256:
        raise SystemExit(f"prompts changed after the freeze: {digest} != {FROZEN_SHA256}")
    return digest


def fmt_time(when) -> str:
    return when.strftime("%Y-%m-%d %H:%M") if when is not None else "unknown date"


# R2's word options: a record's content words that are not in the question, in order, at most 8, then "none".
# A fixed English function-word list, written once with the prompts.
STOPWORDS = frozenset("""
a about above after again against all also am an and another any are as at be because been before being below
between both but by can can't cannot could did didn't do does doesn't doing don't down during each even ever every
few for from further get gets got had has have haven't having he her here hers herself him himself his how i i'd
i'll i'm i've if in into is isn't it it's its itself just let's like made make many may me more most much must my
myself never no nor not now of off oh on once one only or other our ours ourselves out over own per please quite
rather really same she should so some still such than that that's the their theirs them themselves then there
these they this those through to too under until up upon us very was wasn't we we're were what when where which
while who whom why will with without would yes yet you your yours yourself ha
""".split())


def content_words(record: str, question: str, limit: int = 8) -> list[str]:
    import re

    asked = {w.lower() for w in re.findall(r"[A-Za-z0-9]+(?:['.:][A-Za-z0-9]+)*", question)}
    words: list[str] = []
    seen: set[str] = set()
    for word in re.findall(r"[A-Za-z0-9]+(?:['.:][A-Za-z0-9]+)*", record):
        low = word.lower()
        if low in STOPWORDS or low in asked or low in seen or len(low) < 2:
            continue
        seen.add(low)
        words.append(word)
        if len(words) == limit:
            break
    return words


# Post-hoc arms (R2b, R4, W2b), designed by the lead after the first results, because R2 and W2 made recursion
# depend on the answer "partly", which no model gave. Written once, tried once on made-up cases
# (prompt_trials.py --posthoc), then frozen under their own hash; the prompts above are unchanged.
POSTHOC_TEMPLATES = {
    "R2b.pick_record": "Question: {question}\n\nWhich record is most likely to lead to the answer?",
    "W2b.depends_on": ("These statements come from one person's chat with an assistant. \"I\" is that person.\n\n"
                       "Statement ({date_check}): {check}\n\n"
                       "Which earlier statement, if any, does this one depend on?"),
    "W2b.recheck": ("These statements come from one person's chat with an assistant. \"I\" is that person.\n\n"
                    "Old statement ({date_old}): {old}\n"
                    "It depends on this statement ({date_dep}), which is no longer true: {dep}\n"
                    "New statement ({date_new}): {new}\n\n"
                    "After the new statement, is the old statement still true?"),
}
POSTHOC_NONE = {"R2b.pick_record": "none of them", "W2b.depends_on": "none"}
POSTHOC_FROZEN_SHA256 = "27b76bc6245bb9173a79c504a5c5edb4fe9f803dd60ab9522eafc479baf2f5a1"  # frozen 2026-11-11 after one trial


def posthoc_sha256() -> str:
    return hashlib.sha256(json.dumps({"templates": POSTHOC_TEMPLATES, "none": POSTHOC_NONE},
                                     sort_keys=True).encode()).hexdigest()


def check_posthoc_frozen() -> str:
    digest = posthoc_sha256()
    if POSTHOC_FROZEN_SHA256 is not None and digest != POSTHOC_FROZEN_SHA256:
        raise SystemExit(f"post-hoc prompts changed after the freeze: {digest} != {POSTHOC_FROZEN_SHA256}")
    return digest


# Round 2 (post-hoc, designed by the lead after rounds 1 and 2): W3b's one topic question and R5's single
# answer question. Tried once on made-up cases (prompt_trials.py --round2) and frozen under their own hash; the
# dictionaries above are untouched, so earlier runs still check against their own hashes.
ROUND2_TEMPLATES = {
    "W3b.topic": ("These statements come from one person's chat with an assistant. \"I\" is that person.\n\n"
                  "New statement ({date_new}): {new}\n\n"
                  "Which of these earlier statements is about the same thing as the new one?"),
    "R5.any_answer": "Question: {question}\n\nDo any of these records answer the question?",
}
ROUND2_NONE = {"W3b.topic": "none of them", "R5.any_answer": "none of them"}
ROUND2_FROZEN_SHA256 = "f7bb50dece39d9674e963600c9265b1b7491260b84b4cc5873086f397564a47f"  # frozen 2026-11-11 after one trial


def round2_sha256() -> str:
    return hashlib.sha256(json.dumps({"templates": ROUND2_TEMPLATES, "none": ROUND2_NONE},
                                     sort_keys=True).encode()).hexdigest()


def check_round2_frozen() -> str:
    digest = round2_sha256()
    if ROUND2_FROZEN_SHA256 is not None and digest != ROUND2_FROZEN_SHA256:
        raise SystemExit(f"round-2 prompts changed after the freeze: {digest} != {ROUND2_FROZEN_SHA256}")
    return digest

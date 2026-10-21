"""A question shape router: does this question ask for something the graph answers better than text ranking?

The blind validation found the typed walk wins on questions that ask for a
list, a count, things of a subtype, or a chain of relations, and loses on single
fact lookups, temporal questions, corrections and negation. This router reads
only the question's own words, with rules written from general phrasing and the
relation vocabulary, not from any fixture question:

- count: "how many", "number of", "in total", "altogether";
- subtype: "which kind of", "what types of" (a kind named in the plural, or after which);
- list: which or what followed within two words by a plural noun ("which cities",
  "what programming languages"), "which of the", "what are the <plural>", "who are";
- chain: two relational nouns (manager, partner, sister, ...) joined by "of" or a
  possessive, as in "the manager of Jordan's partner".

A question none of these fits goes to today's ranking unchanged. A yes or no
question, a "when" question and a "how long" question are never a list.
"""
from __future__ import annotations

import re

# Relational nouns: the people words of the vocabulary's relations (manager_of, partner_of, kin_of, friend_of, member_of,
# works_at). A chain needs two of them.
RELATIONAL = frozenset("""manager mentor boss employer partner spouse wife husband fiance fiancee boyfriend girlfriend sister brother
mother father parent child daughter son niece nephew aunt uncle cousin grandmother grandfather friend colleague teammate
coworker neighbor owner""".split())
# Words ending in s that are not plural nouns.
NOT_PLURAL = frozenset("""does has was is this his its us yes always perhaps across less plus bus gas focus status pass class
glass business address access process express canvas atlas lens series species news physics mathematics""".split())
GRAPH_SHAPES = ("count", "subtype", "list", "chain")

_COUNT = re.compile(r"\b(how many|number of|in total|altogether)\b")
_SUBTYPE = re.compile(r"\b(which (?:kind|type|sort|category)s? of|what (?:kinds|types|sorts|categories) of)\b")
# A question that opens with one of these is a yes or no, "when" or "how long" question, never a list.
_NOT_A_LIST = re.compile(r"^(when|how long|how does|how do|how did|how is|how was|does|do|did|is|are|was|were|can|could|"
                         r"will|would|has|have|had|should)\b")
# The first verb of the opening phrase ends the noun phrase after which or what.
VERBS = frozenset("""is are was were does do did will would has have had can could should come comes went go goes""".split())
_ARE_PLURAL = re.compile(r"^(?:what|which|who)\s+(?:are|were)\s+(?:the\s+|all\s+(?:the\s+)?|these\s+|those\s+)?([a-z]+)")


def _plural(word: str) -> bool:
    return len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "'s")) and word not in NOT_PLURAL


def _chain(text: str) -> bool:
    """Two relational nouns in one question with "of" or a possessive between them ("the manager of Jordan's partner")."""
    tokens = re.findall(r"[a-z0-9']+", text)
    found = [index for index, token in enumerate(tokens) if re.sub(r"'s$", "", token) in RELATIONAL]
    return any("of" in tokens[first + 1:second] or any(token.endswith("'s") for token in tokens[first:second])
               for first, second in zip(found, found[1:]))


def shape(question: str) -> str:
    """The shape of a question: count, subtype, list, chain or other."""
    text = " ".join(question.lower().replace("\u2019", "'").split()).rstrip("?! .")
    if _COUNT.search(text):
        return "count"
    if _SUBTYPE.search(text):
        return "subtype"
    if not _NOT_A_LIST.match(text):
        tokens = text.split()
        if re.match(r"^(?:which\s+(?:of|ones)|who\s+(?:are|were))\b", text):
            return "list"
        match = _ARE_PLURAL.match(text)
        if match and _plural(match.group(1)):
            return "list"
        if tokens[0] in ("which", "what"):
            for token in tokens[1:4]:
                if token in VERBS:
                    break
                if _plural(token):
                    return "list"
    return "chain" if _chain(text) else "other"


def route(question: str) -> str:
    """"graph" for a question that asks for a list, a count, things of a subtype or a chain; "text" for the rest."""
    return "graph" if shape(question) in GRAPH_SHAPES else "text"


# What a blind question's type tag implies for the route.
GRAPH_TYPES = frozenset({"list", "multi-hop", "aggregation"})


def expected_route(question_type: str) -> str:
    return "graph" if question_type in GRAPH_TYPES else "text"


def accuracy(questions: list[dict]) -> dict:
    """The router's agreement with the routes the type tags imply, and the share of lookups it sends to the graph.

    ``questions`` are dicts with ``question`` and ``type``. The result is by type too, so a reader can see where it errs.
    """
    by_type: dict[str, dict] = {}
    agree = 0
    for item in questions:
        routed = route(item["question"])
        right = routed == expected_route(item["type"])
        agree += right
        entry = by_type.setdefault(item["type"], {"n": 0, "routed_to_graph": 0})
        entry["n"] += 1
        entry["routed_to_graph"] += routed == "graph"
    lookups = by_type.get("lookup", {"n": 0, "routed_to_graph": 0})
    return {"n": len(questions), "accuracy": round(agree / len(questions), 3) if questions else 0.0,
            "lookups_routed_to_graph": lookups["routed_to_graph"], "lookups": lookups["n"],
            "lookup_routed_share": round(lookups["routed_to_graph"] / lookups["n"], 3) if lookups["n"] else 0.0,
            "by_type": dict(sorted(by_type.items()))}


__all__ = ["route", "shape", "accuracy", "expected_route"]

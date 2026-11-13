from prme.retrieval import relations as rel


def route_with(question, skip, concepts=None):
    """route_question with a hook: skip(word, kind, words, index) True drops that word's contribution."""
    words = rel._words(question)
    if not words or rel._TEMPORAL_RE.search(question):
        return None
    known = rel.concept_map(concepts)
    possessed, rest = set(), set()
    negative = any(word in rel._NEGATIVE_WORDS for word in words)
    for index, raw in enumerate(words):
        word = raw.removesuffix("'s")
        before = words[index - 1] if index > 0 else ""
        owned = before in rel._POSSESSIVE_WORDS or (
            before.endswith("'s") and rel.role_relation(before.removesuffix("'s")) is None
            and rel.singular(before.removesuffix("'s")) not in known)
        found = set()
        people = rel.role_relation(word)
        if people is not None:
            if not skip(word, "role", words, index, question):
                found.add(rel._step(*people))
        elif rel.singular(word) in known:
            if not skip(word, "concept", words, index, question):
                found.update(known[rel.singular(word)])
        elif word not in rel._STOPWORDS and word not in rel._NAME_WORDS:
            if not skip(word, "cue", words, index, question):
                for name, cues in rel.DERIVED_CUES.items():
                    if name not in rel.UNROUTED_RELATIONS and rel.stem(word) in cues:
                        found.add(rel._step(name, polarity="negative" if name == "prefers" and negative else "positive"))
            stated = rel._PREDICATE_WORDS.get(word)
            if stated is not None and not skip(word, "predicate_word", words, index, question):
                found.add(rel._step(stated.relation, stated.role, "negative" if stated.negative else "positive"))
            owned = False
        (possessed if owned else rest).update(found)
    if not possessed and not rest:
        return None
    if possessed and rest - possessed:
        return (tuple(sorted(possessed)), tuple(sorted(rest - possessed)))
    return (tuple(sorted(possessed | rest)),)



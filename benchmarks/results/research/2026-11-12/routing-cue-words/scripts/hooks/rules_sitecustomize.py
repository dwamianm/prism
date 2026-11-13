# Task 27 scratch: PRME_T27_RULE picks a candidate router rule, applied by wrapping route_question
# in the pipeline. Research instrumentation only; nothing in src/ changes.
import os

_rule = os.environ.get("PRME_T27_RULE")
if _rule:
    import prme.retrieval.pipeline as _pipeline
    from prme.retrieval import relations as rel

    ISSUE = {"take", "call", "part", "present", "sign", "lead", "like", "want", "keep", "run"}
    STOP = {rel.stem(w) for w in ISSUE} | {"tak", "lik", "runn"}

    def _skip(word, kind, words, index, question):
        if _rule == "issue-stoplist":
            return kind in ("cue", "predicate_word") and rel.stem(word) in STOP
        if _rule == "issue-plus-use":
            return kind in ("cue", "predicate_word") and rel.stem(word) in STOP | {"use", "us", "using"}
        if _rule == "use-no-want":
            stop = (STOP - {rel.stem("want")}) | {"use", "us", "using"}
            return kind in ("cue", "predicate_word") and rel.stem(word) in stop
        if _rule == "use-no-want-adoption":
            stop = (STOP - {rel.stem("want")}) | {"use", "us", "using"}
            if kind == "cue" and word in ("adoption", "adoptions"):
                return True
            return kind in ("cue", "predicate_word") and rel.stem(word) in stop
        if _rule == "candidate":
            # stop list (issue words but "want", plus "use"), and no derived cue from an -ion noun
            stop = (STOP - {rel.stem("want")}) | {"use", "us", "using"}
            if kind == "cue" and word.endswith(("ion", "ions")):
                return True
            return kind in ("cue", "predicate_word") and rel.stem(word) in stop
        if _rule == "no-derived-cue":
            return kind == "cue"
        raise ValueError(_rule)

    def _route(question, concepts=None):
        from t27sim_route import route_with
        return route_with(question, _skip, concepts)

    _pipeline.route_question = _route

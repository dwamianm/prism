"""Conservative recognition of unresolved English personal references."""

_PERSONAL_REFERENCES = frozenset({
    "i", "me", "myself", "my", "mine", "we", "us", "ourselves", "our", "ours",
    "you", "yourself", "yourselves", "your", "yours", "he", "him", "himself", "his",
    "she", "her", "herself", "hers", "they", "them", "themselves", "their", "theirs", "it", "itself",
})
_NON_PERSONAL_TYPES = frozenset({"country", "place", "location", "technology", "concept", "product", "tool",
                                 "language", "programming_language", "chemical", "element", "number"})


def unresolved_personal_reference(name: str, entity_type: str | None = None) -> bool:
    """A literal pronoun is not a stable person/group identity.

    This is a conservative English guard, not coreference resolution. Explicitly
    typed non-person meanings such as US/country and IT/technology are distinct.
    """
    kind = entity_type.strip().casefold() if isinstance(entity_type, str) else ""
    return name.strip().casefold() in _PERSONAL_REFERENCES and kind not in _NON_PERSONAL_TYPES

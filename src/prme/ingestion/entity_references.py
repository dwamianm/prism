"""Resolve extraction-local references without guessing aliases or identities."""

from collections import defaultdict
from collections.abc import Hashable
from typing import TYPE_CHECKING, Generic, TypeVar

from prme.models.entity_identity import unresolved_personal_reference

if TYPE_CHECKING:
    from prme.ingestion.schema import ExtractionResult

T = TypeVar("T", bound=Hashable)


class EntityReferences(Generic[T]):
    """A name may refer to several types; resolve only one distinct identity."""

    def __init__(self):
        self._names: dict[str, dict[str, set[T]]] = defaultdict(lambda: defaultdict(set))

    def add(self, name: str, entity_type: str, identity: T) -> None:
        self._names[name.strip().lower()][entity_type].add(identity)

    def resolve(self, name: str, entity_type: str | None = None) -> tuple[T | None, str]:
        types = self._names.get(name.strip().lower(), {})
        candidates = types.get(entity_type, set()) if entity_type is not None else {
            identity for identities in types.values() for identity in identities
        }
        if len(candidates) == 1:
            return next(iter(candidates)), "resolved"
        return None, "ambiguous" if candidates else "missing"


def reference_errors(result: "ExtractionResult") -> list[str]:
    """Structural errors for provider schema feedback; no semantic entailment claim."""
    refs = EntityReferences[tuple[str, str]]()
    for entity in result.entities:
        refs.add(entity.name, entity.entity_type, (entity.name.strip().lower(), entity.entity_type))
    errors = []
    references = [(f"facts[{i}].subject", fact.subject, fact.subject_entity_type)
                  for i, fact in enumerate(result.facts)]
    for i, fact in enumerate(result.facts):
        _, status = refs.resolve(fact.object, fact.object_entity_type)
        if fact.object_entity_type is not None or status == "ambiguous":
            references.append((f"facts[{i}].object", fact.object, fact.object_entity_type))
    for i, rel in enumerate(result.relationships):
        references.extend([(f"relationships[{i}].source_entity", rel.source_entity, rel.source_entity_type),
                           (f"relationships[{i}].target_entity", rel.target_entity, rel.target_entity_type)])
    for path, name, entity_type in references:
        _, status = refs.resolve(name, entity_type)
        # Literal personal references are deliberately not required to be
        # named entities. The materializer gives an unlisted reference a
        # source-event-local identity; it never guesses a durable referent.
        if status == "missing" and unresolved_personal_reference(name, entity_type):
            continue
        if status != "resolved":
            errors.append(f"{path} is {status}: use a listed entity name and specify its entity_type if ambiguous")
    return errors

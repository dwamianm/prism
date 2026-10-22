"""Ask a second time who a pronoun means, for the claims that kept one (``enable_pronoun_repair``).

A stored memory such as "They chose Tokyo, Kyoto, Kanazawa, and Osaka." names no
one, so extraction reads it alone and keeps "They" as the claim's subject: a
question that names Alex and Japan reaches it only through its words. Showing
every extraction the owner's earlier memories (``enable_windowed_extraction``)
or resolving the text of every claim (``enable_fact_text_resolution``) changed
claims that were fine, so this asks only about the sources whose first
extraction left a claim on a pronoun, and changes only that claim.

After a source's first extraction, a claim whose subject or object is a pronoun
(they, them, their, he, she, it and the like, and we, us and our when no speaker
is named) is flagged. One more request shows the model the owner's preceding
memories and, for each flagged claim, only the claim's own sentences. The model
returns the names the pronoun means, or nothing. Ingestion then accepts a name
only when it is a proper name written in the claim's sentence, in the source
message, as the speaker, or in a memory the request showed; a pronoun field
with any name that fails is left as it was, and so is every other claim of the
message.

How the grounding check treats a resolved name that is not in the quote:
grounding requires a claim's subject and object to occur in its evidence
quote, and a resolved name by definition does not. The quote stays the claim's
evidence, verbatim, and the check on it is unchanged. The name is grounded by
its own rule above, and the claim records where it came from in
``metadata.resolved_from_context`` (the turns of the window that wrote the
name, and for each field the pronoun and the names that replaced it). A claim
never carries a name that no turn the model was shown, and no sentence of the
message, writes.

An accepted repair gives the claim its own sentence as its text with the names
written in (``grounding_method`` ``pronoun_repair_v1``). A field that resolves
to one name is rebound to it, so the claim attaches to that person's entity; a
field that resolves to several (they, we) stays on the pronoun, its text names
every member and each is listed as an entity, so the mention links that
``enable_mention_links`` makes reach each of them. The repair runs before the
extraction is saved, so reingest, which replaces what was derived from a source
with what the engine now derives, replaces the pronoun version too.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError

from prme.ingestion.grounding import _mentioned, claim_sentences
from prme.ingestion.schema import ExtractedEntity, ExtractionResult
from prme.models.entity_identity import PERSONAL_REFERENCES, unresolved_personal_reference

logger = structlog.get_logger(__name__)

REPAIR_METHOD = "pronoun_repair_v1"

# A pronoun for someone other than the speaker.
_THIRD_PERSON = frozenset(
    "they them their theirs themselves he him his himself she her hers herself it its itself".split()
)
# A pronoun for a group the speaker belongs to. A turn with a named speaker has its own rules for them.
_FIRST_PERSON_PLURAL = frozenset("we us our ours ourselves".split())
_POSSESSIVE = frozenset("his their theirs hers its our ours".split())

REPAIR_SYSTEM_PROMPT = """You resolve pronouns in claims read from one stored memory.

Each numbered claim was read from the memory alone, and the pronoun in its subject or object (the fields listed under
"resolve") names no one. Earlier memories of the same owner may be shown, oldest first, for reference only.

For each field to resolve, give the names of the person, people or thing the pronoun means. Use only names that are
written in the earlier memories, in the claim's own sentence, or in the speaker line. Write every name exactly as it is
written there, a full name when one is written. For a group pronoun such as they or we, give every member in the order
they are named; for we, include the speaker when the speaker is named. When the memories do not say who the pronoun
means, or you would have to guess, give no names for that field. Never invent a name. Never answer with a pronoun, a
role or a description such as "the team" instead of a name. Resolve only the fields listed.
"""


@dataclass(frozen=True)
class PronounClaim:
    """A claim whose subject or object is a pronoun, as the repair request shows it.

    ``fields`` names which of subject and object to resolve, ``sentence`` is
    the claim's own sentence or sentences of the source, and ``fact_index`` is
    where the claim sits in its extraction (never sent to the model).
    """

    index: int
    subject: str
    predicate: str
    object: str
    fields: tuple[str, ...]
    sentence: str
    fact_index: int = -1


class _Name(BaseModel):
    name: str = Field(description="A name exactly as it is written in the memories or the sentence")
    entity_type: str = Field(
        default="person", description="One of: person, organization, location, product, concept, event"
    )


class _RepairedClaim(BaseModel):
    claim: int = Field(description="The claim's number")
    subject: list[_Name] = Field(
        default_factory=list, description="Names the subject pronoun means; empty when it is not listed or unknown"
    )
    object: list[_Name] = Field(
        default_factory=list, description="Names the object pronoun means; empty when it is not listed or unknown"
    )


class PronounRepairResponse(BaseModel):
    """What the model returns: for each claim, the names each listed pronoun means."""

    claims: list[_RepairedClaim] = Field(default_factory=list)


def _earlier_memories_message(window: Sequence[str]) -> str:
    memories = "\n\n".join(f"[earlier memory {n}] {line}" for n, line in enumerate(window, 1))
    return f"Earlier memories of the same owner, oldest first, for reference only:\n\n{memories}"


def _claims_message(claims: Sequence[PronounClaim], speaker: str | None) -> str:
    lines = []
    if speaker:
        lines.append(f"Speaker: {speaker}")
        lines.append("")
    lines.append("Claims to resolve:")
    for claim in claims:
        lines += [
            "",
            f"claim {claim.index}: subject \"{claim.subject}\" | predicate \"{claim.predicate}\" | "
            f"object \"{claim.object}\"",
            f"sentence: {claim.sentence}",
            f"resolve: {', '.join(claim.fields)}",
        ]
    return "\n".join(lines)


def repair_messages(
    window: Sequence[str], claims: Sequence[PronounClaim], speaker: str | None = None,
) -> list[dict[str, str]]:
    """The chat messages of a repair request, as the provider sends them.

    The same window and claims always give the same messages, so a benchmark
    cache can key on them, as it does for a batched extraction.
    """
    messages = [{"role": "system", "content": REPAIR_SYSTEM_PROMPT}]
    if window:
        messages.append({"role": "user", "content": _earlier_memories_message(window)})
    messages.append({"role": "user", "content": _claims_message(claims, speaker)})
    return messages


def repair_request_sha256() -> str:
    """The digest of a sample request, so a change to the prompt or its layout changes the settings."""
    sample = PronounClaim(1, "They", "chose", "Tokyo", ("subject",), "They chose Tokyo.")
    return hashlib.sha256(json.dumps(repair_messages(["Sample memory."], [sample], "Sample")).encode()).hexdigest()


@dataclass(frozen=True)
class WindowTurn:
    """One earlier memory the request showed, as the checker reads it."""

    event_id: str
    speaker: str | None
    text: str


def flagged_claims(
    result: ExtractionResult, passage_source: str, *, named_speaker: bool, grounding_speaker: str | None = None,
) -> list[PronounClaim]:
    """The claims of a first extraction that sit on a pronoun and have a sentence of their own to repair.

    A first person "I" is never flagged: the speaker rules bind it to the
    speaker or the owner. We, us and our are flagged only when the turn names
    no speaker, because with one the speaker rules cover the group. A claim
    that already has resolved text, or whose subject and object share no
    sentence, is left alone.
    """
    flagged: list[PronounClaim] = []
    for position, fact in enumerate(result.facts):
        if fact.resolved_text:
            continue
        fields = []
        for field, value, kind in (("subject", fact.subject, fact.subject_entity_type),
                                   ("object", fact.object, fact.object_entity_type)):
            word = value.strip().casefold()
            if not unresolved_personal_reference(value, kind):
                continue
            if word in _THIRD_PERSON or (word in _FIRST_PERSON_PLURAL and not named_speaker):
                fields.append(field)
        if not fields:
            continue
        sentence = claim_sentences(fact.evidence_quote or passage_source, fact.subject, fact.object,
                                   speaker=grounding_speaker, first_person_forms=True)
        if not sentence:
            continue
        flagged.append(PronounClaim(
            index=len(flagged) + 1, subject=fact.subject, predicate=fact.predicate, object=fact.object,
            fields=tuple(fields), sentence=sentence, fact_index=position,
        ))
    return flagged


def _join(names: Sequence[str]) -> str:
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _written(sentence: str, pronoun: str, names: Sequence[str]) -> tuple[str, int]:
    """The sentence with each whole-word ``pronoun`` replaced by the names, and how many were replaced."""
    joined = _join(names)
    if pronoun.strip().casefold() in _POSSESSIVE:
        joined = f"{joined}'" if joined.endswith("s") else f"{joined}'s"
    pattern = re.compile(r"(?<![\w'’])" + re.escape(pronoun.strip()) + r"(?![\w'’])", re.IGNORECASE)
    return pattern.subn(lambda _: joined, sentence)


def _proper_name(name: str) -> bool:
    words = name.casefold().split()
    return bool(words) and name[:1].isupper() and not any(word in PERSONAL_REFERENCES for word in words)


def _sources(name: str, claim: PronounClaim, source_text: str, source_event_id: str, speaker: str | None,
             window: Sequence[WindowTurn]) -> list[dict] | None:
    """Where a name is written, or None when it is written nowhere the model was shown."""
    sources: list[dict] = []
    if _mentioned(name, claim.sentence) or _mentioned(name, source_text):
        sources.append({"source": "source_turn", "event_id": source_event_id})
    if speaker and _mentioned(name, speaker):
        sources.append({"source": "speaker", "event_id": source_event_id})
    for turn in window:
        if _mentioned(name, turn.text) or (turn.speaker and _mentioned(name, turn.speaker)):
            sources.append({"source": "window", "event_id": turn.event_id})
    return sources or None


def _answers(raw: Any) -> dict[int, _RepairedClaim]:
    try:
        return {item.claim: item for item in PronounRepairResponse.model_validate(raw).claims}
    except ValidationError:
        return {}


def apply_repairs(
    result: ExtractionResult, claims: Sequence[PronounClaim], raw: Any, *, source_text: str, source_event_id: str,
    speaker: str | None, window: Sequence[WindowTurn],
) -> ExtractionResult:
    """The extraction with each flagged claim the model's answer repairs, and no other change.

    ``raw`` is the provider's parsed response. A field is repaired only when
    the model gave names for it, each is a proper name written where
    :func:`_sources` finds it, and the claim's sentence holds the pronoun to
    replace; a claim none of whose listed fields repairs keeps its first
    extraction.
    """
    answers = _answers(raw)
    facts = list(result.facts)
    entities = list(result.entities)
    for claim in claims:
        answer = answers.get(claim.index)
        fact = facts[claim.fact_index]
        if answer is None:
            continue
        sentence = claim.sentence
        updates: dict[str, Any] = {}
        replacements: list[dict] = []
        for field in claim.fields:
            named = getattr(answer, field)
            pronoun = getattr(fact, field)
            names = [" ".join(item.name.split()) for item in named]
            if not names or len({name.casefold() for name in names}) != len(names):
                continue
            where = [_sources(name, claim, source_text, source_event_id, speaker, window) if _proper_name(name)
                     else None for name in names]
            if any(item is None for item in where):
                logger.warning("pronoun_repair_name_rejected", field=field, claim=claim.index)
                continue
            written, count = _written(sentence, pronoun, names)
            if not count:
                continue
            sentence = written
            sources: list[dict] = []
            for item in where:
                sources += [entry for entry in item if entry not in sources]
            replacements.append({"kind": "name", "field": field, "original": pronoun, "replacement": _join(names),
                                 "names": names, "sources": sources})
            kinds = []
            for item, name in zip(named, names):
                listed = next((entity for entity in entities if entity.name.strip().casefold() == name.casefold()), None)
                kind = listed.entity_type if listed is not None else (item.entity_type or "person").strip().lower()
                kinds.append(kind)
                if listed is None:
                    entities.append(ExtractedEntity(name=name, entity_type=kind))
            if len(names) == 1:
                # One name is who the claim is about, so it attaches to that entity.
                updates[field] = names[0]
                updates[f"{field}_entity_type"] = kinds[0]
        if not replacements:
            continue
        window_ids: list[str] = []
        for item in replacements:
            for entry in item["sources"]:
                if entry["source"] == "window" and entry["event_id"] not in window_ids:
                    window_ids.append(entry["event_id"])
        record = {"method": REPAIR_METHOD, "original": claim.sentence, "replacements": replacements,
                  "window_event_ids": window_ids}
        facts[claim.fact_index] = fact.model_copy(update={**updates, "resolved_text": sentence, "resolution": record})
    return result.model_copy(update={"facts": facts, "entities": entities})


def is_repair(resolution: dict | None) -> bool:
    """Whether a fact's resolution record is a pronoun repair."""
    return bool(resolution) and resolution.get("method") == REPAIR_METHOD


def resolved_from_context(resolution: dict) -> dict:
    """What ``metadata.resolved_from_context`` records: the window turns, and each field's pronoun and names."""
    return {
        "event_ids": list(resolution.get("window_event_ids") or []),
        "fields": {item["field"]: {"pronoun": item["original"], "names": list(item["names"])}
                   for item in resolution.get("replacements") or []},
    }


def with_names(fact, resolution: dict):
    """The fact with each repaired pronoun field written as its names, for a statement of the claim."""
    named = {item["field"]: item["replacement"] for item in resolution.get("replacements") or []}
    return fact.model_copy(update={field: value for field, value in named.items()})

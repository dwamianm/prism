"""Closed admitted representations for protected extraction plans.

The input has already passed source grounding and generated-output sanitation.
This module selects the fields admitted as memory; a supporting passage is never
the fallback statement. The original sanitized source remains a separate object.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

from prme.models import MemoryEdge, MemoryNode
from prme.models.speaker import SPEAKER_METADATA_KEY, normalize_speaker
from prme.models.value_bindings import VALUE_BINDINGS_METADATA_KEY, MemoryValueBinding
from prme.privacy.policy import PrivacyDenied
from prme.privacy.records import PRIVACY_KEY
from prme.types import EpistemicType, NodeType

if TYPE_CHECKING:
    from prme.privacy.capabilities import SanitizedContent

STATEMENT_VERSION = "admitted_statement_v1"

_CLAIM_FIELDS = frozenset({
    "subject", "predicate", "object", "polarity", "subject_entity_type",
    "object_entity_type", "subject_link_status", "object_link_status",
    "extraction_kind", "condition", "condition_state", "temporal_intent",
    "replaces_object", "resolved_date",
})
_ENTITY_FIELDS = frozenset({"entity_type", "identity_status", "reference_event_id", "owner_id"})
_DIRECT_FIELDS = frozenset({
    "subject", "predicate", "object", "polarity", "condition", "condition_state",
    "temporal_intent", "replaces_object",
})


def _text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PrivacyDenied("privacy_invalid_result")
    return value


def _opaque(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise PrivacyDenied("privacy_invalid_result") from None


def _copy_fields(metadata: dict, names: frozenset[str]) -> dict:
    result = {}
    for name in names:
        value = metadata.get(name)
        if value is not None:
            result[name] = _text(value)
    return result


def _structural_metadata(metadata: dict, *, user_id: str) -> dict:
    """Preserve the closed structural fields used by existing graph algorithms."""
    result = {}
    if "owner_name" in metadata:
        value = metadata["owner_name"]
        if (not isinstance(value, dict) or set(value) != {"event_id", "basis"}
                or value["basis"] not in {"speaker", "self_introduction"}):
            raise PrivacyDenied("privacy_invalid_result")
        result["owner_name"] = {"event_id": _opaque(value["event_id"]), "basis": value["basis"]}
    for key in ("speaker_reference", "owner_reference"):
        if key not in metadata:
            continue
        value = metadata[key]
        names = {"fields", "speaker", "basis"} if key == "speaker_reference" else {"fields", "user_id"}
        if not isinstance(value, dict) or not set(value).issubset(names):
            raise PrivacyDenied("privacy_invalid_result")
        fields = value.get("fields")
        if (not isinstance(fields, list) or not fields
                or any(field not in {"subject", "object"} for field in fields)):
            raise PrivacyDenied("privacy_invalid_result")
        entry = {"fields": list(dict.fromkeys(fields))}
        if key == "speaker_reference":
            entry["speaker"] = normalize_speaker(value.get("speaker"))
            if entry["speaker"] is None:
                raise PrivacyDenied("privacy_invalid_result")
            if "basis" in value:
                if value["basis"] not in {"self_introduction", "owner_name"}:
                    raise PrivacyDenied("privacy_invalid_result")
                entry["basis"] = value["basis"]
        else:
            if value.get("user_id") != user_id:
                raise PrivacyDenied("privacy_boundary_mismatch")
            entry["user_id"] = user_id
        result[key] = entry
    return result


def _quantity(metadata: dict, content: str, *, original_passage: str | None = None) -> dict | None:
    """Reuse object_decimal_v1; an optional invalid quantity stays absent."""
    from prme.ingestion.grounding import validate_extracted_quantity
    from prme.ingestion.schema import ExtractedQuantity

    raw = metadata.get("quantity")
    if (not isinstance(raw, dict) or set(raw) != {"value", "unit", "source_text", "grounding"}
            or raw.get("grounding") != "object_decimal_v1"):
        return None
    try:
        quantity = ExtractedQuantity.model_validate({name: raw[name] for name in ("value", "unit", "source_text")})
    except ValueError:
        return None
    obj = metadata.get("object")
    if not isinstance(obj, str):
        return None
    # Ground the old passage first, before discarding it. Rechecking only the
    # narrower statement could otherwise remove an approximation/range cue.
    for passage in (original_passage, content):
        if passage is not None and validate_extracted_quantity(
            quantity, object_value=obj, claim_passage=passage,
        ) is None:
            return None
    return {"value": str(quantity.value), "unit": quantity.unit,
            "source_text": quantity.source_text, "grounding": "object_decimal_v1"}


def _statement(metadata: dict) -> str:
    subject, predicate, obj = (_text(metadata.get(name)) for name in ("subject", "predicate", "object"))
    attribution = metadata.get("speaker_reference", {})
    speaker = attribution.get("speaker")
    if speaker:
        if "subject" in attribution["fields"]:
            subject = speaker
        if "object" in attribution["fields"]:
            obj = speaker
    statement = f"{subject} {predicate.replace('_', ' ')} {obj}"
    polarity = metadata.get("polarity")
    if polarity == "negative":
        statement = f"It is not true that {statement}"
    elif polarity != "positive":
        raise PrivacyDenied("privacy_invalid_result")
    if metadata.get("condition"):
        statement += f" ({metadata['condition']})"
    return statement.rstrip(".") + "."


def project_admitted_node(node: MemoryNode, source: SanitizedContent | None = None) -> MemoryNode:
    """Project one grounded extracted entity/claim, preserving its truth clocks."""
    metadata = node.metadata or {}
    if node.node_type == NodeType.ENTITY:
        projected = _copy_fields(metadata, _ENTITY_FIELDS)
        if "entity_type" not in projected:
            raise PrivacyDenied("privacy_invalid_result")
        for key in ("reference_event_id", "owner_id"):
            if key in projected:
                projected[key] = _opaque(projected[key])
        if "owner_id" in projected and projected["owner_id"] != node.user_id:
            raise PrivacyDenied("privacy_boundary_mismatch")
        content = _text(node.content)
    else:
        projected = _copy_fields(metadata, _CLAIM_FIELDS)
        if projected.get("extraction_kind") not in {"fact", "relationship"}:
            raise PrivacyDenied("privacy_invalid_result")
        projected.update(_structural_metadata(metadata, user_id=node.user_id))
        if node.epistemic_type == EpistemicType.CONDITIONAL and not projected.get("condition"):
            raise PrivacyDenied("privacy_evidence_ambiguous")
        content = _statement(projected)
        quantity = _quantity(metadata, content, original_passage=metadata.get("evidence_quote"))
        if quantity is not None:
            projected["quantity"] = quantity
        projected["grounding_method"] = STATEMENT_VERSION
    projected.update(_structural_metadata(metadata, user_id=node.user_id))
    speaker = metadata.get(SPEAKER_METADATA_KEY)
    if source is not None:
        try:
            fields = json.loads(source.fields_json)
            speaker = fields.get("speaker") or speaker
        except (TypeError, ValueError):
            raise PrivacyDenied("privacy_invalid_result") from None
    if speaker is not None:
        projected[SPEAKER_METADATA_KEY] = normalize_speaker(speaker)
    if PRIVACY_KEY in metadata:
        projected[PRIVACY_KEY] = dict(metadata[PRIVACY_KEY])
    return node.model_copy(deep=True, update={"content": content, "metadata": projected})


def project_direct_node(node: MemoryNode) -> MemoryNode:
    """Caller text is intentionally admitted after the common sanitizer."""
    metadata = node.metadata or {}
    projected = _copy_fields(metadata, _DIRECT_FIELDS)
    if node.epistemic_type == EpistemicType.CONDITIONAL and not projected.get("condition"):
        raise PrivacyDenied("privacy_evidence_ambiguous")
    if SPEAKER_METADATA_KEY in metadata:
        projected[SPEAKER_METADATA_KEY] = normalize_speaker(metadata[SPEAKER_METADATA_KEY])
    if VALUE_BINDINGS_METADATA_KEY in metadata:
        bindings = metadata[VALUE_BINDINGS_METADATA_KEY]
        if not isinstance(bindings, list):
            raise PrivacyDenied("privacy_invalid_result")
        admitted = []
        for raw in bindings:
            try:
                binding = MemoryValueBinding.model_validate(raw)
            except ValueError:
                raise PrivacyDenied("privacy_invalid_result") from None
            if binding.presentation not in node.content:
                raise PrivacyDenied("privacy_invalid_result")
            admitted.append(binding.model_dump(mode="json"))
        projected[VALUE_BINDINGS_METADATA_KEY] = admitted
    quantity = _quantity(metadata, node.content)
    if quantity is not None:
        projected["quantity"] = quantity
    if PRIVACY_KEY in metadata:
        projected[PRIVACY_KEY] = dict(metadata[PRIVACY_KEY])
    return node.model_copy(deep=True, update={"metadata": projected})


def project_admitted_edge(edge: MemoryEdge) -> MemoryEdge:
    metadata = edge.metadata or {}
    projected = {}
    # Aliases retain the exact structural barrier, never free-form proposal
    # evidence, names or review explanations.
    if "relation" in metadata:
        if metadata["relation"] != "alias":
            raise PrivacyDenied("privacy_invalid_result")
        projected["relation"] = "alias"
    if "identity_verified" in metadata:
        value = metadata["identity_verified"]
        if value not in (True, False, "true", "false"):
            raise PrivacyDenied("privacy_invalid_result")
        projected["identity_verified"] = value
    for key in ("proposal_operation_id", "review_operation_id"):
        if key in metadata:
            projected[key] = _opaque(metadata[key])
    if "basis" in metadata:
        if metadata["basis"] not in {"group_member", "subject", "object"}:
            raise PrivacyDenied("privacy_invalid_result")
        projected["basis"] = metadata["basis"]
    if "named_in" in metadata:
        projected["named_in"] = _opaque(metadata["named_in"])
    return edge.model_copy(deep=True, update={"metadata": projected or None})


def project_admitted_graph(graph, indexes, source: SanitizedContent) -> None:
    """Project all plan/index copies synchronously before provider/persistence."""
    for key, node in tuple(graph.nodes.items()):
        admitted = project_admitted_node(node, source)
        graph.nodes[key] = admitted
        if key in indexes.vectors:
            indexes.vectors[key] = admitted.content
        if key in indexes.lexical:
            indexes.lexical[key] = indexes.lexical[key].model_copy(update={"content": admitted.content})
    graph.edges[:] = [project_admitted_edge(edge) for edge in graph.edges]
    graph.replacements[:] = [project_admitted_edge(edge) for edge in graph.replacements]

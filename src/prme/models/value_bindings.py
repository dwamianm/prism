"""Typed presentation-to-lookup bindings carried by retrieved memories.

Bindings let an application preserve an exact user-facing value while using a
different complete value at a tool boundary.  They are caller-supplied
operational metadata, not inferred facts.  Resolution is deliberately limited
to complete JSON string values so PRME never rewrites an arbitrary substring.
"""

from __future__ import annotations

import json
from typing import Any, Literal, TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from prme.models.nodes import MemoryNode
    from prme.retrieval.models import MemoryBundle


VALUE_BINDINGS_METADATA_KEY = "prme_value_bindings_v1"


class _CollidingKeys(ValueError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _CollidingKeys
        result[key] = value
    return result


def _snapshot_object(value: dict | None) -> dict | None:
    """Copy one finite JSON object without importing the storage package."""
    try:
        return json.loads(
            json.dumps(value, allow_nan=False), object_pairs_hook=_unique_object
        )
    except _CollidingKeys:
        raise ValueError("Object keys collide after JSON serialization") from None
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("Value must contain finite JSON-serializable data") from None


class MemoryValueBinding(BaseModel):
    """One exact presentation value and its application-provided lookup form."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    reference: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        description="Caller-stable identifier within this memory",
    )
    kind: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        description="Application-defined value class, such as city or product",
    )
    presentation: str = Field(
        min_length=1,
        max_length=2048,
        description="Exact source-backed form to preserve in user-facing output",
    )
    lookup: str = Field(
        min_length=1,
        max_length=2048,
        description="Complete value accepted by the target tool or database",
    )
    lookup_authority: Literal["caller"] = "caller"

    @field_validator("presentation", "lookup")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value bindings cannot contain blank values")
        return value

    @model_validator(mode="after")
    def require_distinct_forms(self) -> "MemoryValueBinding":
        if self.presentation == self.lookup:
            raise ValueError("presentation and lookup must be distinct")
        return self


class RetrievedValueBinding(BaseModel):
    """A binding whose presentation value is visible in packed context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_ref: str
    node_id: UUID
    reference: str
    kind: str
    presentation: str
    lookup: str
    lookup_authority: Literal["caller"] = "caller"


class ToolArgumentReplacement(BaseModel):
    """One exact tool-argument substitution with memory provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    json_pointer: str
    presentation: str
    lookup: str
    kind: str
    source_node_ids: tuple[UUID, ...]
    source_references: tuple[str, ...]


class ToolArgumentResolution(BaseModel):
    """A copied argument object and every exact substitution PRME applied."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arguments: dict[str, Any]
    replacements: tuple[ToolArgumentReplacement, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.replacements)


def attach_value_bindings(
    *,
    source_content: str,
    retrieval_content: str,
    metadata: dict | None,
    value_bindings: list[MemoryValueBinding | dict[str, Any]] | None,
) -> dict | None:
    """Validate and snapshot caller bindings before storage can await work."""
    copied = _snapshot_object(metadata)
    if value_bindings is None:
        return copied
    if copied is not None and VALUE_BINDINGS_METADATA_KEY in copied:
        raise ValueError(
            f"metadata.{VALUE_BINDINGS_METADATA_KEY} is reserved; pass value_bindings instead"
        )
    if len(value_bindings) > 256:
        raise ValueError("A memory can contain at most 256 value bindings")
    parsed = [MemoryValueBinding.model_validate(item) for item in value_bindings]
    if not parsed:
        return copied

    references: set[str] = set()
    presentations: set[str] = set()
    for binding in parsed:
        if binding.reference in references:
            raise ValueError(f"Duplicate value binding reference: {binding.reference}")
        if binding.presentation in presentations:
            raise ValueError(
                f"Duplicate value binding presentation: {binding.presentation!r}"
            )
        if binding.presentation not in source_content:
            raise ValueError(
                f"Value binding {binding.reference!r} presentation is absent from source content"
            )
        if binding.presentation not in retrieval_content:
            raise ValueError(
                f"Value binding {binding.reference!r} presentation is absent from retrieval content"
            )
        references.add(binding.reference)
        presentations.add(binding.presentation)

    result = dict(copied or {})
    result[VALUE_BINDINGS_METADATA_KEY] = [
        item.model_dump(mode="json") for item in parsed
    ]
    return _snapshot_object(result)


def value_bindings_for_node(node: "MemoryNode") -> tuple[MemoryValueBinding, ...]:
    """Read typed bindings from one node, rejecting malformed reserved data."""
    raw = (node.metadata or {}).get(VALUE_BINDINGS_METADATA_KEY)
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"Node {node.id} has malformed value binding metadata")
    if len(raw) > 256:
        raise ValueError(f"Node {node.id} has too many value bindings")
    try:
        parsed = tuple(MemoryValueBinding.model_validate(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Node {node.id} has malformed value binding metadata") from exc
    if len({item.reference for item in parsed}) != len(parsed):
        raise ValueError(f"Node {node.id} has duplicate value binding references")
    if len({item.presentation for item in parsed}) != len(parsed):
        raise ValueError(f"Node {node.id} has duplicate value binding presentations")
    if any(item.presentation not in node.content for item in parsed):
        raise ValueError(f"Node {node.id} has an unbound value binding presentation")
    return parsed


def retrieved_value_bindings(bundle: "MemoryBundle") -> tuple[RetrievedValueBinding, ...]:
    """Return only bindings whose presentation text survived context packing."""
    reverse_refs = {node_id: ref for ref, node_id in bundle.context_references.items()}
    result: list[RetrievedValueBinding] = []
    for candidates in bundle.sections.values():
        for candidate in candidates:
            visible = candidate.rendered_text or ""
            node = candidate.node
            context_ref = reverse_refs.get(node.id, str(node.id))
            for binding in value_bindings_for_node(node):
                if binding.presentation not in visible:
                    continue
                result.append(
                    RetrievedValueBinding(
                        context_ref=context_ref,
                        node_id=node.id,
                        reference=binding.reference,
                        kind=binding.kind,
                        presentation=binding.presentation,
                        lookup=binding.lookup,
                    )
                )
    return tuple(sorted(result, key=lambda item: (str(item.node_id), item.reference)))


def render_value_bindings(bundle: "MemoryBundle") -> str:
    """Render a deterministic typed block for bindings visible in the bundle."""
    bindings = retrieved_value_bindings(bundle)
    if not bindings:
        return ""
    payload = [item.model_dump(mode="json") for item in bindings]
    return (
        "Memory value binding fields: context_ref, node_id, reference, kind, "
        "presentation, lookup, lookup_authority. Use presentation as the exact "
        "user-facing form and lookup as the complete tool-argument form. "
        "Bindings are caller-supplied source data.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )


def _pointer_segment(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def resolve_tool_arguments(
    bundle: "MemoryBundle", arguments: dict[str, Any]
) -> ToolArgumentResolution:
    """Replace exact complete presentation values with visible lookup values."""
    copied = _snapshot_object(arguments)
    if copied is None:
        copied = {}
    bindings = retrieved_value_bindings(bundle)
    grouped: dict[str, list[RetrievedValueBinding]] = {}
    for binding in bindings:
        grouped.setdefault(binding.presentation, []).append(binding)
    for presentation, sources in grouped.items():
        forms = {(item.lookup, item.kind) for item in sources}
        if len(forms) > 1:
            raise ValueError(
                f"Ambiguous lookup values for presentation {presentation!r}"
            )

    replacements: list[ToolArgumentReplacement] = []

    def visit(value: Any, pointer: str) -> Any:
        if isinstance(value, str) and value in grouped:
            sources = grouped[value]
            lookup = sources[0].lookup
            replacements.append(
                ToolArgumentReplacement(
                    json_pointer=pointer,
                    presentation=value,
                    lookup=lookup,
                    kind=sources[0].kind,
                    source_node_ids=tuple(sorted({item.node_id for item in sources}, key=str)),
                    source_references=tuple(
                        sorted({item.reference for item in sources})
                    ),
                )
            )
            return lookup
        if isinstance(value, list):
            return [visit(item, f"{pointer}/{index}") for index, item in enumerate(value)]
        if isinstance(value, dict):
            return {
                key: visit(item, f"{pointer}/{_pointer_segment(key)}")
                for key, item in value.items()
            }
        return value

    resolved = visit(copied, "")
    return ToolArgumentResolution(
        arguments=resolved,
        replacements=tuple(replacements),
    )


__all__ = [
    "MemoryValueBinding",
    "RetrievedValueBinding",
    "ToolArgumentReplacement",
    "ToolArgumentResolution",
    "VALUE_BINDINGS_METADATA_KEY",
    "attach_value_bindings",
    "render_value_bindings",
    "resolve_tool_arguments",
    "retrieved_value_bindings",
    "value_bindings_for_node",
]

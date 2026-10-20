"""Shared bounded content admission; explicit local recognition, no custody."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import time
import unicodedata
from typing import Any, Protocol
from uuid import UUID, uuid4

from prme.privacy.capabilities import (
    AdmissionContent, BudgetContinuation, OperationBudget, SanitizedContent, SourceOnlyRegion,
)
from prme.privacy.local_analyzer import Detection, LocalAnalyzer, VERSIONS
from prme.privacy.policy import PolicySnapshot, PrivacyDenied
from prme.privacy.pseudonyms import Pseudonyms, issued_token_spans, validate_issued
from prme.privacy.recognizers import (
    DETECTOR_SECONDS, INERT_PREFIX, MAX_DEPTH, MAX_INPUT_BYTES, MAX_LEAVES,
    REGISTRY_VERSION, RESERVED_PREFIX, SANITIZER_VERSION, RuleProvider, Span,
    bounded_spans, credential_assignment, validate_rules,
)
from prme.privacy.strict_schema import ClosedSchema, SchemaProvider, parse_json


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise PrivacyDenied("privacy_unsupported_input") from None


def sanitized_checksum(fields_json: str, role: str = "user", event_time: str | None = None) -> str:
    """This checksum is private and covers only the admitted representation."""
    unit = canonical({"domain": "prme-sanitized-source-v1", "fields_json": fields_json,
                      "role": role, "event_time": event_time})
    return hashlib.sha256(unit.encode("utf-8")).hexdigest()


class SourceClassifier(Protocol):
    async def classify(self, fields_json: str, snapshot: PolicySnapshot) -> tuple[SourceOnlyRegion, ...]:
        """Classify sanitized source using trusted engine context, never metadata."""
        ...


@dataclass(repr=False)
class _BudgetState:
    identity: UUID
    input_bytes: int = 0
    detector_nanoseconds: int = 0
    leaves: int = 0
    failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def continuation(self) -> BudgetContinuation:
        return BudgetContinuation(self.identity, self.input_bytes, self.detector_nanoseconds, self.leaves)


def restore_budget(continuation: BudgetContinuation) -> OperationBudget:
    """Called only after authenticated envelope and live claim checks."""
    if (type(continuation) is not BudgetContinuation or not isinstance(continuation.operation_id, UUID)
            or any(type(value) is not int or value < 0 for value in (
                continuation.input_bytes, continuation.detector_nanoseconds, continuation.leaves))
            or continuation.input_bytes > MAX_INPUT_BYTES or continuation.leaves > MAX_LEAVES
            or continuation.detector_nanoseconds >= int(DETECTOR_SECONDS * 1e9)):
        raise PrivacyDenied("privacy_processing_unavailable")
    return OperationBudget(continuation.operation_id, _BudgetState(
        continuation.operation_id, continuation.input_bytes, continuation.detector_nanoseconds, continuation.leaves))


def _budget(budget: OperationBudget) -> _BudgetState:
    if type(budget) is not OperationBudget or not isinstance(budget.operation_id, UUID):
        raise PrivacyDenied("privacy_invalid_result")
    if budget.state is None:
        budget.state = _BudgetState(budget.operation_id)
    if type(budget.state) is not _BudgetState or budget.state.identity != budget.operation_id or budget.state.failed:
        raise PrivacyDenied("privacy_invalid_result")
    return budget.state


def _at(value: Any, path: tuple[str | int, ...]) -> str:
    try:
        for part in path:
            if type(part) not in (str, int):
                raise ValueError()
            value = value[part]
        if not isinstance(value, str):
            raise ValueError()
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        raise PrivacyDenied("privacy_invalid_result") from None


def validate_source(source: SanitizedContent, snapshot: PolicySnapshot) -> Any:
    if (type(source) is not SanitizedContent or source.source_snapshot != snapshot
            or not source.complete or not source.unambiguous
            or source.sanitizer_version != SANITIZER_VERSION or source.registry_version != REGISTRY_VERSION
            or source.detector_versions != VERSIONS):
        raise PrivacyDenied("privacy_boundary_mismatch")
    value = parse_json(source.fields_json)
    if canonical(value) != source.fields_json or not hmac.compare_digest(
            sanitized_checksum(source.fields_json, source.source_role, source.source_time), source.private_checksum):
        raise PrivacyDenied("privacy_invalid_result")
    for region in source.source_only:
        if type(region) is not SourceOnlyRegion:
            raise PrivacyDenied("privacy_invalid_result")
        text = _at(value, region.path)
        if type(region.start) is not int or type(region.end) is not int or not 0 <= region.start < region.end <= len(text):
            raise PrivacyDenied("privacy_invalid_result")
    return value


class LocalSanitizer:
    """Explicit capability for standard, sealed and capture-none policies.

    Providers of rules, schemas, classification and keys are trusted deployment
    capabilities. Raw requests cannot select their material or authorize tokens.
    """

    def __init__(self, *, analyzer: LocalAnalyzer | None = None, rule_provider: RuleProvider | None = None,
                 schema_provider: SchemaProvider | None = None, key_provider: Any = None,
                 pseudonym_epoch: UUID | None = None, source_classifier: SourceClassifier | None = None) -> None:
        self.analyzer = analyzer or LocalAnalyzer()
        self._rules = rule_provider
        self._schemas = schema_provider
        self._pseudonyms = Pseudonyms(key_provider) if key_provider is not None else None
        self._epoch = pseudonym_epoch
        self._classifier = source_classifier

    def close(self) -> None:
        self.analyzer.close()

    async def admit(self, content: AdmissionContent) -> SanitizedContent:
        state = _budget(content.budget)
        async with state.lock:
            try:
                if state.failed:
                    raise PrivacyDenied("privacy_invalid_result")
                return await self._admit(content, state)
            except PrivacyDenied:
                state.failed = True
                raise
            except asyncio.CancelledError:
                state.failed = True
                raise
            except Exception:
                state.failed = True
                raise PrivacyDenied("privacy_capability_unavailable") from None

    async def _admit(self, content: AdmissionContent, state: _BudgetState) -> SanitizedContent:
        policy = content.snapshot.policy
        if (policy.language != "en" or policy.sanitizer_version != SANITIZER_VERSION
                or policy.registry_version != REGISTRY_VERSION
                or content.mode not in {"ingress", "query", "generated_output"}):
            raise PrivacyDenied("privacy_configuration_invalid")
        if content.source_role not in {"user", "human", "participant", "assistant", "tool", "system"}:
            raise PrivacyDenied("privacy_unsupported_input")
        if content.source_time is not None:
            from datetime import datetime
            parsed = datetime.fromisoformat(content.source_time)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise PrivacyDenied("privacy_unsupported_input")
        actions = {item.category: item.action for item in policy.actions()}
        if actions.get("credential") != "block":
            raise PrivacyDenied("privacy_configuration_invalid")
        source_value = None
        issued = frozenset()
        epoch = self._epoch
        if content.mode == "generated_output":
            if content.source is None:
                raise PrivacyDenied("privacy_boundary_mismatch")
            source_value = validate_source(content.source, content.snapshot)
            if (content.issued_tokens, content.pseudonym_epoch) != (
                    content.source.issued_tokens, content.source.pseudonym_epoch):
                raise PrivacyDenied("privacy_boundary_mismatch")
            if (content.source_role, content.source_time) != (content.source.source_role, content.source.source_time):
                raise PrivacyDenied("privacy_boundary_mismatch")
            epoch = content.pseudonym_epoch
            issued = validate_issued(content.issued_tokens, epoch)
        elif content.source is not None or content.issued_tokens or content.pseudonym_epoch is not None:
            raise PrivacyDenied("privacy_boundary_mismatch")
        rules = ()
        if policy.exclusion_refs:
            if self._rules is None:
                raise PrivacyDenied("privacy_capability_unavailable")
            rules = await self._rules.resolve(content.snapshot, policy.exclusion_refs)
            validate_rules(rules)
        schema = None
        if policy.strict_schema_ref:
            if self._schemas is None:
                raise PrivacyDenied("privacy_capability_unavailable")
            schema = await self._schemas.resolve(content.snapshot, policy.strict_schema_ref)
            if type(schema) is not ClosedSchema or schema.reference != policy.strict_schema_ref:
                raise PrivacyDenied("privacy_invalid_result")
            schema.configuration()
        # Six bytes is the largest JSON escape expansion of an ASCII input
        # byte; structural allowance is bounded by the fixed leaf limit.
        if len(content.fields_json.encode("utf-8")) > MAX_INPUT_BYTES * 6 + MAX_LEAVES * 32:
            raise PrivacyDenied("privacy_unsupported_input")
        value = parse_json(content.fields_json)
        envelope = {"content", "retrieval_content", "metadata", "speaker", "value_bindings"}
        if content.mode in {"ingress", "query"} and (
                not isinstance(value, dict) or set(value) != envelope
                or not isinstance(value["content"], str) or not isinstance(value["metadata"], dict)):
            raise PrivacyDenied("privacy_unsupported_input")
        texts: list[str] = []
        # The walk collects every key and string value in canonical order. Keys
        # are checked with their values before that relation can be separated.
        leaf_count = 0
        scalar_bytes = 0

        def collect(item, depth=0):
            nonlocal leaf_count, scalar_bytes
            if depth > MAX_DEPTH:
                raise PrivacyDenied("privacy_unsupported_input")
            if isinstance(item, dict):
                normalized = set()
                for key, child in item.items():
                    if not isinstance(key, str) or unicodedata.normalize("NFC", key) in normalized:
                        raise PrivacyDenied("privacy_unsupported_input")
                    normalized.add(unicodedata.normalize("NFC", key))
                    if credential_assignment(key, child):
                        raise PrivacyDenied()
                    if depth != 0 or content.mode == "generated_output":
                        texts.append(key)
                    collect(child, depth + 1)
            elif isinstance(item, list):
                for child in item:
                    collect(child, depth + 1)
            else:
                leaf_count += 1
                if state.leaves + leaf_count > MAX_LEAVES:
                    raise PrivacyDenied("privacy_unsupported_input")
                if isinstance(item, str):
                    texts.append(item)
                elif item is not None and type(item) not in (bool, int, float):
                    raise PrivacyDenied("privacy_unsupported_input")
                elif isinstance(item, float) and not math.isfinite(item):
                    raise PrivacyDenied("privacy_unsupported_input")
                elif item is not None:
                    scalar_bytes += len(canonical(item).encode())

        collect(value)
        size = scalar_bytes + sum(len(text.encode("utf-8")) for text in texts)
        state.input_bytes += size
        state.leaves += leaf_count
        if state.input_bytes > MAX_INPUT_BYTES or state.leaves > MAX_LEAVES:
            raise PrivacyDenied("privacy_unsupported_input")
        if source_value is None:
            # Freeze the inert source spelling before any recognizer sees it.
            # No source-provided reserved prefix is an engine-issued identity.
            texts = [text.replace(RESERVED_PREFIX, INERT_PREFIX) for text in texts]
        if source_value is not None:
            for region in content.source.source_only:
                prohibited = _at(source_value, region.path)[region.start:region.end]
                if any(prohibited in text for text in texts):
                    raise PrivacyDenied("privacy_evidence_ambiguous")
        # Source-authenticated tokens are masked without changing offsets.
        masks = [issued_token_spans(text, issued, epoch) if source_value is not None else () for text in texts]
        def mask_text(text, regions):
            pieces, position = [], 0
            for start, end in regions:
                pieces.extend((text[position:start], " " * (end - start)))
                position = end
            return "".join(pieces) + text[position:]

        scans = tuple(mask_text(text, mask) for text, mask in zip(texts, masks, strict=True))
        await self.analyzer.ready()
        started = time.monotonic_ns()
        try:
            deadline = started + int(DETECTOR_SECONDS * 1e9) - state.detector_nanoseconds
            bounded = tuple(bounded_spans(text, rules, deadline_ns=deadline) for text in scans)
            if sum(len(spans) for spans in bounded) > MAX_INPUT_BYTES:
                raise PrivacyDenied("privacy_capability_unavailable")
            if any(span.category == "credential" for spans in bounded for span in spans):
                raise PrivacyDenied()
            remaining = DETECTOR_SECONDS - (state.detector_nanoseconds + time.monotonic_ns() - started) / 1e9
            detection = await self.analyzer.analyze(scans, seconds=remaining)
        finally:
            state.detector_nanoseconds += time.monotonic_ns() - started
        if (type(detection) is not Detection or not detection.complete or detection.versions != VERSIONS
                or len(detection.spans) != len(texts) or state.detector_nanoseconds >= int(DETECTOR_SECONDS * 1e9)):
            raise PrivacyDenied("privacy_capability_unavailable")
        handle = None
        if issued:
            if self._pseudonyms is None or epoch is None:
                raise PrivacyDenied("privacy_capability_unavailable")
            handle = await self._pseudonyms.handle(content.snapshot, epoch)
        counts: Counter[str] = Counter()
        tokens = set(issued)
        removed_people = set()
        transformed = []
        try:
            for text, local, extra in zip(texts, detection.spans, bounded, strict=True):
                spans = tuple(set(local) | set(extra))
                for span in spans:
                    span.validate(text)
                if any(actions[span.category] == "block" for span in spans):
                    raise PrivacyDenied()
                selected = sorted((span for span in spans if actions[span.category] != "allow"),
                                  key=lambda span: (span.start, span.end, span.category))
                groups: list[list[Span]] = []
                group_end = 0
                for span in selected:
                    if groups and span.start < group_end:
                        groups[-1].append(span)
                        group_end = max(group_end, span.end)
                    else:
                        groups.append([span])
                        group_end = span.end
                replacements = []
                for group in groups:
                    start, end = min(span.start for span in group), max(span.end for span in group)
                    redactions = [span for span in group if actions[span.category] == "redact"]
                    if redactions:
                        category = sorted(span.category for span in redactions)[0]
                        replacement = f"[{category.upper()}_REMOVED]"
                        for span in redactions:
                            if span.category == "person":
                                removed_people.add(text[span.start:span.end])
                    else:
                        if len({(span.category, span.start, span.end) for span in group}) != 1:
                            raise PrivacyDenied("privacy_evidence_ambiguous")
                        category = group[0].category
                        if self._pseudonyms is None or epoch is None:
                            raise PrivacyDenied("privacy_capability_unavailable")
                        if handle is None:
                            handle = await self._pseudonyms.handle(content.snapshot, epoch)
                        replacement = self._pseudonyms.issue(handle, category, text[start:end])
                        if source_value is not None and replacement not in issued:
                            raise PrivacyDenied("privacy_boundary_mismatch")
                        tokens.add(replacement)
                    replacements.append((start, end, replacement))
                if len(removed_people) > 1:
                    raise PrivacyDenied("privacy_evidence_ambiguous")
                for span in set(spans):
                    counts[f"{span.category}:{actions[span.category]}"] += 1
                # Offset edits apply to the exact inert spelling scanned above.
                pieces = []
                position = 0
                for start, end, replacement in replacements:
                    piece = text[position:start]
                    pieces.append(piece)
                    pieces.append(replacement)
                    position = end
                piece = text[position:]
                pieces.append(piece)
                transformed.append("".join(pieces))
        finally:
            if handle is not None:
                handle.close()
        replacements = iter(transformed)

        def rebuild(item, depth=0):
            if isinstance(item, dict):
                output = {}
                normalized = set()
                for original_key, child in item.items():
                    key = next(replacements) if depth != 0 or content.mode == "generated_output" else original_key
                    normal = unicodedata.normalize("NFC", key)
                    if normal in normalized:
                        raise PrivacyDenied("privacy_evidence_ambiguous")
                    normalized.add(normal)
                    output[key] = rebuild(child, depth + 1)
                return output
            if isinstance(item, list):
                return [rebuild(child, depth + 1) for child in item]
            return next(replacements) if isinstance(item, str) else item

        admitted = rebuild(value)
        if schema:
            self._strict(schema, admitted, content.mode, frozenset(tokens))
        fields_json = canonical(admitted)
        regions = ()
        if content.mode == "ingress" and self._classifier is not None:
            regions = await self._classifier.classify(fields_json, content.snapshot)
            if not isinstance(regions, tuple) or len(regions) > MAX_LEAVES:
                raise PrivacyDenied("privacy_invalid_result")
        result = SanitizedContent(
            fields_json=fields_json, source_id=content.source.source_id if content.source else uuid4(),
            representation_id=uuid4(), private_checksum=sanitized_checksum(fields_json, content.source_role, content.source_time),
            sanitizer_version=SANITIZER_VERSION, registry_version=REGISTRY_VERSION,
            detector_versions=VERSIONS, action_counts=tuple(sorted(counts.items())),
            issued_tokens=tuple(sorted(tokens)), pseudonym_epoch=epoch if tokens else None,
            representation_role="admitted_memory" if content.mode == "generated_output" else "sanitized_source",
            source_snapshot=content.snapshot, continuation=state.continuation(), source_only=regions,
            source_role=content.source_role, source_time=content.source_time,
        )
        validate_source(result, content.snapshot)
        return result

    @staticmethod
    def _strict(schema: ClosedSchema, value: Any, mode: str, tokens: frozenset[str]) -> None:
        if mode in {"ingress", "query"}:
            if (not isinstance(value, dict) or set(value) != {
                    "content", "retrieval_content", "metadata", "speaker", "value_bindings"}
                    or value["metadata"] or value["speaker"] is not None
                    or value["retrieval_content"] is not None or value["value_bindings"] not in (None, [])):
                raise PrivacyDenied("privacy_unsupported_input")
            schema.validate(parse_json(value["content"]), tokens)
        else:
            schema.validate(value, tokens)

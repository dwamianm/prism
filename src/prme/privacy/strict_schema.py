"""Closed immutable value schemas; no caller code, regex or free-text escape."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Literal, Protocol
from uuid import UUID

from prme.privacy.policy import PolicySnapshot, PrivacyDenied

ValidatorKind = Literal[
    "opaque_id", "issued_scoped_pseudonym", "boolean", "bounded_integer",
    "exact_decimal", "timezone_aware_timestamp", "finite_enum",
]
_KINDS = frozenset((
    "opaque_id", "issued_scoped_pseudonym", "boolean", "bounded_integer",
    "exact_decimal", "timezone_aware_timestamp", "finite_enum",
))
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z", re.ASCII)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z", re.ASCII)


def parse_json(value: str):
    """Reject duplicate keys and nonfinite numbers before information is lost."""
    import json

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError()
            result[key] = item
        return result

    def invalid_constant(_):
        raise ValueError()

    try:
        return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (ValueError, TypeError, RecursionError):
        raise PrivacyDenied("privacy_unsupported_input") from None


@dataclass(frozen=True, repr=False)
class ValueValidator:
    kind: ValidatorKind
    required: bool = True
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()

    def configuration(self) -> None:
        if self.kind not in _KINDS or type(self.required) is not bool:
            raise PrivacyDenied("privacy_configuration_invalid")
        if self.kind == "bounded_integer":
            if (type(self.minimum) is not int or type(self.maximum) is not int
                    or self.minimum > self.maximum):
                raise PrivacyDenied("privacy_configuration_invalid")
        elif self.minimum is not None or self.maximum is not None:
            raise PrivacyDenied("privacy_configuration_invalid")
        if self.kind == "finite_enum":
            if (not isinstance(self.choices, tuple) or not 1 <= len(self.choices) <= 64
                    or any(not isinstance(item, str) or not item or len(item.encode("utf-8")) > 256
                           for item in self.choices) or len(set(self.choices)) != len(self.choices)):
                raise PrivacyDenied("privacy_configuration_invalid")
        elif self.choices:
            raise PrivacyDenied("privacy_configuration_invalid")

    def valid(self, value: object, issued_tokens: frozenset[str]) -> bool:
        if self.kind == "boolean":
            return type(value) is bool
        if self.kind == "bounded_integer":
            return type(value) is int and self.minimum <= value <= self.maximum
        if not isinstance(value, str):
            return False
        if self.kind == "issued_scoped_pseudonym":
            return value in issued_tokens
        if self.kind == "finite_enum":
            return value in self.choices
        if self.kind == "opaque_id":
            try:
                return str(UUID(value)) == value
            except ValueError:
                return False
        if self.kind == "exact_decimal":
            if not _DECIMAL.fullmatch(value) or len(value) > 256:
                return False
            try:
                return Decimal(value).is_finite()
            except InvalidOperation:
                return False
        if self.kind == "timezone_aware_timestamp":
            try:
                parsed = datetime.fromisoformat(value)
                return parsed.tzinfo is not None and parsed.utcoffset() is not None
            except ValueError:
                return False
        return False


@dataclass(frozen=True, repr=False)
class ClosedSchema:
    reference: UUID
    fields: tuple[tuple[str, ValueValidator], ...]
    version: Literal["prme-strict-schema-v1"] = "prme-strict-schema-v1"

    def configuration(self) -> None:
        if (self.version != "prme-strict-schema-v1" or not isinstance(self.reference, UUID)
                or not isinstance(self.fields, tuple) or not 1 <= len(self.fields) <= 64):
            raise PrivacyDenied("privacy_configuration_invalid")
        names = set()
        for field in self.fields:
            if not isinstance(field, tuple) or len(field) != 2:
                raise PrivacyDenied("privacy_configuration_invalid")
            name, validator = field
            if (not isinstance(name, str) or not _FIELD.fullmatch(name) or name in names
                    or type(validator) is not ValueValidator):
                raise PrivacyDenied("privacy_configuration_invalid")
            names.add(name)
            validator.configuration()

    def validate(self, value: object, issued_tokens: frozenset[str]) -> None:
        if not isinstance(value, dict):
            raise PrivacyDenied("privacy_unsupported_input")
        declarations = dict(self.fields)
        if set(value) - declarations.keys():
            raise PrivacyDenied("privacy_unsupported_input")
        for name, validator in self.fields:
            if name not in value:
                if validator.required:
                    raise PrivacyDenied("privacy_unsupported_input")
            elif not validator.valid(value[name], issued_tokens):
                raise PrivacyDenied("privacy_unsupported_input")


class SchemaProvider(Protocol):
    async def resolve(self, snapshot: PolicySnapshot, reference: UUID) -> ClosedSchema:
        """Return the exact immutable schema authorized for this boundary."""
        ...

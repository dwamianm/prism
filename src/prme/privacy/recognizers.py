"""Closed v1 recognition rules. Results contain offsets, never matched values."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Protocol
from uuid import UUID

from prme.privacy.policy import Category, PolicySnapshot, PrivacyDenied

SANITIZER_VERSION = "prme-sanitizer-v1"
REGISTRY_VERSION = "prme-recognizers-v1"
MAX_INPUT_BYTES = 65_536
MAX_RULES = 64
MAX_LITERAL_BYTES = 256
MAX_RULE_BYTES = 8_192
MAX_DEPTH = 8
MAX_LEAVES = 1_024
DETECTOR_SECONDS = 2.0

CATEGORIES: tuple[Category, ...] = (
    "credential", "email", "phone", "person", "location", "us_ssn",
    "payment_card", "ip_address", "custom_identifier", "sensitive_topic",
)
LOCAL_ENTITIES = {
    "EMAIL_ADDRESS": ("email", 0.5),
    "PHONE_NUMBER": ("phone", 0.4),
    "PERSON": ("person", 0.5),
    "LOCATION": ("location", 0.5),
    "US_SSN": ("us_ssn", 0.5),
    "CREDIT_CARD": ("payment_card", 0.5),
    "IP_ADDRESS": ("ip_address", 0.5),
}
RESERVED_PREFIX = "[PRME_PSEUDONYM_V1:"
INERT_PREFIX = "[LITERAL_PRME_PSEUDONYM_V1:"


@dataclass(frozen=True, repr=False)
class Span:
    category: str
    start: int
    end: int
    recognizer: str

    def validate(self, text: str) -> None:
        if (self.category not in CATEGORIES or type(self.start) is not int
                or type(self.end) is not int or not 0 <= self.start < self.end <= len(text)
                or not isinstance(self.recognizer, str)):
            raise PrivacyDenied("privacy_invalid_result")


@dataclass(frozen=True, repr=False)
class CustomRule:
    """Private resolved rule material; policy inspection exposes only its ref."""

    kind: Literal["literal_identifier", "literal_topic", "typed_identifier"]
    literal: str | None = None
    category: str | None = None

    def validate(self) -> int:
        if self.kind == "typed_identifier":
            if self.literal is not None or self.category not in LOCAL_ENTITIES_CATEGORIES:
                raise PrivacyDenied("privacy_configuration_invalid")
            return 0
        if self.kind not in {"literal_identifier", "literal_topic"} or self.category is not None:
            raise PrivacyDenied("privacy_configuration_invalid")
        try:
            size = len(self.literal.encode("utf-8")) if isinstance(self.literal, str) else 0
        except UnicodeError:
            raise PrivacyDenied("privacy_configuration_invalid") from None
        if not 1 <= size <= MAX_LITERAL_BYTES:
            raise PrivacyDenied("privacy_configuration_invalid")
        return size


LOCAL_ENTITIES_CATEGORIES = frozenset(value[0] for value in LOCAL_ENTITIES.values())


class RuleProvider(Protocol):
    async def resolve(self, snapshot: PolicySnapshot, references: tuple[UUID, ...]) -> tuple[CustomRule, ...]:
        """Resolve immutable, boundary-bound private rules. Never execute input."""
        ...


def validate_rules(rules: tuple[CustomRule, ...]) -> None:
    if (not isinstance(rules, tuple) or len(rules) > MAX_RULES
            or any(type(rule) is not CustomRule for rule in rules)):
        raise PrivacyDenied("privacy_configuration_invalid")
    if sum(rule.validate() for rule in rules) > MAX_RULE_BYTES:
        raise PrivacyDenied("privacy_configuration_invalid")


_ASSIGNMENT_KEY = r"(?:password|passwd|pwd|api_key|access_token|client_secret)"
_ASSIGNMENT = re.compile(
    rf"(?<![A-Za-z0-9_]){_ASSIGNMENT_KEY}(?![A-Za-z0-9_])"
    r"""[ \t]{0,8}[:=][ \t]{0,8}['"]?[^\s'"]{1,4096}['"]?""",
    re.IGNORECASE | re.ASCII,
)
_BEARER = re.compile(
    r"(?<![A-Za-z0-9_])Bearer[ \t]{1,8}[A-Za-z0-9._~+/-]{8,4096}={0,2}",
    re.IGNORECASE | re.ASCII,
)
_GHP = re.compile(r"(?<![A-Za-z0-9_])ghp_[A-Za-z0-9]{36}(?![A-Za-z0-9_])", re.ASCII)
_PEM_HEADERS = tuple(
    f"-----BEGIN {kind}-----"
    for kind in ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY",
                 "OPENSSH PRIVATE KEY", "ENCRYPTED PRIVATE KEY")
)
_AT = re.compile(r"\[at\]|\(at\)")
_DOT = re.compile(r" ?(?:\[dot\]|\(dot\)) ?")
_LOCAL = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-")
_LABEL = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")


def credential_assignment(key: str, value: object) -> bool:
    """Metadata assignments retain the relation between key and string value."""
    return (isinstance(value, str) and bool(re.fullmatch(_ASSIGNMENT_KEY, key, re.IGNORECASE | re.ASCII))
            and bool(_ASSIGNMENT.match(f"{key}={value}")))


def credential_spans(text: str) -> list[Span]:
    spans = []
    for pattern, name in ((_ASSIGNMENT, "credential-assignment-v1"),
                          (_BEARER, "credential-bearer-v1"), (_GHP, "credential-ghp-v1")):
        spans.extend(Span("credential", match.start(), match.end(), name) for match in pattern.finditer(text))
    for header in _PEM_HEADERS:
        start = text.find(header)
        while start != -1:
            spans.append(Span("credential", start, start + len(header), "credential-pem-v1"))
            start = text.find(header, start + len(header))
    return spans


def obfuscated_email_spans(text: str) -> list[Span]:
    """Read bounded labels directly in the original text; no lossy offset map."""
    spans = []
    for at in _AT.finditer(text):
        local_end = at.start() - (text[at.start() - 1:at.start()] == " ")
        start = local_end
        while start > 0 and text[start - 1] in _LOCAL and local_end - start <= 64:
            start -= 1
        if not 1 <= local_end - start <= 64:
            continue
        position = at.end() + (text[at.end():at.end() + 1] == " ")
        labels = []
        end = position
        while position < len(text):
            begin = position
            while position < len(text) and text[position] in _LABEL and position - begin <= 63:
                position += 1
            label = text[begin:position]
            if not 1 <= len(label) <= 63:
                labels = []
                break
            labels.append(label)
            end = position
            dot = _DOT.match(text, position)
            if dot is None:
                break
            position = dot.end()
        if (len(labels) >= 2 and 2 <= len(labels[-1]) <= 24
                and labels[-1].isascii() and labels[-1].isalpha()):
            spans.append(Span("email", start, end, "obfuscated-email-v1"))
    return spans


def bounded_spans(text: str, rules: tuple[CustomRule, ...] = ()) -> list[Span]:
    spans = credential_spans(text) + obfuscated_email_spans(text)
    for rule in rules:
        if rule.kind == "typed_identifier":
            continue  # the same explicit local registry already scans this category
        assert rule.literal is not None
        category = "sensitive_topic" if rule.kind == "literal_topic" else "custom_identifier"
        start = text.find(rule.literal)
        while start != -1:
            spans.append(Span(category, start, start + len(rule.literal), f"{rule.kind}-v1"))
            start = text.find(rule.literal, start + 1)
    return spans


def coverage_descriptor() -> dict:
    """Only fixed, non-content registry facts may appear in policy inspection."""
    return {
        "sanitizer_version": SANITIZER_VERSION,
        "registry_version": REGISTRY_VERSION,
        "languages": ["en"],
        "language_detection": "caller_declared_only",
        "packages": {"presidio-analyzer": "2.2.364", "spacy": "3.8.11", "en_core_web_sm": "3.8.0"},
        "categories": list(CATEGORIES),
        "phone_regions": ["US", "GB"],
        "custom_rules": ["literal_identifier", "literal_topic", "typed_identifier"],
        "limits": {"input_utf8_bytes": MAX_INPUT_BYTES, "rules": MAX_RULES,
                   "literal_utf8_bytes": MAX_LITERAL_BYTES, "custom_utf8_bytes": MAX_RULE_BYTES,
                   "metadata_depth": MAX_DEPTH, "metadata_leaves": MAX_LEAVES,
                   "detector_milliseconds": int(DETECTOR_SECONDS * 1000)},
        "limitations": [
            "No reliable automatic language detection.",
            "Encoded secrets, homoglyphs and unlisted credentials may be missed.",
            "Names and locations are heuristic; topics require configured exact literals.",
            "A clean scan does not prove absence of secrets; pseudonyms are not anonymous.",
        ],
    }

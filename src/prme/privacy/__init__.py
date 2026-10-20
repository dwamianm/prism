"""Opt-in policy admission. Protected product integration remains gated."""

from prme.privacy.capabilities import PrivacyRuntime
from prme.privacy.policy import CategoryAction, MemoryRetention, PolicySnapshot, PrivacyDenied, PrivacyPolicy
from prme.privacy.recognizers import CustomRule, RuleProvider
from prme.privacy.sanitizer import LocalSanitizer, SourceClassifier
from prme.privacy.strict_schema import ClosedSchema, SchemaProvider, ValueValidator

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.privacy.crypto import CryptoProtector
    from prme.privacy.keys import KeyBoundary, LocalAccessAuthority, SecretKeyHandle, StoredProcessingAuthorizer
    from prme.privacy.local_keys import NativeKeyCustody, public_registry

_LAZY = {
    "CryptoProtector": "prme.privacy.crypto",
    "KeyBoundary": "prme.privacy.keys",
    "LocalAccessAuthority": "prme.privacy.keys",
    "SecretKeyHandle": "prme.privacy.keys",
    "StoredProcessingAuthorizer": "prme.privacy.keys",
    "NativeKeyCustody": "prme.privacy.local_keys",
    "public_registry": "prme.privacy.local_keys",
}


def __getattr__(name):
    if name not in _LAZY:
        raise AttributeError(name)
    from importlib import import_module
    result = getattr(import_module(_LAZY[name]), name)
    globals()[name] = result
    return result

__all__ = ["ClosedSchema", "CustomRule", "LocalSanitizer", "RuleProvider", "SchemaProvider", "SourceClassifier", "ValueValidator", "CategoryAction", "MemoryRetention", "PolicySnapshot", "PrivacyDenied", "PrivacyPolicy", "PrivacyRuntime",
           "CryptoProtector", "KeyBoundary", "LocalAccessAuthority", "SecretKeyHandle", "StoredProcessingAuthorizer",
           "NativeKeyCustody", "public_registry"]


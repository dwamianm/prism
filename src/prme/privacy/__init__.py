"""Opt-in policy admission. Protected product integration remains gated."""

from prme.privacy.capabilities import PrivacyRuntime
from prme.privacy.policy import CategoryAction, MemoryRetention, PolicySnapshot, PrivacyDenied, PrivacyPolicy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prme.privacy.crypto import CryptoProtector
    from prme.privacy.keys import KeyBoundary, LocalAccessAuthority, SecretKeyHandle, StoredProcessingAuthorizer
    from prme.privacy.local_keys import NativeKeyCustody, public_registry
    from prme.privacy.recognizers import CustomRule, RuleProvider
    from prme.privacy.sanitizer import LocalSanitizer, SourceClassifier
    from prme.privacy.strict_schema import ClosedSchema, SchemaProvider, ValueValidator

_LAZY = {
    "CryptoProtector": "prme.privacy.crypto",
    "KeyBoundary": "prme.privacy.keys",
    "LocalAccessAuthority": "prme.privacy.keys",
    "SecretKeyHandle": "prme.privacy.keys",
    "StoredProcessingAuthorizer": "prme.privacy.keys",
    "NativeKeyCustody": "prme.privacy.local_keys",
    "public_registry": "prme.privacy.local_keys",
    "CustomRule": "prme.privacy.recognizers",
    "RuleProvider": "prme.privacy.recognizers",
    "LocalSanitizer": "prme.privacy.sanitizer",
    "SourceClassifier": "prme.privacy.sanitizer",
    "ClosedSchema": "prme.privacy.strict_schema",
    "SchemaProvider": "prme.privacy.strict_schema",
    "ValueValidator": "prme.privacy.strict_schema",
}


def __getattr__(name):
    if name not in _LAZY:
        raise AttributeError(name)
    from importlib import import_module
    result = getattr(import_module(_LAZY[name]), name)
    globals()[name] = result
    return result

__all__ = ["CategoryAction", "MemoryRetention", "PolicySnapshot", "PrivacyDenied", "PrivacyPolicy", "PrivacyRuntime",
           "CryptoProtector", "KeyBoundary", "LocalAccessAuthority", "SecretKeyHandle", "StoredProcessingAuthorizer",
           "NativeKeyCustody", "public_registry", "ClosedSchema", "CustomRule", "LocalSanitizer",
           "RuleProvider", "SchemaProvider", "SourceClassifier", "ValueValidator"]

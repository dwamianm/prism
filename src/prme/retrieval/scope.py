"""Validate and snapshot retrieval scope before any asynchronous work."""
from collections.abc import Sequence
from typing import TypeAlias

from prme.types import Scope

ScopeInput: TypeAlias = Scope | str | Sequence[Scope | str] | None


def normalize_scope(scope: ScopeInput) -> list[Scope] | None:
    """Only None means unfiltered; every supplied name must be valid.

    Copy sequences so caller mutation during an await cannot alter the request.
    Accept enum values and their string names consistently across Python and
    transport callers. Reject empty/unsupported inputs instead of broadening.
    """
    if scope is None:
        return None
    if isinstance(scope, str):
        values = [scope]
    elif isinstance(scope, Sequence) and not isinstance(scope, (bytes, bytearray)):
        values = list(scope)
    else:
        raise ValueError("scope must be a scope name or a nonempty sequence of names")
    if not values:
        raise ValueError("scope must not be empty; use None for an unfiltered request")
    try:
        return [Scope(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError("scope contains an invalid scope name") from exc

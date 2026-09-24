"""Shared model client construction for PRME runtime callers."""

from prme.model_runtime.generation import create_instructor_client, resolve_provider_connection

__all__ = ["create_instructor_client", "resolve_provider_connection"]

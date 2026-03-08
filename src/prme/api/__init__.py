"""PRME HTTP API layer (FastAPI).

Provides REST endpoints for store, retrieve, organize, and lifecycle
operations. The API is an optional dependency — install with:

    pip install prme[api]

Usage:

    from prme.api.app import create_app
    app = create_app()

Or run directly:

    python -m prme.api
"""

from prme.api.app import create_app

__all__ = ["create_app"]

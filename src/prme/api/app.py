"""PRME FastAPI application factory.

Creates and configures the FastAPI app with lifespan management
for the MemoryEngine. The engine is stored in app.state and shared
across all request handlers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from prme import __version__
from prme.config import PRMEConfig

logger = logging.getLogger(__name__)


def create_app(config: PRMEConfig | None = None, *, privacy_runtime=None, embedding_provider=None) -> FastAPI:
    """Create and configure the PRME FastAPI application.

    Args:
        config: Optional PRME configuration. Defaults to PRMEConfig().

    Returns:
        Configured FastAPI application with engine lifespan management.
    """
    if config is None:
        config = PRMEConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Manage MemoryEngine lifecycle."""
        from prme.storage.engine import MemoryEngine

        logger.info("Starting PRME MemoryEngine...")
        engine = await MemoryEngine.create(config, privacy_runtime=privacy_runtime, embedding_provider=embedding_provider)
        app.state.engine = engine
        logger.info("PRME MemoryEngine ready (backend=%s)", config.backend)

        yield

        logger.info("Shutting down PRME MemoryEngine...")
        await engine.close()
        app.state.engine = None
        logger.info("PRME MemoryEngine shut down")

    app = FastAPI(
        title="PRME API",
        description="Portable Relational Memory Engine — HTTP API",
        version=__version__,
        lifespan=lifespan,
    )

    # Expose config to request handlers (auth dependency reads it).
    app.state.config = config
    app.state.engine = None

    # CORS — disabled unless origins are explicitly pinned (issue #34).
    # Credentials are never allowed with a wildcard origin: Starlette
    # reflects the request Origin in that combination, letting any
    # website read responses cross-origin.
    cors_origins = config.api.cors_origins
    if cors_origins:
        allow_credentials = (
            config.api.cors_allow_credentials and "*" not in cors_origins
        )
        if config.api.cors_allow_credentials and not allow_credentials:
            logger.warning(
                "cors_allow_credentials ignored: wildcard origin in "
                "cors_origins. Pin explicit origins to allow credentials."
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler
    from prme.privacy.policy import PrivacyDenied

    @app.exception_handler(PrivacyDenied)
    async def privacy_denied(request: Request, exc: PrivacyDenied) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.code})

    @app.exception_handler(RequestValidationError)
    async def privacy_validation(request: Request, exc: RequestValidationError):
        body = exc.body
        if config.privacy_policy is not None or (isinstance(body, dict) and body.get("policy") is not None):
            return JSONResponse(status_code=422, content={"detail": "privacy_configuration_invalid"})
        return await request_validation_exception_handler(request, exc)

    # Generic handler for unexpected errors: log details server-side,
    # never echo internals to the caller (issue #34).
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        if config.privacy_policy is None and privacy_runtime is None:
            logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
        else:
            logger.error("privacy_request_failed")
        return JSONResponse(
            status_code=500, content={"detail": "Internal server error"}
        )

    # Register routes
    from prme.api.routes import public_router, router

    app.include_router(public_router)
    app.include_router(router)

    return app

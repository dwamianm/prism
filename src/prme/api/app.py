"""PRME FastAPI application factory.

Creates and configures the FastAPI app with lifespan management
for the MemoryEngine. The engine is stored in app.state and shared
across all request handlers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from prme.config import PRMEConfig

logger = logging.getLogger(__name__)


def create_app(config: PRMEConfig | None = None) -> FastAPI:
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
        engine = await MemoryEngine.create(config)
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
        version="0.3.0",
        lifespan=lifespan,
    )

    # CORS middleware — enabled by default for broad compatibility
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register routes
    from prme.api.routes import router

    app.include_router(router)

    return app

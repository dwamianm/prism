"""PRME API server runner.

Convenience function for starting the PRME HTTP server.
Can also be invoked via ``python -m prme.api``.
"""

from __future__ import annotations

import logging

from prme.config import PRMEConfig

logger = logging.getLogger(__name__)


_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    config: PRMEConfig | None = None,
    log_level: str = "info",
) -> None:
    """Start the PRME HTTP API server.

    Args:
        host: Bind address. Defaults to "127.0.0.1" (loopback only).
            Binding to a non-loopback address (e.g. "0.0.0.0") exposes
            the API to the network and requires explicit opt-in; set
            an API key (PRME_API_API_KEY) or front it with an
            authenticating reverse proxy first.
        port: Port number. Defaults to 8000.
        config: Optional PRME configuration.
        log_level: Uvicorn log level. Defaults to "info".
    """
    import uvicorn

    from prme.api.app import create_app

    if config is None:
        config = PRMEConfig()

    if host not in _LOOPBACK_HOSTS:
        if config.api.api_key is None:
            logger.warning(
                "Binding to %s with NO API key configured: anyone on the "
                "network can read, write, and archive memories. Set "
                "PRME_API_API_KEY or bind to 127.0.0.1.",
                host,
            )
        else:
            logger.warning(
                "Binding to non-loopback address %s — API is exposed to "
                "the network (bearer-token auth is enabled).",
                host,
            )

    app = create_app(config)
    uvicorn.run(app, host=host, port=port, log_level=log_level)

"""PRME API server runner.

Convenience function for starting the PRME HTTP server.
Can also be invoked via ``python -m prme.api``.
"""

from __future__ import annotations

import logging

from prme.config import PRMEConfig

logger = logging.getLogger(__name__)


def run_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    config: PRMEConfig | None = None,
    log_level: str = "info",
) -> None:
    """Start the PRME HTTP API server.

    Args:
        host: Bind address. Defaults to "0.0.0.0".
        port: Port number. Defaults to 8000.
        config: Optional PRME configuration.
        log_level: Uvicorn log level. Defaults to "info".
    """
    import uvicorn

    from prme.api.app import create_app

    app = create_app(config)
    uvicorn.run(app, host=host, port=port, log_level=log_level)

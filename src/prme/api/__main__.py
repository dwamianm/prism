"""Entry point for ``python -m prme.api``.

Starts the PRME HTTP API server with default settings.
Configuration is loaded from environment variables (PRME_ prefix).
"""

from __future__ import annotations

import argparse


def main() -> None:
    """Parse CLI arguments and start the server."""
    parser = argparse.ArgumentParser(
        description="PRME HTTP API Server",
        prog="python -m prme.api",
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port (default: 8000)"
    )
    parser.add_argument(
        "--log-level", default="info", help="Log level (default: info)"
    )
    args = parser.parse_args()

    from prme.api.server import run_server

    run_server(host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()

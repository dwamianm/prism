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
        # The unauthenticated override must be spelled out in full.
        allow_abbrev=False,
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Bind address (default: 127.0.0.1). A non-loopback address such "
            "as 0.0.0.0 requires API credentials (PRME_API_USER_KEYS or "
            "PRME_API_API_KEY); without them startup is refused. Only literal "
            "loopback addresses and localhost count as loopback."
        ),
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port (default: 8000)"
    )
    parser.add_argument(
        "--log-level", default="info", help="Log level (default: info)"
    )
    parser.add_argument(
        "--allow-unauthenticated-external-bind",
        action="store_true",
        help=(
            "Start on a non-loopback --host with no API credentials. Only for "
            "a server reachable solely through an authenticating reverse "
            "proxy: the API itself accepts every request, and every caller "
            "has operator access to all users' memories."
        ),
    )
    args = parser.parse_args()

    from prme.api.server import UnauthenticatedBindError, run_server

    try:
        run_server(
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            allow_unauthenticated_external_bind=(
                args.allow_unauthenticated_external_bind
            ),
        )
    except UnauthenticatedBindError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()

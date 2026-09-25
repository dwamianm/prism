"""PRME API server runner.

Convenience function for starting the PRME HTTP server.
Can also be invoked via ``python -m prme.api``.

The runner refuses to listen beyond loopback while no API credentials are
configured, unless the caller explicitly overrides that for a server that is
reachable only through an authenticating reverse proxy.
"""

from __future__ import annotations

import ipaddress
import logging
import socket

from prme.config import PRMEConfig

logger = logging.getLogger(__name__)


class UnauthenticatedBindError(ValueError):
    """The API would listen beyond loopback with no API credentials configured."""


def _is_loopback_address(value: str) -> bool:
    """Whether a literal IP address is loopback, counting IPv4-mapped IPv6 forms.

    Raises:
        ValueError: ``value`` is not a literal IP address.
    """
    address = ipaddress.ip_address(value)
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def _is_loopback_host(host: str) -> bool:
    """Whether the server would bind ``host`` to loopback addresses only.

    Literal addresses are checked directly (all of 127.0.0.0/8, ``::1`` and
    IPv4-mapped loopback). The only name accepted is ``localhost``, and only
    when every address it resolves to is loopback, since the server binds
    every address a name resolves to. Any other name counts as a network
    address: it would be resolved again when the server binds, after the
    engine has started, so a check now could not vouch for the bind. An empty
    host binds every interface.
    """
    if not host:
        return False
    try:
        return _is_loopback_address(host)
    except ValueError:
        pass
    if host.lower() != "localhost":
        return False
    try:
        infos = socket.getaddrinfo(
            host, None, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE,
        )
    except (OSError, UnicodeError):
        return False
    try:
        return bool(infos) and all(_is_loopback_address(info[4][0]) for info in infos)
    except ValueError:
        return False


def _check_bind(
    host: str,
    config: PRMEConfig,
    *,
    allow_unauthenticated_external_bind: bool,
) -> None:
    """Refuse an unauthenticated non-loopback bind unless explicitly overridden.

    Raises:
        UnauthenticatedBindError: ``host`` is not loopback, no API credentials
            are configured and the override is not set.
    """
    if _is_loopback_host(host):
        return
    if config.api.auth_enabled:
        logger.warning(
            "Binding to non-loopback address %s: the API is exposed to the "
            "network (bearer-token auth is enabled).",
            host,
        )
        return
    if not allow_unauthenticated_external_bind:
        raise UnauthenticatedBindError(
            f"Refusing to start the PRME API on {host!r} without "
            "authentication: anyone who can reach it could read, write and "
            "archive memories. Only literal loopback addresses and localhost "
            "count as loopback. Set PRME_API_USER_KEYS or PRME_API_API_KEY, or "
            "bind to 127.0.0.1. Only when an authenticating reverse proxy is "
            "the sole way to reach this address, pass "
            "--allow-unauthenticated-external-bind "
            "(allow_unauthenticated_external_bind=True in run_server)."
        )
    logger.warning(
        "UNAUTHENTICATED EXTERNAL BIND: serving the PRME API on %s with no API "
        "credentials because allow_unauthenticated_external_bind is set. "
        "Anyone who can reach this address can read, write and archive every "
        "user's memories unless an authenticating reverse proxy is the only "
        "way in.",
        host,
    )


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    config: PRMEConfig | None = None,
    log_level: str = "info",
    *,
    allow_unauthenticated_external_bind: bool = False,
) -> None:
    """Start the PRME HTTP API server.

    Args:
        host: Bind address. Defaults to "127.0.0.1" (loopback only).
            A non-loopback address (e.g. "0.0.0.0") exposes the API to the
            network and requires API credentials (PRME_API_USER_KEYS or
            PRME_API_API_KEY); without them startup is refused. Only literal
            loopback addresses and "localhost" count as loopback.
        port: Port number. Defaults to 8000.
        config: Optional PRME configuration.
        log_level: Uvicorn log level. Defaults to "info".
        allow_unauthenticated_external_bind: Start on a non-loopback host
            with no API credentials anyway, with a warning. Only for a server
            that is reachable solely through an authenticating reverse proxy.
            Every caller that gets through has operator access to all users.

    Raises:
        UnauthenticatedBindError: ``host`` is not loopback, no API credentials
            are configured and the override is not set. Raised before the
            app, the engine or a listener is created.
    """
    import uvicorn

    from prme.api.app import create_app

    if config is None:
        config = PRMEConfig()

    _check_bind(
        host,
        config,
        allow_unauthenticated_external_bind=allow_unauthenticated_external_bind,
    )

    app = create_app(config)
    uvicorn.run(app, host=host, port=port, log_level=log_level)

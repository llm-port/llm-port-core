"""Reusable TLS configuration helpers for backing-store connections.

These helpers translate user-facing settings (mode + optional CA bundle
path) into the concrete SSL arguments expected by asyncpg, httpx,
redis-py, and aio_pika.

Design notes
------------
* All helpers are pure functions with no implicit defaults that would
  silently weaken security: callers explicitly pass the mode they want.
* ``build_asyncpg_ssl`` returns the value asyncpg expects via
  ``connect_args={"ssl": <value>}`` — either ``False`` (off),
  the string ``"prefer"`` / ``"require"``, or an :class:`ssl.SSLContext`
  for verifying configurations.
* ``rewrite_amqp_url_for_tls`` upgrades an ``amqp://`` URL to ``amqps://``
  when TLS is requested, leaving an ``amqps://`` URL untouched.
"""

from __future__ import annotations

import enum
import ssl
from typing import Any

from yarl import URL


class SSLMode(str, enum.Enum):
    """asyncpg-style SSL mode."""

    DISABLE = "disable"
    PREFER = "prefer"
    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


def _ctx_for_mode(mode: SSLMode, ca_bundle: str | None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=ca_bundle)
    if mode == SSLMode.VERIFY_FULL:
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    elif mode == SSLMode.VERIFY_CA:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:  # REQUIRE
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def build_asyncpg_ssl(mode: str | SSLMode, ca_bundle: str | None = None) -> Any:
    """Translate an SSL mode into the asyncpg ``ssl=`` connect-arg value.

    Returns ``False`` for ``disable``, the literal string ``"prefer"``
    for ``prefer`` (asyncpg negotiates without verification), and an
    :class:`ssl.SSLContext` for ``require`` / ``verify-ca`` /
    ``verify-full``.
    """
    m = SSLMode(mode) if not isinstance(mode, SSLMode) else mode
    if m == SSLMode.DISABLE:
        return False
    if m == SSLMode.PREFER:
        return "prefer"
    return _ctx_for_mode(m, ca_bundle)


def build_httpx_verify(verify: bool, ca_bundle: str | None) -> Any:
    """Return the value for ``httpx.AsyncClient(verify=...)``.

    * ``verify=False`` → ``False`` (disabled, MITM-vulnerable).
    * ``verify=True`` + ``ca_bundle`` set → bundle path (custom trust).
    * ``verify=True`` + no bundle → ``True`` (system trust store).
    """
    if not verify:
        return False
    if ca_bundle:
        return ca_bundle
    return True


def build_redis_ssl_kwargs(enabled: bool, ca_bundle: str | None) -> dict[str, Any]:
    """Return kwargs accepted by ``redis.asyncio.ConnectionPool.from_url``.

    Returns an empty dict when TLS is disabled. When enabled, callers
    are expected to pass a ``rediss://`` URL or rely on the kwargs
    returned here being applied at connection time.
    """
    if not enabled:
        return {}
    kwargs: dict[str, Any] = {"ssl": True, "ssl_cert_reqs": "required"}
    if ca_bundle:
        kwargs["ssl_ca_certs"] = ca_bundle
    return kwargs


def rewrite_amqp_url_for_tls(url: str, enabled: bool) -> str:
    """Upgrade an ``amqp://`` URL to ``amqps://`` when ``enabled`` is True.

    Leaves the URL unchanged when TLS is already in use or disabled.
    """
    if not enabled:
        return url
    parsed = URL(url)
    if parsed.scheme == "amqps":
        return url
    if parsed.scheme == "amqp":
        return str(parsed.with_scheme("amqps"))
    return url

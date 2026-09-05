import os
import shutil
import ssl
from pathlib import Path
from typing import Any

import uvicorn

from llm_port_api.settings import settings


def set_multiproc_dir() -> None:
    """
    Sets mutiproc_dir env variable.

    This function cleans up the multiprocess directory
    and recreates it. This actions are required by prometheus-client
    to share metrics between processes.

    After cleanup, it sets two variables.
    Uppercase and lowercase because different
    versions of the prometheus-client library
    depend on different environment variables,
    so I've decided to export all needed variables,
    to avoid undefined behaviour.
    """
    shutil.rmtree(settings.prometheus_dir, ignore_errors=True)
    Path(settings.prometheus_dir).mkdir(parents=True)
    os.environ["prometheus_multiproc_dir"] = str(  # noqa: SIM112
        settings.prometheus_dir.expanduser().absolute(),
    )
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(
        settings.prometheus_dir.expanduser().absolute(),
    )


def _ssl_version_for(name: str) -> int | None:
    """Map a settings string like ``"TLSv1_2"`` to ``ssl.PROTOCOL_*``."""
    mapping = {
        "TLSv1_2": ssl.PROTOCOL_TLSv1_2,
        "TLSv1_3": ssl.PROTOCOL_TLS_SERVER,  # 1.3 is negotiated; use TLS_SERVER
    }
    return mapping.get(name)


def _build_uvicorn_ssl_kwargs() -> dict[str, Any]:
    """Build uvicorn SSL kwargs from edge-TLS settings.

    Returns an empty dict when ``tls_edge_mode == "off"`` so the server
    binds plain HTTP.  ACME mode is reserved for a follow-up PR; for
    now it falls back to ``upload`` semantics when cert/key paths are set.
    """
    mode = (settings.tls_edge_mode or "off").lower()
    if mode == "off":
        return {}
    if not (settings.tls_edge_cert_file and settings.tls_edge_key_file):
        # Misconfiguration: TLS requested but no materials supplied.
        # Fall back to plaintext rather than crash; warn loudly.
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).warning(
            "tls_edge_mode=%s but tls_edge_cert_file/tls_edge_key_file are unset; "
            "starting plaintext.",
            mode,
        )
        return {}

    kwargs: dict[str, Any] = {
        "ssl_keyfile": settings.tls_edge_key_file,
        "ssl_certfile": settings.tls_edge_cert_file,
    }
    if settings.tls_edge_ca_file:
        kwargs["ssl_ca_certs"] = settings.tls_edge_ca_file
    version = _ssl_version_for(settings.tls_edge_min_version)
    if version is not None:
        kwargs["ssl_version"] = version
    if settings.tls_edge_ciphers:
        kwargs["ssl_ciphers"] = settings.tls_edge_ciphers
    return kwargs


def main() -> None:
    """Entrypoint of the application."""
    set_multiproc_dir()
    ssl_kwargs = _build_uvicorn_ssl_kwargs()
    # When TLS is enabled, bind the configured HTTPS port instead of
    # the plaintext one. Operators that want to serve both can run a
    # second process on settings.port without TLS.
    port = settings.tls_edge_https_port if ssl_kwargs else settings.port
    uvicorn.run(
        "llm_port_api.web.application:get_app",
        workers=settings.workers_count,
        host=settings.host,
        port=port,
        reload=settings.reload,
        log_level=settings.log_level.value.lower(),
        factory=True,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()

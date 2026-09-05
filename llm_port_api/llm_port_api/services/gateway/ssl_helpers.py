"""Build LiteLLM SSL kwargs from a routed provider instance.

LiteLLM (since v1.x) accepts two SSL-related per-call kwargs:

* ``ssl_verify`` — ``bool`` (verify on/off) or ``str`` (path to a CA
  bundle file).
* ``ssl_certificate`` — ``str`` (path to a PEM file containing client
  cert + key concatenated) for mTLS.

We support storing PEM material **encrypted** in the DB on
:class:`~llm_port_api.db.models.gateway.LLMProviderInstance` and
materialising it to a process-local temp file the first time it is
needed.  Files are cached by provider instance ``id`` and refreshed
when the encrypted ciphertext changes (cheap content hash).
"""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from llm_port_api.settings import settings

logger = logging.getLogger(__name__)

_CACHE_DIR: Path | None = None
# instance_id -> (purpose, content_hash) -> file path
_FILE_CACHE: dict[tuple[uuid.UUID, str, str], Path] = {}


def _cache_dir() -> Path:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = Path(tempfile.mkdtemp(prefix="llmport-tls-out-"))
        atexit.register(_cleanup)
    return _CACHE_DIR


def _cleanup() -> None:  # pragma: no cover
    if _CACHE_DIR is None or not _CACHE_DIR.exists():
        return
    for path in _CACHE_DIR.glob("*"):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        _CACHE_DIR.rmdir()
    except OSError:
        pass


def _materialise_pem(
    instance_id: uuid.UUID,
    purpose: str,
    pem_content: str,
) -> Path:
    """Write PEM ``pem_content`` to a 0600 file, cached by instance + hash."""
    digest = hashlib.sha256(pem_content.encode("utf-8")).hexdigest()[:16]
    key = (instance_id, purpose, digest)
    cached = _FILE_CACHE.get(key)
    if cached and cached.exists():
        return cached
    path = _cache_dir() / f"{instance_id}-{purpose}-{digest}.pem"
    # Write atomically with restrictive permissions (POSIX only — best
    # effort on Windows where chmod is a no-op for these bits).
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, pem_content.encode("utf-8"))
    finally:
        os.close(fd)
    _FILE_CACHE[key] = path
    return path


def build_ssl_kwargs(
    *,
    instance_id: uuid.UUID,
    ssl_verify_mode: str | None,
    ssl_ca_bundle_pem: str | None,
    ssl_client_cert_pem: str | None,
    ssl_client_key_pem: str | None,
) -> dict[str, Any]:
    """Translate per-provider SSL settings into LiteLLM call kwargs.

    Returns an empty dict when nothing per-provider is configured —
    LiteLLM then inherits the global default set in lifespan.
    """
    mode = (ssl_verify_mode or "default").lower()
    kwargs: dict[str, Any] = {}

    if mode == "insecure":
        if not settings.tls_outbound_allow_insecure:
            logger.warning(
                "Provider %s requested ssl_verify=False but "
                "tls_outbound_allow_insecure=False; ignoring (verifying).",
                instance_id,
            )
        else:
            kwargs["ssl_verify"] = False
            return kwargs

    if mode in ("verify_custom_ca", "verify") and ssl_ca_bundle_pem:
        ca_path = _materialise_pem(instance_id, "ca", ssl_ca_bundle_pem)
        kwargs["ssl_verify"] = str(ca_path)
    elif mode == "verify":
        # Verify with system trust store
        kwargs["ssl_verify"] = True

    if ssl_client_cert_pem:
        # LiteLLM expects a single PEM containing cert + key for mTLS.
        combined = ssl_client_cert_pem
        if ssl_client_key_pem and ssl_client_key_pem not in ssl_client_cert_pem:
            combined = ssl_client_cert_pem.rstrip() + "\n" + ssl_client_key_pem
        cert_path = _materialise_pem(instance_id, "cert", combined)
        kwargs["ssl_certificate"] = str(cert_path)

    return kwargs

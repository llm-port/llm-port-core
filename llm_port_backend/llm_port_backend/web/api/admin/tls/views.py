"""Admin endpoints to upload, validate, activate and remove edge TLS material.

Material lifecycle
------------------
1. **Upload** (``POST /admin/tls/upload``) — operator submits a PEM cert,
   PEM key, and optional chain. The handler validates the pair (key
   matches cert, cert is currently within its validity window) and
   stores the encrypted PEMs in ``system_setting_secret`` keyed
   ``tls.edge.cert_pem`` / ``tls.edge.key_pem`` / ``tls.edge.chain_pem``.
2. **Test** (``POST /admin/tls/test``) — re-runs the same validation
   against the most recently uploaded material without writing to disk.
3. **Activate** (``POST /admin/tls/activate``) — decrypts the secrets
   and writes them to ``settings.tls_edge_material_dir``. The API
   process picks them up at next restart via ``__main__.py``.
4. **Status** (``GET /admin/tls/status``) — reports whether material
   is uploaded and/or activated, plus parsed cert metadata.
5. **Delete** (``DELETE /admin/tls``) — removes the secret rows and the
   on-disk files.

All endpoints require superuser AND active root mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, rsa
from cryptography.x509.oid import ExtensionOID, NameOID
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.users import User
from llm_port_backend.services.system_settings.crypto import SettingsCrypto
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import (
    audit_action,
    get_root_mode_active,
    require_superuser,
)
from llm_port_backend.web.api.admin.tls.schema import (
    TlsActivateResponseDTO,
    TlsCertSummaryDTO,
    TlsStatusDTO,
    TlsValidationDTO,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_KEK_VERSION = "v1"
_SECRET_KEY_CERT = "tls.edge.cert_pem"
_SECRET_KEY_KEY = "tls.edge.key_pem"
_SECRET_KEY_CHAIN = "tls.edge.chain_pem"
_MAX_PEM_SIZE = 64 * 1024  # 64 KiB is plenty for PEM material


# ── crypto helpers ───────────────────────────────────────────────────────────


def _crypto() -> SettingsCrypto:
    return SettingsCrypto(settings.settings_master_key)


def _normalise_pem(raw: bytes, *, label: str) -> str:
    """Decode upload bytes, enforce a size cap and a PEM header check."""
    if len(raw) > _MAX_PEM_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{label} exceeds {_MAX_PEM_SIZE} bytes",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} is not valid UTF-8 PEM",
        ) from exc
    if "-----BEGIN " not in text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} is not PEM-encoded",
        )
    return text


def _parse_cert(pem: str) -> x509.Certificate:
    try:
        return x509.load_pem_x509_certificate(pem.encode("utf-8"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid certificate PEM: {exc}",
        ) from exc


def _parse_key(pem: str):  # type: ignore[no-untyped-def]
    try:
        return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid private key PEM: {exc}",
        ) from exc


def _public_keys_match(cert: x509.Certificate, key) -> bool:  # type: ignore[no-untyped-def]
    cert_pub = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return cert_pub == key_pub


def _summarise(cert: x509.Certificate) -> TlsCertSummaryDTO:
    sans: list[str] = []
    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        sans = [name.value for name in ext.value if hasattr(name, "value")]  # type: ignore[union-attr]
    except x509.ExtensionNotFound:
        sans = []
    fp = cert.fingerprint(hashes.SHA256()).hex()
    return TlsCertSummaryDTO(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        serial_hex=f"{cert.serial_number:x}",
        fingerprint_sha256=fp,
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        sans=sans,
        is_self_signed=cert.subject == cert.issuer,
    )


def _validate_pair(
    cert_pem: str,
    key_pem: str,
    chain_pem: str | None,
) -> TlsValidationDTO:
    """Run the full validation pipeline and collect errors."""
    errors: list[str] = []
    cert = _parse_cert(cert_pem)
    key = _parse_key(key_pem)
    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey, dsa.DSAPrivateKey)):
        errors.append("Unsupported private key type")
    if not _public_keys_match(cert, key):
        errors.append("Private key does not match certificate public key")
    now = datetime.now(timezone.utc)
    if cert.not_valid_before_utc > now:
        errors.append("Certificate is not yet valid (notBefore in the future)")
    if cert.not_valid_after_utc < now:
        errors.append("Certificate is expired (notAfter in the past)")
    if chain_pem:
        # Best-effort parse — chain validation against a trust store is
        # out of scope; we just ensure each block is parseable.
        for idx, block in enumerate(_iter_pem_blocks(chain_pem)):
            try:
                x509.load_pem_x509_certificate(block.encode("utf-8"))
            except ValueError as exc:
                errors.append(f"Chain block #{idx} is not a valid certificate: {exc}")
    return TlsValidationDTO(valid=not errors, errors=errors, cert=_summarise(cert))


def _iter_pem_blocks(pem: str) -> Iterable[str]:
    block: list[str] = []
    capturing = False
    for line in pem.splitlines():
        if line.startswith("-----BEGIN "):
            capturing = True
            block = [line]
        elif line.startswith("-----END "):
            block.append(line)
            yield "\n".join(block) + "\n"
            block = []
            capturing = False
        elif capturing:
            block.append(line)


# ── persistence helpers ──────────────────────────────────────────────────────


async def _store_secret(
    dao: SystemSettingsDAO,
    key: str,
    plaintext: str,
    user_id: uuid.UUID | None,
) -> None:
    crypto = _crypto()
    ciphertext = crypto.encrypt(plaintext)
    nonce = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:16]
    await dao.upsert_secret(
        key=key,
        ciphertext=ciphertext,
        nonce=nonce,
        kek_version=_KEK_VERSION,
        updated_by=user_id,
    )


async def _load_secret(dao: SystemSettingsDAO, key: str) -> str | None:
    row = await dao.get_secret(key)
    if row is None:
        return None
    return _crypto().decrypt(row.ciphertext)


def _activated_paths() -> dict[str, str]:
    base = Path(settings.tls_edge_material_dir)
    return {
        "cert": str(base / settings.tls_edge_cert_basename),
        "key": str(base / settings.tls_edge_key_basename),
        "chain": str(base / settings.tls_edge_chain_basename),
    }


def _write_pem_secure(path: Path, content: str, *, mode: int) -> None:
    """Write *content* to *path* with explicit permission bits (0o600 for keys)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)


# ── routes ───────────────────────────────────────────────────────────────────


@router.get("/status", response_model=TlsStatusDTO)
async def tls_status(
    _user: Annotated[User, Depends(require_superuser)],
    _root: Annotated[bool, Depends(get_root_mode_active)],
    dao: SystemSettingsDAO = Depends(),
) -> TlsStatusDTO:
    """Report the current edge-TLS upload/activation status."""
    cert_pem = await _load_secret(dao, _SECRET_KEY_CERT)
    paths = _activated_paths()
    on_disk_cert = Path(paths["cert"]).is_file() and Path(paths["key"]).is_file()
    summary = None
    if on_disk_cert:
        try:
            disk_pem = Path(paths["cert"]).read_text(encoding="utf-8")
            summary = _summarise(_parse_cert(disk_pem))
        except Exception:  # noqa: BLE001  (status endpoint must never crash)
            logger.exception("Failed to summarise activated cert at %s", paths["cert"])
    elif cert_pem:
        summary = _summarise(_parse_cert(cert_pem))
    return TlsStatusDTO(
        has_uploaded_material=cert_pem is not None,
        has_activated_material=on_disk_cert,
        activated_cert=summary,
        activated_paths=paths if on_disk_cert else None,
    )


@router.post("/upload", response_model=TlsValidationDTO)
async def tls_upload(
    request: Request,
    user: Annotated[User, Depends(require_superuser)],
    _root: Annotated[bool, Depends(get_root_mode_active)],
    cert: Annotated[UploadFile, File(description="PEM-encoded server certificate")],
    key: Annotated[UploadFile, File(description="PEM-encoded private key")],
    chain: Annotated[UploadFile | None, File(description="Optional PEM chain")] = None,
    allow_plaintext: Annotated[bool, Form()] = False,
    audit_dao: AuditDAO = Depends(),
    dao: SystemSettingsDAO = Depends(),
) -> TlsValidationDTO:
    """Upload, validate, and store edge TLS material (encrypted at rest)."""
    if request.url.scheme != "https" and not allow_plaintext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Refusing to upload private key over plaintext HTTP. "
                "Either submit over HTTPS or pass allow_plaintext=true."
            ),
        )
    cert_pem = _normalise_pem(await cert.read(), label="cert")
    key_pem = _normalise_pem(await key.read(), label="key")
    chain_pem = (
        _normalise_pem(await chain.read(), label="chain") if chain is not None else None
    )
    result = _validate_pair(cert_pem, key_pem, chain_pem)
    if not result.valid:
        await audit_action(
            "tls.upload",
            "tls",
            "edge",
            AuditResult.FAILURE,
            user.id,
            "high",
            audit_dao,
            metadata_json=json.dumps({"errors": result.errors}),
        )
        return result
    await _store_secret(dao, _SECRET_KEY_CERT, cert_pem, user.id)
    await _store_secret(dao, _SECRET_KEY_KEY, key_pem, user.id)
    if chain_pem:
        await _store_secret(dao, _SECRET_KEY_CHAIN, chain_pem, user.id)
    await audit_action(
        "tls.upload",
        "tls",
        "edge",
        AuditResult.SUCCESS,
        user.id,
        "high",
        audit_dao,
        metadata_json=json.dumps(
            {"fingerprint": result.cert.fingerprint_sha256 if result.cert else None}
        ),
    )
    return result


@router.post("/test", response_model=TlsValidationDTO)
async def tls_test(
    _user: Annotated[User, Depends(require_superuser)],
    _root: Annotated[bool, Depends(get_root_mode_active)],
    dao: SystemSettingsDAO = Depends(),
) -> TlsValidationDTO:
    """Re-validate the most recently uploaded material without activating."""
    cert_pem = await _load_secret(dao, _SECRET_KEY_CERT)
    key_pem = await _load_secret(dao, _SECRET_KEY_KEY)
    if cert_pem is None or key_pem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No uploaded TLS material to test",
        )
    chain_pem = await _load_secret(dao, _SECRET_KEY_CHAIN)
    return _validate_pair(cert_pem, key_pem, chain_pem)


@router.post("/activate", response_model=TlsActivateResponseDTO)
async def tls_activate(
    user: Annotated[User, Depends(require_superuser)],
    _root: Annotated[bool, Depends(get_root_mode_active)],
    audit_dao: AuditDAO = Depends(),
    dao: SystemSettingsDAO = Depends(),
) -> TlsActivateResponseDTO:
    """Materialise the uploaded PEMs to disk for the API process to load."""
    cert_pem = await _load_secret(dao, _SECRET_KEY_CERT)
    key_pem = await _load_secret(dao, _SECRET_KEY_KEY)
    if cert_pem is None or key_pem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No uploaded TLS material to activate",
        )
    chain_pem = await _load_secret(dao, _SECRET_KEY_CHAIN)
    # Re-validate before activation; a previously valid cert may now be expired.
    validation = _validate_pair(cert_pem, key_pem, chain_pem)
    if not validation.valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Cannot activate invalid material", "errors": validation.errors},
        )
    paths = _activated_paths()
    _write_pem_secure(Path(paths["cert"]), cert_pem, mode=0o644)
    _write_pem_secure(Path(paths["key"]), key_pem, mode=0o600)
    if chain_pem:
        _write_pem_secure(Path(paths["chain"]), chain_pem, mode=0o644)
    await audit_action(
        "tls.activate",
        "tls",
        "edge",
        AuditResult.SUCCESS,
        user.id,
        "high",
        audit_dao,
        metadata_json=json.dumps({"paths": paths}),
    )
    return TlsActivateResponseDTO(activated=True, paths=paths, restart_required=True)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def tls_delete(
    user: Annotated[User, Depends(require_superuser)],
    _root: Annotated[bool, Depends(get_root_mode_active)],
    audit_dao: AuditDAO = Depends(),
    dao: SystemSettingsDAO = Depends(),
) -> None:
    """Remove uploaded secrets and on-disk activated material."""
    # Best-effort delete on each scope.
    for key in (_SECRET_KEY_CERT, _SECRET_KEY_KEY, _SECRET_KEY_CHAIN):
        row = await dao.get_secret(key)
        if row is not None:
            await dao.session.delete(row)
    paths = _activated_paths()
    for path in paths.values():
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to remove activated TLS file %s", path)
    await audit_action(
        "tls.delete",
        "tls",
        "edge",
        AuditResult.SUCCESS,
        user.id,
        "high",
        audit_dao,
        metadata_json=None,
    )

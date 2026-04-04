"""Backup and restore helpers for llm.port databases and volumes.

Shared by ``llmport backup``, ``llmport restore``, and
``llmport upgrade``.  All database operations run via
``docker exec`` against the running ``llm-port-postgres`` container —
no direct network connection is needed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from llmport import __version__
from llmport.core.compose import ComposeContext, up as compose_up
from llmport.core.console import console, error, info, success, warning
from llmport.core.registry import DATABASES, POSTGRES_CONTAINER

# Named Docker volumes that can be included in a backup.
BACKUP_VOLUMES: list[str] = [
    "pg_data",
    "minio_data",
    "ch_data",
    "ch_logs",
    "loki_data",
    "grafana_data",
    "llm_port_models",
]

# Alembic migrator containers (service name → database).
_MIGRATOR_SERVICES: dict[str, str] = {
    "llm-port-backend-migrator": "llm_port_backend",
    "llm-port-api-migrator": "llm_api",
    "llm-port-mcp-migrator": "llm_mcp",
    "llm-port-skills-migrator": "llm_skills",
    "llm-port-pii-migrator": "pii",
}


# ── Result types ──────────────────────────────────────────────────


@dataclass
class BackupResult:
    """Outcome of a backup operation."""

    backup_dir: Path
    databases: dict[str, str] = field(default_factory=dict)  # db → dump file
    env_snapshot: str = ""
    volumes: list[str] = field(default_factory=list)
    manifest_path: Path | None = None
    ok: bool = True
    errors: list[str] = field(default_factory=list)


@dataclass
class RestoreResult:
    """Outcome of a restore operation."""

    databases_restored: list[str] = field(default_factory=list)
    env_restored: bool = False
    volumes_restored: list[str] = field(default_factory=list)
    ok: bool = True
    errors: list[str] = field(default_factory=list)


# ── Checksums ─────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Database dump / restore ───────────────────────────────────────


def _docker_bin() -> str:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("docker not found on PATH")
    return docker


def dump_databases(
    backup_dir: Path,
    *,
    databases: list[str] | None = None,
) -> dict[str, str]:
    """Dump each database with ``pg_dump -Fc`` via docker exec.

    Returns a mapping of *database name* → *dump filename* for
    databases that were successfully dumped.
    """
    docker = _docker_bin()
    targets = databases or list(DATABASES)
    results: dict[str, str] = {}

    for db in targets:
        dump_file = f"{db}.dump"
        dump_path = backup_dir / dump_file

        info(f"  Dumping database: {db}")
        proc = subprocess.run(  # noqa: S603
            [
                docker, "exec", POSTGRES_CONTAINER,
                "pg_dump", "-U", "postgres", "-Fc", db,
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            warning(f"  pg_dump failed for {db}: {stderr}")
            continue

        dump_path.write_bytes(proc.stdout)
        results[db] = dump_file

    return results


def restore_databases(
    backup_dir: Path,
    manifest: dict,
) -> list[str]:
    """Restore databases from custom-format dumps.

    Returns the list of database names that were successfully restored.
    """
    docker = _docker_bin()
    restored: list[str] = []
    db_section = manifest.get("databases", {})

    for db, meta in db_section.items():
        dump_file = meta.get("file", f"{db}.dump")
        dump_path = backup_dir / dump_file
        if not dump_path.exists():
            warning(f"  Dump file missing for {db}: {dump_file}")
            continue

        info(f"  Restoring database: {db}")

        # Read the dump file and pipe it into pg_restore via stdin.
        dump_bytes = dump_path.read_bytes()
        proc = subprocess.run(  # noqa: S603
            [
                docker, "exec", "-i", POSTGRES_CONTAINER,
                "pg_restore", "-U", "postgres",
                "--clean", "--if-exists", "-d", db,
            ],
            input=dump_bytes,
            capture_output=True,
            check=False,
        )
        # pg_restore may return non-zero for harmless warnings
        # (e.g. "role does not exist" during --clean).
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and "ERROR" in stderr.upper():
            warning(f"  pg_restore warnings for {db}: {stderr[:200]}")
        restored.append(db)

    return restored


# ── Migration heads ───────────────────────────────────────────────


def get_migration_heads(ctx: ComposeContext) -> dict[str, str]:
    """Collect current Alembic revision heads from migrator containers.

    Runs ``alembic current`` inside each migrator service.  Returns a
    mapping of *database name* → *revision hash* (or empty string if
    the head could not be determined).
    """
    docker = _docker_bin()
    heads: dict[str, str] = {}

    for service, db in _MIGRATOR_SERVICES.items():
        proc = subprocess.run(  # noqa: S603
            [
                docker, "compose",
                *_compose_flags(ctx),
                "run", "--rm", "--no-deps", service,
                "alembic", "current",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        head = ""
        if proc.returncode == 0:
            # Output looks like: "a1b2c3d4e5f6 (head)"
            for line in proc.stdout.strip().splitlines():
                if "(head)" in line:
                    head = line.split()[0]
                    break
        heads[db] = head

    return heads


def _compose_flags(ctx: ComposeContext) -> list[str]:
    """Build raw compose flags from a ComposeContext (without 'docker compose')."""
    flags: list[str] = []
    for f in ctx.compose_files:
        flags.extend(["-f", str(f)])
    if ctx.env_file and Path(str(ctx.env_file)).exists():
        flags.extend(["--env-file", str(ctx.env_file)])
    if ctx.project_dir:
        flags.extend(["--project-directory", str(ctx.project_dir)])
    for p in ctx.profiles:
        flags.extend(["--profile", p])
    return flags


# ── .env snapshot ─────────────────────────────────────────────────


def snapshot_env(env_path: Path, backup_dir: Path) -> str:
    """Copy the .env file into the backup directory.

    Returns the filename inside backup_dir, or empty string on failure.
    """
    if not env_path.exists():
        warning("  No .env file found — skipping env snapshot.")
        return ""
    dest = backup_dir / ".env.bak"
    shutil.copy2(env_path, dest)
    return ".env.bak"


def restore_env(backup_dir: Path, env_path: Path) -> bool:
    """Restore the .env snapshot from a backup."""
    src = backup_dir / ".env.bak"
    if not src.exists():
        warning("  No .env snapshot in backup.")
        return False
    shutil.copy2(src, env_path)
    return True


# ── Volume snapshots ──────────────────────────────────────────────


def snapshot_volumes(backup_dir: Path, *, volumes: list[str] | None = None) -> list[str]:
    """Tar each named Docker volume into the backup directory.

    Uses a temporary ``alpine`` container to read from the volume mount.
    Returns list of created archive filenames.
    """
    docker = _docker_bin()
    targets = volumes or list(BACKUP_VOLUMES)
    created: list[str] = []

    for vol in targets:
        archive = f"{vol}.tar.gz"
        archive_path = backup_dir / archive
        info(f"  Snapshotting volume: {vol}")
        proc = subprocess.run(  # noqa: S603
            [
                docker, "run", "--rm",
                "-v", f"{vol}:/data:ro",
                "-v", f"{backup_dir}:/backup",
                "alpine",
                "tar", "czf", f"/backup/{archive}", "-C", "/data", ".",
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            warning(f"  Volume snapshot failed for {vol}: {stderr[:200]}")
            continue
        created.append(archive)

    return created


def restore_volumes(backup_dir: Path, manifest: dict) -> list[str]:
    """Restore volume archives from a backup.

    Returns list of volume names that were restored.
    """
    docker = _docker_bin()
    archives = manifest.get("volumes", [])
    restored: list[str] = []

    for archive in archives:
        archive_path = backup_dir / archive
        if not archive_path.exists():
            warning(f"  Volume archive missing: {archive}")
            continue
        # Derive volume name from archive filename (e.g. "pg_data.tar.gz" → "pg_data")
        vol = archive.replace(".tar.gz", "")
        info(f"  Restoring volume: {vol}")
        proc = subprocess.run(  # noqa: S603
            [
                docker, "run", "--rm",
                "-v", f"{vol}:/data",
                "-v", f"{backup_dir}:/backup:ro",
                "alpine",
                "sh", "-c", f"rm -rf /data/* && tar xzf /backup/{archive} -C /data",
            ],
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            warning(f"  Volume restore failed for {vol}: {stderr[:200]}")
            continue
        restored.append(vol)

    return restored


# ── Manifest ──────────────────────────────────────────────────────


def write_manifest(
    backup_dir: Path,
    *,
    databases: dict[str, str],
    env_snapshot: str,
    volumes: list[str],
    migration_heads: dict[str, str],
) -> Path:
    """Write ``manifest.json`` with file checksums and metadata."""
    db_entries: dict[str, dict] = {}
    for db, dump_file in databases.items():
        dump_path = backup_dir / dump_file
        db_entries[db] = {
            "file": dump_file,
            "sha256": _sha256(dump_path) if dump_path.exists() else "",
            "migration_head": migration_heads.get(db, ""),
        }

    manifest = {
        "version": 1,
        "created_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "llmport_version": __version__,
        "databases": db_entries,
        "env_snapshot": env_snapshot,
        "volumes": volumes,
    }

    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def read_manifest(backup_dir: Path) -> dict | None:
    """Read and parse ``manifest.json`` from a backup directory."""
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify_backup(backup_dir: Path, manifest: dict) -> list[str]:
    """Verify database dump checksums against the manifest.

    Returns a list of error messages (empty if all OK).
    """
    errors: list[str] = []
    for db, meta in manifest.get("databases", {}).items():
        dump_file = meta.get("file", "")
        expected = meta.get("sha256", "")
        if not dump_file or not expected:
            continue
        dump_path = backup_dir / dump_file
        if not dump_path.exists():
            errors.append(f"Missing dump file: {dump_file}")
            continue
        actual = _sha256(dump_path)
        if actual != expected:
            errors.append(f"Checksum mismatch for {dump_file}: expected {expected[:12]}… got {actual[:12]}…")
    return errors


# ── Retention ─────────────────────────────────────────────────────


def rotate_backups(backup_root: Path, *, retain: int = 5) -> int:
    """Remove oldest backup directories beyond the retention count.

    Assumes backup directories are named with ISO timestamps so
    lexicographic sort == chronological sort.  Returns the number
    of directories removed.
    """
    if retain <= 0:
        return 0

    dirs = sorted(
        [d for d in backup_root.iterdir() if d.is_dir() and (d / "manifest.json").exists()],
        key=lambda d: d.name,
    )
    to_remove = dirs[:-retain] if len(dirs) > retain else []
    for d in to_remove:
        shutil.rmtree(d)
    return len(to_remove)


# ── Orchestrators ─────────────────────────────────────────────────


def create_backup(
    ctx: ComposeContext,
    *,
    output_dir: Path,
    include_volumes: bool = False,
    db_only: bool = False,
    retain: int = 5,
) -> BackupResult:
    """Full backup orchestration.

    1. Ensure postgres is running
    2. Create timestamped directory
    3. Dump databases
    4. Snapshot .env
    5. Optionally snapshot volumes
    6. Collect migration heads
    7. Write manifest
    8. Rotate old backups
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = output_dir / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    result = BackupResult(backup_dir=backup_dir)

    # 1. Ensure postgres is running
    info("Ensuring PostgreSQL is running…")
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    # 2. Dump databases
    console.print("\n[bold cyan]Dumping databases…[/bold cyan]")
    result.databases = dump_databases(backup_dir)
    if not result.databases:
        result.ok = False
        result.errors.append("No databases were dumped successfully.")
        return result

    # 3. Snapshot .env
    if not db_only and ctx.env_file:
        console.print("\n[bold cyan]Snapshotting .env…[/bold cyan]")
        result.env_snapshot = snapshot_env(Path(str(ctx.env_file)), backup_dir)

    # 4. Snapshot volumes
    if include_volumes and not db_only:
        console.print("\n[bold cyan]Snapshotting Docker volumes…[/bold cyan]")
        result.volumes = snapshot_volumes(backup_dir)

    # 5. Collect migration heads
    console.print("\n[bold cyan]Collecting migration heads…[/bold cyan]")
    heads = get_migration_heads(ctx)

    # 6. Write manifest
    console.print("\n[bold cyan]Writing manifest…[/bold cyan]")
    result.manifest_path = write_manifest(
        backup_dir,
        databases=result.databases,
        env_snapshot=result.env_snapshot,
        volumes=result.volumes,
        migration_heads=heads,
    )

    # 7. Rotate
    removed = rotate_backups(output_dir, retain=retain)
    if removed:
        info(f"Rotated {removed} old backup(s).")

    return result


def restore_backup(
    ctx: ComposeContext,
    *,
    backup_dir: Path,
    db_only: bool = False,
    skip_env: bool = False,
) -> RestoreResult:
    """Full restore orchestration.

    1. Read and verify manifest
    2. Ensure postgres is running
    3. Restore databases
    4. Restore .env
    5. Restore volumes
    """
    result = RestoreResult()

    manifest = read_manifest(backup_dir)
    if manifest is None:
        result.ok = False
        result.errors.append("No manifest.json found in backup directory.")
        return result

    # Verify checksums
    check_errors = verify_backup(backup_dir, manifest)
    if check_errors:
        result.ok = False
        result.errors.extend(check_errors)
        return result

    # 1. Ensure postgres is running
    info("Ensuring PostgreSQL is running…")
    compose_up(ctx, services=["postgres"], detach=True, wait=True, timeout=60)

    # 2. Restore databases
    console.print("\n[bold cyan]Restoring databases…[/bold cyan]")
    result.databases_restored = restore_databases(backup_dir, manifest)

    # 3. Restore .env
    if not db_only and not skip_env and manifest.get("env_snapshot") and ctx.env_file:
        console.print("\n[bold cyan]Restoring .env…[/bold cyan]")
        result.env_restored = restore_env(backup_dir, Path(str(ctx.env_file)))

    # 4. Restore volumes
    if not db_only and manifest.get("volumes"):
        console.print("\n[bold cyan]Restoring Docker volumes…[/bold cyan]")
        result.volumes_restored = restore_volumes(backup_dir, manifest)

    return result

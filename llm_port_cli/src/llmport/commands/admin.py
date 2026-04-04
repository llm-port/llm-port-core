"""``llmport admin`` — administrative commands for a running deployment."""

from __future__ import annotations

import re
import secrets
import shutil
import string
import subprocess
import sys

import click

from llmport.core.console import console, error, info, success, warning


def _generate_password(length: int = 24) -> str:
    """Generate a random password (letters + digits + punctuation subset)."""
    alphabet = string.ascii_letters + string.digits + "!@#$%&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# Minimal email validation — rejects obvious injection attempts.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-.]+$")


@click.group("admin")
def admin_group() -> None:
    """Administrative commands for a running llm.port deployment."""


@admin_group.command("reset-password")
@click.option(
    "--email",
    default="admin@localhost",
    show_default=True,
    help="Email address of the user whose password will be reset.",
)
@click.option(
    "--password",
    default=None,
    help="New password.  Auto-generated if omitted.",
)
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def reset_password_cmd(*, email: str, password: str | None, yes: bool) -> None:
    """Reset a user's password directly via the database.

    This bypasses the API and sets the password hash in PostgreSQL.
    The backend container must be running (needed for password hashing).
    """
    # Validate email early to prevent any injection
    if not _EMAIL_RE.match(email):
        error(f"Invalid email address: {email}")
        sys.exit(1)

    docker = shutil.which("docker")
    if not docker:
        error("docker not found on PATH")
        sys.exit(1)

    new_password = password or _generate_password()

    if not yes:
        click.confirm(
            f"Reset password for '{email}'?",
            abort=True,
        )

    # ── 1. Hash the password inside the backend container ─────
    info("Hashing new password…")
    hash_script = (
        "from fastapi_users.password import PasswordHelper; "
        "print(PasswordHelper().hash(input()))"
    )

    hash_result = subprocess.run(  # noqa: S603
        [
            docker, "exec", "-i", "llm-port-backend",
            "python", "-c", hash_script,
        ],
        input=new_password,
        capture_output=True,
        text=True,
        check=False,
    )
    if hash_result.returncode != 0:
        error("Failed to hash password — is the backend container running?")
        if hash_result.stderr.strip():
            error(f"  {hash_result.stderr.strip()}")
        sys.exit(1)

    hashed = hash_result.stdout.strip()
    if not hashed:
        error("Password hashing returned empty result.")
        sys.exit(1)

    # ── 2. Update the password in PostgreSQL ──────────────────
    info("Updating password in database…")

    # Both `hashed` (bcrypt output we generated) and `email` (validated
    # above by regex) are safe for SQL string interpolation here.
    sql = (
        f"UPDATE \"user\" SET hashed_password = '{hashed}' "
        f"WHERE email = '{email}';"
    )

    pg_result = subprocess.run(  # noqa: S603
        [
            docker, "exec", "llm-port-postgres",
            "psql", "-U", "postgres", "-d", "llm_port_backend",
            "-c", sql,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if pg_result.returncode != 0:
        error("Database update failed — is the postgres container running?")
        if pg_result.stderr.strip():
            error(f"  {pg_result.stderr.strip()}")
        sys.exit(1)

    # Check if a row was actually updated
    if "UPDATE 0" in pg_result.stdout:
        warning(f"No user found with email '{email}'.")
        sys.exit(1)

    success(f"Password reset for '{email}'.")

    # ── 3. Display credentials ────────────────────────────────
    from rich.panel import Panel
    from rich.text import Text

    lines = Text()
    lines.append("  Email:     ", style="bold")
    lines.append(email, style="cyan")
    lines.append("\n  Password:  ", style="bold")
    lines.append(new_password, style="cyan")
    lines.append("\n\n  Store this in a password manager.", style="dim")

    console.print()
    console.print(
        Panel(
            lines,
            title="[bold yellow]New Credentials[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )

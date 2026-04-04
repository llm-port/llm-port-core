"""First-admin bootstrap — calls ``POST /api/bootstrap``.

Used by ``llmport deploy`` and ``llmport dev init`` to create the
initial admin user after services are up.  The raw credentials are
displayed once and optionally saved to a local file.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import httpx

from llmport.core.console import console, error, success, warning


def wait_for_backend(base_url: str, *, timeout: int = 120) -> bool:
    """Poll the backend health endpoint until it responds 200."""
    deadline = time.monotonic() + timeout
    health_url = f"{base_url}/api/health"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(health_url, timeout=5)
            if resp.status_code == 200:  # noqa: PLR2004
                return True
        except httpx.HTTPError:
            pass
        time.sleep(2)
    return False


def check_needs_bootstrap(base_url: str) -> bool | None:
    """Return True if the system needs bootstrap, False if already done, None on error."""
    try:
        resp = httpx.get(f"{base_url}/api/bootstrap/status", timeout=10)
        if resp.status_code == 200:  # noqa: PLR2004
            return resp.json().get("needs_bootstrap", False)
    except httpx.HTTPError:
        pass
    return None


def run_bootstrap(
    base_url: str,
    *,
    email: str = "admin@localhost",
    password: str | None = None,
    generate_api_token: bool = True,
    tenant_id: str = "default",
) -> dict | None:
    """Call the bootstrap endpoint and return the credentials dict, or None on failure."""
    payload: dict = {
        "email": email,
        "generate_api_token": generate_api_token,
        "tenant_id": tenant_id,
    }
    if password:
        payload["password"] = password

    try:
        resp = httpx.post(
            f"{base_url}/api/bootstrap",
            json=payload,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        error(f"Could not reach backend: {exc}")
        return None

    if resp.status_code == 201:  # noqa: PLR2004
        return resp.json()

    if resp.status_code == 409:  # noqa: PLR2004
        warning("System is already bootstrapped — admin users exist.")
        return None

    error(f"Bootstrap failed (HTTP {resp.status_code}): {resp.text}")
    return None


def display_credentials(creds: dict) -> None:
    """Print credentials in a prominent panel with a save warning."""
    from rich.panel import Panel  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415

    lines = Text()
    lines.append("  Admin email:     ", style="bold")
    lines.append(creds["email"], style="cyan")
    lines.append("\n  Admin password:  ", style="bold")
    lines.append(creds["password"], style="cyan")
    if creds.get("api_token"):
        lines.append("\n  API token:       ", style="bold")
        # Show first 40 chars + ellipsis for readability
        token = creds["api_token"]
        display_token = token[:40] + "…" if len(token) > 40 else token
        lines.append(display_token, style="cyan")
    lines.append("\n")
    lines.append(
        "\n  Store these in a password manager or secure vault.\n"
        "  The password is hashed and the token is stateless —\n"
        "  neither can be retrieved from the system after this point.",
        style="dim",
    )

    console.print()
    console.print(
        Panel(
            lines,
            title="[bold yellow]⚠  SAVE THESE CREDENTIALS — THEY CANNOT BE RECOVERED  ⚠[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )
    )


def save_credentials_file(creds: dict, directory: Path) -> Path | None:
    """Write credentials to a restricted file (chmod 600). Returns the path."""
    creds_path = directory / ".bootstrap-credentials"
    content_lines = [
        f"# llm.port bootstrap credentials — generated {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"ADMIN_EMAIL={creds['email']}",
        f"ADMIN_PASSWORD={creds['password']}",
    ]
    if creds.get("api_token"):
        content_lines.append(f"API_TOKEN={creds['api_token']}")
    content_lines.append(
        "\n# DELETE THIS FILE after storing the credentials in a secure vault."
    )

    try:
        creds_path.write_text("\n".join(content_lines) + "\n", encoding="utf-8")
        # Restrict read/write to owner only
        if os.name != "nt":
            creds_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return creds_path
    except OSError as exc:
        error(f"Could not save credentials file: {exc}")
        return None


def bootstrap_interactive(
    base_url: str,
    install_dir: Path,
    *,
    auto_confirm: bool = False,
    default_email: str = "admin@localhost",
    default_password: str | None = None,
) -> dict | None:
    """Full interactive bootstrap flow (prompt → call API → display → save).

    If *auto_confirm* is True, uses defaults and auto-saves without prompting.
    Returns the credentials dict if successful, None otherwise.
    """
    import click  # noqa: PLC0415

    # ── Check if bootstrap is needed ──────────────────────────
    needs = check_needs_bootstrap(base_url)
    if needs is None:
        warning("Could not determine bootstrap status — skipping admin setup.")
        return None
    if not needs:
        console.print("[dim]  Admin user already exists — skipping bootstrap.[/dim]")
        return None

    # ── Gather inputs ─────────────────────────────────────────
    if auto_confirm:
        email = default_email
        password = default_password
    else:
        email = click.prompt("  Admin email", default=default_email)
        password_input = click.prompt(
            "  Admin password (leave blank to auto-generate)",
            default="",
            hide_input=True,
            show_default=False,
        )
        password = password_input or None

    # ── Call bootstrap API ────────────────────────────────────
    creds = run_bootstrap(
        base_url,
        email=email,
        password=password,
        generate_api_token=True,
    )
    if not creds:
        return None

    success("Admin user created.")

    # ── Display credentials ───────────────────────────────────
    display_credentials(creds)

    # ── Save to file ──────────────────────────────────────────
    if auto_confirm:
        save_file = True
    else:
        save_file = click.confirm("\n  Save to file?", default=False)

    if save_file:
        saved = save_credentials_file(creds, install_dir)
        if saved:
            success(f"Saved to {saved}")
            if os.name != "nt":
                console.print(f"  [dim](permissions: 600 — owner read/write only)[/dim]")
            warning("Delete this file after storing the credentials elsewhere.")

    return creds

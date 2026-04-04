"""``llmport observe`` — observability commands for cost tracking."""

from __future__ import annotations

import sys

import click
import httpx

from llmport.core.api_client import ApiClient
from llmport.core.console import console, error, info, success
from llmport.core.settings import load_config


def _ensure_token(cfg) -> str | None:
    """Return the configured API token, or prompt for login credentials."""
    if cfg.api_token:
        return cfg.api_token
    # No token stored — prompt the user for credentials.
    email = click.prompt("  Admin email", default=cfg.admin_email or "admin@localhost")
    password = click.prompt(f"  Password for {email}", hide_input=True, show_default=False)
    try:
        resp = httpx.post(
            f"{cfg.api_url.rstrip('/')}/api/auth/jwt/login",
            data={"username": email, "password": password},
            timeout=15,
        )
        if resp.status_code == 200:  # noqa: PLR2004
            token = resp.json().get("access_token", "")
            if token:
                return token
        error(f"Login failed (HTTP {resp.status_code})")
        return None
    except httpx.HTTPError as exc:
        error(f"Could not reach backend: {exc}")
        return None


@click.group("observe")
def observe_group() -> None:
    """Observability commands — cost tracking and analytics."""


@observe_group.command("force-calculate")
@click.option(
    "-y", "--yes",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def force_calculate_cmd(*, yes: bool) -> None:
    """Force-recalculate costs for all historical LLM requests.

    Re-applies the current price catalog to every row in the request
    log.  Rows with a matching model in the price catalog get updated
    cost estimates; rows without a match are marked as unavailable.
    """
    cfg = load_config()

    if not yes:
        click.confirm(
            "This will recalculate cost estimates for ALL historical requests "
            "using the current price catalog. Continue?",
            abort=True,
        )

    info("Authenticating…")
    token = _ensure_token(cfg)
    if not token:
        error("Authentication failed. Cannot proceed.")
        sys.exit(1)

    # Temporarily set the token for this session.
    cfg.api_token = token

    with ApiClient(cfg) as client:
        if not client.healthy():
            error("Backend is not reachable. Is llm.port running?")
            sys.exit(1)

        info("Recalculating costs — this may take a moment…")

        with console.status("[info]Recalculating…[/info]"):
            resp = client.post("/api/admin/observability/recalculate-costs")

        if resp.status_code != 200:  # noqa: PLR2004
            error(f"Recalculation failed (HTTP {resp.status_code}): {resp.text}")
            sys.exit(1)

        result = resp.json()
        recalculated = result.get("recalculated", 0)
        unavailable = result.get("unavailable", 0)

        success("Cost recalculation complete.")
        console.print(f"  [green]{recalculated}[/green] requests updated with current pricing")
        console.print(f"  [yellow]{unavailable}[/yellow] requests have no matching price catalog entry")

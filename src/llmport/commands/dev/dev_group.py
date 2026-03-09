"""``llmport dev`` — developer workflow commands."""

from __future__ import annotations

import click


@click.group("dev")
def dev_group() -> None:
    """Developer workflow — clone, set up, and run the dev environment."""


def _register_dev_commands() -> None:
    """Import and register dev subcommands."""
    # Importing these modules triggers the @dev_group.command() decorators
    from llmport.commands.dev import dev_doctor as _doctor  # noqa: F401
    from llmport.commands.dev import dev_init as _init  # noqa: F401
    from llmport.commands.dev import dev_up as _up  # noqa: F401
    from llmport.commands.dev import dev_status as _status  # noqa: F401


_register_dev_commands()

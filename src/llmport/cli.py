"""CLI entry point — Click root group and subcommand registration.

This module defines the ``llmport`` command group and wires up all
subcommands.  Run ``llmport --help`` for the full command tree.
"""

from __future__ import annotations

import click

from llmport import __version__


class AliasedGroup(click.Group):
    """Click group that supports command abbreviations.

    For instance ``llmport st`` resolves to ``llmport status`` if no
    other command starts with ``st``.
    """

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        matches = [x for x in self.list_commands(ctx) if x.startswith(cmd_name)]
        if not matches:
            return None
        if len(matches) == 1:
            return click.Group.get_command(self, ctx, matches[0])
        ctx.fail(f"Ambiguous command '{cmd_name}'. Did you mean: {', '.join(sorted(matches))}?")
        return None  # unreachable, but satisfies type checker


@click.group(cls=AliasedGroup, invoke_without_command=True)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
@click.option("-q", "--quiet", is_flag=True, help="Suppress informational output.")
@click.version_option(version=__version__, prog_name="llmport")
@click.pass_context
def cli(ctx: click.Context, *, verbose: bool, quiet: bool) -> None:
    """llmport — CLI installer and management tool for llm.port."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── Register subcommands ──────────────────────────────────────────

def register_core_commands(group: click.Group | None = None) -> None:
    """Import and register all core subcommand groups and commands.

    Accepts an optional *group* parameter so the EE CLI can call this
    with its own root group.  Defaults to ``cli`` when called internally.
    """
    target = group or cli

    from llmport.commands.version import version_cmd
    from llmport.commands.doctor import doctor_cmd
    from llmport.commands.status import status_cmd
    from llmport.commands.up import up_cmd
    from llmport.commands.down import down_cmd
    from llmport.commands.logs_cmd import logs_cmd
    from llmport.commands.config import config_group
    from llmport.commands.module import module_group
    from llmport.commands.tune import tune_cmd
    from llmport.commands.deploy import deploy_cmd
    from llmport.commands.dev.dev_group import dev_group

    target.add_command(version_cmd, "version")
    target.add_command(doctor_cmd, "doctor")
    target.add_command(status_cmd, "status")
    target.add_command(up_cmd, "up")
    target.add_command(down_cmd, "down")
    target.add_command(logs_cmd, "logs")
    target.add_command(config_group, "config")
    target.add_command(module_group, "module")
    target.add_command(tune_cmd, "tune")
    target.add_command(deploy_cmd, "deploy")
    target.add_command(dev_group, "dev")


register_core_commands()


def main() -> None:
    """Package entry point."""
    cli()


if __name__ == "__main__":
    main()

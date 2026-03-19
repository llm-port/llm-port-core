"""CLI entrypoint for llm_port_node_agent."""

from __future__ import annotations

import asyncio
import logging

from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.service import NodeAgentService


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def _run() -> None:
    config = AgentConfig.from_env()
    _configure_logging(config.log_level)
    service = NodeAgentService(config)
    try:
        await service.run_forever()
    finally:
        await service.close()


def main() -> None:
    """Process entrypoint."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()

import os
import shutil
import sys
from pathlib import Path

import uvicorn

from llm_port_backend.settings import settings


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
    Path(settings.prometheus_dir).mkdir(parents=True, exist_ok=True)
    os.environ["prometheus_multiproc_dir"] = str(  # noqa: SIM112
        settings.prometheus_dir.expanduser().absolute(),
    )
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(
        settings.prometheus_dir.expanduser().absolute(),
    )


def main() -> None:
    """Entrypoint of the application."""
    # On Windows, aiodocker needs the proactor event loop for named-pipe access
    # to the Docker Engine.
    if sys.platform == "win32":
        import asyncio  # noqa: PLC0415

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    set_multiproc_dir()
    uvicorn.run(
        "llm_port_backend.web.application:get_app",
        workers=settings.workers_count,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.value.lower(),
        factory=True,
    )


if __name__ == "__main__":
    main()

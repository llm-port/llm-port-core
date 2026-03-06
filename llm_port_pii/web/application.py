import logging
from importlib import metadata
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI
from fastapi.responses import UJSONResponse
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from llm_port_pii.log import configure_logging
from llm_port_pii.settings import settings
from llm_port_pii.web.api.router import api_router
from llm_port_pii.web.lifespan import lifespan_setup

APP_ROOT = Path(__file__).parent.parent

# ── Optional EE plugin ────────────────────────────────────────────
try:
    from llm_port_ee.plugins.pii import pii_plugin  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    pii_plugin = None
# ──────────────────────────────────────────────────────────────────


def get_app() -> FastAPI:
    """
    Get FastAPI application.

    This is the main constructor of an application.

    :return: application.
    """
    configure_logging()
    if settings.sentry_dsn:
        # Enables sentry integration.
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            traces_sample_rate=settings.sentry_sample_rate,
            environment=settings.environment,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                LoggingIntegration(
                    level=logging.getLevelName(
                        settings.log_level.value,
                    ),
                    event_level=logging.ERROR,
                ),
                SqlalchemyIntegration(),
            ],
        )
    app = FastAPI(
        title="llm_port_pii",
        version=metadata.version("llm_port_pii"),
        lifespan=lifespan_setup,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        default_response_class=UJSONResponse,
    )

    # EE plugin routes are included FIRST so they shadow Core 402-gated
    # endpoints with enterprise implementations.
    if pii_plugin is not None:
        app.include_router(router=pii_plugin.router())

    # Main router for the API.
    app.include_router(router=api_router, prefix="/api")
    # Adds static directory.
    # This directory is used to access swagger files.
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")

    return app

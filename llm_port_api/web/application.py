import logging
from importlib import metadata
from pathlib import Path

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import Response, UJSONResponse
from fastapi.staticfiles import StaticFiles
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from llm_port_api.log import configure_logging
from llm_port_api.services.gateway.errors import GatewayError, error_response
from llm_port_api.settings import settings
from llm_port_api.web.api.router import api_router, public_router
from llm_port_api.web.lifespan import lifespan_setup

APP_ROOT = Path(__file__).parent.parent


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
            ],
        )
    app = FastAPI(
        title="llm_port_api",
        version=metadata.version("llm_port_api"),
        lifespan=lifespan_setup,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        default_response_class=UJSONResponse,
    )

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> Response:
        del request
        return error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_type=exc.error_type,
            param=exc.param,
            code=exc.code,
        )

    # Main router for the API.
    app.include_router(router=api_router, prefix="/api")
    # Public OpenAI-compatible gateway routes.
    app.include_router(router=public_router)
    # Adds static directory.
    # This directory is used to access swagger files.
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")

    return app

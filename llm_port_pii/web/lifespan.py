import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk.resources import (
    DEPLOYMENT_ENVIRONMENT,
    SERVICE_NAME,
    TELEMETRY_SDK_LANGUAGE,
    Resource,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import set_tracer_provider
from prometheus_fastapi_instrumentator.instrumentation import (
    PrometheusFastApiInstrumentator,
)

from llm_port_pii.services.pii.service import PIIService
from llm_port_pii.settings import settings

log = logging.getLogger(__name__)

# ── Optional EE plugin ────────────────────────────────────────────
try:
    from llm_port_ee import setup_ee, teardown_ee  # type: ignore[import-untyped]
    from llm_port_ee.plugins.pii import pii_plugin  # type: ignore[import-untyped]

    _EE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _EE_AVAILABLE = False
# ──────────────────────────────────────────────────────────────────


def setup_opentelemetry(app: FastAPI) -> None:  # pragma: no cover
    """
    Enables opentelemetry instrumentation.

    :param app: current application.
    """
    if not settings.opentelemetry_endpoint:
        return

    tracer_provider = TracerProvider(
        resource=Resource(
            attributes={
                SERVICE_NAME: "llm_port_pii",
                TELEMETRY_SDK_LANGUAGE: "python",
                DEPLOYMENT_ENVIRONMENT: settings.environment,
            },
        ),
    )

    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.opentelemetry_endpoint,
                insecure=True,
            ),
        ),
    )

    excluded_endpoints = [
        app.url_path_for("health_check"),
        app.url_path_for("openapi"),
        app.url_path_for("swagger_ui_html"),
        app.url_path_for("swagger_ui_redirect"),
        app.url_path_for("redoc_html"),
        "/metrics",
    ]

    FastAPIInstrumentor().instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls=",".join(excluded_endpoints),
    )
    if settings.redis_enabled:
        RedisInstrumentor().instrument(
            tracer_provider=tracer_provider,
        )

    set_tracer_provider(tracer_provider=tracer_provider)


def stop_opentelemetry(app: FastAPI) -> None:  # pragma: no cover
    """
    Disables opentelemetry instrumentation.

    :param app: current application.
    """
    if not settings.opentelemetry_endpoint:
        return

    FastAPIInstrumentor().uninstrument_app(app)
    if settings.redis_enabled:
        RedisInstrumentor().uninstrument()


def setup_prometheus(app: FastAPI) -> None:  # pragma: no cover
    """
    Enables prometheus integration.

    :param app: current application.
    """
    PrometheusFastApiInstrumentator(should_group_status_codes=False).instrument(
        app,
    ).expose(app, should_gzip=True, name="prometheus_metrics")


@asynccontextmanager
async def lifespan_setup(
    app: FastAPI,
) -> AsyncGenerator[None, None]:  # pragma: no cover
    """
    Actions to run on application startup.

    This function uses fastAPI app to store data
    in the state, such as db_engine.

    :param app: the fastAPI application.
    :return: function that actually performs actions.
    """

    app.middleware_stack = None
    setup_opentelemetry(app)
    setup_prometheus(app)

    # Initialize Presidio PII service (loads spaCy model - slow first time)
    app.state.pii_service = PIIService.create(
        default_language=settings.pii_default_language,
        default_score_threshold=settings.pii_score_threshold,
    )

    # ── Optional EE plugin bootstrap ─────────────────────────
    if _EE_AVAILABLE:
        try:
            await setup_ee(
                app,
                module_name="pii-pro",
                mount_health=False,
                mount_middleware=False,
            )
            await pii_plugin.startup(app)
            log.info("PII Enterprise plugin loaded successfully.")
        except SystemExit:
            log.warning(
                "EE license validation failed for pii-pro; "
                "running in Core-only mode.",
            )
        except Exception:
            log.exception("Failed to load PII Enterprise plugin.")
    # ──────────────────────────────────────────────────────────

    app.middleware_stack = app.build_middleware_stack()

    yield

    # ── EE teardown ──────────────────────────────────────────
    if _EE_AVAILABLE and getattr(app.state, "license", None) is not None:
        try:
            await pii_plugin.shutdown(app)
            await teardown_ee(app)
        except Exception:
            log.exception("Error during PII Enterprise plugin teardown.")
    # ──────────────────────────────────────────────────────────

    stop_opentelemetry(app)

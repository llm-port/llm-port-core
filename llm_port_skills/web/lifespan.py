import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
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

from llm_port_skills.settings import settings

logger = logging.getLogger(__name__)


def setup_opentelemetry(app: FastAPI) -> None:  # pragma: no cover
    """Enable OpenTelemetry instrumentation."""
    if not settings.opentelemetry_endpoint:
        return

    tracer_provider = TracerProvider(
        resource=Resource(
            attributes={
                SERVICE_NAME: "llm_port_skills",
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

    excluded_endpoints = ["/api/health", "/metrics"]
    FastAPIInstrumentor().instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls=",".join(excluded_endpoints),
    )
    set_tracer_provider(tracer_provider=tracer_provider)


def stop_opentelemetry(app: FastAPI) -> None:  # pragma: no cover
    """Disable OpenTelemetry instrumentation."""
    if not settings.opentelemetry_endpoint:
        return
    FastAPIInstrumentor().uninstrument_app(app)


def setup_prometheus(app: FastAPI) -> None:  # pragma: no cover
    """Enable Prometheus metrics."""
    PrometheusFastApiInstrumentator(should_group_status_codes=False).instrument(
        app,
    ).expose(app, should_gzip=True, name="prometheus_metrics")


def _init_cache(app: FastAPI) -> None:
    """Initialise Redis or NoOp cache backend."""
    if settings.redis_enabled:
        from redis.asyncio import ConnectionPool

        pool = ConnectionPool.from_url(str(settings.redis_url))
        app.state.redis_pool = pool
        logger.info("Redis cache initialised.")
    else:
        app.state.redis_pool = None
        logger.info("Redis not configured — cache features disabled.")


@asynccontextmanager
async def lifespan_setup(
    app: FastAPI,
) -> AsyncGenerator[None, None]:  # pragma: no cover
    """Application startup and shutdown lifecycle."""
    app.middleware_stack = None

    _init_cache(app)
    setup_opentelemetry(app)
    setup_prometheus(app)

    app.middleware_stack = app.build_middleware_stack()

    yield

    if app.state.redis_pool is not None:
        await app.state.redis_pool.disconnect()
    stop_opentelemetry(app)

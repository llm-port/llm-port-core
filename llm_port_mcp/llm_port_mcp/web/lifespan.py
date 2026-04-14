import asyncio
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

from llm_port_mcp.db import crypto
from llm_port_mcp.settings import settings

logger = logging.getLogger(__name__)


def setup_opentelemetry(app: FastAPI) -> None:  # pragma: no cover
    """Enable OpenTelemetry instrumentation."""
    if not settings.opentelemetry_endpoint:
        return

    tracer_provider = TracerProvider(
        resource=Resource(
            attributes={
                SERVICE_NAME: "llm_port_mcp",
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


async def _init_connection_manager(app: FastAPI) -> None:
    """Initialise MCP connection manager and connect enabled servers."""
    from llm_port_mcp.services.connection_manager import MCPConnectionManager

    manager = MCPConnectionManager()
    app.state.connection_manager = manager

    # Connect to all enabled servers from DB
    from llm_port_mcp.db.session import async_session_factory
    from llm_port_mcp.services.dao import MCPDao

    async with async_session_factory() as session:
        dao = MCPDao(session)
        servers = await dao.list_enabled_servers()

    for server in servers:
        try:
            await manager.start(server)
            logger.info("Connected to MCP server: %s (%s)", server.name, server.id)
        except Exception:
            logger.warning(
                "Failed to connect to MCP server: %s (%s) — "
                "will retry on next heartbeat",
                server.name,
                server.id,
                exc_info=True,
            )
        except BaseException:
            # anyio cancel-scope / TaskGroup errors surface as
            # BaseExceptionGroup which bypasses ``except Exception``.
            # Log and continue so one unreachable server doesn't take
            # down the entire MCP gateway.
            logger.warning(
                "Failed to connect to MCP server: %s (%s) — "
                "transport-level error, will retry on next heartbeat",
                server.name,
                server.id,
                exc_info=True,
            )


async def _shutdown_connection_manager(app: FastAPI) -> None:
    """Disconnect all active MCP servers."""
    manager = getattr(app.state, "connection_manager", None)
    if manager is not None:
        await manager.stop_all()


def _start_heartbeat_task(app: FastAPI) -> None:
    """Start background heartbeat task for active MCP servers."""
    async def _heartbeat_loop() -> None:
        manager = app.state.connection_manager
        while True:
            await asyncio.sleep(settings.default_heartbeat_interval_sec)
            try:
                await manager.heartbeat_all(
                    redis_pool=app.state.redis_pool,
                )
            except Exception:
                logger.debug("Heartbeat cycle error", exc_info=True)

    app.state.heartbeat_task = asyncio.create_task(_heartbeat_loop())


def _stop_heartbeat_task(app: FastAPI) -> None:
    """Cancel the background heartbeat task."""
    task = getattr(app.state, "heartbeat_task", None)
    if task is not None:
        task.cancel()


@asynccontextmanager
async def lifespan_setup(
    app: FastAPI,
) -> AsyncGenerator[None, None]:  # pragma: no cover
    """Application startup and shutdown lifecycle."""
    # ── STARTUP ──
    app.middleware_stack = None

    # Encryption
    crypto.configure(settings.encryption_key)

    # Cache
    _init_cache(app)

    # Observability
    setup_opentelemetry(app)
    setup_prometheus(app)

    # HTTP client for outbound calls (PII service, etc.)
    import httpx

    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )

    # MCP connection manager
    await _init_connection_manager(app)
    _start_heartbeat_task(app)

    app.middleware_stack = app.build_middleware_stack()

    yield

    # ── SHUTDOWN ──
    _stop_heartbeat_task(app)
    await _shutdown_connection_manager(app)
    await app.state.http_client.aclose()
    if app.state.redis_pool is not None:
        await app.state.redis_pool.disconnect()
    stop_opentelemetry(app)

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import (
    DEPLOYMENT_ENVIRONMENT,
    SERVICE_NAME,
    TELEMETRY_SDK_LANGUAGE,
    Resource,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_fastapi_instrumentator.instrumentation import (
    PrometheusFastApiInstrumentator,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from taskiq.instrumentation import TaskiqInstrumentor

from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.services.rabbit.lifespan import init_rabbit, shutdown_rabbit
from llm_port_backend.settings import settings
from llm_port_backend.tkq import broker

log = logging.getLogger(__name__)


def _setup_db(app: FastAPI) -> None:  # pragma: no cover
    """
    Creates connection to the database.

    This function creates SQLAlchemy engine instance,
    session_factory for creating sessions
    and stores them in the application's state property.

    :param app: fastAPI application.
    """
    engine = create_async_engine(
        str(settings.db_url),
        echo=settings.db_echo,
        connect_args={"ssl": False},
    )
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory
    graph_engine = create_async_engine(
        str(settings.llm_graph_db_url),
        echo=False,
        connect_args={"ssl": False},
    )
    app.state.llm_graph_trace_engine = graph_engine
    app.state.llm_graph_trace_session_factory = async_sessionmaker(
        graph_engine,
        expire_on_commit=False,
    )


def setup_opentelemetry(app: FastAPI) -> None:  # pragma: no cover
    """
    Enables opentelemetry instrumentation.

    :param app: current application.
    """
    if not settings.opentelemetry_endpoint:
        return

    otlp_resource = Resource(
        attributes={
            SERVICE_NAME: "llm-port-backend",
            TELEMETRY_SDK_LANGUAGE: "python",
            DEPLOYMENT_ENVIRONMENT: settings.environment,
        }
    )

    tracer_provider = TracerProvider(resource=otlp_resource)

    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.opentelemetry_endpoint,
            )
        )
    )
    trace.set_tracer_provider(tracer_provider=tracer_provider)

    meter_provider = MeterProvider(
        resource=otlp_resource,
        metric_readers=[
            (PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=settings.opentelemetry_endpoint))),
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=otlp_resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=settings.opentelemetry_endpoint)),
    )
    logging.getLogger().addHandler(
        LoggingHandler(
            level=logging.NOTSET,
            logger_provider=logger_provider,
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
    SQLAlchemyInstrumentor().instrument(
        tracer_provider=tracer_provider,
        engine=app.state.db_engine.sync_engine,
    )
    AioPikaInstrumentor().instrument(
        tracer_provider=tracer_provider,
    )
    TaskiqInstrumentor().instrument_broker(
        broker,
        tracer_provider=tracer_provider,
    )


def stop_opentelemetry(app: FastAPI) -> None:  # pragma: no cover
    """
    Disables opentelemetry instrumentation.

    :param app: current application.
    """
    if not settings.opentelemetry_endpoint:
        return

    FastAPIInstrumentor().uninstrument_app(app)
    SQLAlchemyInstrumentor().uninstrument()
    AioPikaInstrumentor().uninstrument()
    TaskiqInstrumentor().uninstrument_broker(broker)


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
) -> AsyncGenerator[None]:  # pragma: no cover
    """
    Actions to run on application startup.

    This function uses fastAPI app to store data
    in the state, such as db_engine.

    :param app: the fastAPI application.
    :return: function that actually performs actions.
    """

    app.middleware_stack = None
    if not broker.is_worker_process:
        await broker.startup()
    _setup_db(app)
    setup_opentelemetry(app)
    init_rabbit(app)
    setup_prometheus(app)
    app.state.docker = DockerService()
    app.state.llm_service = LLMService(app.state.docker)
    app.middleware_stack = app.build_middleware_stack()

    # Seed a default admin user in dev mode so the UI is usable immediately
    if settings.environment == "dev":
        await _seed_dev_user(app)
        await _seed_rbac(app)

    yield
    if not broker.is_worker_process:
        await broker.shutdown()
    await app.state.db_engine.dispose()
    if hasattr(app.state, "llm_graph_trace_engine"):
        await app.state.llm_graph_trace_engine.dispose()

    await shutdown_rabbit(app)
    stop_opentelemetry(app)
    await app.state.docker.close()


async def _seed_dev_user(app: FastAPI) -> None:
    """Create admin@localhost / admin superuser if it doesn't exist (dev only)."""
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    from llm_port_backend.db.models.users import User  # noqa: PLC0415

    async with app.state.db_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == "admin@localhost"),  # type: ignore[arg-type]
        )
        existing = result.scalars().first()
        if existing:
            log.info("Dev admin user already exists (id=%s)", existing.id)
            return

        ph = PasswordHelper()
        user = User(
            email="admin@localhost",
            hashed_password=ph.hash("admin"),
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )
        session.add(user)
        try:
            await session.commit()
            log.info("Seeded dev admin user admin@localhost (password: admin)")
        except IntegrityError:
            # Another startup process may insert the same dev user concurrently.
            # Treat duplicate-email conflicts as a successful seed.
            await session.rollback()
            result = await session.execute(
                select(User).where(User.email == "admin@localhost"),  # type: ignore[arg-type]
            )
            existing = result.scalars().first()
            if existing:
                log.info("Dev admin user already exists (id=%s)", existing.id)
                return
            raise


async def _seed_rbac(app: FastAPI) -> None:
    """Seed default RBAC roles, permissions, and assign admin role to dev user."""
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.dao.rbac_dao import RbacDAO  # noqa: PLC0415
    from llm_port_backend.db.models.users import User  # noqa: PLC0415

    async with app.state.db_session_factory() as session:
        rbac_dao = RbacDAO(session)
        await rbac_dao.seed_defaults()

        # Assign admin role to the dev user
        result = await session.execute(
            select(User).where(User.email == "admin@localhost"),  # type: ignore[arg-type]
        )
        dev_user = result.scalars().first()
        if dev_user:
            admin_role = await rbac_dao.get_role_by_name("admin")
            if admin_role:
                await rbac_dao.assign_role(dev_user.id, admin_role.id)

        await session.commit()
        log.info("Seeded RBAC roles and permissions")

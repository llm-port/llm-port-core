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
from llm_port_backend.services.llm.gateway_sync import GatewaySyncService
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


async def _ensure_api_secret_read_grants(app: FastAPI) -> None:  # pragma: no cover
    """Ensure llm_port_api DB role can read backend secret settings.

    llm_port_api loads its JWT verification secret from
    ``llm_port_backend.system_setting_secret`` at startup. If the
    ``llm_user`` role lacks SELECT rights, API falls back to empty secret
    and returns ``JWT secret is not configured``.
    """
    from sqlalchemy import text  # noqa: PLC0415

    async with app.state.db_session_factory() as session:
        try:
            role_exists = await session.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = 'llm_user'"),
            )
            if role_exists.scalar_one_or_none() is None:
                log.warning("Role 'llm_user' not found; skipping API secret-read grants.")
                await session.rollback()
                return
            await session.execute(text("GRANT USAGE ON SCHEMA public TO llm_user"))
            await session.execute(text("GRANT SELECT ON TABLE system_setting_secret TO llm_user"))
            await session.execute(
                text(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE llm_port_backend IN SCHEMA public "
                    "GRANT SELECT ON TABLES TO llm_user",
                ),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("Failed to ensure llm_user secret-read grants.")


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


async def _load_secrets_from_db(app: FastAPI) -> None:  # pragma: no cover
    """Load encrypted secrets from ``system_setting_secret`` into settings.

    Both ``llm_port_backend.users_secret`` and ``llm_port_api.jwt_secret``
    are read, decrypted with ``SettingsCrypto``, and pushed into the
    runtime ``settings`` singleton so that downstream code (e.g.
    ``generate_api_token``) can simply reference ``settings.users_secret``.
    """
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415

    if not settings.settings_master_key:
        log.warning("SETTINGS_MASTER_KEY is empty – cannot load secrets from DB.")
        return

    crypto = SettingsCrypto(settings.settings_master_key)
    keys_to_attrs = {
        "llm_port_backend.users_secret": "users_secret",
    }

    async with app.state.db_session_factory() as session:
        from sqlalchemy import text  # noqa: PLC0415

        for db_key, attr_name in keys_to_attrs.items():
            row = await session.execute(
                text("SELECT ciphertext FROM system_setting_secret WHERE key = :k"),
                {"k": db_key},
            )
            result = row.fetchone()
            if result is None:
                log.info("Secret '%s' not yet stored in DB, keeping env/default.", db_key)
                continue
            try:
                plaintext = crypto.decrypt(result[0])
                object.__setattr__(settings, attr_name, plaintext)
                log.info("Loaded secret '%s' from DB.", db_key)
            except Exception:
                log.exception("Failed to decrypt secret '%s'.", db_key)


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
    await _ensure_api_secret_read_grants(app)

    # Load secrets from DB before any service that needs them
    await _load_secrets_from_db(app)

    # In dev mode, auto-seed required secrets so the stack works out of the box
    if settings.environment == "dev":
        await _seed_secrets(app)

    setup_opentelemetry(app)
    init_rabbit(app)
    setup_prometheus(app)
    app.state.docker = DockerService()
    gateway_session_factory = getattr(app.state, "llm_graph_trace_session_factory", None)
    gateway_sync = GatewaySyncService(gateway_session_factory)
    app.state.llm_service = LLMService(
        app.state.docker, gateway_sync=gateway_sync,
    )
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


async def _seed_secrets(app: FastAPI) -> None:  # pragma: no cover
    """Auto-generate and store JWT / auth secrets if they are missing (dev mode).

    ``llm_port_backend.users_secret`` and ``llm_port_api.jwt_secret`` are
    seeded with the **same** random value so that tokens signed by the
    backend can be verified by the API gateway.

    After seeding, the values are also pushed into ``settings`` so the
    current process can use them immediately (the same way
    ``_load_secrets_from_db`` would do on the next restart).
    """
    import secrets as _secrets  # noqa: PLC0415

    from sqlalchemy import text  # noqa: PLC0415

    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415

    if not settings.settings_master_key:
        log.warning("Cannot seed secrets: SETTINGS_MASTER_KEY is empty.")
        return

    crypto = SettingsCrypto(settings.settings_master_key)

    # Both keys share the same value so the backend can sign and the API can verify.
    jwt_keys = ("llm_port_backend.users_secret", "llm_port_api.jwt_secret")

    async with app.state.db_session_factory() as session:
        # Serialize secret seeding across concurrent startup processes.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('llm_port_backend_seed_secrets'))"),
        )

        # Prefer existing backend secret if present; otherwise generate a new shared secret.
        current_row = await session.execute(
            text("SELECT ciphertext FROM system_setting_secret WHERE key = :k"),
            {"k": "llm_port_backend.users_secret"},
        )
        current_ciphertext = current_row.scalar_one_or_none()

        if current_ciphertext is None:
            shared_secret = _secrets.token_urlsafe(32)
        else:
            try:
                shared_secret = crypto.decrypt(current_ciphertext)
            except Exception:
                log.exception("Failed to decrypt existing users secret; reseeding.")
                shared_secret = _secrets.token_urlsafe(32)

        ciphertext = crypto.encrypt(shared_secret)
        for db_key in jwt_keys:
            await session.execute(
                text(
                    "INSERT INTO system_setting_secret "
                    "(key, ciphertext, nonce, kek_version, updated_by) "
                    "VALUES (:k, :c, '', 1, NULL) "
                    "ON CONFLICT (key) DO UPDATE SET "
                    "ciphertext = EXCLUDED.ciphertext, "
                    "nonce = EXCLUDED.nonce, "
                    "kek_version = EXCLUDED.kek_version, "
                    "updated_by = EXCLUDED.updated_by",
                ),
                {"k": db_key, "c": ciphertext},
            )
            log.info("Seeded secret '%s' into DB.", db_key)

        await session.commit()
        object.__setattr__(settings, "users_secret", shared_secret)


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

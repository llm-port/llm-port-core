import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
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
from llm_port_backend.services.notifications import (
    GatewayAlertMonitor,
    MailerClient,
    NotificationDispatcher,
)
from llm_port_backend.services.rabbit.lifespan import init_rabbit, shutdown_rabbit
from llm_port_backend.settings import settings
from llm_port_backend.tkq import broker

log = logging.getLogger(__name__)

# ── Optional EE plugin ────────────────────────────────────────────
try:
    from llm_port_ee import setup_ee, teardown_ee  # type: ignore[import-untyped]
    from llm_port_ee.plugins.backend import backend_plugin  # type: ignore[import-untyped]

    _EE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _EE_AVAILABLE = False
# ──────────────────────────────────────────────────────────────────

_RUNTIME_VALUE_KEYS: dict[str, str] = {
    "llm_port_api.pii_enabled": "pii_enabled",
    "llm_port_api.pii_service_url": "pii_service_url",
    "llm_port_mailer.enabled": "mailer_enabled",
    "llm_port_mailer.service_url": "mailer_service_url",
    "llm_port_mailer.frontend_base_url": "mailer_frontend_base_url",
    "llm_port_mailer.admin_recipients": "mailer_admin_recipients",
    "llm_port_mailer.alert_5xx_threshold_percent": "mailer_alert_5xx_threshold_percent",
    "llm_port_mailer.alert_5xx_window_minutes": "mailer_alert_5xx_window_minutes",
    "llm_port_mailer.alert_cooldown_minutes": "mailer_alert_cooldown_minutes",
    "llm_port_mailer.smtp.host": "mailer_smtp_host",
    "llm_port_mailer.smtp.port": "mailer_smtp_port",
    "llm_port_mailer.smtp.starttls": "mailer_smtp_starttls",
    "llm_port_mailer.smtp.ssl": "mailer_smtp_ssl",
    "llm_port_mailer.from_email": "mailer_from_email",
    "llm_port_mailer.from_name": "mailer_from_name",
}
_RUNTIME_SECRET_KEYS: dict[str, str] = {
    "llm_port_backend.users_secret": "users_secret",
    "llm_port_mailer.api_token": "mailer_api_token",
    "llm_port_mailer.smtp.username": "mailer_smtp_username",
    "llm_port_mailer.smtp.password": "mailer_smtp_password",
    "llm_port_mailer.grafana_webhook_token": "mailer_grafana_webhook_token",
}


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


async def _load_runtime_settings_from_db(app: FastAPI) -> None:  # pragma: no cover
    """Load runtime settings from DB-backed system_setting tables."""
    import os  # noqa: PLC0415

    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415

    env_prefix = (settings.model_config.get("env_prefix") or "").upper()

    async with app.state.db_session_factory() as session:
        from sqlalchemy import text  # noqa: PLC0415

        try:
            rows = await session.execute(
                text("SELECT key, value_json FROM system_setting_value"),
            )
            for row in rows.mappings():
                db_key = str(row["key"])
                attr_name = _RUNTIME_VALUE_KEYS.get(db_key)
                if attr_name is None:
                    continue
                # Explicit env-var overrides take precedence over DB values.
                env_key = f"{env_prefix}{attr_name}".upper()
                if os.environ.get(env_key):
                    log.debug(
                        "Skipping DB override for '%s' – env var %s is set.",
                        db_key,
                        env_key,
                    )
                    continue
                value = _extract_setting_value(row["value_json"])
                object.__setattr__(settings, attr_name, value)
        except Exception:
            log.exception("Failed to load runtime value settings from DB.")

        if not settings.settings_master_key:
            log.warning("SETTINGS_MASTER_KEY is empty – cannot load secret runtime settings from DB.")
            return

        crypto = SettingsCrypto(settings.settings_master_key)
        try:
            rows = await session.execute(
                text("SELECT key, ciphertext FROM system_setting_secret"),
            )
            for row in rows.mappings():
                db_key = str(row["key"])
                attr_name = _RUNTIME_SECRET_KEYS.get(db_key)
                if attr_name is None:
                    continue
                try:
                    plaintext = crypto.decrypt(str(row["ciphertext"]))
                except Exception:
                    log.exception("Failed to decrypt secret '%s'.", db_key)
                    continue
                object.__setattr__(settings, attr_name, plaintext)
        except Exception:
            log.exception("Failed to load runtime secret settings from DB.")


def _extract_setting_value(value_json: object) -> object:
    """Unwrap {'value': ...} payloads stored in system_setting_value."""
    if isinstance(value_json, dict):
        return value_json.get("value", value_json)
    return value_json


async def _start_notification_runtime(app: FastAPI) -> None:
    """Start background notification dispatcher and gateway alert monitor."""
    http_client = httpx.AsyncClient()
    try:
        mailer_client = MailerClient(http_client=http_client)
        dispatcher = NotificationDispatcher(
            session_factory=app.state.db_session_factory,
            mailer_client=mailer_client,
        )
        dispatcher.start()
        app.state.notification_http_client = http_client
        app.state.notification_dispatcher = dispatcher

        gateway_session_factory = getattr(app.state, "llm_graph_trace_session_factory", None)
        if gateway_session_factory is None:
            return
        monitor = GatewayAlertMonitor(
            backend_session_factory=app.state.db_session_factory,
            gateway_session_factory=gateway_session_factory,
        )
        monitor.start()
        app.state.gateway_alert_monitor = monitor
    except Exception:
        log.exception("Failed to start notification runtime.")
        await http_client.aclose()


async def _stop_notification_runtime(app: FastAPI) -> None:
    """Stop notification background workers and close shared HTTP client."""
    monitor: GatewayAlertMonitor | None = getattr(app.state, "gateway_alert_monitor", None)
    if monitor is not None:
        await monitor.stop()

    dispatcher: NotificationDispatcher | None = getattr(app.state, "notification_dispatcher", None)
    if dispatcher is not None:
        await dispatcher.stop()

    http_client: httpx.AsyncClient | None = getattr(app.state, "notification_http_client", None)
    if http_client is not None:
        await http_client.aclose()


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

    # Register built-in optional modules before any request is served.
    from llm_port_backend.services.core_modules import register_core_modules  # noqa: PLC0415

    register_core_modules()

    if not broker.is_worker_process:
        await broker.startup()
    _setup_db(app)
    await _ensure_api_secret_read_grants(app)

    # Load DB-backed runtime settings before services that need them
    await _load_runtime_settings_from_db(app)

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
    if not broker.is_worker_process:
        await _start_notification_runtime(app)

    # ── Optional EE plugin bootstrap ─────────────────────────
    if _EE_AVAILABLE:
        try:
            await setup_ee(
                app,
                module_name="observability-pro",
                mount_health=False,
                mount_middleware=False,
            )
            await backend_plugin.startup(app)
            log.info("Backend Enterprise plugin loaded successfully.")
        except SystemExit:
            log.warning(
                "EE license validation failed for observability-pro; "
                "running in Core-only mode.",
            )
        except Exception:
            log.exception("Failed to load Backend Enterprise plugin.")
    # ──────────────────────────────────────────────────────────

    app.middleware_stack = app.build_middleware_stack()

    # Seed a default admin user in dev mode so the UI is usable immediately
    if settings.environment == "dev":
        await _seed_dev_user(app)
        await _seed_rbac(app)

    yield

    # ── EE teardown ──────────────────────────────────────────
    if _EE_AVAILABLE and getattr(app.state, "license", None) is not None:
        try:
            await backend_plugin.shutdown(app)
            await teardown_ee(app)
        except Exception:
            log.exception("Error during Backend Enterprise plugin teardown.")
    # ──────────────────────────────────────────────────────────

    await _stop_notification_runtime(app)

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

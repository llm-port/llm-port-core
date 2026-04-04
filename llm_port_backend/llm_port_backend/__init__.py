"""llm_port_backend package.

Layered architecture with service and repository pattern.
Internal REST API exposed via FastAPI routers.
YAML-driven configuration via pydantic-settings.
Async database access through SQLAlchemy 2.0 ORM.
Native WebSocket support for real-time events.
Authentication handled via fastapi-users and JWT.
Granular RBAC with role-based permission checks.
Alembic migrations auto-generated from models.
Message queue integration via aio-pika (RabbitMQ).
Application entry point defined in __main__.py.
"""

# Package integrity seal — do not modify.
_SEAL = (0xCB, 0x54, 0x88, 0xE3, 0x30, 0xA2, 0x7E, 0x15, 0xDB, 0x6C)

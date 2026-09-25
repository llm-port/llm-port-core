"""LLM Server models: providers, models, artifacts, runtimes, and download jobs."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ProviderType(enum.StrEnum):
    """Supported LLM engine types."""

    VLLM = "vllm"
    LLAMACPP = "llamacpp"
    TGI = "tgi"
    OLLAMA = "ollama"
    CLOUD = "cloud"


class ProviderTarget(enum.StrEnum):
    """Where the engine runs."""

    LOCAL_DOCKER = "local_docker"
    REMOTE_ENDPOINT = "remote_endpoint"
    #: Served by an inference deployment on one of our own clusters.
    #:
    #: Neither of the other two fits, and the difference is not cosmetic: a
    #: cluster-backed provider is started by scaling a deployment, its logs
    #: are per-replica across several machines, and it has no container on
    #: this host to start or stop. Calling it ``local_docker`` would offer
    #: controls that cannot work; calling it ``remote_endpoint`` would hide
    #: that we own it.
    INFERENCE_CLUSTER = "inference_cluster"


class ModelSource(enum.StrEnum):
    """How a model was acquired."""

    HUGGINGFACE = "huggingface"
    LOCAL_PATH = "local_path"
    ARCHIVE_IMPORT = "archive_import"
    REMOTE = "remote"


class ModelStatus(enum.StrEnum):
    """Lifecycle status of a model."""

    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    FAILED = "failed"
    DELETING = "deleting"


class ArtifactFormat(enum.StrEnum):
    """File format of a model artifact."""

    SAFETENSORS = "safetensors"
    GGUF = "gguf"
    OTHER = "other"


class RuntimeStatus(enum.StrEnum):
    """Lifecycle status of a runtime instance."""

    CREATING = "creating"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class DownloadJobStatus(enum.StrEnum):
    """Lifecycle status of a model download job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELED = "canceled"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


class LLMProvider(Base):
    """An engine type registration (e.g. vLLM on local Docker)."""

    __tablename__ = "llm_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[ProviderType] = mapped_column(
        SAEnum(
            ProviderType,
            name="provider_type",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    target: Mapped[ProviderTarget] = mapped_column(
        SAEnum(
            ProviderTarget,
            name="provider_target",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ProviderTarget.LOCAL_DOCKER,
    )
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    endpoint_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="Base URL for remote_endpoint providers.",
    )
    api_key_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Encrypted API key for remote endpoint auth.",
    )
    litellm_provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="LiteLLM provider prefix (e.g. 'anthropic', 'openrouter').",
    )
    litellm_model: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        doc="LiteLLM model identifier (e.g. 'claude-sonnet-4-20250514').",
    )
    extra_params: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        doc="Provider-specific params (headers, api_version, etc.).",
    )
    residency_override: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        doc=(
            "Where an administrator says this provider's prompts go -- "
            "'machines', 'private' or 'external' -- when detection cannot "
            "know: a LAN proxy that forwards to a cloud API, a cloud VPC on "
            "private addresses. NULL: detected (services/llm/residency.py)."
        ),
    )
    source_kind: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc=(
            "What owns this row, when something does: 'inference_deployment' "
            "for a provider derived from a deployment, NULL for one a person "
            "created. Same vocabulary as the gateway's llm_provider_instance, "
            "on purpose -- two layers describing ownership differently is how "
            "they drift."
        ),
    )
    source_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        doc="The owning record's id, paired with source_kind.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def is_derived(self) -> bool:
        """Whether a deployment owns this row rather than a person.

        A derived provider must not be edited or deleted from the providers
        screen: the deployment would recreate or overwrite it, and the two
        would disagree in between.
        """
        return bool(self.source_kind)


class LLMModel(Base):
    """Logical model record (may have multiple physical artifacts)."""

    __tablename__ = "llm_models"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[ModelSource] = mapped_column(
        SAEnum(
            ModelSource,
            name="model_source",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    hf_repo_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    hf_revision: Mapped[str | None] = mapped_column(String(256), nullable=True)
    license_ack_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[ModelStatus] = mapped_column(
        SAEnum(
            ModelStatus,
            name="model_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ModelStatus.DOWNLOADING,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ModelArtifact(Base):
    """Physical file on disk belonging to a model."""

    __tablename__ = "model_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    format: Mapped[ArtifactFormat] = mapped_column(
        SAEnum(
            ArtifactFormat,
            name="artifact_format",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    engine_compat: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class LLMRuntime(Base):
    """A running (or stopped) LLM inference engine instance."""

    __tablename__ = "llm_runtimes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[RuntimeStatus] = mapped_column(
        SAEnum(
            RuntimeStatus,
            name="runtime_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=RuntimeStatus.CREATING,
        index=True,
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    openai_compat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    generic_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    container_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    execution_target: Mapped[str] = mapped_column(String(32), nullable=False, default="local")
    assigned_node_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    desired_state: Mapped[str] = mapped_column(String(64), nullable=False, default="running")
    placement_explain_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_command_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node_command.id", ondelete="SET NULL"),
        nullable=True,
    )
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DownloadJob(Base):
    """Tracks background model download progress."""

    __tablename__ = "download_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[DownloadJobStatus] = mapped_column(
        SAEnum(
            DownloadJobStatus,
            name="download_job_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=DownloadJobStatus.QUEUED,
        index=True,
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    log_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

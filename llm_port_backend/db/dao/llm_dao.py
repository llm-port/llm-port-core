"""DAOs for the LLM server subsystem."""

import uuid

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.llm import (
    ArtifactFormat,
    DownloadJob,
    DownloadJobStatus,
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ModelArtifact,
    ModelSource,
    ModelStatus,
    ProviderTarget,
    ProviderType,
    RuntimeStatus,
)


# -----------------------------------------------------------------------
# Provider DAO
# -----------------------------------------------------------------------


class ProviderDAO:
    """CRUD operations for LLM providers."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        name: str,
        type_: ProviderType,
        target: ProviderTarget = ProviderTarget.LOCAL_DOCKER,
        capabilities: dict | None = None,
    ) -> LLMProvider:
        """Create a new provider."""
        provider = LLMProvider(
            id=uuid.uuid4(),
            name=name,
            type=type_,
            target=target,
            capabilities=capabilities,
        )
        self.session.add(provider)
        await self.session.flush()
        return provider

    async def get(self, provider_id: uuid.UUID) -> LLMProvider | None:
        """Fetch a provider by ID."""
        result = await self.session.execute(
            select(LLMProvider).where(LLMProvider.id == provider_id),
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[LLMProvider]:
        """Return all providers."""
        result = await self.session.execute(
            select(LLMProvider).order_by(LLMProvider.created_at.desc()),
        )
        return list(result.scalars().all())

    async def update(
        self,
        provider_id: uuid.UUID,
        *,
        name: str | None = None,
        capabilities: dict | None = None,
    ) -> LLMProvider | None:
        """Patch writable fields on a provider."""
        provider = await self.get(provider_id)
        if provider is None:
            return None
        if name is not None:
            provider.name = name
        if capabilities is not None:
            provider.capabilities = capabilities
        return provider

    async def delete(self, provider_id: uuid.UUID) -> bool:
        """Delete a provider. Returns False if not found."""
        provider = await self.get(provider_id)
        if provider is None:
            return False
        await self.session.delete(provider)
        return True

    async def has_runtimes(self, provider_id: uuid.UUID) -> bool:
        """Check if any runtimes reference this provider."""
        result = await self.session.execute(
            select(LLMRuntime.id)
            .where(LLMRuntime.provider_id == provider_id)
            .limit(1),
        )
        return result.scalar_one_or_none() is not None


# -----------------------------------------------------------------------
# Model DAO
# -----------------------------------------------------------------------


class ModelDAO:
    """CRUD operations for LLM models."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        display_name: str,
        source: ModelSource,
        *,
        hf_repo_id: str | None = None,
        hf_revision: str | None = None,
        license_ack_required: bool = False,
        tags: list[str] | None = None,
        status: ModelStatus = ModelStatus.DOWNLOADING,
    ) -> LLMModel:
        """Create a new model record."""
        model = LLMModel(
            id=uuid.uuid4(),
            display_name=display_name,
            source=source,
            hf_repo_id=hf_repo_id,
            hf_revision=hf_revision,
            license_ack_required=license_ack_required,
            tags=tags,
            status=status,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def get(self, model_id: uuid.UUID) -> LLMModel | None:
        """Fetch a model by ID."""
        result = await self.session.execute(
            select(LLMModel).where(LLMModel.id == model_id),
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        status_filter: ModelStatus | None = None,
    ) -> list[LLMModel]:
        """Return all models, optionally filtered by status."""
        query = select(LLMModel).order_by(LLMModel.created_at.desc())
        if status_filter:
            query = query.where(LLMModel.status == status_filter)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def set_status(
        self,
        model_id: uuid.UUID,
        status: ModelStatus,
    ) -> LLMModel | None:
        """Transition model status."""
        model = await self.get(model_id)
        if model is None:
            return None
        model.status = status
        return model

    async def delete(self, model_id: uuid.UUID) -> bool:
        """Delete a model. Returns False if not found."""
        model = await self.get(model_id)
        if model is None:
            return False
        await self.session.delete(model)
        return True

    async def is_used_by_running_runtime(self, model_id: uuid.UUID) -> bool:
        """Check if any non-stopped runtime is using this model."""
        result = await self.session.execute(
            select(LLMRuntime.id)
            .where(
                LLMRuntime.model_id == model_id,
                LLMRuntime.status.notin_([RuntimeStatus.STOPPED, RuntimeStatus.ERROR]),
            )
            .limit(1),
        )
        return result.scalar_one_or_none() is not None


# -----------------------------------------------------------------------
# Artifact DAO
# -----------------------------------------------------------------------


class ArtifactDAO:
    """CRUD operations for model artifacts."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create_batch(
        self,
        model_id: uuid.UUID,
        artifacts: list[dict],
    ) -> list[ModelArtifact]:
        """Bulk-insert artifact records for a model."""
        records = []
        for art in artifacts:
            record = ModelArtifact(
                id=uuid.uuid4(),
                model_id=model_id,
                format=ArtifactFormat(art["format"]),
                path=art["path"],
                size_bytes=art.get("size_bytes", 0),
                sha256=art.get("sha256"),
                engine_compat=art.get("engine_compat"),
            )
            self.session.add(record)
            records.append(record)
        await self.session.flush()
        return records

    async def list_by_model(self, model_id: uuid.UUID) -> list[ModelArtifact]:
        """Return all artifacts for a model."""
        result = await self.session.execute(
            select(ModelArtifact)
            .where(ModelArtifact.model_id == model_id)
            .order_by(ModelArtifact.created_at),
        )
        return list(result.scalars().all())

    async def delete_by_model(self, model_id: uuid.UUID) -> int:
        """Delete all artifacts for a model. Returns count deleted."""
        from sqlalchemy import delete as sa_delete  # noqa: PLC0415

        stmt = sa_delete(ModelArtifact).where(ModelArtifact.model_id == model_id)
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]


# -----------------------------------------------------------------------
# Runtime DAO
# -----------------------------------------------------------------------


class RuntimeDAO:
    """CRUD operations for LLM runtimes."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        name: str,
        provider_id: uuid.UUID,
        model_id: uuid.UUID,
        *,
        generic_config: dict | None = None,
        provider_config: dict | None = None,
        openai_compat: bool = True,
    ) -> LLMRuntime:
        """Create a new runtime record."""
        runtime = LLMRuntime(
            id=uuid.uuid4(),
            name=name,
            provider_id=provider_id,
            model_id=model_id,
            status=RuntimeStatus.CREATING,
            generic_config=generic_config,
            provider_config=provider_config,
            openai_compat=openai_compat,
        )
        self.session.add(runtime)
        await self.session.flush()
        return runtime

    async def get(self, runtime_id: uuid.UUID) -> LLMRuntime | None:
        """Fetch a runtime by ID."""
        result = await self.session.execute(
            select(LLMRuntime).where(LLMRuntime.id == runtime_id),
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        status_filter: RuntimeStatus | None = None,
    ) -> list[LLMRuntime]:
        """Return all runtimes, optionally filtered by status."""
        query = select(LLMRuntime).order_by(LLMRuntime.created_at.desc())
        if status_filter:
            query = query.where(LLMRuntime.status == status_filter)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def set_status(
        self,
        runtime_id: uuid.UUID,
        status: RuntimeStatus,
    ) -> LLMRuntime | None:
        """Transition runtime status."""
        runtime = await self.get(runtime_id)
        if runtime is None:
            return None
        runtime.status = status
        return runtime

    async def set_container_ref(
        self,
        runtime_id: uuid.UUID,
        container_ref: str,
        endpoint_url: str | None = None,
    ) -> LLMRuntime | None:
        """Link a runtime to its Docker container."""
        runtime = await self.get(runtime_id)
        if runtime is None:
            return None
        runtime.container_ref = container_ref
        if endpoint_url is not None:
            runtime.endpoint_url = endpoint_url
        return runtime

    async def delete(self, runtime_id: uuid.UUID) -> bool:
        """Delete a runtime. Returns False if not found."""
        runtime = await self.get(runtime_id)
        if runtime is None:
            return False
        await self.session.delete(runtime)
        return True


# -----------------------------------------------------------------------
# Download Job DAO
# -----------------------------------------------------------------------


class DownloadJobDAO:
    """CRUD operations for model download jobs."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        model_id: uuid.UUID,
    ) -> DownloadJob:
        """Create a queued download job for a model."""
        job = DownloadJob(
            id=uuid.uuid4(),
            model_id=model_id,
            status=DownloadJobStatus.QUEUED,
            progress=0,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> DownloadJob | None:
        """Fetch a job by ID."""
        result = await self.session.execute(
            select(DownloadJob).where(DownloadJob.id == job_id),
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        status_filter: DownloadJobStatus | None = None,
        model_id: uuid.UUID | None = None,
    ) -> list[DownloadJob]:
        """Return download jobs, optionally filtered."""
        query = select(DownloadJob).order_by(DownloadJob.created_at.desc())
        if status_filter:
            query = query.where(DownloadJob.status == status_filter)
        if model_id:
            query = query.where(DownloadJob.model_id == model_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def update_progress(
        self,
        job_id: uuid.UUID,
        progress: int,
        status: DownloadJobStatus | None = None,
    ) -> DownloadJob | None:
        """Update download progress and optionally status."""
        job = await self.get(job_id)
        if job is None:
            return None
        job.progress = progress
        if status is not None:
            job.status = status
        return job

    async def set_failed(
        self,
        job_id: uuid.UUID,
        error_message: str,
    ) -> DownloadJob | None:
        """Mark a job as failed with an error message."""
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = DownloadJobStatus.FAILED
        job.error_message = error_message
        return job

    async def set_canceled(self, job_id: uuid.UUID) -> DownloadJob | None:
        """Mark a job as canceled."""
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = DownloadJobStatus.CANCELED
        return job

    async def cancel_active_for_model(self, model_id: uuid.UUID) -> int:
        """Cancel all QUEUED / RUNNING jobs for a model. Returns count affected."""
        from sqlalchemy import update  # noqa: PLC0415

        stmt = (
            update(DownloadJob)
            .where(
                DownloadJob.model_id == model_id,
                DownloadJob.status.in_(
                    [DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING],
                ),
            )
            .values(status=DownloadJobStatus.CANCELED)
        )
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

    async def delete_by_model(self, model_id: uuid.UUID) -> int:
        """Delete all jobs for a model. Returns count deleted."""
        from sqlalchemy import delete as sa_delete  # noqa: PLC0415

        stmt = sa_delete(DownloadJob).where(DownloadJob.model_id == model_id)
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

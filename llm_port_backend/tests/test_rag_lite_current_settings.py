"""RAG Lite embeds with the provider as it is set now, in every process.

Found by pointing RAG Lite at another embedding server on a running system:
the setting is applied live in the web process only, so the taskiq worker
kept embedding documents with the old provider while searches embedded the
query with the new one. And a provider created on the Providers page is
stored without ``/v1``, so the embedding client posted to the server's root.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.system_settings import SystemSettingValue
from llm_port_backend.services.rag_lite.embedding import EmbeddingClient, api_base
from llm_port_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from llm_port_backend.settings import settings


def test_a_provider_url_with_or_without_v1_reaches_the_api() -> None:
    assert api_base("http://127.0.0.1:7997") == "http://127.0.0.1:7997/v1"
    assert api_base("http://10.88.10.71:8102/v1") == "http://10.88.10.71:8102/v1"
    assert api_base("http://h:8000/v1/") == "http://h:8000/v1"
    assert EmbeddingClient(base_url="http://127.0.0.1:7997", model="m").base_url == "http://127.0.0.1:7997/v1"


async def test_a_process_reads_rag_lite_settings_as_they_are_now(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As a worker the live change never reached."""
    monkeypatch.setattr(settings, "rag_lite_embedding_model", "old-model")
    monkeypatch.setattr(settings, "rag_lite_embedding_dim", 768)
    monkeypatch.setattr(settings, "rag_lite_chunk_max_tokens", 512)
    dbsession.add_all([
        SystemSettingValue(key="rag_lite.embedding_model", value_json={"value": "new-model"}),
        SystemSettingValue(key="rag_lite.embedding_dim", value_json={"value": 1024}),
        SystemSettingValue(key="rag_lite.chunk_max_tokens", value_json={"value": ""}),  # blank: keep
    ])
    await dbsession.flush()

    await refresh_runtime_values(dbsession, prefix="rag_lite.")

    assert settings.rag_lite_embedding_model == "new-model"
    assert settings.rag_lite_embedding_dim == 1024
    assert settings.rag_lite_chunk_max_tokens == 512

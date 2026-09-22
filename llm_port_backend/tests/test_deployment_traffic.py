"""Per-request figures for one deployment, measured at the gateway.

The deployment card could say how many copies were running and nothing about
whether they were doing anything. Ray's own metrics answer that, but only
through Prometheus -- so the one screen an operator opens to check that
expensive hardware is working went blank whenever the monitoring stack did.

Every request already passes the gateway, which records latency, tokens and
(on a streaming response) time to first token per request. Nothing new had to
be measured; the figures had to be found. The join was already there too: the
gateway registers a provider instance per deployment, stamped
``source_kind='inference_deployment'`` with the deployment's id.

The delicate part is arithmetic rather than plumbing. Tokens per second over
the *window* is exact and useless -- 475 tokens across an hour in which the
engine worked for three seconds reads as 0.13 tok/s on hardware that does
about 150, and an operator checking their machine would conclude it was
broken. The card reports generation speed instead.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.services.observability.service import ObservabilityService

pytestmark = pytest.mark.anyio()


#: Just enough of the gateway schema for the query under test.
#:
#: These tables live in the ``llm_api`` database, which the backend's test
#: fixture does not create -- so skipping when they are absent left every
#: test here skipped, and the arithmetic (the part most worth checking)
#: shipped unexercised. Creating them is better: Postgres DDL is
#: transactional, so they disappear with the test's rollback, and the columns
#: below are exactly the ones the query reads. A column the real schema has
#: and this one does not is a column the query does not touch.
_GATEWAY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS llm_provider_instance (
        id           uuid PRIMARY KEY,
        type         varchar(64)  NOT NULL,
        base_url     text         NOT NULL,
        enabled      boolean      NOT NULL DEFAULT true,
        source_kind  varchar(64),
        source_id    varchar(128)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_gateway_request_log (
        id                   uuid PRIMARY KEY,
        request_id           varchar(128) NOT NULL,
        tenant_id            varchar(128) NOT NULL,
        user_id              varchar(128) NOT NULL,
        endpoint             varchar(128) NOT NULL,
        status_code          integer      NOT NULL,
        latency_ms           integer      NOT NULL DEFAULT 0,
        ttft_ms              integer,
        prompt_tokens        integer,
        completion_tokens    integer,
        provider_instance_id uuid,
        created_at           timestamptz  NOT NULL DEFAULT now()
    )
    """,
)


@pytest.fixture()
async def gateway(dbsession: AsyncSession) -> AsyncSession:
    """A session with the gateway tables present."""
    for statement in _GATEWAY_SCHEMA:
        await dbsession.execute(text(statement))
    await dbsession.flush()
    return dbsession


async def _instance(session: AsyncSession, deployment_id: uuid.UUID) -> uuid.UUID:
    instance_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO llm_provider_instance
                (id, type, base_url, enabled, source_kind, source_id)
            VALUES (:id, 'vllm', 'http://node:8000', true,
                    'inference_deployment', :source_id)
            """
        ),
        {"id": instance_id, "source_id": str(deployment_id)},
    )
    return instance_id


async def _request(
    session: AsyncSession,
    instance_id: uuid.UUID,
    *,
    latency_ms: int,
    ttft_ms: int | None,
    completion_tokens: int,
    prompt_tokens: int = 10,
    status_code: int = 200,
    age_sec: int = 5,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO llm_gateway_request_log
                (id, request_id, tenant_id, user_id, endpoint, status_code,
                 latency_ms, ttft_ms, prompt_tokens, completion_tokens,
                 provider_instance_id, created_at)
            VALUES (:id, :request_id, 't', 'u', '/v1/chat/completions',
                    :status_code, :latency_ms, :ttft_ms, :prompt_tokens,
                    :completion_tokens, :instance_id, :created_at)
            """
        ),
        {
            "id": uuid.uuid4(),
            "request_id": uuid.uuid4().hex,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "ttft_ms": ttft_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "instance_id": instance_id,
            "created_at": datetime.now(tz=UTC) - timedelta(seconds=age_sec),
        },
    )


# ── the numbers an operator reads ────────────────────────────────────────


async def test_tokens_per_second_measures_the_hardware_not_the_idle_time(
    gateway: AsyncSession,
) -> None:
    """The distinction the card exists to make.

    One request: 300 tokens produced in 2s of generating, inside a one-hour
    window. Over the window that is 0.08 tok/s. While generating it is 150.
    Only the second tells the operator anything about their machine.
    """
    deployment_id = uuid.uuid4()
    instance_id = await _instance(gateway, deployment_id)
    await _request(
        gateway, instance_id, latency_ms=2_100, ttft_ms=100, completion_tokens=300
    )

    traffic = await ObservabilityService(gateway).get_deployment_traffic(
        str(deployment_id), window_sec=3600
    )

    assert traffic is not None
    assert traffic["output_tokens_per_sec"] == pytest.approx(150.0, rel=0.02)


async def test_a_non_streaming_request_still_counts_toward_speed(
    gateway: AsyncSession,
) -> None:
    """No TTFT to subtract, so the whole latency is generating time."""
    deployment_id = uuid.uuid4()
    instance_id = await _instance(gateway, deployment_id)
    await _request(
        gateway, instance_id, latency_ms=1_000, ttft_ms=None, completion_tokens=100
    )

    traffic = await ObservabilityService(gateway).get_deployment_traffic(
        str(deployment_id)
    )
    assert traffic["output_tokens_per_sec"] == pytest.approx(100.0, rel=0.02)


async def test_percentiles_ignore_requests_that_have_no_ttft(
    gateway: AsyncSession,
) -> None:
    """Only streaming responses have one.

    Counting a non-streaming request as TTFT 0 would drag the percentile
    toward zero and make the deployment look faster than it is.
    """
    deployment_id = uuid.uuid4()
    instance_id = await _instance(gateway, deployment_id)
    for ttft in (100, 100, 100):
        await _request(
            gateway, instance_id, latency_ms=500, ttft_ms=ttft, completion_tokens=10
        )
    await _request(
        gateway, instance_id, latency_ms=500, ttft_ms=None, completion_tokens=10
    )

    traffic = await ObservabilityService(gateway).get_deployment_traffic(
        str(deployment_id)
    )
    assert traffic["p50_ttft_ms"] == pytest.approx(100.0)


async def test_failures_are_counted_and_rated(gateway: AsyncSession) -> None:
    deployment_id = uuid.uuid4()
    instance_id = await _instance(gateway, deployment_id)
    await _request(
        gateway, instance_id, latency_ms=100, ttft_ms=20, completion_tokens=5
    )
    await _request(
        gateway,
        instance_id,
        latency_ms=100,
        ttft_ms=None,
        completion_tokens=0,
        status_code=500,
    )

    traffic = await ObservabilityService(gateway).get_deployment_traffic(
        str(deployment_id)
    )
    assert traffic["requests"] == 2
    assert traffic["errors"] == 1
    assert traffic["error_rate"] == pytest.approx(0.5)


# ── absences rendered as absences ────────────────────────────────────────


async def test_a_deployment_with_no_gateway_instance_reports_nothing(
    gateway: AsyncSession,
) -> None:
    """Not wired up, which is different from wired up and idle.

    ``None`` lets the card say so instead of drawing a row of confident
    zeros for a deployment the gateway has never heard of.
    """
    assert (
        await ObservabilityService(gateway).get_deployment_traffic(str(uuid.uuid4()))
        is None
    )


async def test_an_idle_deployment_reports_a_real_zero(
    gateway: AsyncSession,
) -> None:
    """Registered and served nothing: counts are 0, rates are unknown."""
    deployment_id = uuid.uuid4()
    await _instance(gateway, deployment_id)

    traffic = await ObservabilityService(gateway).get_deployment_traffic(
        str(deployment_id)
    )
    assert traffic is not None
    assert traffic["requests"] == 0
    # A rate over no requests is not 0%.
    assert traffic["error_rate"] is None
    assert traffic["p50_ttft_ms"] is None
    assert traffic["output_tokens_per_sec"] is None


async def test_only_this_deployments_requests_are_counted(
    gateway: AsyncSession,
) -> None:
    """Two deployments share the log table and must not share figures."""
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    my_instance = await _instance(gateway, mine)
    their_instance = await _instance(gateway, theirs)
    await _request(
        gateway, my_instance, latency_ms=100, ttft_ms=10, completion_tokens=7
    )
    for _ in range(5):
        await _request(
            gateway, their_instance, latency_ms=100, ttft_ms=10, completion_tokens=99
        )

    traffic = await ObservabilityService(gateway).get_deployment_traffic(str(mine))
    assert traffic["requests"] == 1
    assert traffic["completion_tokens"] == 7


async def test_requests_outside_the_window_are_excluded(
    gateway: AsyncSession,
) -> None:
    """The card describes recent behaviour, not the deployment's whole life."""
    deployment_id = uuid.uuid4()
    instance_id = await _instance(gateway, deployment_id)
    await _request(
        gateway, instance_id, latency_ms=100, ttft_ms=10, completion_tokens=5, age_sec=30
    )
    await _request(
        gateway,
        instance_id,
        latency_ms=100,
        ttft_ms=10,
        completion_tokens=5_000,
        age_sec=7_200,
    )

    traffic = await ObservabilityService(gateway).get_deployment_traffic(
        str(deployment_id), window_sec=300
    )
    assert traffic["requests"] == 1
    assert traffic["completion_tokens"] == 5


async def test_several_instances_of_one_deployment_are_summed(
    gateway: AsyncSession,
) -> None:
    """A deployment can be registered more than once across a rollout."""
    deployment_id = uuid.uuid4()
    first = await _instance(gateway, deployment_id)
    second = await _instance(gateway, deployment_id)
    await _request(gateway, first, latency_ms=100, ttft_ms=10, completion_tokens=3)
    await _request(gateway, second, latency_ms=100, ttft_ms=10, completion_tokens=4)

    traffic = await ObservabilityService(gateway).get_deployment_traffic(
        str(deployment_id)
    )
    assert traffic["requests"] == 2
    assert traffic["completion_tokens"] == 7

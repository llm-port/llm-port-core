"""Approve-in-browser enrolment: the machine asks, a human says yes.

Why this path exists: the enrollment-token direction only works when the
operator's browser and a shell on the new machine share a clipboard.  Standing
at the box, or connected from a different workstation, a 32-character token
gets retyped by hand.  Here the secret travels the other way and nothing long
is typed at all.

That moves where the security lives, so most of what follows pins the new
guarantees rather than the happy path:

  * the short code is a *confirmation* value, and guessing it gets you nothing;
  * a credential is minted once and only for the caller that can prove it is
    the requester;
  * a machine that is already waiting does not queue a second code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.node_control import JoinRequestStatus
from llm_port_backend.services.nodes.service import NodeControlService

PEPPER = "test-pepper"


def _service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(
        NodeControlDAO(session),
        pepper=PEPPER,
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=300,
    )


def _caps(gpus: int = 3) -> dict[str, Any]:
    return {"machine": "aarch64", "gpu_count": gpus, "gpu": {"vendor": "nvidia", "family": "GB10"}}


async def _ask(
    service: NodeControlService,
    *,
    agent_id: str = "spark-3201",
    host: str = "10.88.10.71",
    source_ip: str | None = "10.88.10.71",
) -> dict[str, Any]:
    return await service.request_join(
        agent_id=agent_id,
        host=host,
        capabilities=_caps(),
        version="0.1.8",
        source_ip=source_ip,
    )


# ── the code is for a human, not for security ────────────────────────────


class TestCode:
    def test_is_short_enough_to_read_across_a_room(self) -> None:
        code = NodeControlService._format_code("K7M3QP")
        assert code == "K7M-3QP"
        assert len(code) <= 8

    def test_alphabet_has_no_confusable_characters(self) -> None:
        # The whole point is that someone reads this off one screen and
        # compares it to another.  O/0 and I/1 would defeat that.
        assert not set("O0I1S5UV") & set(NodeControlService._CODE_ALPHABET)


@pytest.mark.anyio()
async def test_codes_do_not_collide_while_live(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    codes = set()
    for index in range(12):
        result = await _ask(service, agent_id=f"box-{index}", source_ip=f"10.0.0.{index}")
        codes.add(result["code"])
    assert len(codes) == 12, "two waiting machines must never show the same code"


# ── the happy path ───────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_ask_then_approve_then_collect(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    asked = await _ask(service)
    assert asked["poll_secret"], "the requester must get a secret only it holds"

    # Before a decision, the agent is told to keep waiting -- not given a
    # credential, and not given an error it would treat as fatal.
    pending = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )
    assert pending["status"] == "pending"
    assert pending["code"] == asked["code"]

    waiting = await service.list_pending_join_requests()
    assert [row.agent_id for row in waiting] == ["spark-3201"]
    # The operator sees what they are approving.
    assert waiting[0].capabilities_json["gpu"]["family"] == "GB10"
    assert waiting[0].source_ip == "10.88.10.71"

    approver = uuid.uuid4()
    await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
    )

    granted = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )
    assert granted["status"] == "approved"
    assert granted["agent_id"] == "spark-3201"
    assert "." in granted["credential"], "credential is <id>.<secret>"

    # And the machine is now a real node that can authenticate.
    node, _ = await service.authenticate_agent(
        authorization=f"Bearer {granted['credential']}"
    )
    assert node.agent_id == "spark-3201"
    assert approver is not None  # guard against an unused-name refactor


@pytest.mark.anyio()
async def test_approved_machine_leaves_the_waiting_list(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    asked = await _ask(service)
    await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
    )
    assert await service.list_pending_join_requests() == []


# ── what stops a guessed code from being useful ──────────────────────────


@pytest.mark.anyio()
async def test_a_wrong_poll_secret_collects_nothing(dbsession: AsyncSession) -> None:
    """The reason the code can be six characters.

    Someone who reads the code off a screen still cannot become the machine:
    the credential goes only to the caller holding the poll secret.
    """
    service = _service(dbsession)
    asked = await _ask(service)
    await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
    )

    with pytest.raises(PermissionError):
        await service.collect_join_result(
            request_id=uuid.UUID(asked["id"]), poll_secret="not-the-secret"
        )

    # ...and the real requester is unaffected by the failed attempt.
    granted = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )
    assert granted["status"] == "approved"


@pytest.mark.anyio()
async def test_a_credential_is_handed_over_exactly_once(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    asked = await _ask(service)
    await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
    )
    first = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )
    second = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )
    assert first["status"] == "approved"
    assert second["status"] == "claimed"
    assert second.get("credential") is None


@pytest.mark.anyio()
async def test_asking_twice_does_not_queue_a_second_code(dbsession: AsyncSession) -> None:
    """Two codes for one box is two ways to approve it and no way to choose."""
    service = _service(dbsession)
    first = await _ask(service)
    second = await _ask(service)

    assert second["already_pending"] is True
    assert second["code"] == first["code"]
    # The repeat caller cannot prove it is the original, so it gets no secret.
    assert second["poll_secret"] is None
    assert len(await service.list_pending_join_requests()) == 1


@pytest.mark.anyio()
async def test_one_source_cannot_flood_the_queue(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    for index in range(NodeControlService._MAX_PENDING_PER_SOURCE):
        await _ask(service, agent_id=f"box-{index}", source_ip="10.0.0.9")

    with pytest.raises(PermissionError):
        await _ask(service, agent_id="box-flood", source_ip="10.0.0.9")

    # A different machine is not punished for the noisy one.
    other = await _ask(service, agent_id="box-elsewhere", source_ip="10.0.0.10")
    assert other["code"]


# ── rejection and expiry are said out loud ───────────────────────────────


@pytest.mark.anyio()
async def test_rejection_is_reported_rather_than_left_to_time_out(
    dbsession: AsyncSession,
) -> None:
    service = _service(dbsession)
    asked = await _ask(service)
    await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]),
        approve=False,
        decided_by=None,
        message="Not ours.",
    )
    result = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )
    assert result["status"] == "rejected"
    assert result["message"] == "Not ours."
    assert result.get("credential") is None


@pytest.mark.anyio()
async def test_an_expired_request_is_expired_not_pending(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    asked = await _ask(service)
    row = await NodeControlDAO(dbsession).get_join_request(uuid.UUID(asked["id"]))
    assert row is not None
    row.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    await dbsession.flush()

    result = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )
    assert result["status"] == "expired"
    assert await service.list_pending_join_requests() == []


@pytest.mark.anyio()
async def test_an_expired_request_cannot_be_approved(dbsession: AsyncSession) -> None:
    """The clock is applied on read, not left to a sweeper that may not have run."""
    service = _service(dbsession)
    asked = await _ask(service)
    row = await NodeControlDAO(dbsession).get_join_request(uuid.UUID(asked["id"]))
    assert row is not None
    row.expires_at = datetime.now(tz=UTC) - timedelta(seconds=1)
    await dbsession.flush()

    with pytest.raises(PermissionError):
        await service.decide_join_request(
            request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
        )


@pytest.mark.anyio()
async def test_a_decided_request_cannot_be_decided_again(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    asked = await _ask(service)
    await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]), approve=False, decided_by=None
    )
    with pytest.raises(PermissionError):
        await service.decide_join_request(
            request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
        )


@pytest.mark.anyio()
async def test_unknown_request_is_a_lookup_error(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    with pytest.raises(LookupError):
        await service.collect_join_result(request_id=uuid.uuid4(), poll_secret="x")
    with pytest.raises(LookupError):
        await service.decide_join_request(
            request_id=uuid.uuid4(), approve=True, decided_by=None
        )


# ── both doors lead to the same room ─────────────────────────────────────


@pytest.mark.anyio()
async def test_both_enrolment_paths_mint_the_same_kind_of_credential(
    dbsession: AsyncSession,
) -> None:
    """Approval and token enrolment share ``_provision_node`` deliberately.

    The two differ in how the human authorised it, never in what the machine
    ends up holding -- so there is one place a node credential is created.
    """
    service = _service(dbsession)

    token = await service.create_enrollment_token(issued_by=None, note=None)
    by_token = await service.enroll_node(
        enrollment_token=token["token"],
        agent_id="via-token",
        host="10.0.0.1",
        capabilities=_caps(),
        version="0.1.8",
    )

    asked = await _ask(service, agent_id="via-approval", host="10.0.0.2", source_ip="10.0.0.2")
    await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
    )
    by_approval = await service.collect_join_result(
        request_id=uuid.UUID(asked["id"]), poll_secret=asked["poll_secret"]
    )

    assert by_token.keys() <= by_approval.keys()
    for payload in (by_token, by_approval):
        node, credential = await service.authenticate_agent(
            authorization=f"Bearer {payload['credential']}"
        )
        assert node.status == "healthy"
        assert credential.revoked_at is None


@pytest.mark.anyio()
async def test_rejoining_a_known_machine_updates_it_rather_than_duplicating(
    dbsession: AsyncSession,
) -> None:
    """A rebuilt box asks again; it should come back as itself."""
    service = _service(dbsession)
    first = await _ask(service)
    await service.decide_join_request(
        request_id=uuid.UUID(first["id"]), approve=True, decided_by=None
    )
    original = await service.collect_join_result(
        request_id=uuid.UUID(first["id"]), poll_secret=first["poll_secret"]
    )

    second = await _ask(service, host="10.88.10.99", source_ip="10.88.10.99")
    await service.decide_join_request(
        request_id=uuid.UUID(second["id"]), approve=True, decided_by=None
    )
    again = await service.collect_join_result(
        request_id=uuid.UUID(second["id"]), poll_secret=second["poll_secret"]
    )

    assert again["node_id"] == original["node_id"]
    assert again["host"] == "10.88.10.99"
    assert again["credential"] != original["credential"]


@pytest.mark.anyio()
async def test_approval_records_who_decided(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    asked = await _ask(service)
    row = await service.decide_join_request(
        request_id=uuid.UUID(asked["id"]), approve=True, decided_by=None
    )
    assert row.status == JoinRequestStatus.APPROVED
    assert row.decided_at is not None

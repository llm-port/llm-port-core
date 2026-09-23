"""The chat proxy shares one gateway client instead of building one per request.

Building an ``httpx.AsyncClient`` loads the CA bundle into a new SSL context:
~400 ms on the dev workstation, on the event loop. The chat page makes eight
to ten proxy calls per message, so a client per call stalled the backend for
3-5 s on every send -- measured: /api/chat/models took 480 ms alone and 3.9 s
each with eight in flight, against 10 ms at the gateway itself.
"""

from __future__ import annotations

import asyncio

import pytest

from llm_port_backend.services.chat import gateway_client
from llm_port_backend.web.api.chat import views


@pytest.mark.anyio()
async def test_every_request_gets_the_same_client() -> None:
    try:
        assert views._client() is views._client()
    finally:
        await gateway_client.close_shared_client()


@pytest.mark.anyio()
async def test_the_client_builds_its_http_client_once() -> None:
    client = views._client()
    try:
        first = client._ensure_client()
        assert client._ensure_client() is first
    finally:
        await gateway_client.close_shared_client()


@pytest.mark.anyio()
async def test_closing_on_shutdown_releases_it() -> None:
    client = views._client()
    client._ensure_client()
    await gateway_client.close_shared_client()
    assert client._client is None
    assert views._client() is not client, "a later request gets a fresh one"
    await gateway_client.close_shared_client()


def test_each_event_loop_gets_its_own() -> None:
    """A pool belongs to the loop it was first used on."""

    async def grab() -> gateway_client.GatewayChatClient:
        return views._client()

    # Both loops stay alive, so neither can be mistaken for the other.
    loops = [asyncio.new_event_loop() for _ in range(2)]
    try:
        first, second = (loop.run_until_complete(grab()) for loop in loops)
        assert first is not second
        assert loops[0].run_until_complete(grab()) is first
    finally:
        for loop in loops:
            loop.run_until_complete(gateway_client.close_shared_client())
            loop.close()

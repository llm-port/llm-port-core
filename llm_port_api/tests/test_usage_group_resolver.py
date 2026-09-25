"""The usage-group resolver must never cost a request more than a dict lookup."""

from __future__ import annotations

import asyncio

import pytest

from llm_port_api.services.gateway.usage_group import UsageGroupResolver


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Fetch:
    def __init__(self, answers: dict[str, str | None], *, fail: bool = False) -> None:
        self.answers = answers
        self.fail = fail
        self.calls: list[str] = []
        self.gate: asyncio.Event | None = None

    async def __call__(self, user_id: str) -> str | None:
        self.calls.append(user_id)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise ConnectionError("backend db down")
        return self.answers.get(user_id)


@pytest.mark.anyio
async def test_a_hit_is_served_from_cache_without_fetching() -> None:
    fetch = _Fetch({"u1": "g1"})
    resolver = UsageGroupResolver(fetch, clock=_Clock())
    assert await resolver.resolve("u1") == "g1"
    assert await resolver.resolve("u1") == "g1"
    assert fetch.calls == ["u1"]


@pytest.mark.anyio
async def test_a_user_without_a_group_is_cached_as_none() -> None:
    fetch = _Fetch({})
    resolver = UsageGroupResolver(fetch, clock=_Clock())
    assert await resolver.resolve("nobody") is None
    assert await resolver.resolve("nobody") is None
    assert fetch.calls == ["nobody"]


@pytest.mark.anyio
async def test_an_entry_expires_after_the_ttl() -> None:
    clock = _Clock()
    fetch = _Fetch({"u1": "g1"})
    resolver = UsageGroupResolver(fetch, ttl_sec=300, clock=clock)
    await resolver.resolve("u1")
    clock.now += 299
    await resolver.resolve("u1")
    assert fetch.calls == ["u1"]
    clock.now += 2
    await resolver.resolve("u1")
    assert fetch.calls == ["u1", "u1"]


@pytest.mark.anyio
async def test_a_failure_resolves_to_none_and_is_retried_soon() -> None:
    clock = _Clock()
    fetch = _Fetch({"u1": "g1"}, fail=True)
    resolver = UsageGroupResolver(fetch, ttl_sec=300, error_ttl_sec=15, clock=clock)
    assert await resolver.resolve("u1") is None
    clock.now += 10
    assert await resolver.resolve("u1") is None  # still inside the error TTL
    assert fetch.calls == ["u1"]
    fetch.fail = False
    clock.now += 6
    assert await resolver.resolve("u1") == "g1"


@pytest.mark.anyio
async def test_concurrent_misses_for_one_user_share_one_fetch() -> None:
    fetch = _Fetch({"u1": "g1"})
    fetch.gate = asyncio.Event()
    resolver = UsageGroupResolver(fetch, clock=_Clock())
    tasks = [asyncio.create_task(resolver.resolve("u1")) for _ in range(5)]
    await asyncio.sleep(0)
    fetch.gate.set()
    assert await asyncio.gather(*tasks) == ["g1"] * 5
    assert fetch.calls == ["u1"]


@pytest.mark.anyio
async def test_invalidate_forgets_the_user() -> None:
    fetch = _Fetch({"u1": "g1"})
    resolver = UsageGroupResolver(fetch, clock=_Clock())
    await resolver.resolve("u1")
    resolver.invalidate("u1")
    await resolver.resolve("u1")
    assert fetch.calls == ["u1", "u1"]


@pytest.mark.anyio
async def test_the_cache_is_bounded() -> None:
    clock = _Clock()
    fetch = _Fetch({f"u{i}": f"g{i}" for i in range(5)})
    resolver = UsageGroupResolver(fetch, max_entries=3, clock=clock)
    for i in range(5):
        clock.now += 1
        await resolver.resolve(f"u{i}")
    assert len(resolver._cache) <= 3  # noqa: SLF001

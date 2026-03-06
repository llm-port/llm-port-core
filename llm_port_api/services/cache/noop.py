"""NoOpCache — fail-open stub used when Redis is absent.

Behavior
--------
* ``get`` / ``mget`` → ``None`` (cache miss).
* ``set``            → no-op.
* ``incr``           → returns *amount* (as if the counter started at 0).
                       This makes ``current <= limit`` true for any sane
                       rate limit, giving **fail-open** semantics.
* ``eval_lua``       → returns ``"1"`` (lease acquired / always succeed).
* ``close``          → no-op.
"""

from __future__ import annotations

import logging
from typing import Sequence

log = logging.getLogger(__name__)


class NoOpCache:
    """In-memory no-op cache — every read is a miss, every write is silent."""

    def __init__(self) -> None:
        log.info(
            "CacheBackend initialised as NoOpCache — "
            "rate limiting and concurrency leasing are disabled (fail-open).",
        )

    async def get(self, key: str) -> bytes | None:  # noqa: ARG002
        return None

    async def set(  # noqa: ARG002
        self,
        key: str,
        value: bytes | str,
        *,
        ttl_sec: int = 0,
    ) -> None:
        return

    async def incr(  # noqa: ARG002
        self,
        key: str,
        amount: int = 1,
        *,
        ttl_sec: int = 0,
    ) -> int:
        return amount

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:  # noqa: ARG002
        return [None] * len(keys)

    async def eval_lua(  # noqa: ARG002
        self,
        script: str,
        num_keys: int,
        *args: str,
    ) -> str:
        return "1"

    async def close(self) -> None:
        return

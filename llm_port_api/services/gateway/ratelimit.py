from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_port_api.services.cache.protocol import CacheBackend


@dataclass(slots=True, frozen=True)
class RateLimitResult:
    """Result for a single limit check."""

    allowed: bool
    current: int
    limit: int
    retry_after_sec: int


class RateLimiter:
    """Simple fixed-window rate limiter."""

    def __init__(self, cache: CacheBackend) -> None:
        self.cache = cache

    async def _check(
        self,
        *,
        key_prefix: str,
        subject: str,
        amount: int,
        limit: int | None,
        window_sec: int = 60,
    ) -> RateLimitResult | None:
        if limit is None or limit <= 0:
            return None
        now = int(time.time())
        bucket = now // window_sec
        ttl = max(window_sec - (now % window_sec), 1)
        key = f"{key_prefix}:{subject}:{bucket}"
        current = await self.cache.incr(key, amount, ttl_sec=ttl)
        allowed = current <= limit
        retry_after = ttl if not allowed else 0
        return RateLimitResult(
            allowed=allowed,
            current=current,
            limit=limit,
            retry_after_sec=retry_after,
        )

    async def check_rpm(
        self, *, tenant_id: str, limit: int | None,
    ) -> RateLimitResult | None:
        """Check request-per-minute for tenant."""
        return await self._check(
            key_prefix="ratelimit:rpm",
            subject=tenant_id,
            amount=1,
            limit=limit,
        )

    async def check_tpm(
        self,
        *,
        tenant_id: str,
        tokens: int,
        limit: int | None,
    ) -> RateLimitResult | None:
        """Check token-per-minute for tenant."""
        amount = max(int(math.ceil(tokens)), 1)
        return await self._check(
            key_prefix="ratelimit:tpm",
            subject=tenant_id,
            amount=amount,
            limit=limit,
        )

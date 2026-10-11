"""Sliding Window Counter rate limiter algorithm using weighted two-window approximation."""

import redis.asyncio as aioredis
from rate_limiter.base import BaseRateLimiter, RateLimitResult
from rate_limiter.manager import LuaScriptManager


class SlidingWindowCounterLimiter(BaseRateLimiter):
    """Sliding Window Counter algorithm (weighted approximation).

    Maintains current window count and previous window count in a Redis Hash.
    Approximates sliding requests as:
      estimated = (previous_count * (1 - time_into_current / window)) + current_count
    Achieves O(1) memory and O(1) execution time.
    """

    def __init__(self, redis_client: aioredis.Redis, manager: LuaScriptManager | None = None) -> None:
        self.redis = redis_client
        self.manager = manager or LuaScriptManager(redis_client)

    async def allow(self, key: str, limit: int, window: int) -> RateLimitResult:
        """Evaluate sliding window counter approximation atomically via Lua."""
        redis_key = f"rl:swc:{key}"
        raw_res = await self.manager.execute(
            "sliding_window_counter",
            keys=[redis_key],
            args=[limit, window],
        )

        allowed = bool(raw_res[0])
        remaining = int(raw_res[1])
        retry_after = float(raw_res[2])
        reset_at = float(raw_res[3])

        return RateLimitResult(
            allowed=allowed,
            limit=limit,
            remaining=remaining,
            retry_after=retry_after,
            reset_at=reset_at,
        )

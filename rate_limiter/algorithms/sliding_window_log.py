"""Sliding Window Log rate limiter algorithm using Redis Sorted Sets."""

import redis.asyncio as aioredis
from rate_limiter.base import BaseRateLimiter, RateLimitResult
from rate_limiter.manager import LuaScriptManager


class SlidingWindowLogLimiter(BaseRateLimiter):
    """Sliding Window Log algorithm.

    Records each event timestamp as a member in a Redis Sorted Set (ZSET).
    Strictly removes elements older than (now - window) and counts surviving members.
    Gives 100% precision over trailing sliding time window.
    """

    def __init__(self, redis_client: aioredis.Redis, manager: LuaScriptManager | None = None) -> None:
        self.redis = redis_client
        self.manager = manager or LuaScriptManager(redis_client)

    async def allow(self, key: str, limit: int, window: int) -> RateLimitResult:
        """Evaluate sliding window log atomically in Redis via Lua."""
        redis_key = f"rl:swl:{key}"
        raw_res = await self.manager.execute(
            "sliding_window_log",
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

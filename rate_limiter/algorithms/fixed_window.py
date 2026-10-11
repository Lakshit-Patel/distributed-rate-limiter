"""Fixed Window Counter rate limiter algorithm implementation."""

import redis.asyncio as aioredis
from rate_limiter.base import BaseRateLimiter, RateLimitResult
from rate_limiter.manager import LuaScriptManager


class FixedWindowLimiter(BaseRateLimiter):
    """Fixed Window Counter algorithm.

    Divides time into fixed discrete slots of length `window` seconds.
    Maintains a single atomic counter per key in Redis for that window.
    """

    def __init__(self, redis_client: aioredis.Redis, manager: LuaScriptManager | None = None) -> None:
        self.redis = redis_client
        self.manager = manager or LuaScriptManager(redis_client)

    async def allow(self, key: str, limit: int, window: int) -> RateLimitResult:
        """Evaluate fixed window limit atomically in Redis via Lua."""
        # Key namespace partition for fixed window
        redis_key = f"rl:fw:{key}"
        raw_res = await self.manager.execute(
            "fixed_window",
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

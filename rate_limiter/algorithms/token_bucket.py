"""Token Bucket rate limiter algorithm implementation."""

import redis.asyncio as aioredis
from rate_limiter.base import BaseRateLimiter, RateLimitResult
from rate_limiter.manager import LuaScriptManager


class TokenBucketLimiter(BaseRateLimiter):
    """Token Bucket algorithm.

    Allows burst traffic up to `capacity` (`limit`), refilling tokens continuously
    at a steady rate `limit / window` tokens per second.
    """

    def __init__(self, redis_client: aioredis.Redis, manager: LuaScriptManager | None = None) -> None:
        self.redis = redis_client
        self.manager = manager or LuaScriptManager(redis_client)

    async def allow(self, key: str, limit: int, window: int, requested: int = 1) -> RateLimitResult:
        """Evaluate token bucket acquisition atomically in Redis via Lua."""
        redis_key = f"rl:tb:{key}"
        raw_res = await self.manager.execute(
            "token_bucket",
            keys=[redis_key],
            args=[limit, window, requested],
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

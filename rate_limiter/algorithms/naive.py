"""Naive non-atomic GET-then-SET rate limiter.

Created specifically for interview comparison and correctness testing to demonstrate
the catastrophic race conditions that occur under concurrency without atomic Lua scripts.
"""

import asyncio
import time
import redis.asyncio as aioredis
from rate_limiter.base import BaseRateLimiter, RateLimitResult


class NaiveGetSetLimiter(BaseRateLimiter):
    """Naive rate limiter that performs separate GET and SET calls over network.

    Vulnerable to TOCTOU (Time-of-Check to Time-of-Use) race conditions under concurrency:
    Multiple parallel requests read current < limit at the same time before any of them
    can increment the counter, resulting in massive quota leakage.
    """

    def __init__(self, redis_client: aioredis.Redis, simulated_delay: float = 0.001) -> None:
        self.redis = redis_client
        self.simulated_delay = simulated_delay

    async def allow(self, key: str, limit: int, window: int) -> RateLimitResult:
        redis_key = f"rl:naive:{key}"
        
        # 1. Non-atomic GET
        val = await self.redis.get(redis_key)
        current = int(val) if val else 0
        
        # Simulated tiny network latency or event loop yield
        if self.simulated_delay > 0:
            await asyncio.sleep(self.simulated_delay)

        now = time.time()
        reset_at = now + window

        # 2. Check and conditional SET
        if current < limit:
            new_val = current + 1
            await self.redis.set(redis_key, str(new_val), ex=window)
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=limit - new_val,
                retry_after=0.0,
                reset_at=reset_at,
            )
        else:
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                retry_after=float(window),
                reset_at=reset_at,
            )

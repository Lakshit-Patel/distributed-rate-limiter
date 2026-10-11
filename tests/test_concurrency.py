"""Concurrency tests and comparison between atomic Lua scripts vs naive GET-SET."""

import asyncio
import pytest
import redis.asyncio as aioredis
from rate_limiter.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from rate_limiter.algorithms.token_bucket import TokenBucketLimiter
from rate_limiter.algorithms.fixed_window import FixedWindowLimiter
from rate_limiter.algorithms.naive import NaiveGetSetLimiter


@pytest.mark.asyncio
async def test_atomic_lua_concurrency_no_leakage(redis_client: aioredis.Redis):
    """Fire N parallel requests at limit L and assert EXACTLY L are allowed.

    Demonstrates mathematically that atomic Redis Lua scripts prevent race conditions.
    """
    limiter = SlidingWindowCounterLimiter(redis_client)
    limit = 10
    window = 10
    total_concurrent_requests = 50
    key = "concurrent_user_atomic"

    async def make_request():
        return await limiter.allow(key, limit, window)

    # Fire 50 coroutines concurrently using asyncio.gather
    results = await asyncio.gather(*(make_request() for _ in range(total_concurrent_requests)))

    allowed_count = sum(1 for r in results if r.allowed)
    denied_count = sum(1 for r in results if not r.allowed)

    assert allowed_count == limit, f"Expected exactly {limit} allowed, got {allowed_count}"
    assert denied_count == (total_concurrent_requests - limit)


@pytest.mark.asyncio
async def test_token_bucket_concurrency_no_leakage(redis_client: aioredis.Redis):
    """Test Token Bucket under high concurrency strictly enforces burst capacity."""
    limiter = TokenBucketLimiter(redis_client)
    capacity = 15
    window = 10
    total_concurrent = 60
    key = "concurrent_user_tb"

    results = await asyncio.gather(*(limiter.allow(key, capacity, window) for _ in range(total_concurrent)))

    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == capacity


@pytest.mark.asyncio
async def test_naive_get_then_set_race_condition_leakage(redis_client: aioredis.Redis):
    """Demonstrate why Lua scripts are mandatory.

    A naive implementation reading GET and then performing SET over the network
    inevitably suffers from TOCTOU race conditions under concurrency, leaking
    significantly more requests than the configured limit L.
    """
    naive_limiter = NaiveGetSetLimiter(redis_client, simulated_delay=0.002)
    limit = 5
    window = 5
    total_concurrent = 25
    key = "concurrent_user_naive"

    results = await asyncio.gather(*(naive_limiter.allow(key, limit, window) for _ in range(total_concurrent)))

    allowed_count = sum(1 for r in results if r.allowed)

    # In a naive GET-then-SET under concurrency, multiple workers read count < limit
    # before any worker's SET completes. Therefore, allowed_count > limit.
    assert allowed_count > limit, (
        f"Expected naive limiter to leak requests under concurrency, "
        f"but got {allowed_count} (limit: {limit})."
    )

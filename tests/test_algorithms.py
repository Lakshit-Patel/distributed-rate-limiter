"""Unit tests for all 4 rate limiting algorithms running against real Redis."""

import asyncio
import pytest
import redis.asyncio as aioredis
from rate_limiter.algorithms.fixed_window import FixedWindowLimiter
from rate_limiter.algorithms.sliding_window_log import SlidingWindowLogLimiter
from rate_limiter.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from rate_limiter.algorithms.token_bucket import TokenBucketLimiter


@pytest.mark.asyncio
async def test_fixed_window_boundary_and_rollover(redis_client: aioredis.Redis):
    """Test Fixed Window permits up to limit, rejects excess, and rolls over cleanly."""
    limiter = FixedWindowLimiter(redis_client)
    limit = 3
    window = 1
    key = "test_user_fw"

    # 1. First 3 requests allowed
    r1 = await limiter.allow(key, limit, window)
    assert r1.allowed is True
    assert r1.remaining == 2

    r2 = await limiter.allow(key, limit, window)
    assert r2.allowed is True
    assert r2.remaining == 1

    r3 = await limiter.allow(key, limit, window)
    assert r3.allowed is True
    assert r3.remaining == 0

    # 2. 4th request rejected
    r4 = await limiter.allow(key, limit, window)
    assert r4.allowed is False
    assert r4.remaining == 0
    assert r4.retry_after > 0

    # 3. Wait for window rollover
    await asyncio.sleep(1.1)

    # 4. Quota must reset
    r5 = await limiter.allow(key, limit, window)
    assert r5.allowed is True
    assert r5.remaining == 2


@pytest.mark.asyncio
async def test_sliding_window_log_strict_boundaries(redis_client: aioredis.Redis):
    """Test Sliding Window Log precision over trailing time window."""
    limiter = SlidingWindowLogLimiter(redis_client)
    limit = 2
    window = 1
    key = "test_user_swl"

    r1 = await limiter.allow(key, limit, window)
    assert r1.allowed is True
    assert r1.remaining == 1

    r2 = await limiter.allow(key, limit, window)
    assert r2.allowed is True
    assert r2.remaining == 0

    # Immediate next request rejected
    r3 = await limiter.allow(key, limit, window)
    assert r3.allowed is False
    assert r3.retry_after > 0

    # Sleep past window
    await asyncio.sleep(1.1)

    # Allowed again
    r4 = await limiter.allow(key, limit, window)
    assert r4.allowed is True
    assert r4.remaining == 1


@pytest.mark.asyncio
async def test_sliding_window_counter_weighted_decay(redis_client: aioredis.Redis):
    """Test Sliding Window Counter two-window weighted approximation behavior."""
    limiter = SlidingWindowCounterLimiter(redis_client)
    limit = 5
    window = 2
    key = "test_user_swc"

    # Fill current quota
    for i in range(limit):
        res = await limiter.allow(key, limit, window)
        assert res.allowed is True

    # 6th request rejected
    rejected = await limiter.allow(key, limit, window)
    assert rejected.allowed is False
    assert rejected.remaining == 0


@pytest.mark.asyncio
async def test_token_bucket_burst_and_refill(redis_client: aioredis.Redis):
    """Test Token Bucket burst capacity and steady replenishment rate."""
    limiter = TokenBucketLimiter(redis_client)
    capacity = 5
    window = 2  # refill rate = 5 tokens per 2 seconds = 2.5 tokens/sec
    key = "test_user_tb"

    # 1. Burst consumption: take all 5 tokens instantly
    for i in range(capacity):
        res = await limiter.allow(key, capacity, window, requested=1)
        assert res.allowed is True
        assert res.remaining == capacity - 1 - i

    # 2. Bucket empty
    empty_res = await limiter.allow(key, capacity, window, requested=1)
    assert empty_res.allowed is False
    assert empty_res.retry_after > 0

    # 3. Wait 0.8 seconds -> should replenish ~2 tokens
    await asyncio.sleep(0.8)

    refilled_res = await limiter.allow(key, capacity, window, requested=1)
    assert refilled_res.allowed is True

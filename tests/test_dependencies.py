"""Tests for FastAPI route-level rate limiting dependencies."""

import pytest
from fastapi import FastAPI, Depends, Request
from httpx import AsyncClient, ASGITransport
import redis.asyncio as aioredis

from rate_limiter.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from rate_limiter.middleware.depends import rate_limit


@pytest.mark.asyncio
async def test_route_dependency_allows_and_rejects(redis_client: aioredis.Redis):
    """Default key function allows requests up to the limit, then returns 429."""
    app = FastAPI()
    app.state.trusted_proxies = {"127.0.0.1"}

    limiter = SlidingWindowCounterLimiter(redis_client)

    @app.get("/protected")
    async def protected(
        request: Request,
        result=Depends(rate_limit(limiter, limit=2, window=60)),
    ):
        return {
            "remaining": result.remaining,
            "stored_remaining": request.state.rate_limit_result.remaining,
        }

    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-User-ID": "dependency-test-user"}

        first = await client.get("/protected", headers=headers)
        assert first.status_code == 200
        assert first.json()["remaining"] == 1
        assert first.json()["stored_remaining"] == 1

        second = await client.get("/protected", headers=headers)
        assert second.status_code == 200
        assert second.json()["remaining"] == 0

        third = await client.get("/protected", headers=headers)
        assert third.status_code == 429
        assert third.json()["detail"] == "Rate limit exceeded: 2 requests per 60s."
        assert third.headers["X-RateLimit-Limit"] == "2"
        assert third.headers["X-RateLimit-Remaining"] == "0"
        assert "X-RateLimit-Reset" in third.headers
        assert int(third.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_route_dependency_custom_key_function(redis_client: aioredis.Redis):
    """Custom key function groups requests independently of X-User-ID."""
    app = FastAPI()
    limiter = SlidingWindowCounterLimiter(redis_client)

    def tenant_key(request: Request) -> str:
        return f"tenant:{request.headers.get('X-Tenant-ID', 'unknown')}"

    @app.get("/tenant")
    async def tenant_endpoint(
        result=Depends(
            rate_limit(
                limiter,
                limit=1,
                window=60,
                key_func=tenant_key,
            )
        ),
    ):
        return {"remaining": result.remaining}

    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/tenant",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"},
        )
        assert first.status_code == 200
        assert first.json()["remaining"] == 0

        second = await client.get(
            "/tenant",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-2"},
        )
        assert second.status_code == 429

        third = await client.get(
            "/tenant",
            headers={"X-Tenant-ID": "tenant-b", "X-User-ID": "user-1"},
        )
        assert third.status_code == 200

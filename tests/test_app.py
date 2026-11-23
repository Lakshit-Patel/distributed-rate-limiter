"""Integration tests for the actual FastAPI application."""

import importlib

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_application_routes_and_rate_limits(redis_client, monkeypatch):
    """Exercise real application routes against isolated test Redis."""

    host = redis_client.connection_pool.connection_kwargs["host"]
    port = redis_client.connection_pool.connection_kwargs["port"]

    monkeypatch.setenv("REDIS_URL", f"redis://{host}:{port}/0")
    monkeypatch.setenv("INSTANCE_ID", "test-api-instance")
    monkeypatch.setenv("RATE_LIMIT_FAIL_POLICY", "fail_open")

    monkeypatch.setattr(
        aioredis,
        "from_url",
        lambda url, **kwargs: aioredis.Redis.from_url(
            url, protocol=2, **kwargs
        ),
    )

    import rate_limiter.app as application
    application = importlib.reload(application)

    await application.redis_client.flushall()

    async with application.app.router.lifespan_context(application.app):
        transport = ASGITransport(
            app=application.app,
            client=("127.0.0.1", 50000),
        )

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {
                "status": "ok",
                "instance_id": "test-api-instance",
            }
            assert "X-RateLimit-Limit" not in health.headers

            demo = await client.get(
                "/api/v1/demo-cluster",
                headers={"X-User-ID": "demo-user"},
            )
            assert demo.status_code == 200
            assert demo.json()["served_by_instance"] == "test-api-instance"
            assert demo.headers["X-RateLimit-Limit"] == "10"

            resource_headers = {"X-User-ID": "resource-test-user"}

            for _ in range(10):
                response = await client.get(
                    "/api/v1/resource",
                    headers=resource_headers,
                )
                assert response.status_code == 200

            rejected = await client.get(
                "/api/v1/resource",
                headers=resource_headers,
            )
            assert rejected.status_code == 429
            assert rejected.headers["X-RateLimit-Limit"] == "10"

            strict_headers = {"X-User-ID": "strict-test-user"}

            for _ in range(3):
                response = await client.get(
                    "/api/v1/strict-tier",
                    headers=strict_headers,
                )
                assert response.status_code == 200

            strict_rejected = await client.get(
                "/api/v1/strict-tier",
                headers=strict_headers,
            )
            assert strict_rejected.status_code == 429

            burst = await client.get(
                "/api/v1/burst-tier",
                headers={"X-User-ID": "burst-test-user"},
            )
            assert burst.status_code == 200
            assert burst.json()["tier"] == "token-bucket-burst"
            assert burst.json()["tokens_left"] == 19

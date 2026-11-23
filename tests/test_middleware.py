"""Tests for FastAPI middleware, headers, trusted proxy spoofing, and failure policies."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
import redis.asyncio as aioredis
from rate_limiter.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from rate_limiter.middleware.middleware import (
    RateLimiterMiddleware,
    FailurePolicy,
    extract_client_ip,
    default_key_func,
)


@pytest.mark.asyncio
async def test_middleware_headers_and_429(redis_client: aioredis.Redis):
    """Test rate limiting middleware attaches standard headers and emits 429 when exhausted."""
    app = FastAPI()
    limiter = SlidingWindowCounterLimiter(redis_client)

    app.add_middleware(
        RateLimiterMiddleware,
        limiter=limiter,
        limit=2,
        window=5,
        trusted_proxies=["127.0.0.1"],
    )

    @app.get("/test")
    async def endpoint():
        return {"ok": True}

    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Req 1: Allowed
        res1 = await client.get("/test")
        assert res1.status_code == 200
        assert res1.headers["X-RateLimit-Limit"] == "2"
        assert res1.headers["X-RateLimit-Remaining"] == "1"
        assert "X-RateLimit-Reset" in res1.headers

        # Req 2: Allowed
        res2 = await client.get("/test")
        assert res2.status_code == 200
        assert res2.headers["X-RateLimit-Remaining"] == "0"

        # Req 3: Denied 429
        res3 = await client.get("/test")
        assert res3.status_code == 429
        assert "Retry-After" in res3.headers
        assert res3.headers["X-RateLimit-Remaining"] == "0"
        data = res3.json()
        assert data["error"] == "Too Many Requests"


@pytest.mark.asyncio
async def test_untrusted_spoofed_ip_header_is_ignored():
    """Security Test: A client sending a spoofed X-Forwarded-For or CF-Connecting-IP

    from an untrusted socket peer MUST NOT change its rate-limit key.
    """
    from starlette.requests import Request

    class DummyClient:
        host = "198.51.100.25"  # Untrusted external public IP
        port = 45678

    scope = {
        "type": "http",
        "client": ("198.51.100.25", 45678),
        "headers": [
            (b"x-forwarded-for", b"203.0.113.199"),  # Attacker attempting to spoof victim IP
            (b"cf-connecting-ip", b"203.0.113.200"),
        ],
    }
    req = Request(scope)

    trusted_proxies = {"10.0.0.1", "127.0.0.1"}

    # Client IP extractor must reject headers and use socket peer
    extracted = extract_client_ip(req, trusted_proxies)
    assert extracted == "198.51.100.25"
    assert extracted != "203.0.113.199"
    assert extracted != "203.0.113.200"

    # Now verify trusted proxy scenario
    scope_trusted = {
        "type": "http",
        "client": ("10.0.0.1", 12345),  # Direct peer is trusted reverse proxy
        "headers": [
            (b"x-forwarded-for", b"203.0.113.50, 10.0.0.1"),
        ],
    }
    req_trusted = Request(scope_trusted)
    extracted_trusted = extract_client_ip(req_trusted, trusted_proxies)
    assert extracted_trusted == "203.0.113.50"


@pytest.mark.asyncio
async def test_redis_down_fail_open_policy():
    """Test that when Redis is unreachable and policy is FAIL_OPEN, requests proceed with degraded header."""
    # Connect to unreachable port
    dead_client = aioredis.from_url("redis://127.0.0.1:59999/0", socket_timeout=0.05)
    limiter = SlidingWindowCounterLimiter(dead_client)

    app = FastAPI()
    app.add_middleware(
        RateLimiterMiddleware,
        limiter=limiter,
        limit=10,
        window=60,
        failure_policy=FailurePolicy.FAIL_OPEN,
        redis_timeout_sec=0.05,
    )

    @app.get("/guarded")
    async def guarded():
        return {"status": "success"}

    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/guarded")
        assert res.status_code == 200
        assert res.json() == {"status": "success"}
        assert res.headers.get("X-RateLimit-Degraded") == "true"


@pytest.mark.asyncio
async def test_redis_down_fail_closed_policy():
    """Test that when Redis is unreachable and policy is FAIL_CLOSED, requests are rejected with 429."""
    dead_client = aioredis.from_url("redis://127.0.0.1:59999/0", socket_timeout=0.05)
    limiter = SlidingWindowCounterLimiter(dead_client)

    app = FastAPI()
    app.add_middleware(
        RateLimiterMiddleware,
        limiter=limiter,
        limit=10,
        window=60,
        failure_policy=FailurePolicy.FAIL_CLOSED,
        redis_timeout_sec=0.05,
    )

    @app.get("/guarded")
    async def guarded():
        return {"status": "success"}

    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/guarded")
        assert res.status_code == 429
        assert "Rate limiter service unavailable" in res.json()["error"]


@pytest.mark.asyncio
async def test_middleware_custom_key_function(redis_client):
    """Middleware uses the configured custom key function."""
    app = FastAPI()

    limiter = SlidingWindowCounterLimiter(redis_client)

    def custom_key(request):
        return f"custom:{request.headers.get('X-Tenant-ID', 'unknown')}"

    app.add_middleware(
        RateLimiterMiddleware,
        limiter=limiter,
        limit=1,
        window=60,
        key_func=custom_key,
    )

    @app.get("/")
    async def root():
        return {"ok": True}

    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/", headers={"X-Tenant-ID": "tenant-a"})
        second = await client.get("/", headers={"X-Tenant-ID": "tenant-a"})
        third = await client.get("/", headers={"X-Tenant-ID": "tenant-b"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert third.status_code == 200


@pytest.mark.asyncio
async def test_middleware_excluded_path_skips_limiter(redis_client):
    """Excluded paths must not consume Redis quota."""
    app = FastAPI()

    class ShouldNotBeCalledLimiter:
        async def allow(self, *args, **kwargs):
            raise AssertionError("Limiter should not run for excluded paths")

    app.add_middleware(
        RateLimiterMiddleware,
        limiter=ShouldNotBeCalledLimiter(),
        limit=1,
        window=60,
        exclude_paths=["/health"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_trusted_proxy_cidr_range():
    """A peer inside a trusted CIDR range may supply forwarded client IP."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "client": ("10.0.0.5", 12345),
        "headers": [
            (b"x-forwarded-for", b"203.0.113.25"),
        ],
    }

    request = Request(scope)

    result = extract_client_ip(
        request,
        {"10.0.0.0/24"},
    )

    assert result == "203.0.113.25"


def test_cf_connecting_ip_takes_precedence():
    """CF-Connecting-IP takes precedence over X-Forwarded-For."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "client": ("10.0.0.5", 12345),
        "headers": [
            (b"cf-connecting-ip", b"203.0.113.30"),
            (b"x-forwarded-for", b"203.0.113.40"),
        ],
    }

    request = Request(scope)

    result = extract_client_ip(
        request,
        {"10.0.0.0/24"},
    )

    assert result == "203.0.113.30"


def test_api_key_takes_precedence_over_user_id():
    """API key must be used when both API key and user ID are present."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "client": ("198.51.100.10", 12345),
        "headers": [
            (b"x-api-key", b"test-api-key"),
            (b"x-user-id", b"test-user"),
        ],
    }

    request = Request(scope)
    result = default_key_func(request, set())
    assert result == "apikey:test-api-key"

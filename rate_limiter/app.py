"""Demo FastAPI Application showcasing distributed rate limiting across instances."""

import os
import socket
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
import redis.asyncio as aioredis

from rate_limiter.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from rate_limiter.algorithms.token_bucket import TokenBucketLimiter
from rate_limiter.algorithms.fixed_window import FixedWindowLimiter
from rate_limiter.algorithms.sliding_window_log import SlidingWindowLogLimiter
from rate_limiter.middleware.middleware import RateLimiterMiddleware, FailurePolicy
from rate_limiter.middleware.depends import rate_limit

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INSTANCE_ID = os.getenv("INSTANCE_ID", socket.gethostname())
FAIL_POLICY = os.getenv("RATE_LIMIT_FAIL_POLICY", "fail_open")
TRUSTED_PROXIES = os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1,nginx").split(",")

redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
default_limiter = SlidingWindowCounterLimiter(redis_client)
token_bucket_limiter = TokenBucketLimiter(redis_client)
fixed_window_limiter = FixedWindowLimiter(redis_client)
sliding_log_limiter = SlidingWindowLogLimiter(redis_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup state
    app.state.trusted_proxies = set(TRUSTED_PROXIES)
    yield
    await redis_client.aclose()


app = FastAPI(
    title="Distributed Rate Limiter Showcase API",
    version="1.0.0",
    lifespan=lifespan,
)

# Attach global rate limiting middleware: default 10 requests per 10 seconds
app.add_middleware(
    RateLimiterMiddleware,
    limiter=default_limiter,
    limit=10,
    window=10,
    failure_policy=FailurePolicy(FAIL_POLICY),
    trusted_proxies=TRUSTED_PROXIES,
    exclude_paths=["/health", "/docs", "/openapi.json"],
)


@app.get("/health")
async def health():
    """Health check endpoint, excluded from rate limiting."""
    return {"status": "ok", "instance_id": INSTANCE_ID}


@app.get("/api/v1/resource")
async def get_resource(request: Request):
    """Standard protected resource governed by global middleware."""
    return {
        "message": "Access granted to resource.",
        "instance_id": INSTANCE_ID,
        "client": request.client.host if request.client else "unknown",
    }


@app.get("/api/v1/strict-tier")
async def strict_tier(
    result=Depends(rate_limit(sliding_log_limiter, limit=3, window=5)),
):
    """Strictly rate-limited endpoint using Sliding Window Log: 3 req / 5s."""
    return {
        "tier": "strict-sliding-log",
        "instance_id": INSTANCE_ID,
        "remaining": result.remaining,
    }


@app.get("/api/v1/burst-tier")
async def burst_tier(
    result=Depends(rate_limit(token_bucket_limiter, limit=20, window=10)),
):
    """Burst-capable endpoint using Token Bucket: 20 tokens refilling over 10s."""
    return {
        "tier": "token-bucket-burst",
        "instance_id": INSTANCE_ID,
        "tokens_left": result.remaining,
    }


@app.get("/api/v1/demo-cluster")
async def demo_cluster(request: Request):
    """Endpoint demonstrating that distinct instances share the same distributed Redis quota."""
    return {
        "cluster_demo": "Identical quota shared across instances",
        "served_by_instance": INSTANCE_ID,
    }

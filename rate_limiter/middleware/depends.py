"""FastAPI rate limiting dependencies for granular per-route limits."""

from typing import Callable, Optional
from fastapi import Request, HTTPException, Depends
from rate_limiter.base import BaseRateLimiter
from rate_limiter.middleware.middleware import default_key_func


def rate_limit(
    limiter: BaseRateLimiter,
    limit: int,
    window: int,
    key_func: Optional[Callable[[Request], str]] = None,
):
    """FastAPI route-level dependency to enforce specific limits on individual routes."""

    async def dependency(request: Request):
        trusted_proxies = getattr(request.app.state, "trusted_proxies", {"127.0.0.1", "::1"})
        key = key_func(request) if key_func else default_key_func(request, trusted_proxies)

        result = await limiter.allow(key, limit, window)
        if not result.allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {limit} requests per {window}s.",
                headers={
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": str(result.remaining),
                    "X-RateLimit-Reset": str(int(result.reset_at)),
                    "Retry-After": str(max(1, int(result.retry_after))),
                },
            )
        request.state.rate_limit_result = result
        return result

    return dependency

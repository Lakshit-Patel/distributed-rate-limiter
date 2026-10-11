"""FastAPI rate limiting configuration, dependency, and middleware."""

from enum import Enum
from typing import Callable, Optional, Set, List
import logging
import ipaddress
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from starlette.types import ASGIApp
import redis.asyncio as aioredis

from rate_limiter.base import BaseRateLimiter, RateLimitResult
from rate_limiter.algorithms.sliding_window_counter import SlidingWindowCounterLimiter

logger = logging.getLogger("rate_limiter")


class FailurePolicy(str, Enum):
    """Failure policies when Redis is unavailable or times out."""
    FAIL_OPEN = "fail_open"
    FAIL_CLOSED = "fail_closed"


def extract_client_ip(request: Request, trusted_proxies: Set[str]) -> str:
    """Extract client IP, strictly validating proxy trust to defeat header spoofing.

    Security Rule:
    Only trust X-Forwarded-For or CF-Connecting-IP if the direct socket peer (request.client.host)
    is explicitly in trusted_proxies. If an untrusted direct peer sends an X-Forwarded-For
    header, IGNORE IT and bind directly to the socket peer address.
    """
    peer_ip = request.client.host if request.client else "127.0.0.1"

    is_trusted = False
    for trusted in trusted_proxies:
        try:
            if "/" in trusted:
                if ipaddress.ip_address(peer_ip) in ipaddress.ip_network(trusted):
                    is_trusted = True
                    break
            else:
                if peer_ip == trusted:
                    is_trusted = True
                    break
        except ValueError:
            if peer_ip == trusted:
                is_trusted = True
                break

    if not is_trusted:
        return peer_ip

    # Peer is a trusted reverse proxy (e.g., Nginx, Cloudflare)
    # Check CF-Connecting-IP first
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    # Check X-Forwarded-For: leftmost entry represents original client
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[0]

    return peer_ip


def default_key_func(request: Request, trusted_proxies: Set[str]) -> str:
    """Default key extractor: extracts API Key, User-ID header, or verified IP."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key}"

    user_id = request.headers.get("X-User-ID")
    if user_id:
        return f"user:{user_id}"

    client_ip = extract_client_ip(request, trusted_proxies)
    return f"ip:{client_ip}"


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """ASGI Middleware to enforce distributed rate limits on incoming HTTP requests.

    Attaches standard IETF/RFC rate limiting headers:
      - X-RateLimit-Limit
      - X-RateLimit-Remaining
      - X-RateLimit-Reset
      - Retry-After (on HTTP 429)
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: BaseRateLimiter,
        limit: int = 100,
        window: int = 60,
        failure_policy: FailurePolicy = FailurePolicy.FAIL_OPEN,
        redis_timeout_sec: float = 0.1,
        trusted_proxies: Optional[List[str]] = None,
        key_func: Optional[Callable[[Request], str]] = None,
        exclude_paths: Optional[List[str]] = None,
    ) -> None:
        super().__init__(app)
        self.limiter = limiter
        self.limit = limit
        self.window = window
        self.failure_policy = failure_policy
        self.redis_timeout_sec = redis_timeout_sec
        self.trusted_proxies = set(trusted_proxies or ["127.0.0.1", "::1"])
        self.key_func = key_func
        self.exclude_paths = set(exclude_paths or ["/health", "/docs", "/openapi.json"])

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for health-checks and documentation
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Allow route-level overrides if previously populated
        limit = getattr(request.state, "rate_limit", self.limit)
        window = getattr(request.state, "rate_window", self.window)

        # Resolve rate limiting key
        if self.key_func:
            key = self.key_func(request)
        else:
            key = default_key_func(request, self.trusted_proxies)

        # Evaluate rate limiter with safety timeout and failure policy
        result: Optional[RateLimitResult] = None
        degraded = False

        try:
            # Wrap execution with short timeout to protect API latency SLA
            import asyncio
            result = await asyncio.wait_for(
                self.limiter.allow(key, limit, window),
                timeout=self.redis_timeout_sec,
            )
        except Exception as exc:
            # Redis is unreachable, timed out, or crashed
            degraded = True
            logger.warning(
                f"[RateLimiter] Redis failure for key '{key}' policy={self.failure_policy}: {exc}"
            )
            if self.failure_policy == FailurePolicy.FAIL_CLOSED:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "Rate limiter service unavailable (fail-closed)",
                        "detail": "Upstream rate limit backend unreachable; request denied for backend protection.",
                    },
                    headers={"Retry-After": "5"},
                )
            # FAIL_OPEN: proceed through to backend handler
            result = RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=1,
                retry_after=0.0,
                reset_at=0.0,
            )

        if not result.allowed:
            headers = {
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(result.remaining),
                "X-RateLimit-Reset": str(int(result.reset_at)),
                "Retry-After": str(max(1, int(result.retry_after))),
            }
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "detail": f"Rate limit quota exceeded ({limit} req / {window}s).",
                    "retry_after_seconds": result.retry_after,
                },
                headers=headers,
            )

        # Proceed with request
        response: Response = await call_next(request)

        # Attach rate limiting headers
        response.headers["X-RateLimit-Limit"] = str(result.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        response.headers["X-RateLimit-Reset"] = str(int(result.reset_at))
        if degraded:
            response.headers["X-RateLimit-Degraded"] = "true"

        return response

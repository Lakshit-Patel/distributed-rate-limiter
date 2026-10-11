"""Middleware module exports."""

from rate_limiter.middleware.middleware import (
    RateLimiterMiddleware,
    FailurePolicy,
    extract_client_ip,
    default_key_func,
)
from rate_limiter.middleware.depends import rate_limit

__all__ = [
    "RateLimiterMiddleware",
    "FailurePolicy",
    "extract_client_ip",
    "default_key_func",
    "rate_limit",
]

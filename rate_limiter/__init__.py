"""Rate Limiter core package exports."""

from rate_limiter.base import BaseRateLimiter, RateLimitResult
from rate_limiter.manager import LuaScriptManager
from rate_limiter.algorithms.fixed_window import FixedWindowLimiter
from rate_limiter.algorithms.sliding_window_log import SlidingWindowLogLimiter
from rate_limiter.algorithms.sliding_window_counter import SlidingWindowCounterLimiter
from rate_limiter.algorithms.token_bucket import TokenBucketLimiter
from rate_limiter.algorithms.naive import NaiveGetSetLimiter
from rate_limiter.middleware.middleware import RateLimiterMiddleware, FailurePolicy

__version__ = "0.1.0"

__all__ = [
    "BaseRateLimiter",
    "RateLimitResult",
    "LuaScriptManager",
    "FixedWindowLimiter",
    "SlidingWindowLogLimiter",
    "SlidingWindowCounterLimiter",
    "TokenBucketLimiter",
    "NaiveGetSetLimiter",
    "RateLimiterMiddleware",
    "FailurePolicy",
]

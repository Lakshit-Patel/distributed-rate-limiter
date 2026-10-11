"""Core data models and abstract interfaces for distributed rate limiting."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class RateLimitResult:
    """Immutable result object returned by all rate limiting algorithms.

    Attributes:
        allowed: True if request was admitted under the rate threshold, False otherwise.
        limit: The quota allowed within the configured time window.
        remaining: Units or tokens remaining for this client in the current window.
        retry_after: Seconds the client must wait before retrying (0.0 if allowed).
        reset_at: Unix epoch timestamp in seconds when quota fully resets.
    """
    allowed: bool
    limit: int
    remaining: int
    retry_after: float
    reset_at: float


class BaseRateLimiter(ABC):
    """Abstract Base Class providing a uniform API across all rate limiting strategies.

    Any algorithm implementation (Fixed Window, Sliding Window Log, Sliding Window Counter,
    Token Bucket) must implement the `allow` coroutine and return a RateLimitResult.
    """

    @abstractmethod
    async def allow(self, key: str, limit: int, window: int) -> RateLimitResult:
        """Evaluate if an action identified by `key` is allowed.

        Args:
            key: Unique identifier for the client entity (e.g., 'rl:user:123' or 'rl:ip:10.0.0.1').
            limit: Maximum requests or tokens permitted per window.
            window: Time window size in integer seconds.

        Returns:
            RateLimitResult with evaluation decision and metadata headers.
        """
        raise NotImplementedError

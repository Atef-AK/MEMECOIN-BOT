"""
Rate limiter for external API calls with per-provider tracking.
"""

from __future__ import annotations

import asyncio
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token-bucket rate limiter for API calls."""

    def __init__(self, max_requests: int, period_seconds: float) -> None:
        self.max_requests = max_requests
        self.period = period_seconds
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request slot is available."""
        async with self._lock:
            now = time.monotonic()
            # Remove expired timestamps
            self._timestamps = [
                ts for ts in self._timestamps if now - ts < self.period
            ]

            if len(self._timestamps) >= self.max_requests:
                # Wait until the oldest request expires
                wait_time = self.period - (now - self._timestamps[0])
                if wait_time > 0:
                    logger.debug(f"Rate limit: waiting {wait_time:.2f}s")
                    await asyncio.sleep(wait_time)

            self._timestamps.append(time.monotonic())

    @property
    def remaining(self) -> int:
        now = time.monotonic()
        active = [ts for ts in self._timestamps if now - ts < self.period]
        return max(0, self.max_requests - len(active))


class RateLimiterRegistry:
    """Registry of rate limiters for different API providers."""

    def __init__(self) -> None:
        self._limiters: dict[str, RateLimiter] = {}

    def register(self, provider: str, max_requests: int, period_seconds: float) -> None:
        self._limiters[provider] = RateLimiter(max_requests, period_seconds)

    def get(self, provider: str) -> RateLimiter:
        if provider not in self._limiters:
            # Default: 10 requests per second
            self._limiters[provider] = RateLimiter(10, 1.0)
        return self._limiters[provider]

    async def acquire(self, provider: str) -> None:
        await self.get(provider).acquire()


# Default registry with known provider limits
_registry = RateLimiterRegistry()

# DEX Screener: 300/min for pairs/tokens
_registry.register("dexscreener", 250, 60.0)  # Leave headroom

# Solana RPC: 5 requests per second for public RPC
_registry.register("solana_rpc", 5, 1.0)

# Jupiter: varies by tier
_registry.register("jupiter", 50, 1.0)

# Telegram: 30 messages per second
_registry.register("telegram", 25, 1.0)



def get_rate_limiter_registry() -> RateLimiterRegistry:
    return _registry

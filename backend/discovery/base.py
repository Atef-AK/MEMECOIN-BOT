"""
Abstract base for pluggable token discovery providers.
"""

from __future__ import annotations

import abc
from typing import AsyncIterator

from backend.models.token import DiscoveredToken


class BaseDiscoveryProvider(abc.ABC):
    """
    Abstract base class for token discovery providers.
    Implementations can source tokens from DEX Screener, on-chain events, etc.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Provider name for logging and metrics."""
        ...

    @abc.abstractmethod
    async def start(self) -> None:
        """Start the discovery provider (connect to APIs, open streams)."""
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop the discovery provider and clean up resources."""
        ...

    @abc.abstractmethod
    async def discover(self) -> AsyncIterator[DiscoveredToken]:
        """Yield newly discovered tokens."""
        ...
        yield  # type: ignore

    async def health_check(self) -> bool:
        """Check if the provider is healthy."""
        return True

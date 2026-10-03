"""
Discovery manager that orchestrates multiple discovery providers,
deduplicates tokens, and feeds them into the analysis pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Coroutine, Any

from backend.config.settings import get_settings
from backend.core.kill_switch import get_kill_switch
from backend.models.token import DiscoveredToken

from .base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)


class DiscoveryManager:
    """
    Manages multiple discovery providers.
    Deduplicates by mint address and filters by token age.
    """

    def __init__(self) -> None:
        self._providers: list[BaseDiscoveryProvider] = []
        self._seen_mints: set[str] = set()
        self._running = False
        self._on_token_callbacks: list[Callable[[DiscoveredToken], Coroutine[Any, Any, None]]] = []
        self._poll_interval: float = 0.5  # seconds between discovery checks for instant sub-second sniping
        self._stats = {
            "total_discovered": 0,
            "duplicates_skipped": 0,
            "age_filtered": 0,
        }

    def register_provider(self, provider: BaseDiscoveryProvider) -> None:
        """Register a discovery provider."""
        self._providers.append(provider)
        logger.info(f"Registered discovery provider: {provider.name}")

    def on_token_discovered(
        self, callback: Callable[[DiscoveredToken], Coroutine[Any, Any, None]]
    ) -> None:
        """Register a callback for newly discovered tokens."""
        self._on_token_callbacks.append(callback)

    async def start(self) -> None:
        """Start all discovery providers."""
        self._running = True
        for provider in self._providers:
            try:
                await provider.start()
            except Exception as e:
                logger.error(f"Failed to start provider {provider.name}: {e}")

        logger.info(
            f"Discovery manager started with {len(self._providers)} providers"
        )

    async def stop(self) -> None:
        """Stop all discovery providers."""
        self._running = False
        for provider in self._providers:
            try:
                await provider.stop()
            except Exception as e:
                logger.error(f"Failed to stop provider {provider.name}: {e}")

        logger.info("Discovery manager stopped")

    async def run(self) -> None:
        """Main discovery loop running all providers concurrently."""
        tasks = [
            asyncio.create_task(self._run_provider(provider))
            for provider in self._providers
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()

    async def _run_provider(self, provider: BaseDiscoveryProvider) -> None:
        """Run discovery loop for an individual provider."""
        settings = get_settings()
        kill_switch = get_kill_switch()

        while self._running:
            if kill_switch.is_active:
                await asyncio.sleep(1.0)
                continue

            try:
                async for token in provider.discover():
                    if not self._running:
                        return

                    # Deduplicate
                    if token.mint_address in self._seen_mints:
                        self._stats["duplicates_skipped"] += 1
                        continue

                    # Age filter
                    age = self._calculate_age(token)
                    if age is not None:
                        if age < settings.min_token_age_seconds:
                            self._stats["age_filtered"] += 1
                            continue
                        if age > settings.max_token_age_seconds:
                            self._stats["age_filtered"] += 1
                            continue

                    self._seen_mints.add(token.mint_address)
                    self._stats["total_discovered"] += 1

                    # Limit seen set size
                    if len(self._seen_mints) > 50000:
                        to_remove = list(self._seen_mints)[:25000]
                        for m in to_remove:
                            self._seen_mints.discard(m)

                    # Notify callbacks
                    for callback in self._on_token_callbacks:
                        try:
                            await callback(token)
                        except Exception as e:
                            logger.error(
                                f"Discovery callback error: {e}", exc_info=True
                            )

            except Exception as e:
                logger.error(
                    f"Discovery error from {provider.name}: {e}",
                    exc_info=True,
                )

            # Responsive polling: 0.1s for WS queue, standard interval for REST
            poll_time = 0.1 if "ws" in provider.name else self._poll_interval
            await asyncio.sleep(poll_time)

    def _calculate_age(self, token: DiscoveredToken) -> float | None:
        """Calculate token age in seconds. Returns None if unknown."""
        if token.created_at:
            now = datetime.now(timezone.utc)
            created = token.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return (now - created).total_seconds()
        return None

    @property
    def stats(self) -> dict:
        return {
            **self._stats,
            "seen_mints": len(self._seen_mints),
            "providers": len(self._providers),
            "running": self._running,
        }

    async def health_check(self) -> dict:
        """Check health of all providers."""
        results = {}
        for provider in self._providers:
            try:
                results[provider.name] = await provider.health_check()
            except Exception:
                results[provider.name] = False
        return results

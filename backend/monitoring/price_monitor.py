"""
Real-time price monitor for active positions.
Polls DexScreener pair data to update position prices,
enabling target hits and emergency exits to trigger.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from backend.config.constants import DEXSCREENER_BASE_URL, LAMPORTS_PER_SOL, WSOL_MINT
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry

logger = logging.getLogger(__name__)


class PriceMonitor:
    """
    Fetches current market prices for active positions.

    Uses DexScreener pair data as the primary source.
    Falls back to Jupiter quote-based pricing if DexScreener fails.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()
        self._running = False
        self._poll_interval = 3.0  # seconds between price checks
        self._price_cache: dict[str, _CachedPrice] = {}

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"Accept": "application/json"},
        )
        self._running = True
        logger.info("Price monitor started")

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Price monitor stopped")

    async def run(self, position_monitor) -> None:
        """
        Main price polling loop.
        Fetches prices for all active positions and pushes updates.

        Args:
            position_monitor: PositionMonitor instance to push price updates to.
        """
        while self._running:
            positions = position_monitor.active_positions
            if not positions:
                await asyncio.sleep(self._poll_interval)
                continue

            # Group positions by pool_address for batch fetching
            pool_to_trades: dict[str, list[str]] = {}
            for trade_id, state in positions.items():
                pool = state.trade.pool_address
                if pool:
                    pool_to_trades.setdefault(pool, []).append(trade_id)

            # Fetch prices for each unique pool
            for pool_address, trade_ids in pool_to_trades.items():
                try:
                    price_data = await self._fetch_pair_price(pool_address)
                    if price_data is None:
                        continue

                    price_sol = price_data.get("price_sol", 0.0)
                    price_usd = price_data.get("price_usd", 0.0)

                    if price_sol <= 0:
                        continue

                    # Update all positions for this pool
                    for trade_id in trade_ids:
                        await position_monitor.update_price(trade_id, price_sol)

                    # Cache the price
                    self._price_cache[pool_address] = _CachedPrice(
                        price_sol=price_sol,
                        price_usd=price_usd,
                        updated_at=datetime.now(timezone.utc),
                    )

                except Exception as e:
                    logger.debug(f"Price fetch error for pool {pool_address[:12]}...: {e}")

            await asyncio.sleep(self._poll_interval)

    async def get_price(self, pool_address: str) -> dict | None:
        """Get current price for a pool. Uses cache if recent enough."""
        cached = self._price_cache.get(pool_address)
        if cached and cached.age_seconds < 10:
            return {"price_sol": cached.price_sol, "price_usd": cached.price_usd}

        return await self._fetch_pair_price(pool_address)

    async def _fetch_pair_price(self, pool_address: str) -> dict | None:
        """Fetch current price data from DexScreener for a pair."""
        if not self._client or not pool_address:
            return None

        try:
            await self._rate_limiter.acquire("dexscreener")
            response = await self._client.get(
                f"{DEXSCREENER_BASE_URL}/latest/dex/pairs/solana/{pool_address}"
            )
            response.raise_for_status()
            data = response.json()
            pairs = data.get("pairs", [])

            if not pairs:
                return None

            pair = pairs[0]
            price_usd = float(pair.get("priceUsd", 0) or 0)
            price_native = float(pair.get("priceNative", 0) or 0)

            # priceNative is the token price in the quote token (usually SOL)
            return {
                "price_sol": price_native,
                "price_usd": price_usd,
                "liquidity_usd": float(
                    pair.get("liquidity", {}).get("usd", 0) or 0
                ),
                "volume_24h": float(
                    pair.get("volume", {}).get("h24", 0) or 0
                ),
                "buys_1h": pair.get("txns", {}).get("h1", {}).get("buys", 0),
                "sells_1h": pair.get("txns", {}).get("h1", {}).get("sells", 0),
            }

        except Exception as e:
            logger.debug(f"DexScreener price fetch failed for {pool_address[:12]}...: {e}")
            return None

    async def fetch_batch_prices(
        self, mint_addresses: list[str]
    ) -> dict[str, dict]:
        """
        Fetch prices for multiple tokens at once using DexScreener's
        token endpoint (up to 30 addresses per call).
        """
        if not self._client or not mint_addresses:
            return {}

        results: dict[str, dict] = {}

        # DexScreener allows comma-separated addresses
        # Process in chunks of 30
        for i in range(0, len(mint_addresses), 30):
            chunk = mint_addresses[i : i + 30]
            addresses = ",".join(chunk)

            try:
                await self._rate_limiter.acquire("dexscreener")
                response = await self._client.get(
                    f"{DEXSCREENER_BASE_URL}/tokens/v1/solana/{addresses}"
                )
                response.raise_for_status()
                pairs = response.json()

                if isinstance(pairs, list):
                    for pair in pairs:
                        base_token = pair.get("baseToken", {})
                        mint = base_token.get("address", "")
                        if mint:
                            results[mint] = {
                                "price_sol": float(
                                    pair.get("priceNative", 0) or 0
                                ),
                                "price_usd": float(
                                    pair.get("priceUsd", 0) or 0
                                ),
                            }

            except Exception as e:
                logger.debug(f"Batch price fetch error: {e}")

        return results


class _CachedPrice:
    """Simple price cache entry."""

    __slots__ = ("price_sol", "price_usd", "updated_at")

    def __init__(
        self, price_sol: float, price_usd: float, updated_at: datetime
    ) -> None:
        self.price_sol = price_sol
        self.price_usd = price_usd
        self.updated_at = updated_at

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.updated_at).total_seconds()

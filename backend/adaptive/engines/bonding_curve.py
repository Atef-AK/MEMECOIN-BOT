"""
Bonding curve analysis engine.
Classifies where a token sits on its bonding curve and calculates
flow metrics to determine the optimal entry/exit timing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from backend.config.constants import DEXSCREENER_BASE_URL
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.adaptive import BondingCurveState
from backend.models.opportunity import BondingCurveMetrics

logger = logging.getLogger(__name__)

# Pump.fun graduation threshold (approximately 85 SOL bonded)
PUMP_FUN_GRADUATION_SOL = 85.0


class BondingCurveEngine:
    """
    Analyzes bonding curve state for tokens on Pump.fun / PumpSwap.

    Classifies market state as:
    EARLY → BUILDING → ACCELERATING → PARABOLIC → STALLING →
    REVERSING → GRADUATING → POST_GRADUATION
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"Accept": "application/json"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def analyze(
        self,
        pool_address: str,
        dex: str,
        liquidity_usd: float = 0.0,
        token_age_seconds: float = 0.0,
    ) -> BondingCurveMetrics:
        """Analyze bonding curve state for a token."""
        metrics = BondingCurveMetrics()

        # Only applicable for pump.fun / pumpswap tokens
        if dex not in ("pump_fun", "pumpswap", "Pump.fun", "PumpSwap"):
            metrics.state = BondingCurveState.NOT_APPLICABLE
            return metrics

        try:
            pair_data = await self._fetch_pair_data(pool_address)
            if not pair_data:
                return metrics

            txns = pair_data.get("txns", {})
            volume = pair_data.get("volume", {})

            # Extract 5-minute and 1-hour transaction data
            buys_5m = txns.get("m5", {}).get("buys", 0)
            sells_5m = txns.get("m5", {}).get("sells", 0)
            buys_1h = txns.get("h1", {}).get("buys", 0)
            sells_1h = txns.get("h1", {}).get("sells", 0)

            vol_5m = float(volume.get("m5", 0) or 0)
            vol_1h = float(volume.get("h1", 0) or 0)

            liq_usd = float(
                pair_data.get("liquidity", {}).get("usd", 0) or 0
            ) or liquidity_usd

            # Estimate SOL-denominated liquidity for curve progress
            price_native = float(pair_data.get("priceNative", 0) or 0)

            # Calculate flow rates (using 5-minute window = 300 seconds)
            if buys_5m > 0 or sells_5m > 0:
                total_5m = buys_5m + sells_5m
                # Rough estimate: split volume proportionally
                buy_ratio = buys_5m / total_5m if total_5m > 0 else 0.5
                metrics.sol_entering_per_second = (vol_5m * buy_ratio) / 300
                metrics.sol_leaving_per_second = (vol_5m * (1 - buy_ratio)) / 300
                metrics.net_sol_flow_per_second = (
                    metrics.sol_entering_per_second - metrics.sol_leaving_per_second
                )

            # Unique buyers per second (5m window)
            metrics.unique_buyers_per_second = buys_5m / 300 if buys_5m > 0 else 0

            # Acceleration: compare 5m rate to 1h average rate
            if buys_1h > 0:
                rate_5m = buys_5m / 5  # per minute
                rate_1h = buys_1h / 60  # per minute
                metrics.buy_acceleration = rate_5m / rate_1h if rate_1h > 0 else 1.0
            if sells_1h > 0:
                sell_rate_5m = sells_5m / 5
                sell_rate_1h = sells_1h / 60
                metrics.sell_acceleration = sell_rate_5m / sell_rate_1h if sell_rate_1h > 0 else 1.0

            # Volume acceleration
            if vol_1h > 0 and vol_5m > 0:
                vol_rate_5m = vol_5m / 5
                vol_rate_1h = vol_1h / 60
                metrics.volume_acceleration = vol_rate_5m / vol_rate_1h if vol_rate_1h > 0 else 1.0

            # Price acceleration from DexScreener priceChange
            price_change_5m = float(
                pair_data.get("priceChange", {}).get("m5", 0) or 0
            )
            price_change_1h = float(
                pair_data.get("priceChange", {}).get("h1", 0) or 0
            )
            if abs(price_change_1h) > 0:
                metrics.price_acceleration = price_change_5m / (price_change_1h / 12) if price_change_1h != 0 else 1.0
            else:
                metrics.price_acceleration = 1.0

            # Buyer diversity: ratio of buys to total txs
            total_txns_5m = buys_5m + sells_5m
            metrics.buyer_diversity = buys_5m / total_txns_5m if total_txns_5m > 0 else 0.5

            # Estimate bonding curve progress
            # Pump.fun pools graduate at ~85 SOL bonded
            # Use liquidity as proxy
            if liq_usd > 0:
                # Very rough: assume SOL price ~ $150
                estimated_sol = liq_usd / 150
                metrics.progress_percent = min(100.0, (estimated_sol / PUMP_FUN_GRADUATION_SOL) * 100)

            # Classify state
            metrics.state = self._classify_state(
                metrics=metrics,
                token_age_seconds=token_age_seconds,
                buys_5m=buys_5m,
                sells_5m=sells_5m,
                price_change_5m=price_change_5m,
            )

        except Exception as e:
            logger.debug(f"Bonding curve analysis error: {e}")

        return metrics

    def _classify_state(
        self,
        metrics: BondingCurveMetrics,
        token_age_seconds: float,
        buys_5m: int,
        sells_5m: int,
        price_change_5m: float,
    ) -> BondingCurveState:
        """Classify the current bonding curve market state."""
        progress = metrics.progress_percent

        # Post-graduation
        if progress >= 100:
            return BondingCurveState.POST_GRADUATION

        # Graduating (>90% progress with strong inflow)
        if progress >= 90 and metrics.net_sol_flow_per_second > 0:
            return BondingCurveState.GRADUATING

        # Reversing (negative flow, sells dominating)
        if (
            metrics.sell_acceleration > 2.0
            or (sells_5m > buys_5m * 1.5 and sells_5m >= 5)
            or price_change_5m < -15
        ):
            return BondingCurveState.REVERSING

        # Stalling (low activity, no acceleration)
        if (
            buys_5m + sells_5m < 3
            and metrics.buy_acceleration < 0.5
        ):
            return BondingCurveState.STALLING

        # Parabolic (extreme acceleration)
        if (
            metrics.buy_acceleration > 3.0
            and metrics.volume_acceleration > 3.0
            and price_change_5m > 20
        ):
            return BondingCurveState.PARABOLIC

        # Accelerating (strong momentum)
        if (
            metrics.buy_acceleration > 1.5
            and metrics.volume_acceleration > 1.3
            and metrics.net_sol_flow_per_second > 0
        ):
            return BondingCurveState.ACCELERATING

        # Building (moderate activity, positive flow)
        if (
            buys_5m >= 3
            and metrics.net_sol_flow_per_second >= 0
            and progress > 10
        ):
            return BondingCurveState.BUILDING

        # Early (very new, low progress)
        if token_age_seconds < 120 or progress < 10:
            return BondingCurveState.EARLY

        return BondingCurveState.BUILDING

    async def _fetch_pair_data(self, pool_address: str) -> dict | None:
        if not self._client or not pool_address:
            return None
        try:
            await self._rate_limiter.acquire("dexscreener")
            resp = await self._client.get(
                f"{DEXSCREENER_BASE_URL}/latest/dex/pairs/solana/{pool_address}"
            )
            resp.raise_for_status()
            pairs = resp.json().get("pairs", [])
            return pairs[0] if pairs else None
        except Exception as e:
            logger.debug(f"Pair fetch failed: {e}")
            return None

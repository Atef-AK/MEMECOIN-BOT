"""
Market behavior observer.
Observes a token's trading activity during a configurable window
to detect organic vs. suspicious trading patterns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import httpx

from backend.config.constants import DEXSCREENER_BASE_URL
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.security import MarketBehaviorReport

logger = logging.getLogger(__name__)


class MarketBehaviorObserver:
    """
    Observes real-time trading activity for a token over a short window.

    Uses DexScreener's pair data (txns, volume) to build a picture of:
    - Buy/sell ratio and unique trader counts
    - Volume velocity (USD/second)
    - Price stability during observation
    - Signs of wash trading or coordinated dumps

    The observation is non-blocking: it takes two snapshots
    (start + end) rather than streaming, to avoid holding up
    the pipeline for the full observation window.
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

    async def observe(
        self,
        mint_address: str,
        pool_address: str,
        observation_seconds: float | None = None,
    ) -> MarketBehaviorReport:
        """
        Observe a token's market behavior over a short period.

        Takes a snapshot at the start, waits, takes another snapshot,
        and computes the delta to populate the MarketBehaviorReport.

        Args:
            mint_address: The token's mint address.
            pool_address: The pool/pair address on DexScreener.
            observation_seconds: How long to observe. Defaults to settings.

        Returns:
            Populated MarketBehaviorReport with score.
        """
        settings = get_settings()
        if observation_seconds is None:
            observation_seconds = float(settings.observation_seconds)

        report = MarketBehaviorReport(
            mint_address=mint_address,
            observation_seconds=observation_seconds,
        )

        if not pool_address or not self._client:
            # Can't observe without a pool address
            report.passed = True
            report.score = 2.5  # Neutral default
            return report

        try:
            # Snapshot 1: beginning of observation
            snap_start = await self._fetch_pair_snapshot(pool_address)
            if not snap_start:
                report.passed = True
                report.score = 2.5
                return report

            # Wait for the observation period
            # Cap at 60s to avoid blocking the pipeline too long
            wait_time = min(observation_seconds, 60.0)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

            # Snapshot 2: end of observation
            snap_end = await self._fetch_pair_snapshot(pool_address)
            if not snap_end:
                # Use just the start snapshot
                self._populate_from_single_snapshot(report, snap_start)
            else:
                self._populate_from_delta(report, snap_start, snap_end, wait_time)

            # Score the behavior
            self._calculate_score(report)

        except Exception as e:
            logger.debug(f"Market observation error for {mint_address[:12]}: {e}")
            report.passed = True
            report.score = 2.5  # Fail open with neutral score

        return report

    async def quick_observe(
        self,
        mint_address: str,
        pool_address: str,
    ) -> MarketBehaviorReport:
        """
        Quick observation using a single snapshot (no wait).
        Useful when time is critical and we can't afford the observation delay.
        Uses the 5-minute and 1-hour windows from DexScreener as proxies.
        """
        report = MarketBehaviorReport(
            mint_address=mint_address,
            observation_seconds=0,
        )

        if not pool_address or not self._client:
            report.passed = True
            report.score = 2.5
            return report

        try:
            snap = await self._fetch_pair_snapshot(pool_address)
            if snap:
                self._populate_from_single_snapshot(report, snap)
                self._calculate_score(report)
            else:
                report.passed = True
                report.score = 2.5

        except Exception as e:
            logger.debug(f"Quick observation error: {e}")
            report.passed = True
            report.score = 2.5

        return report

    async def _fetch_pair_snapshot(self, pool_address: str) -> dict | None:
        """Fetch current pair data from DexScreener."""
        if not self._client:
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
            txns = pair.get("txns", {})
            volume = pair.get("volume", {})

            return {
                "timestamp": time.monotonic(),
                "price_usd": float(pair.get("priceUsd", 0) or 0),
                "price_native": float(pair.get("priceNative", 0) or 0),
                "liquidity_usd": float(
                    pair.get("liquidity", {}).get("usd", 0) or 0
                ),
                # 5-minute window
                "buys_m5": txns.get("m5", {}).get("buys", 0),
                "sells_m5": txns.get("m5", {}).get("sells", 0),
                # 1-hour window
                "buys_h1": txns.get("h1", {}).get("buys", 0),
                "sells_h1": txns.get("h1", {}).get("sells", 0),
                # 24-hour window
                "buys_h24": txns.get("h24", {}).get("buys", 0),
                "sells_h24": txns.get("h24", {}).get("sells", 0),
                # Volume
                "volume_m5": float(volume.get("m5", 0) or 0),
                "volume_h1": float(volume.get("h1", 0) or 0),
                "volume_h24": float(volume.get("h24", 0) or 0),
                # Price changes
                "price_change_m5": float(
                    pair.get("priceChange", {}).get("m5", 0) or 0
                ),
                "price_change_h1": float(
                    pair.get("priceChange", {}).get("h1", 0) or 0
                ),
            }

        except Exception as e:
            logger.debug(f"Pair snapshot fetch error: {e}")
            return None

    def _populate_from_delta(
        self,
        report: MarketBehaviorReport,
        start: dict,
        end: dict,
        elapsed: float,
    ) -> None:
        """Populate report from the delta between two snapshots."""
        # Use the delta of 5-minute window counts as approximation
        # of activity during our observation period
        report.buy_count = max(0, end["buys_m5"] - start["buys_m5"])
        report.sell_count = max(0, end["sells_m5"] - start["sells_m5"])

        # If delta is zero (observation was shorter than DexScreener's refresh),
        # fall back to the end snapshot's 5-minute data
        if report.buy_count == 0 and report.sell_count == 0:
            report.buy_count = end["buys_m5"]
            report.sell_count = end["sells_m5"]

        total_txns = report.buy_count + report.sell_count
        if total_txns > 0:
            report.buy_sell_ratio = report.buy_count / total_txns

        # Volume during observation
        volume_delta = max(0, end["volume_m5"] - start["volume_m5"])
        if volume_delta == 0:
            volume_delta = end["volume_m5"]  # Use full 5m window

        report.volume_usd = volume_delta
        if elapsed > 0:
            report.volume_velocity = volume_delta / elapsed

        # Price change during observation
        price_start = start.get("price_usd", 0)
        price_end = end.get("price_usd", 0)
        if price_start > 0 and price_end > 0:
            report.price_change_percent = (
                (price_end - price_start) / price_start
            ) * 100

        # Liquidity stability
        liq_start = start.get("liquidity_usd", 0)
        liq_end = end.get("liquidity_usd", 0)
        if liq_start > 0 and liq_end > 0:
            liq_change = abs(liq_end - liq_start) / liq_start * 100
            report.liquidity_stable = liq_change < 20  # <20% change

        # Detect warning signs
        self._detect_anomalies(report, end)

    def _populate_from_single_snapshot(
        self, report: MarketBehaviorReport, snap: dict
    ) -> None:
        """Populate report from a single snapshot using DexScreener windows."""
        # Use the 5-minute window for recent activity
        report.buy_count = snap.get("buys_m5", 0)
        report.sell_count = snap.get("sells_m5", 0)

        total_txns = report.buy_count + report.sell_count
        if total_txns > 0:
            report.buy_sell_ratio = report.buy_count / total_txns

        report.volume_usd = snap.get("volume_m5", 0)
        if report.volume_usd > 0:
            report.volume_velocity = report.volume_usd / 300  # 5 min = 300s

        report.price_change_percent = snap.get("price_change_m5", 0)

        # Use 1-hour data for broader context
        h1_buys = snap.get("buys_h1", 0)
        h1_sells = snap.get("sells_h1", 0)
        # Approximate unique traders (DexScreener doesn't give this directly)
        # Use 70% of buy count as proxy for unique buyers
        report.unique_buyers = max(1, int(h1_buys * 0.7))
        report.unique_sellers = max(0, int(h1_sells * 0.7))

        report.liquidity_stable = True  # Can't determine from single snapshot

        self._detect_anomalies(report, snap)

    def _detect_anomalies(self, report: MarketBehaviorReport, snap: dict) -> None:
        """Detect suspicious trading patterns."""
        # Massive sell dominance (>80% sells) in 5-minute window
        total = report.buy_count + report.sell_count
        if total >= 5 and report.sell_count / total > 0.8:
            report.consecutive_sells = report.sell_count
            report.large_sells = max(0, report.sell_count - report.buy_count)

        # Abnormal volume burst: 5m volume > 50% of 1h volume
        vol_m5 = snap.get("volume_m5", 0)
        vol_h1 = snap.get("volume_h1", 0)
        if vol_h1 > 0 and vol_m5 > 0:
            if vol_m5 / vol_h1 > 0.5:
                report.abnormal_bursts = True

        # Severe price crash (>30% drop in 5 minutes)
        if report.price_change_percent < -30:
            report.critical_failures.append(
                f"Price crashed {report.price_change_percent:.1f}% in 5 minutes"
            )

        # No liquidity
        if not report.liquidity_stable:
            report.critical_failures.append("Liquidity unstable during observation")

    def _calculate_score(self, report: MarketBehaviorReport) -> None:
        """Calculate market behavior score out of 5 points."""
        if report.critical_failures:
            report.score = 0.0
            report.passed = False
            return

        score = 0.0

        # Buy/sell ratio (0-2 points)
        # >0.5 = more buys than sells = healthy
        if report.buy_sell_ratio >= 0.6:
            score += 2.0
        elif report.buy_sell_ratio >= 0.4:
            score += 1.0
        # <0.4 = sell-heavy, no points

        # Volume velocity (0-1 point)
        # Having some volume is good, shows organic interest
        if report.volume_velocity > 10:  # >$10/sec
            score += 1.0
        elif report.volume_velocity > 1:  # >$1/sec
            score += 0.5

        # Liquidity stability (0-1 point)
        if report.liquidity_stable:
            score += 1.0

        # No anomalies bonus (0-1 point)
        if not report.abnormal_bursts and report.consecutive_sells < 5:
            score += 1.0

        report.score = min(5.0, score)
        report.passed = True

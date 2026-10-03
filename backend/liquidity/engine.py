"""
Liquidity analysis engine.
Analyzes actual pool liquidity, LP locks, and liquidity stability.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from backend.config.constants import DEXSCREENER_BASE_URL, RAYDIUM_LP_LOCK_PROGRAM_ID
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.liquidity.lock_detector import LPLockDetector
from backend.models.security import LiquidityReport

logger = logging.getLogger(__name__)


class LiquidityEngine:
    """
    Analyzes liquidity pool data for a token.

    Sources:
    - DEX Screener API for liquidity USD, pool data
    - On-chain RPC for LP lock verification (Raydium Burn & Earn, etc.)
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()
        self._lock_detector = LPLockDetector()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
        )
        self._lock_detector.set_client(self._client)

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def analyze(
        self,
        mint_address: str,
        pool_address: str = "",
        dex: str = "",
        initial_liquidity_usd: float = 0.0,
        market_cap: float = 0.0,
    ) -> LiquidityReport:
        """Perform liquidity analysis for a token/pool pair."""
        report = LiquidityReport(
            mint_address=mint_address,
            pool_address=pool_address,
            dex=dex,
        )
        settings = get_settings()

        try:
            # Fetch current pair data from DexScreener
            pair_data = await self._fetch_pair_data(pool_address)

            if pair_data:
                liquidity = pair_data.get("liquidity", {})
                pair_liq_usd = float(liquidity.get("usd", 0) or 0)
                report.liquidity_usd = pair_liq_usd if pair_liq_usd > 0 else initial_liquidity_usd
                report.base_liquidity = float(liquidity.get("base", 0) or 0)
                report.quote_liquidity = float(liquidity.get("quote", 0) or 0)

                # Calculate liquidity to market cap ratio
                mc = float(pair_data.get("marketCap", 0) or 0) or market_cap
                if mc > 0 and report.liquidity_usd > 0:
                    report.liquidity_to_mcap_ratio = report.liquidity_usd / mc
                elif "pump" in dex.lower():
                    report.liquidity_to_mcap_ratio = 0.90

                # Pool creation time
                pair_created = pair_data.get("pairCreatedAt")
                if pair_created:
                    try:
                        created_ts = float(pair_created) / 1000
                        from datetime import timezone
                        report.pool_age_seconds = (
                            datetime.now(timezone.utc).timestamp() - created_ts
                        )
                    except (ValueError, TypeError):
                        pass

                # Liquidity change
                if initial_liquidity_usd > 0 and report.liquidity_usd > 0:
                    report.liquidity_change_percent = (
                        (report.liquidity_usd - initial_liquidity_usd) /
                        initial_liquidity_usd * 100
                    )
            else:
                # Use provided initial data (e.g. Pump.fun bonding reserve)
                report.liquidity_usd = initial_liquidity_usd or (4500.0 if "pump" in dex.lower() else 0.0)
                if market_cap > 0 and report.liquidity_usd > 0:
                    report.liquidity_to_mcap_ratio = report.liquidity_usd / market_cap
                elif "pump" in dex.lower():
                    report.liquidity_to_mcap_ratio = 0.90

            # Check minimum liquidity
            if report.liquidity_usd < settings.min_liquidity_usd:
                report.critical_failures.append(
                    f"Liquidity ${report.liquidity_usd:,.0f} below minimum "
                    f"${settings.min_liquidity_usd:,.0f}"
                )

            # Check liquidity to market cap ratio
            if (
                report.liquidity_to_mcap_ratio > 0
                and report.liquidity_to_mcap_ratio < settings.min_liquidity_to_mcap_ratio
            ):
                report.critical_failures.append(
                    f"Liquidity/MCap ratio {report.liquidity_to_mcap_ratio:.4f} "
                    f"below minimum {settings.min_liquidity_to_mcap_ratio}"
                )

            # Attempt LP lock detection
            await self._check_lp_lock(report)

            # Calculate score
            self._calculate_score(report)

        except Exception as e:
            logger.error(f"Liquidity analysis error for {mint_address}: {e}", exc_info=True)
            report.critical_failures.append(f"Analysis error: {str(e)}")

        report.passed = len(report.critical_failures) == 0
        return report

    async def _fetch_pair_data(self, pool_address: str) -> dict | None:
        """Fetch pair data from DexScreener."""
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
            return pairs[0] if pairs else None
        except Exception as e:
            logger.debug(f"Failed to fetch pair data for {pool_address}: {e}")
            return None

    async def _check_lp_lock(self, report: LiquidityReport) -> None:
        """
        Detect if LP tokens are locked using on-chain inspection.

        Delegates to LPLockDetector which:
        1. Finds the LP mint for the pool
        2. Gets largest LP token holders
        3. Checks if any are owned by known lock programs
           (Raydium Burn & Earn, etc.) or burn addresses
        4. Calculates the locked/burned percentage
        """
        if not report.pool_address:
            return

        try:
            result = await self._lock_detector.detect(
                pool_address=report.pool_address,
                dex=report.dex,
            )

            report.lp_locked = result.lp_locked
            report.lp_lock_provider = result.lock_provider
            report.lp_lock_percent = result.lock_percent

            if result.lp_locked:
                logger.info(
                    f"🔒 LP locked: {result.lock_percent:.1f}% "
                    f"via {result.lock_provider} for {report.mint_address[:12]}"
                )

        except Exception as e:
            logger.debug(f"LP lock check failed for {report.pool_address[:12]}: {e}")
            report.lp_locked = False
            report.lp_lock_provider = ""
            report.lp_lock_percent = 0.0

    def _calculate_score(self, report: LiquidityReport) -> None:
        """Calculate liquidity score out of 20 points."""
        if report.critical_failures:
            report.score = 0.0
            return

        score = 0.0
        settings = get_settings()

        # Liquidity USD (0-10 points)
        liq = report.liquidity_usd
        if liq >= 100_000:
            score += 10.0
        elif liq >= 50_000:
            score += 8.0
        elif liq >= 30_000:
            score += 6.0
        elif liq >= settings.min_liquidity_usd:
            score += 4.0

        # Liquidity/MCap ratio (0-5 points)
        ratio = report.liquidity_to_mcap_ratio
        if ratio >= 0.20:
            score += 5.0
        elif ratio >= 0.10:
            score += 3.0
        elif ratio >= settings.min_liquidity_to_mcap_ratio:
            score += 1.0

        # LP lock (0-5 points)
        if report.lp_locked:
            if report.lp_lock_percent >= 90:
                score += 5.0
            elif report.lp_lock_percent >= 50:
                score += 3.0
            else:
                score += 1.0
        # No LP lock = 0 bonus, not a penalty

        report.score = min(20.0, score)

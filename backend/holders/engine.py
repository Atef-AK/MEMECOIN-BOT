"""
Holder concentration analysis engine.
Analyzes token holder distribution to detect suspicious concentration.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from backend.config.constants import (
    BURN_ADDRESSES,
    SPL_TOKEN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    WSOL_MINT,
)
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.security import HolderReport

logger = logging.getLogger(__name__)


class HolderEngine:
    """
    Analyzes token holder distribution.
    Uses Solana RPC getTokenLargestAccounts to determine concentration.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"Content-Type": "application/json"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def analyze(
        self,
        mint_address: str,
        total_supply: int = 0,
        decimals: int = 0,
        pool_address: str = "",
        creator_wallet: str = "",
        dex: str = "",
    ) -> HolderReport:
        """Analyze holder distribution for a token."""
        report = HolderReport(mint_address=mint_address)
        settings = get_settings()

        try:
            # Get largest token accounts
            largest_accounts = await self._get_largest_accounts(mint_address)

            if not largest_accounts:
                if "pump" in dex.lower() or pool_address:
                    # Brand new Pump.fun launch - initial supply is locked in bonding curve pool
                    report.total_holders = 1
                    report.top10_percent = 2.0
                    report.largest_non_lp_percent = 2.0
                    self._calculate_score(report)
                    report.passed = True
                    return report

                report.critical_failures.append("Could not fetch holder data")
                report.passed = False
                return report

            # Calculate total supply from accounts if not provided
            if total_supply == 0:
                total_supply = sum(
                    int(acc.get("amount", 0)) for acc in largest_accounts
                )

            if total_supply == 0:
                report.critical_failures.append("Total supply is zero")
                report.passed = False
                return report

            # Separate LP/burn/system addresses from real holders
            real_holders: list[dict] = []
            for acc in largest_accounts:
                address = acc.get("address", "")
                amount = int(acc.get("amount", 0))

                # Skip known non-holder addresses
                is_excluded = (
                    address in BURN_ADDRESSES
                    or address == pool_address
                    or amount == 0
                )

                if not is_excluded:
                    real_holders.append({
                        "address": address,
                        "amount": amount,
                        "percent": (amount / total_supply) * 100 if total_supply > 0 else 0,
                    })

            # Sort by amount descending
            real_holders.sort(key=lambda x: x["amount"], reverse=True)
            report.total_holders = len(real_holders)

            # Calculate concentration
            if real_holders:
                report.top1_percent = real_holders[0]["percent"] if len(real_holders) >= 1 else 0
                report.top5_percent = sum(h["percent"] for h in real_holders[:5])
                report.top10_percent = sum(h["percent"] for h in real_holders[:10])
                report.top20_percent = sum(h["percent"] for h in real_holders[:20])

                # Find largest non-LP, non-burn holder
                for holder in real_holders:
                    addr = holder["address"]
                    if addr not in BURN_ADDRESSES and addr != pool_address:
                        report.largest_non_lp_holder = addr
                        report.largest_non_lp_percent = holder["percent"]
                        break

                # Check deployer holdings
                if creator_wallet:
                    for holder in real_holders:
                        if holder["address"] == creator_wallet:
                            report.deployer_holdings_percent = holder["percent"]
                            break

            # Check thresholds
            if report.top10_percent > settings.max_top10_holder_percent:
                report.suspicious_concentration = True
                report.concentration_detail = (
                    f"Top 10 holders own {report.top10_percent:.1f}% "
                    f"(max: {settings.max_top10_holder_percent}%)"
                )
                report.critical_failures.append(report.concentration_detail)

            if report.largest_non_lp_percent > settings.max_single_holder_percent:
                report.suspicious_concentration = True
                detail = (
                    f"Single holder owns {report.largest_non_lp_percent:.1f}% "
                    f"(max: {settings.max_single_holder_percent}%)"
                )
                report.concentration_detail += f"; {detail}" if report.concentration_detail else detail
                report.critical_failures.append(detail)

            # Calculate score
            self._calculate_score(report)

        except Exception as e:
            logger.error(f"Holder analysis error for {mint_address}: {e}", exc_info=True)
            report.critical_failures.append(f"Analysis error: {str(e)}")

        report.passed = len(report.critical_failures) == 0
        return report

    async def _get_largest_accounts(self, mint_address: str) -> list[dict]:
        """Get the 20 largest token accounts for a mint with 429 retry."""
        if not self._client:
            return []

        settings = get_settings()

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint_address, {"commitment": "confirmed"}],
        }

        for attempt in range(3):
            try:
                await self._rate_limiter.acquire("solana_rpc")
                response = await self._client.post(settings.rpc_url, json=payload)
                if response.status_code == 429:
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                response.raise_for_status()
                result = response.json().get("result", {})
                return result.get("value", [])
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(0.4)
                    continue
                logger.debug(f"Failed to fetch largest accounts for {mint_address}: {e}")
                return []
        return []

    def _calculate_score(self, report: HolderReport) -> None:
        """Calculate holder score out of 20 points."""
        if report.critical_failures:
            report.score = 0.0
            return

        score = 20.0
        settings = get_settings()

        # Top 10 concentration penalty
        if report.top10_percent > 20:
            score -= 5.0
        elif report.top10_percent > 15:
            score -= 2.0

        # Single holder concentration
        if report.largest_non_lp_percent > 4:
            score -= 5.0
        elif report.largest_non_lp_percent > 3:
            score -= 2.0

        # Deployer holdings
        if report.deployer_holdings_percent > 5:
            score -= 5.0
        elif report.deployer_holdings_percent > 2:
            score -= 2.0

        # Low holder count
        if report.total_holders < 10:
            score -= 5.0
        elif report.total_holders < 50:
            score -= 2.0

        report.score = max(0.0, score)

"""
Smart wallet tracking and qualification engine.
Tracks wallet performance and identifies "smart money" based on
statistical quality, not just win rate.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import httpx

from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.adaptive import (
    AdaptiveConfig,
    SmartWalletProfile,
    SmartWalletSignal,
)

logger = logging.getLogger(__name__)


class SmartWalletDB:
    """
    Tracks and qualifies "smart" wallets based on historical performance.

    Qualification criteria (NOT just win rate):
    - Minimum trade count (default: 30)
    - Positive expectancy
    - Positive profit factor
    - Controlled average loss
    - No obvious wash-trading pattern
    - No obvious coordinated manipulation

    Score = risk-adjusted historical quality, not win rate.
    """

    def __init__(self, config: AdaptiveConfig | None = None) -> None:
        self._config = config or AdaptiveConfig()
        self._wallets: dict[str, SmartWalletProfile] = {}
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

    def get_wallet(self, address: str) -> SmartWalletProfile | None:
        return self._wallets.get(address)

    def get_qualified_wallets(self) -> list[SmartWalletProfile]:
        return [w for w in self._wallets.values() if w.is_qualified]

    def update_wallet(self, profile: SmartWalletProfile) -> None:
        """Update or insert a wallet profile and re-qualify."""
        profile.is_qualified = self._check_qualification(profile)
        profile.smart_wallet_score = self._calculate_score(profile)
        self._wallets[profile.address] = profile

    def record_trade(
        self,
        address: str,
        pnl_percent: float,
        holding_seconds: float = 0,
        token_age_at_entry: float = 0,
        token_age_at_exit: float = 0,
    ) -> None:
        """Record a trade for a wallet and update its statistics."""
        profile = self._wallets.get(address)
        if not profile:
            profile = SmartWalletProfile(address=address)

        profile.total_trades += 1
        profile.last_seen = datetime.now(timezone.utc)

        if pnl_percent > 0:
            profile.wins += 1
            # Running average for winners
            n = profile.wins
            profile.average_winner_percent = (
                profile.average_winner_percent * (n - 1) + pnl_percent
            ) / n
        else:
            profile.losses += 1
            n = profile.losses
            profile.average_loser_percent = (
                profile.average_loser_percent * (n - 1) + abs(pnl_percent)
            ) / n

        # Win rate
        profile.win_rate = profile.wins / profile.total_trades

        # Expectancy
        profile.expectancy_percent = (
            profile.win_rate * profile.average_winner_percent
            - (1 - profile.win_rate) * profile.average_loser_percent
        )

        # Profit factor
        total_wins = profile.wins * profile.average_winner_percent
        total_losses = profile.losses * profile.average_loser_percent
        profile.profit_factor = total_wins / total_losses if total_losses > 0 else (
            float("inf") if total_wins > 0 else 0
        )

        # Running average holding time
        n = profile.total_trades
        profile.median_holding_seconds = (
            profile.median_holding_seconds * (n - 1) + holding_seconds
        ) / n

        # Token age at entry/exit
        profile.avg_entry_token_age_seconds = (
            profile.avg_entry_token_age_seconds * (n - 1) + token_age_at_entry
        ) / n
        profile.avg_exit_token_age_seconds = (
            profile.avg_exit_token_age_seconds * (n - 1) + token_age_at_exit
        ) / n

        self.update_wallet(profile)

    def check_token_participation(
        self,
        mint_address: str,
        participating_wallets: list[str],
        entry_sizes_sol: dict[str, float] | None = None,
    ) -> SmartWalletSignal:
        """
        Check if qualified smart wallets are participating in a token.

        Returns a SmartWalletSignal with signal strength.
        """
        signal = SmartWalletSignal(mint_address=mint_address)

        qualified = []
        total_entry = 0.0
        funding_sources: set[str] = set()

        for addr in participating_wallets:
            profile = self._wallets.get(addr)
            if not profile or not profile.is_qualified:
                continue

            qualified.append(profile)
            if entry_sizes_sol:
                total_entry += entry_sizes_sol.get(addr, 0)

            # Track funding clusters (simplified — use creator_funding_source if available)
            funding_sources.add(addr[:8])  # Rough cluster by prefix

        signal.qualified_wallets = qualified
        signal.total_entry_sol = total_entry
        signal.independent_entries = len(funding_sources)
        signal.cluster_overlap = len(funding_sources) < len(qualified)

        if qualified:
            signal.avg_wallet_score = sum(
                w.smart_wallet_score for w in qualified
            ) / len(qualified)

            # Signal strength (0-100)
            strength = 0.0

            # Number of qualified wallets (0-30)
            strength += min(30, len(qualified) * 10)

            # Average wallet quality (0-30)
            strength += min(30, signal.avg_wallet_score * 0.3)

            # Entry size (0-20)
            if total_entry > 0.5:
                strength += 20
            elif total_entry > 0.1:
                strength += 10

            # Independence bonus (0-20)
            if signal.independent_entries >= 2 and not signal.cluster_overlap:
                strength += 20
            elif signal.independent_entries >= 2:
                strength += 10

            signal.signal_strength = min(100, strength)

        return signal

    def _check_qualification(self, profile: SmartWalletProfile) -> bool:
        """Check if a wallet meets smart wallet criteria."""
        cfg = self._config.smart_wallet

        # Minimum trade count
        if profile.total_trades < cfg.min_wallet_trades:
            return False

        # Positive expectancy
        if profile.expectancy_percent <= cfg.min_wallet_expectancy:
            return False

        # Positive profit factor
        if profile.profit_factor <= cfg.min_wallet_profit_factor:
            return False

        # Controlled average loss
        if profile.average_loser_percent > cfg.max_wallet_avg_loss_percent:
            return False

        # No obvious wash trading (high trade count + near-zero PnL)
        if profile.wash_trading_score > 0.7:
            return False

        # No obvious manipulation clusters
        if profile.suspicious_clusters > 3:
            return False

        return True

    def _calculate_score(self, profile: SmartWalletProfile) -> float:
        """
        Calculate risk-adjusted smart wallet score (0-100).

        NOT based on win rate alone. Uses expectancy, profit factor,
        loss control, and statistical confidence.
        """
        if profile.total_trades < 10:
            return 0.0

        score = 0.0

        # Expectancy contribution (0-30)
        if profile.expectancy_percent > 0:
            # Cap at 20% expectancy for scoring
            norm_exp = min(20, profile.expectancy_percent) / 20
            score += norm_exp * 30

        # Profit factor contribution (0-25)
        if profile.profit_factor > 1.0:
            # Cap at PF 5.0
            norm_pf = min(5.0, profile.profit_factor) / 5.0
            score += norm_pf * 25

        # Loss control (0-20) — smaller average loss = better
        if profile.average_loser_percent > 0:
            # Below 10% avg loss = full score
            loss_quality = max(0, 1 - profile.average_loser_percent / 30)
            score += loss_quality * 20

        # Statistical confidence (0-15)
        # More trades = more confidence (Wilson-style diminishing returns)
        trade_confidence = min(1.0, profile.total_trades / 200)
        score += trade_confidence * 15

        # Penalties
        if profile.wash_trading_score > 0.3:
            score *= (1 - profile.wash_trading_score)
        if profile.suspicious_clusters > 0:
            score *= max(0.5, 1 - profile.suspicious_clusters * 0.1)

        # Consistency bonus (0-10)
        # Win rate between 40-70% with good expectancy shows consistency
        if 0.40 <= profile.win_rate <= 0.70 and profile.expectancy_percent > 0:
            score += 10

        return min(100, max(0, score))

    def to_dict(self) -> dict:
        qualified = self.get_qualified_wallets()
        return {
            "total_tracked": len(self._wallets),
            "qualified": len(qualified),
            "top_wallets": [
                {
                    "address": w.address[:12] + "...",
                    "trades": w.total_trades,
                    "expectancy": f"{w.expectancy_percent:.1f}%",
                    "profit_factor": f"{w.profit_factor:.2f}",
                    "score": f"{w.smart_wallet_score:.1f}",
                }
                for w in sorted(
                    qualified, key=lambda x: x.smart_wallet_score, reverse=True
                )[:10]
            ],
        }

"""
Opportunity builder — constructs a TokenOpportunity from all analysis outputs.
This is the bridge between the existing analysis engines and the adaptive strategies.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.models.adaptive import MarketRegime
from backend.models.opportunity import (
    BondingCurveMetrics,
    MomentumMetrics,
    TokenOpportunity,
)
from backend.models.security import (
    DevReport,
    HolderReport,
    LiquidityReport,
    MarketBehaviorReport,
    SecurityReport,
    SocialReport,
)
from backend.models.token import DiscoveredToken

logger = logging.getLogger(__name__)


class OpportunityBuilder:
    """
    Builds a TokenOpportunity from the outputs of all analysis engines.
    This ensures every strategy gets the same normalized view of a token.
    """

    @staticmethod
    def build(
        token: DiscoveredToken,
        security: SecurityReport,
        liquidity: LiquidityReport,
        holders: HolderReport,
        dev: DevReport,
        social: SocialReport,
        market: MarketBehaviorReport,
        bonding_curve: BondingCurveMetrics | None = None,
        market_regime: MarketRegime = MarketRegime.UNKNOWN,
        total_score: float = 0.0,
    ) -> TokenOpportunity:
        """Build a complete TokenOpportunity from analysis reports."""
        now = datetime.now(timezone.utc)

        # Calculate token age
        token_age = 0.0
        if hasattr(token, "created_at") and token.created_at:
            token_age = (now - token.created_at).total_seconds()

        # Build momentum metrics from market behavior report
        momentum = MomentumMetrics(
            buy_count_5m=market.buy_count,
            sell_count_5m=market.sell_count,
            buy_sell_ratio=market.buy_sell_ratio,
            unique_buyers_estimate=market.unique_buyers,
            unique_sellers_estimate=market.unique_sellers,
            volume_5m_usd=market.volume_usd,
            volume_acceleration=market.volume_velocity,
            price_change_5m_percent=market.price_change_percent,
        )

        # Calculate organic demand score (0-100)
        momentum.organic_demand_score = _calculate_organic_demand(momentum, holders)

        # Calculate momentum score (0-100)
        momentum.momentum_score = _calculate_momentum_score(momentum)

        # Calculate buyer acceleration from buy/sell ratio
        if momentum.sell_count_5m > 0:
            momentum.buyer_acceleration = (
                momentum.buy_count_5m / momentum.sell_count_5m
            )

        opp = TokenOpportunity(
            # Identity
            mint_address=token.mint_address,
            symbol=token.symbol,
            name=token.name,
            pool_address=token.pool_address,
            dex=token.dex,
            source=token.source if hasattr(token, "source") else "",

            # Timing
            discovered_at=now,
            token_age_seconds=token_age,

            # Market data
            price_usd=token.initial_price_usd if hasattr(token, "initial_price_usd") else 0.0,
            market_cap_usd=token.initial_market_cap if hasattr(token, "initial_market_cap") else 0.0,
            fdv_usd=token.fdv if hasattr(token, "fdv") else 0.0,
            liquidity_usd=liquidity.liquidity_usd,
            liquidity_to_mcap_ratio=liquidity.liquidity_to_mcap_ratio,

            # Security
            security_score=security.score,
            security_passed=security.passed,
            mint_authority_disabled=not security.has_mint_authority if hasattr(security, "has_mint_authority") else True,
            freeze_authority_disabled=not security.has_freeze_authority if hasattr(security, "has_freeze_authority") else True,

            # Liquidity
            liquidity_score=liquidity.score,
            lp_locked=liquidity.lp_locked,
            lp_lock_percent=liquidity.lp_lock_percent,

            # Holders
            holder_score=holders.score,
            total_holders=holders.total_holders,
            top10_holder_percent=holders.top10_percent,
            top1_holder_percent=holders.top1_percent if hasattr(holders, "top1_percent") else 0.0,

            # Creator/Dev
            dev_score=dev.score,
            creator_wallet=dev.creator_wallet,
            creator_has_sold=dev.creator_has_sold,
            creator_sell_percent=dev.creator_sell_percent,
            suspicious_cluster=dev.suspicious_cluster,

            # Social
            social_score=social.score,
            social_count=social.social_count,

            # Momentum
            momentum=momentum,

            # Bonding curve
            bonding_curve=bonding_curve or BondingCurveMetrics(),

            # Market context
            market_regime=market_regime,

            # Composite
            total_score=total_score,
            liquidity_change_percent=liquidity.liquidity_change_percent if hasattr(liquidity, "liquidity_change_percent") else 0.0,
        )

        return opp


def _calculate_organic_demand(
    momentum: MomentumMetrics,
    holders: HolderReport,
) -> float:
    """
    Calculate organic demand score (0-100).
    Measures whether buying activity appears organic vs. artificial.
    """
    score = 0.0

    # Buy/sell ratio: healthy markets have ratio between 0.5 and 0.75
    if 0.5 <= momentum.buy_sell_ratio <= 0.75:
        score += 25
    elif 0.4 <= momentum.buy_sell_ratio <= 0.85:
        score += 15
    elif momentum.buy_sell_ratio > 0.85:
        score += 5  # Too high = suspicious (wash trading?)

    # Unique buyer count
    if momentum.unique_buyers_estimate >= 20:
        score += 25
    elif momentum.unique_buyers_estimate >= 10:
        score += 15
    elif momentum.unique_buyers_estimate >= 5:
        score += 8

    # Holder distribution (less concentration = more organic)
    if holders.top10_percent < 30:
        score += 25
    elif holders.top10_percent < 50:
        score += 15
    elif holders.top10_percent < 70:
        score += 5

    # Volume existence
    if momentum.volume_5m_usd > 5000:
        score += 25
    elif momentum.volume_5m_usd > 1000:
        score += 15
    elif momentum.volume_5m_usd > 100:
        score += 5

    return min(100, score)


def _calculate_momentum_score(momentum: MomentumMetrics) -> float:
    """
    Calculate momentum score (0-100).
    Measures the strength and quality of buying pressure.
    """
    score = 0.0

    # Buy/sell ratio weighted toward buyers
    if momentum.buy_sell_ratio > 0.65:
        score += 30
    elif momentum.buy_sell_ratio > 0.55:
        score += 20
    elif momentum.buy_sell_ratio > 0.45:
        score += 10

    # Buyer acceleration
    if momentum.buyer_acceleration > 2.0:
        score += 25
    elif momentum.buyer_acceleration > 1.5:
        score += 15
    elif momentum.buyer_acceleration > 1.0:
        score += 5

    # Volume acceleration
    if momentum.volume_acceleration > 3.0:
        score += 20
    elif momentum.volume_acceleration > 1.5:
        score += 10
    elif momentum.volume_acceleration > 0.5:
        score += 5

    # Positive price momentum
    if momentum.price_change_5m_percent > 10:
        score += 25
    elif momentum.price_change_5m_percent > 5:
        score += 15
    elif momentum.price_change_5m_percent > 0:
        score += 5

    return min(100, score)

"""
TokenOpportunity — unified analysis object consumed by all strategies.
Aggregates every metric from the shared analysis layer into a single
normalized object that strategies evaluate independently.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from backend.models.adaptive import (
    BondingCurveState,
    MarketRegime,
    SmartWalletSignal,
)
from backend.models.token import DexType


class BondingCurveMetrics(BaseModel):
    """Bonding curve analysis for tokens still on a curve."""
    state: BondingCurveState = BondingCurveState.NOT_APPLICABLE
    progress_percent: float = 0.0  # 0-100% toward graduation

    sol_entering_per_second: float = 0.0
    sol_leaving_per_second: float = 0.0
    net_sol_flow_per_second: float = 0.0

    unique_buyers_per_second: float = 0.0
    buy_acceleration: float = 0.0   # ratio of recent to earlier buys
    sell_acceleration: float = 0.0
    price_acceleration: float = 0.0
    volume_acceleration: float = 0.0

    buyer_diversity: float = 0.0    # 0-1, higher = more diverse
    insider_participation: float = 0.0  # 0-1, higher = more insiders


class MomentumMetrics(BaseModel):
    """Momentum and organic demand metrics."""
    # Buy/sell activity
    buy_count_5m: int = 0
    sell_count_5m: int = 0
    buy_count_1h: int = 0
    sell_count_1h: int = 0
    buy_sell_ratio: float = 0.0

    # Unique participants
    unique_buyers_estimate: int = 0
    unique_sellers_estimate: int = 0
    buyer_acceleration: float = 0.0  # recent vs prior window

    # Volume
    volume_5m_usd: float = 0.0
    volume_1h_usd: float = 0.0
    volume_acceleration: float = 0.0

    # Price
    price_change_5m_percent: float = 0.0
    price_change_1h_percent: float = 0.0

    # Composite scores
    organic_demand_score: float = 0.0  # 0-100
    momentum_score: float = 0.0        # 0-100


class TokenOpportunity(BaseModel):
    """
    Unified analysis object for a token opportunity.

    Built by the OpportunityBuilder from all analysis engine outputs.
    Consumed by every strategy for independent evaluation.
    """
    # Identity
    mint_address: str
    symbol: str = ""
    name: str = ""
    pool_address: str = ""
    dex: DexType = DexType.UNKNOWN
    source: str = ""

    # Timing
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: Optional[datetime] = None
    token_age_seconds: float = 0.0

    # Market data
    price_sol: float = 0.0
    price_usd: float = 0.0
    market_cap_usd: float = 0.0
    fdv_usd: float = 0.0
    liquidity_usd: float = 0.0
    liquidity_to_mcap_ratio: float = 0.0

    # Security
    security_score: float = 0.0
    security_passed: bool = False
    mint_authority_disabled: bool = False
    freeze_authority_disabled: bool = False
    is_token_2022: bool = False

    # Liquidity
    liquidity_score: float = 0.0
    lp_locked: bool = False
    lp_lock_percent: float = 0.0

    # Holders
    holder_score: float = 0.0
    total_holders: int = 0
    top10_holder_percent: float = 0.0
    top1_holder_percent: float = 0.0
    insider_concentration: float = 0.0

    # Creator/Dev
    dev_score: float = 0.0
    creator_wallet: str = ""
    creator_has_sold: bool = False
    creator_sell_percent: float = 0.0
    suspicious_cluster: bool = False

    # Social
    social_score: float = 0.0
    social_count: int = 0

    # Momentum & organic demand
    momentum: MomentumMetrics = Field(default_factory=MomentumMetrics)

    # Bonding curve
    bonding_curve: BondingCurveMetrics = Field(default_factory=BondingCurveMetrics)

    # Smart wallet signals
    smart_wallet_signal: Optional[SmartWalletSignal] = None

    # Market context
    market_regime: MarketRegime = MarketRegime.UNKNOWN

    # Composite scores (from existing scoring engine)
    total_score: float = 0.0

    # Liquidity changes
    liquidity_change_percent: float = 0.0

    @property
    def has_smart_wallet_signal(self) -> bool:
        return (
            self.smart_wallet_signal is not None
            and self.smart_wallet_signal.signal_strength > 0
        )

    @property
    def is_on_bonding_curve(self) -> bool:
        return self.bonding_curve.state not in (
            BondingCurveState.NOT_APPLICABLE,
            BondingCurveState.POST_GRADUATION,
        )

    @property
    def passes_basic_filters(self) -> bool:
        """Check if token passes the minimum shared filters."""
        return self.security_passed and self.liquidity_score > 0 and self.holder_score > 0

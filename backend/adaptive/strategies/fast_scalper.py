"""
Strategy A — Fast Scalper.
Captures a small early move (+10%) and exits quickly.
"""

from __future__ import annotations

from backend.adaptive.strategies.base import BaseStrategy
from backend.models.adaptive import (
    BondingCurveState,
    ExitStep,
    StrategyName,
    StrategyResult,
)
from backend.models.opportunity import TokenOpportunity


class FastScalperStrategy(BaseStrategy):
    """
    Fast Scalper — capture a quick +10% move and exit 100%.

    Best suited for:
    - Normal or high-momentum regimes
    - Tokens with strong initial organic buying
    - Small capital stages where speed matters

    Entry requirements:
    - Passes all shared security filters
    - Strong initial organic buying (buy/sell ratio)
    - Acceptable bonding-curve activity
    - Minimum observation period completed

    Exit:
    - 100% at +10% NET (configurable)
    - Emergency exit on dev dump, liquidity deterioration, etc.
    """

    @property
    def name(self) -> StrategyName:
        return StrategyName.FAST_SCALPER

    def evaluate(self, opp: TokenOpportunity) -> StrategyResult:
        cfg = self._config.fast_scalper
        result = StrategyResult(strategy=self.name)
        reasons: list[str] = []
        rejections: list[str] = []

        # 1. Shared filters
        shared_fails = self._check_shared_filters(opp)
        if shared_fails:
            result.rejection_reasons = shared_fails
            return result

        # 2. Bonding curve check — accept BUILDING, ACCELERATING, POST_GRADUATION
        bc_fail = self._check_bonding_curve_entry(
            opp,
            allowed_states={
                BondingCurveState.BUILDING,
                BondingCurveState.ACCELERATING,
                BondingCurveState.GRADUATING,
                BondingCurveState.POST_GRADUATION,
            },
        )
        if bc_fail:
            rejections.append(bc_fail)

        # 3. Organic buying check
        if opp.momentum.buy_sell_ratio < 0.45:
            rejections.append(
                f"Buy/sell ratio {opp.momentum.buy_sell_ratio:.2f} too low (<0.45)"
            )
        elif opp.momentum.buy_sell_ratio >= 0.55:
            reasons.append(
                f"Strong buying: {opp.momentum.buy_sell_ratio:.2f} buy/sell ratio"
            )

        # 4. Volume check
        if opp.momentum.volume_5m_usd < 100:
            rejections.append(
                f"Volume too low: ${opp.momentum.volume_5m_usd:.0f} in 5m"
            )
        elif opp.momentum.volume_5m_usd >= 1000:
            reasons.append(f"Good volume: ${opp.momentum.volume_5m_usd:,.0f} in 5m")

        # 5. Liquidity check
        if opp.liquidity_usd < 5000:
            rejections.append(f"Liquidity too low: ${opp.liquidity_usd:,.0f}")
        else:
            reasons.append(f"Liquidity: ${opp.liquidity_usd:,.0f}")

        # 6. Token age — scalper prefers newer tokens
        if opp.token_age_seconds > 3600:
            rejections.append(
                f"Token too old for scalping: {opp.token_age_seconds/60:.0f}m"
            )
        elif opp.token_age_seconds < 600:
            reasons.append(f"Fresh token: {opp.token_age_seconds/60:.0f}m old")

        # 7. Price direction check
        if opp.momentum.price_change_5m_percent < -10:
            rejections.append(
                f"Price dropping: {opp.momentum.price_change_5m_percent:.1f}%"
            )

        if rejections:
            result.rejection_reasons = rejections
            return result

        # Calculate confidence
        confidence = 50.0

        # Organic demand bonus
        if opp.momentum.organic_demand_score > 70:
            confidence += 15
            reasons.append(f"High organic demand: {opp.momentum.organic_demand_score:.0f}")
        elif opp.momentum.organic_demand_score > 50:
            confidence += 8

        # Momentum bonus
        if opp.momentum.momentum_score > 60:
            confidence += 10
            reasons.append(f"Strong momentum: {opp.momentum.momentum_score:.0f}")

        # LP lock bonus
        if opp.lp_locked and opp.lp_lock_percent > 50:
            confidence += 5
            reasons.append(f"LP locked: {opp.lp_lock_percent:.0f}%")

        # Security score bonus
        if opp.security_score >= 25:
            confidence += 5

        # Bonding curve bonus
        if opp.bonding_curve.state == BondingCurveState.ACCELERATING:
            confidence += 10
            reasons.append("Bonding curve accelerating")

        result.should_trade = True
        result.confidence = min(100, confidence)
        result.reasons = reasons

        # Exit plan: 100% at target
        result.exit_steps = [
            ExitStep(target_percent=cfg.target_percent, sell_percent=100.0)
        ]
        result.trailing_stop_percent = 0  # No trailing, just target
        result.max_hold_seconds = cfg.max_hold_seconds

        # Scores
        result.momentum_score = opp.momentum.momentum_score
        result.organic_demand_score = opp.momentum.organic_demand_score
        result.bonding_curve_suitability = (
            80 if opp.bonding_curve.state in (
                BondingCurveState.BUILDING, BondingCurveState.ACCELERATING
            ) else 50
        )

        return result

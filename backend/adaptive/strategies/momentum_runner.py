"""
Strategy B — Momentum Runner.
Captures larger moves with partial profit-taking and trailing stop.
Designed to occasionally catch large winners rather than exiting
immediately at +10%.
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


class MomentumRunnerStrategy(BaseStrategy):
    """
    Momentum Runner — capture large moves with scaled exits.

    Default exit schedule:
      +10% → sell 30%
      +25% → sell 30%
      +50% → sell 20%
      remaining 20% → trailing stop from realized peak

    Entry requirements (stricter than Fast Scalper):
    - Strong buyer acceleration (>1.5x)
    - Increasing unique buyers
    - Healthy buy/sell ratio (>0.55)
    - Strong volume acceleration (>1.3x)
    - Acceptable holder distribution
    - Low insider concentration
    - High organic-demand score
    """

    @property
    def name(self) -> StrategyName:
        return StrategyName.MOMENTUM_RUNNER

    def evaluate(self, opp: TokenOpportunity) -> StrategyResult:
        cfg = self._config.momentum_runner
        result = StrategyResult(strategy=self.name)
        reasons: list[str] = []
        rejections: list[str] = []

        # 1. Shared filters
        shared_fails = self._check_shared_filters(opp)
        if shared_fails:
            result.rejection_reasons = shared_fails
            return result

        # 2. Bonding curve — prefer ACCELERATING or POST_GRADUATION
        bc_fail = self._check_bonding_curve_entry(
            opp,
            allowed_states={
                BondingCurveState.ACCELERATING,
                BondingCurveState.PARABOLIC,
                BondingCurveState.GRADUATING,
                BondingCurveState.POST_GRADUATION,
            },
        )
        if bc_fail:
            rejections.append(bc_fail)

        # 3. Buyer acceleration — MUST be strong
        buyer_accel = opp.momentum.buyer_acceleration
        if buyer_accel < cfg.min_buyer_acceleration:
            rejections.append(
                f"Buyer acceleration {buyer_accel:.2f}x below "
                f"minimum {cfg.min_buyer_acceleration}x"
            )
        else:
            reasons.append(f"Strong buyer acceleration: {buyer_accel:.2f}x")

        # 4. Buy/sell ratio — must be buy-dominated
        ratio = opp.momentum.buy_sell_ratio
        if ratio < cfg.min_buy_sell_ratio:
            rejections.append(
                f"Buy/sell ratio {ratio:.2f} below minimum {cfg.min_buy_sell_ratio}"
            )
        else:
            reasons.append(f"Healthy buy/sell ratio: {ratio:.2f}")

        # 5. Volume acceleration
        vol_accel = opp.momentum.volume_acceleration
        if vol_accel < cfg.min_volume_acceleration:
            rejections.append(
                f"Volume acceleration {vol_accel:.2f}x below "
                f"minimum {cfg.min_volume_acceleration}x"
            )
        else:
            reasons.append(f"Volume accelerating: {vol_accel:.2f}x")

        # 6. Unique buyers — need diversity
        if opp.momentum.unique_buyers_estimate < 5:
            rejections.append(
                f"Too few unique buyers: {opp.momentum.unique_buyers_estimate}"
            )
        elif opp.momentum.unique_buyers_estimate >= 15:
            reasons.append(
                f"Strong buyer diversity: {opp.momentum.unique_buyers_estimate} unique"
            )

        # 7. Holder distribution — reject high concentration
        if opp.top10_holder_percent > 60:
            rejections.append(
                f"Top 10 holders control {opp.top10_holder_percent:.0f}%"
            )

        # 8. Insider concentration
        if opp.insider_concentration > 0.5:
            rejections.append(
                f"High insider concentration: {opp.insider_concentration:.0%}"
            )

        # 9. Liquidity — need sufficient for multi-exit
        if opp.liquidity_usd < 10000:
            rejections.append(
                f"Insufficient liquidity for multi-exit: ${opp.liquidity_usd:,.0f}"
            )
        else:
            reasons.append(f"Sufficient liquidity: ${opp.liquidity_usd:,.0f}")

        # 10. Organic demand — must be high
        if opp.momentum.organic_demand_score < 40:
            rejections.append(
                f"Low organic demand: {opp.momentum.organic_demand_score:.0f}/100"
            )

        if rejections:
            result.rejection_reasons = rejections
            return result

        # Calculate confidence — start higher since requirements are stricter
        confidence = 55.0

        # Momentum score
        if opp.momentum.momentum_score > 70:
            confidence += 15
            reasons.append(f"Very strong momentum: {opp.momentum.momentum_score:.0f}")
        elif opp.momentum.momentum_score > 50:
            confidence += 8

        # Organic demand quality
        if opp.momentum.organic_demand_score > 70:
            confidence += 10
            reasons.append(
                f"High organic demand: {opp.momentum.organic_demand_score:.0f}"
            )

        # LP lock bonus
        if opp.lp_locked and opp.lp_lock_percent > 80:
            confidence += 5
            reasons.append(f"LP locked: {opp.lp_lock_percent:.0f}%")

        # Bonding curve acceleration bonus
        if opp.bonding_curve.state == BondingCurveState.ACCELERATING:
            confidence += 10
            reasons.append("Bonding curve accelerating — momentum entry")
        elif opp.bonding_curve.state == BondingCurveState.PARABOLIC:
            confidence += 5
            reasons.append("Parabolic curve — risky but high upside")

        # Volume momentum
        if opp.momentum.volume_5m_usd > 5000:
            confidence += 5

        # Price trending up
        if opp.momentum.price_change_5m_percent > 5:
            confidence += 5

        result.should_trade = True
        result.confidence = min(100, confidence)
        result.reasons = reasons

        # Exit plan: multi-step partial exits + trailing
        result.exit_steps = [
            ExitStep(
                target_percent=step.target_percent,
                sell_percent=step.sell_percent,
            )
            for step in cfg.exit_steps
        ]
        result.trailing_stop_percent = cfg.trailing_stop_percent
        result.max_hold_seconds = cfg.max_hold_seconds

        # Scores
        result.momentum_score = opp.momentum.momentum_score
        result.organic_demand_score = opp.momentum.organic_demand_score
        result.bonding_curve_suitability = (
            90 if opp.bonding_curve.state in (
                BondingCurveState.ACCELERATING, BondingCurveState.PARABOLIC
            ) else 50
        )

        return result

"""
Strategy C — Smart Wallet Follower.
Identifies tokens receiving early participation from wallets with
historically strong, statistically-verified performance.
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


class SmartWalletFollowerStrategy(BaseStrategy):
    """
    Smart Wallet Follower — enter when qualified smart wallets are buying.

    Does NOT blindly copy every wallet. Requires:
    - Multiple qualified smart wallets entering (or high-confidence single)
    - Sufficient historical sample per wallet (min 30 trades)
    - Positive wallet expectancy and profit factor
    - Independent entries (not from same funding cluster)
    - Token still passes all security/liquidity filters

    Entry score incorporates:
    - Number of qualified smart wallets entering
    - Size of their entries
    - Timing of their entries
    - Historical quality of those wallets
    - Whether entries are independent
    """

    @property
    def name(self) -> StrategyName:
        return StrategyName.SMART_WALLET_FOLLOWER

    def evaluate(self, opp: TokenOpportunity) -> StrategyResult:
        cfg = self._config.smart_wallet
        result = StrategyResult(strategy=self.name)
        reasons: list[str] = []
        rejections: list[str] = []

        # 1. Shared filters
        shared_fails = self._check_shared_filters(opp)
        if shared_fails:
            result.rejection_reasons = shared_fails
            return result

        # 2. Must have smart wallet signal
        if not opp.has_smart_wallet_signal:
            result.rejection_reasons = ["No smart wallet signal detected"]
            return result

        signal = opp.smart_wallet_signal

        # 3. Minimum qualified wallets
        qualified_count = len(signal.qualified_wallets)
        if qualified_count < cfg.min_smart_wallets:
            rejections.append(
                f"Only {qualified_count} qualified wallet(s), "
                f"need {cfg.min_smart_wallets}"
            )
        else:
            reasons.append(f"{qualified_count} qualified smart wallet(s) entering")

        # 4. Minimum wallet score
        if signal.avg_wallet_score < cfg.min_wallet_score:
            rejections.append(
                f"Average wallet score {signal.avg_wallet_score:.1f} "
                f"below minimum {cfg.min_wallet_score}"
            )
        else:
            reasons.append(
                f"Avg wallet quality: {signal.avg_wallet_score:.1f}/100"
            )

        # 5. Check individual wallet quality
        high_quality_wallets = 0
        for wallet in signal.qualified_wallets:
            if wallet.total_trades < cfg.min_wallet_trades:
                continue
            if wallet.expectancy_percent <= cfg.min_wallet_expectancy:
                continue
            if wallet.profit_factor <= cfg.min_wallet_profit_factor:
                continue
            if wallet.average_loser_percent > cfg.max_wallet_avg_loss_percent:
                continue
            high_quality_wallets += 1

        if high_quality_wallets == 0:
            rejections.append(
                "No wallets meet quality thresholds "
                f"(need: {cfg.min_wallet_trades}+ trades, "
                f"positive expectancy, PF > {cfg.min_wallet_profit_factor})"
            )
        else:
            reasons.append(
                f"{high_quality_wallets} wallet(s) pass quality thresholds"
            )

        # 6. Independence check — prefer multiple independent entries
        if signal.cluster_overlap and qualified_count > 1:
            rejections.append(
                "Smart wallets share funding cluster — possible coordination"
            )
        elif signal.independent_entries >= 2:
            reasons.append(
                f"{signal.independent_entries} independent entry sources"
            )

        # 7. Signal strength
        if signal.signal_strength < 30:
            rejections.append(
                f"Signal strength {signal.signal_strength:.0f}/100 too weak"
            )

        # 8. Bonding curve — accept most states except REVERSING
        bc_fail = self._check_bonding_curve_entry(
            opp,
            allowed_states={
                BondingCurveState.EARLY,
                BondingCurveState.BUILDING,
                BondingCurveState.ACCELERATING,
                BondingCurveState.GRADUATING,
                BondingCurveState.POST_GRADUATION,
            },
        )
        if bc_fail:
            rejections.append(bc_fail)

        # 9. Liquidity
        if opp.liquidity_usd < 5000:
            rejections.append(f"Insufficient liquidity: ${opp.liquidity_usd:,.0f}")

        if rejections:
            result.rejection_reasons = rejections
            return result

        # Calculate confidence
        confidence = 45.0  # Start lower — smart wallet needs evidence

        # Signal strength contribution (0-25)
        confidence += min(25, signal.signal_strength * 0.25)

        # Number of qualified wallets (0-15)
        confidence += min(15, high_quality_wallets * 5)

        # Independence bonus (0-10)
        if signal.independent_entries >= 3:
            confidence += 10
            reasons.append("Multiple independent entries — high conviction")
        elif signal.independent_entries >= 2:
            confidence += 5

        # Entry size bonus (0-10)
        if signal.total_entry_sol > 1.0:
            confidence += 10
            reasons.append(
                f"Significant entry size: {signal.total_entry_sol:.2f} SOL"
            )
        elif signal.total_entry_sol > 0.3:
            confidence += 5

        # Organic demand bonus (0-5)
        if opp.momentum.organic_demand_score > 60:
            confidence += 5

        result.should_trade = True
        result.confidence = min(100, confidence)
        result.reasons = reasons

        # Exit plan: target with trailing stop
        result.exit_steps = [
            ExitStep(target_percent=cfg.target_percent, sell_percent=70.0),
        ]
        result.trailing_stop_percent = cfg.trailing_stop_percent
        result.max_hold_seconds = cfg.max_hold_seconds

        # Scores
        result.smart_wallet_score = signal.signal_strength
        result.momentum_score = opp.momentum.momentum_score
        result.organic_demand_score = opp.momentum.organic_demand_score

        return result

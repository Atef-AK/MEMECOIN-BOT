"""
Counterfactual testing engine.
Runs all strategies against the same token opportunity in paper mode
to enable true same-opportunity benchmarking.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from backend.adaptive.performance.database import StrategyPerformanceDB
from backend.adaptive.strategies.base import BaseStrategy
from backend.adaptive.strategies.fast_scalper import FastScalperStrategy
from backend.adaptive.strategies.momentum_runner import MomentumRunnerStrategy
from backend.adaptive.strategies.smart_wallet_follower import SmartWalletFollowerStrategy
from backend.models.adaptive import (
    AdaptiveConfig,
    CapitalStage,
    ExitType,
    MarketRegime,
    StrategyName,
    StrategyResult,
    StrategyTradeRecord,
)
from backend.models.opportunity import TokenOpportunity

logger = logging.getLogger(__name__)


class CounterfactualResult:
    """Result of a counterfactual evaluation for one strategy."""

    __slots__ = (
        "strategy",
        "would_trade",
        "result",
        "estimated_pnl_percent",
        "exit_type",
    )

    def __init__(
        self,
        strategy: StrategyName,
        would_trade: bool = False,
        result: StrategyResult | None = None,
        estimated_pnl_percent: float = 0.0,
        exit_type: str = "",
    ):
        self.strategy = strategy
        self.would_trade = would_trade
        self.result = result
        self.estimated_pnl_percent = estimated_pnl_percent
        self.exit_type = exit_type


class CounterfactualEngine:
    """
    Same-opportunity benchmarking engine.

    For every token that qualifies for evaluation, runs ALL strategies
    in paper mode against the same token. This allows true comparison:

    TOKEN ABC:
      FAST_SCALPER: would trade, est. +9.2% NET
      MOMENTUM_RUNNER: would trade, est. +31.7% NET
      SMART_WALLET: no signal, NO TRADE

    Stores counterfactual results so the bot can learn:
    "What would have happened if I had used another strategy?"
    """

    def __init__(
        self,
        config: AdaptiveConfig | None = None,
        performance_db: StrategyPerformanceDB | None = None,
    ) -> None:
        self._config = config or AdaptiveConfig()
        self._perf_db = performance_db or StrategyPerformanceDB()

        self._strategies: dict[StrategyName, BaseStrategy] = {
            StrategyName.FAST_SCALPER: FastScalperStrategy(self._config),
            StrategyName.MOMENTUM_RUNNER: MomentumRunnerStrategy(self._config),
            StrategyName.SMART_WALLET_FOLLOWER: SmartWalletFollowerStrategy(self._config),
        }

        self._results: list[dict] = []

    def evaluate_all(
        self,
        opportunity: TokenOpportunity,
        selected_strategy: StrategyName,
        actual_pnl_percent: float | None = None,
        market_regime: MarketRegime = MarketRegime.UNKNOWN,
        capital_stage: CapitalStage = CapitalStage.STAGE_1,
    ) -> dict[StrategyName, CounterfactualResult]:
        """
        Run all strategies against the same opportunity.

        Args:
            opportunity: The token being evaluated.
            selected_strategy: Which strategy was actually chosen.
            actual_pnl_percent: If trade completed, what was the actual PnL.
            market_regime: Current market regime.
            capital_stage: Current capital stage.

        Returns:
            Dict of strategy → CounterfactualResult for each strategy.
        """
        results: dict[StrategyName, CounterfactualResult] = {}

        for name, strategy in self._strategies.items():
            # Skip the actually selected strategy
            if name == selected_strategy:
                continue

            strat_result = strategy.evaluate(opportunity)

            cf = CounterfactualResult(
                strategy=name,
                would_trade=strat_result.should_trade,
                result=strat_result,
            )

            if strat_result.should_trade:
                # Estimate what PnL would have been
                cf.estimated_pnl_percent = self._estimate_pnl(
                    strat_result, opportunity
                )
                cf.exit_type = self._determine_likely_exit(strat_result)

            results[name] = cf

        # Store for reporting
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mint_address": opportunity.mint_address,
            "symbol": opportunity.symbol,
            "selected_strategy": selected_strategy.value,
            "actual_pnl": actual_pnl_percent,
            "market_regime": market_regime.value,
            "capital_stage": capital_stage.value,
            "counterfactuals": {
                name.value: {
                    "would_trade": cf.would_trade,
                    "estimated_pnl": cf.estimated_pnl_percent,
                    "exit_type": cf.exit_type,
                    "confidence": cf.result.confidence if cf.result else 0,
                }
                for name, cf in results.items()
            },
        }
        self._results.append(record)

        # Record counterfactual trades in performance DB
        for name, cf in results.items():
            if cf.would_trade:
                trade_record = StrategyTradeRecord(
                    id=f"cf-{uuid.uuid4().hex[:12]}",
                    strategy=name,
                    mint_address=opportunity.mint_address,
                    symbol=opportunity.symbol,
                    timestamp=datetime.now(timezone.utc),
                    market_regime=market_regime,
                    capital_stage=capital_stage,
                    net_pnl_percent=cf.estimated_pnl_percent,
                    exit_type=ExitType.TARGET_HIT,
                    was_selected=False,  # Counterfactual
                    security_score=opportunity.security_score,
                    liquidity_usd=opportunity.liquidity_usd,
                    organic_demand_score=opportunity.momentum.organic_demand_score,
                    momentum_score=opportunity.momentum.momentum_score,
                )
                self._perf_db.record_trade(trade_record)

        return results

    def _estimate_pnl(
        self,
        result: StrategyResult,
        opportunity: TokenOpportunity,
    ) -> float:
        """
        Estimate what the PnL would have been for a counterfactual trade.

        Uses the strategy's exit plan and current token momentum to
        make a rough estimate.
        """
        if not result.exit_steps:
            return 0.0

        # Simple estimation: use price momentum to predict outcome
        momentum = opportunity.momentum.momentum_score
        organic = opportunity.momentum.organic_demand_score

        # Base estimate from first target
        first_target = result.exit_steps[0].target_percent

        # Probability of hitting target based on momentum
        if momentum > 70:
            hit_probability = 0.7
        elif momentum > 50:
            hit_probability = 0.5
        elif momentum > 30:
            hit_probability = 0.3
        else:
            hit_probability = 0.15

        # Expected PnL: weighted average of target hit vs. typical loss
        expected_gain = first_target * 0.85  # 85% of target after fees
        expected_loss = -8.0  # typical loss estimate

        estimated = hit_probability * expected_gain + (1 - hit_probability) * expected_loss

        # Multi-exit strategies can capture more upside
        if len(result.exit_steps) > 1 and momentum > 60:
            # Bonus for trailing stop potential
            estimated *= 1.3

        return round(estimated, 1)

    def _determine_likely_exit(self, result: StrategyResult) -> str:
        """Determine the most likely exit type for a counterfactual."""
        if not result.exit_steps:
            return "unknown"

        if len(result.exit_steps) > 1:
            return "partial_profit"

        return "target_hit"

    def get_recent_results(self, limit: int = 50) -> list[dict]:
        """Get recent counterfactual results."""
        return self._results[-limit:]

    def get_strategy_comparison(self) -> dict[str, dict]:
        """
        Get aggregate comparison across all counterfactual tests.
        Shows which strategy would have been best overall.
        """
        comparison: dict[str, dict] = {}

        for name in self._strategies:
            trades = 0
            would_trade = 0
            total_est_pnl = 0.0

            for record in self._results:
                cf = record.get("counterfactuals", {}).get(name.value)
                if cf:
                    trades += 1
                    if cf["would_trade"]:
                        would_trade += 1
                        total_est_pnl += cf["estimated_pnl"]

            comparison[name.value] = {
                "opportunities_seen": trades,
                "would_have_traded": would_trade,
                "trade_rate": f"{would_trade/trades:.0%}" if trades > 0 else "0%",
                "estimated_total_pnl": f"{total_est_pnl:.1f}%",
                "avg_estimated_pnl": (
                    f"{total_est_pnl/would_trade:.1f}%"
                    if would_trade > 0 else "N/A"
                ),
            }

        return comparison

    def to_dict(self) -> dict:
        return {
            "total_evaluations": len(self._results),
            "comparison": self.get_strategy_comparison(),
        }

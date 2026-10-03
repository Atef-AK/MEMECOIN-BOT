"""
Strategy fitness scoring engine.
Calculates a composite fitness score that does NOT rank strategies
by win rate alone. Uses expectancy, profit factor, risk-adjusted return,
drawdown quality, execution quality, and statistical confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.adaptive.intelligence.confidence import (
    sample_confidence_penalty,
    wilson_lower_bound,
)
from backend.adaptive.performance.rolling_stats import RollingStats, WindowStats
from backend.models.adaptive import AdaptiveConfig, StrategyName, StrategyStatus

logger = logging.getLogger(__name__)


@dataclass
class FitnessResult:
    """Detailed fitness score with component breakdown."""
    strategy: StrategyName
    fitness_score: float = 0.0
    confidence_adjusted_fitness: float = 0.0
    status: StrategyStatus = StrategyStatus.EXPERIMENTAL

    # Components (0-100 each, before weighting)
    expectancy_score: float = 0.0
    profit_factor_score: float = 0.0
    risk_adjusted_score: float = 0.0
    drawdown_score: float = 0.0
    win_rate_score: float = 0.0
    execution_score: float = 0.0
    confidence_score: float = 0.0

    # Raw metrics
    trades: int = 0
    win_rate: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    confidence_penalty: float = 1.0

    def summary(self) -> str:
        return (
            f"{self.strategy.value}: fitness={self.fitness_score:.1f} "
            f"(adj={self.confidence_adjusted_fitness:.1f}) "
            f"status={self.status.value} trades={self.trades} "
            f"expect={self.expectancy:.1f}% PF={self.profit_factor:.2f} "
            f"WR={self.win_rate:.0%} DD={self.max_drawdown:.1f}%"
        )


class StrategyFitnessEngine:
    """
    Calculates strategy fitness scores.

    Fitness formula:
        30% EXPECTANCY
        20% PROFIT FACTOR
        15% RISK-ADJUSTED RETURN
        15% MAX-DRAWDOWN QUALITY
        10% WIN-RATE QUALITY
         5% EXECUTION QUALITY
         5% STATISTICAL CONFIDENCE

    A strategy with 70% win rate but large losses WILL NOT automatically
    beat a strategy with 55% win rate and much better expectancy.
    """

    def __init__(self, config: AdaptiveConfig | None = None) -> None:
        self._config = config or AdaptiveConfig()

    def calculate(
        self,
        strategy: StrategyName,
        stats: RollingStats,
        window: int | None = None,
    ) -> FitnessResult:
        """
        Calculate fitness score for a strategy.

        Args:
            strategy: Which strategy to score.
            stats: Rolling stats for this strategy.
            window: Which window to use (None = all trades).

        Returns:
            FitnessResult with composite score and breakdown.
        """
        cfg = self._config
        ws = stats.get_stats(window)
        result = FitnessResult(strategy=strategy, trades=ws.trades)

        if ws.trades == 0:
            return result

        # Determine strategy status
        result.status = self._get_status(ws.trades)

        # Store raw metrics
        result.win_rate = ws.win_rate
        result.expectancy = ws.expectancy
        result.profit_factor = ws.profit_factor
        result.max_drawdown = ws.max_drawdown

        # ─── Component 1: Expectancy (30%) ────────────────────
        # Normalize: -10% to +20% → 0 to 100
        result.expectancy_score = self._normalize(
            ws.expectancy, low=-10, high=20, clamp=True
        )

        # ─── Component 2: Profit Factor (20%) ─────────────────
        # Normalize: 0 to 5 → 0 to 100
        pf = min(ws.profit_factor, 10)  # Cap at 10 to prevent outlier dominance
        result.profit_factor_score = self._normalize(
            pf, low=0, high=5, clamp=True
        )

        # ─── Component 3: Risk-Adjusted Return (15%) ──────────
        # Expectancy / max_drawdown (like a simplified Calmar ratio)
        if ws.max_drawdown > 0:
            risk_adj = ws.expectancy / ws.max_drawdown * 100
        elif ws.expectancy > 0:
            risk_adj = 100  # No drawdown with positive expectancy = perfect
        else:
            risk_adj = 0
        result.risk_adjusted_score = self._normalize(
            risk_adj, low=0, high=100, clamp=True
        )

        # ─── Component 4: Drawdown Quality (15%) ──────────────
        # Lower drawdown = higher score
        # 0% DD = 100, 50% DD = 0
        result.drawdown_score = max(
            0, 100 - ws.max_drawdown * 2
        )

        # ─── Component 5: Win Rate Quality (10%) ──────────────
        # Use Wilson lower bound to penalize small samples
        wilson_wr = wilson_lower_bound(ws.wins, ws.trades)
        result.win_rate_score = wilson_wr * 100

        # ─── Component 6: Execution Quality (5%) ─────────────
        exec_score = 100.0
        exec_score -= ws.execution_failure_rate * 200  # -2 per % failure
        exec_score -= min(50, ws.avg_slippage_bps / 5)   # -1 per 5bps slippage
        exec_score -= ws.emergency_exit_rate * 100        # -1 per % emergency
        result.execution_score = max(0, exec_score)

        # ─── Component 7: Confidence (5%) ─────────────────────
        result.confidence_penalty = sample_confidence_penalty(
            ws.trades,
            min_trades=cfg.strategy_min_trades,
            confident_trades=cfg.strategy_confident_trades,
        )
        result.confidence_score = result.confidence_penalty * 100

        # ─── Composite Fitness ────────────────────────────────
        fitness = (
            result.expectancy_score * cfg.fitness_weight_expectancy
            + result.profit_factor_score * cfg.fitness_weight_profit_factor
            + result.risk_adjusted_score * cfg.fitness_weight_risk_adjusted
            + result.drawdown_score * cfg.fitness_weight_drawdown
            + result.win_rate_score * cfg.fitness_weight_win_rate
            + result.execution_score * cfg.fitness_weight_execution
            + result.confidence_score * cfg.fitness_weight_confidence
        )

        result.fitness_score = min(100, max(0, fitness))

        # Confidence-adjusted fitness (penalizes small samples)
        result.confidence_adjusted_fitness = (
            result.fitness_score * result.confidence_penalty
        )

        return result

    def _get_status(self, trades: int) -> StrategyStatus:
        """Determine strategy status based on trade count."""
        if trades >= self._config.strategy_confident_trades:
            return StrategyStatus.ESTABLISHED
        elif trades >= self._config.strategy_min_trades:
            return StrategyStatus.DEVELOPING
        return StrategyStatus.EXPERIMENTAL

    @staticmethod
    def _normalize(
        value: float,
        low: float,
        high: float,
        clamp: bool = True,
    ) -> float:
        """Normalize a value from [low, high] to [0, 100]."""
        if high == low:
            return 50.0
        normalized = (value - low) / (high - low) * 100
        if clamp:
            return max(0, min(100, normalized))
        return normalized

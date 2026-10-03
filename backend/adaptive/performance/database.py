"""
Strategy performance database.
Maintains separate statistics per strategy, regime, and capital stage.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from backend.adaptive.performance.rolling_stats import RollingStats, TradeResult
from backend.models.adaptive import (
    CapitalStage,
    MarketRegime,
    StrategyName,
    StrategyTradeRecord,
)

logger = logging.getLogger(__name__)


class StrategyPerformanceDB:
    """
    Maintains completely separate statistics for every strategy.

    Tracks performance across:
    - Overall
    - Per market regime
    - Per capital stage
    - Per regime+stage combination

    Enables the selector to ask:
    "How does FAST_SCALPER perform in HIGH_MOMENTUM at STAGE_1?"
    """

    def __init__(self, windows: list[int] | None = None) -> None:
        self._windows = windows or [20, 50, 100, 200]

        # Overall stats per strategy
        self._overall: dict[StrategyName, RollingStats] = {}

        # Per-regime stats
        self._by_regime: dict[
            tuple[StrategyName, MarketRegime], RollingStats
        ] = defaultdict(lambda: RollingStats(self._windows))

        # Per-capital-stage stats
        self._by_stage: dict[
            tuple[StrategyName, CapitalStage], RollingStats
        ] = defaultdict(lambda: RollingStats(self._windows))

        # All trade records (for persistence)
        self._trades: list[StrategyTradeRecord] = []

        # Initialize overall stats for each strategy
        for strat in StrategyName:
            if strat != StrategyName.NO_TRADE:
                self._overall[strat] = RollingStats(self._windows)

    def record_trade(self, record: StrategyTradeRecord) -> None:
        """Record a completed trade and update all relevant statistics."""
        self._trades.append(record)

        trade_result = TradeResult(
            net_pnl_percent=record.net_pnl_percent,
            is_win=record.net_pnl_percent > 0,
            time_in_trade=record.time_in_trade_seconds,
            exit_type=record.exit_type.value if hasattr(record.exit_type, 'value') else str(record.exit_type),
            execution_success=record.execution_success,
            slippage_bps=record.slippage_sol * 10000 / record.position_size_sol
            if record.position_size_sol > 0 else 0,
        )

        # Update overall stats
        if record.strategy in self._overall:
            self._overall[record.strategy].add_trade(trade_result)
        else:
            self._overall[record.strategy] = RollingStats(self._windows)
            self._overall[record.strategy].add_trade(trade_result)

        # Update per-regime stats
        regime_key = (record.strategy, record.market_regime)
        self._by_regime[regime_key].add_trade(trade_result)

        # Update per-stage stats
        stage_key = (record.strategy, record.capital_stage)
        self._by_stage[stage_key].add_trade(trade_result)

        logger.debug(
            f"Recorded trade for {record.strategy.value}: "
            f"PnL {record.net_pnl_percent:+.1f}% "
            f"({record.market_regime.value}/{record.capital_stage.value})"
        )

    def get_overall_stats(self, strategy: StrategyName) -> RollingStats:
        """Get overall rolling stats for a strategy."""
        if strategy not in self._overall:
            self._overall[strategy] = RollingStats(self._windows)
        return self._overall[strategy]

    def get_regime_stats(
        self, strategy: StrategyName, regime: MarketRegime
    ) -> RollingStats:
        """Get rolling stats for a strategy in a specific regime."""
        return self._by_regime[(strategy, regime)]

    def get_stage_stats(
        self, strategy: StrategyName, stage: CapitalStage
    ) -> RollingStats:
        """Get rolling stats for a strategy at a specific capital stage."""
        return self._by_stage[(strategy, stage)]

    def get_comparable_trades(
        self,
        strategy: StrategyName,
        regime: MarketRegime | None = None,
        stage: CapitalStage | None = None,
        min_liquidity: float | None = None,
        max_token_age: float | None = None,
    ) -> list[StrategyTradeRecord]:
        """
        Get trades comparable to current conditions.
        Used by the selector to find historically similar situations.
        """
        results = [t for t in self._trades if t.strategy == strategy]

        if regime is not None:
            results = [t for t in results if t.market_regime == regime]

        if stage is not None:
            results = [t for t in results if t.capital_stage == stage]

        if min_liquidity is not None:
            results = [t for t in results if t.liquidity_usd >= min_liquidity]

        return results

    def get_trade_count(self, strategy: StrategyName) -> int:
        """Get total trade count for a strategy."""
        if strategy in self._overall:
            return self._overall[strategy].total_trades
        return 0

    def get_all_strategy_summaries(self) -> dict[str, dict]:
        """Get summary stats for all strategies."""
        summaries = {}
        for strategy, stats in self._overall.items():
            if stats.total_trades == 0:
                continue
            overall = stats.get_stats()
            summaries[strategy.value] = {
                "trades": overall.trades,
                "win_rate": f"{overall.win_rate:.1%}",
                "expectancy": f"{overall.expectancy:.1f}%",
                "profit_factor": f"{overall.profit_factor:.2f}",
                "max_drawdown": f"{overall.max_drawdown:.1f}%",
                "total_pnl": f"{overall.total_net_pnl:.1f}%",
                "avg_holding": f"{overall.avg_holding_seconds:.0f}s",
            }
        return summaries

    def to_dict(self) -> dict:
        return {
            "total_trades": sum(
                s.total_trades for s in self._overall.values()
            ),
            "strategies": self.get_all_strategy_summaries(),
        }

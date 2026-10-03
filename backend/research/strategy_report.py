"""
Strategy research report — CLI tool.
Usage: python -m backend.research.strategy_report
"""

from __future__ import annotations

import sys


def main():
    """Generate a comprehensive strategy performance report."""
    # Lazy import to avoid circular imports
    from backend.adaptive.performance.database import StrategyPerformanceDB
    from backend.adaptive.intelligence.fitness import StrategyFitnessEngine
    from backend.models.adaptive import AdaptiveConfig, StrategyName

    config = AdaptiveConfig()
    db = StrategyPerformanceDB()
    fitness_engine = StrategyFitnessEngine(config)

    print("=" * 60)
    print(" STRATEGY PERFORMANCE REPORT")
    print("=" * 60)

    strategies = [
        StrategyName.FAST_SCALPER,
        StrategyName.MOMENTUM_RUNNER,
        StrategyName.SMART_WALLET_FOLLOWER,
    ]

    for strategy in strategies:
        stats = db.get_overall_stats(strategy)
        fitness = fitness_engine.calculate(strategy, stats)

        print(f"\n{'─' * 60}")
        print(f" {strategy.value.upper()}")
        print(f"{'─' * 60}")
        print(f" Status:              {fitness.status.value}")
        print(f" Trades:              {fitness.trades}")
        print(f" Win Rate:            {fitness.win_rate:.1%}")
        print(f" Expectancy:          {fitness.expectancy:+.1f}%")
        print(f" Profit Factor:       {fitness.profit_factor:.2f}")
        print(f" Max Drawdown:        {fitness.max_drawdown:.1f}%")
        print(f" Fitness Score:       {fitness.fitness_score:.1f}")
        print(f" Conf-Adj Fitness:    {fitness.confidence_adjusted_fitness:.1f}")
        print(f" Confidence Penalty:  {fitness.confidence_penalty:.0%}")
        print()
        print(f" Components:")
        print(f"   Expectancy (30%):    {fitness.expectancy_score:.1f}")
        print(f"   Profit Factor (20%): {fitness.profit_factor_score:.1f}")
        print(f"   Risk-Adjusted (15%): {fitness.risk_adjusted_score:.1f}")
        print(f"   Drawdown (15%):      {fitness.drawdown_score:.1f}")
        print(f"   Win Rate (10%):      {fitness.win_rate_score:.1f}")
        print(f"   Execution (5%):      {fitness.execution_score:.1f}")
        print(f"   Confidence (5%):     {fitness.confidence_score:.1f}")

        # Rolling window comparison
        windows = stats.get_all_windows()
        if windows:
            print(f"\n Rolling Windows:")
            print(f"   {'Window':>8} {'Trades':>7} {'WR':>6} {'Expect':>8} {'PF':>6} {'PnL':>8}")
            for w, ws in sorted(windows.items()):
                if ws.trades > 0:
                    print(
                        f"   {w:>8} {ws.trades:>7} "
                        f"{ws.win_rate:>5.0%} {ws.expectancy:>+7.1f}% "
                        f"{ws.profit_factor:>5.2f} {ws.total_net_pnl:>+7.1f}%"
                    )

    print(f"\n{'=' * 60}")
    print(" BEST SUITED UNDER CURRENT CONDITIONS")
    print(f"{'=' * 60}")

    # Find best by confidence-adjusted fitness
    best = None
    best_fitness = 0
    for strategy in strategies:
        stats = db.get_overall_stats(strategy)
        fitness = fitness_engine.calculate(strategy, stats)
        if fitness.confidence_adjusted_fitness > best_fitness:
            best_fitness = fitness.confidence_adjusted_fitness
            best = strategy

    if best:
        print(f"\n Current best: {best.value.upper()}")
        print(f" Conf-Adj Fitness: {best_fitness:.1f}")
        print(f"\n Note: 'Best' means highest confidence-adjusted fitness")
        print(f" under current conditions, NOT permanent superiority.")
    else:
        print("\n No strategy has sufficient data for ranking.")

    print()


if __name__ == "__main__":
    main()

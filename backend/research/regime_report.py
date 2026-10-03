"""
Market regime report — CLI tool.
Usage: python -m backend.research.regime_report
"""

from __future__ import annotations


def main():
    """Generate a market regime performance report."""
    from backend.adaptive.performance.database import StrategyPerformanceDB
    from backend.adaptive.intelligence.fitness import StrategyFitnessEngine
    from backend.models.adaptive import AdaptiveConfig, MarketRegime, StrategyName

    config = AdaptiveConfig()
    db = StrategyPerformanceDB()
    fitness_engine = StrategyFitnessEngine(config)

    strategies = [
        StrategyName.FAST_SCALPER,
        StrategyName.MOMENTUM_RUNNER,
        StrategyName.SMART_WALLET_FOLLOWER,
    ]

    regimes = [r for r in MarketRegime if r != MarketRegime.UNKNOWN]

    print("=" * 80)
    print(" MARKET REGIME PERFORMANCE REPORT")
    print("=" * 80)

    for regime in regimes:
        print(f"\n{'━' * 80}")
        print(f" REGIME: {regime.value.upper()}")
        print(f"{'━' * 80}")

        header = f"  {'Strategy':<25} {'Trades':>7} {'WR':>6} {'Expect':>8} {'PF':>6} {'Fitness':>8}"
        print(header)
        print(f"  {'─' * 70}")

        best_name = None
        best_fitness = 0

        for s in strategies:
            regime_stats = db.get_regime_stats(s, regime)
            if regime_stats.total_trades == 0:
                print(f"  {s.value:<25} {'—':>7} {'—':>6} {'—':>8} {'—':>6} {'—':>8}")
                continue

            fitness = fitness_engine.calculate(s, regime_stats)
            overall = regime_stats.get_stats()

            print(
                f"  {s.value:<25} {overall.trades:>7} "
                f"{overall.win_rate:>5.0%} {overall.expectancy:>+7.1f}% "
                f"{overall.profit_factor:>5.2f} {fitness.confidence_adjusted_fitness:>7.1f}"
            )

            if fitness.confidence_adjusted_fitness > best_fitness:
                best_fitness = fitness.confidence_adjusted_fitness
                best_name = s

        if best_name:
            print(f"\n  → Best suited: {best_name.value}")
        else:
            print(f"\n  → No data for this regime")

    # Summary matrix
    print(f"\n{'=' * 80}")
    print(" REGIME × STRATEGY MATRIX (confidence-adjusted fitness)")
    print(f"{'=' * 80}")

    header = f"  {'Regime':<20}"
    for s in strategies:
        header += f" {s.value:>16}"
    header += f" {'BEST':>16}"
    print(header)
    print(f"  {'─' * 78}")

    for regime in regimes:
        row = f"  {regime.value:<20}"
        best_in_regime = ("none", 0.0)
        for s in strategies:
            regime_stats = db.get_regime_stats(s, regime)
            if regime_stats.total_trades == 0:
                row += f" {'—':>16}"
            else:
                fitness = fitness_engine.calculate(s, regime_stats)
                val = fitness.confidence_adjusted_fitness
                row += f" {val:>15.1f}"
                if val > best_in_regime[1]:
                    best_in_regime = (s.value, val)
        row += f" {best_in_regime[0]:>16}"
        print(row)

    print()


if __name__ == "__main__":
    main()

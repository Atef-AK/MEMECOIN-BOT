"""
Capital scaling report — CLI tool.
Usage: python -m backend.research.capital_scaling_report
"""

from __future__ import annotations


def main():
    """Generate a capital stage scaling analysis report."""
    from backend.adaptive.engines.capital import STAGE_RULES, CapitalEngine
    from backend.adaptive.performance.database import StrategyPerformanceDB
    from backend.adaptive.intelligence.fitness import StrategyFitnessEngine
    from backend.models.adaptive import (
        AdaptiveConfig,
        CapitalStage,
        StrategyName,
        CAPITAL_STAGE_BOUNDARIES,
    )

    config = AdaptiveConfig()
    db = StrategyPerformanceDB()
    fitness_engine = StrategyFitnessEngine(config)
    capital_engine = CapitalEngine(config)

    strategies = [
        StrategyName.FAST_SCALPER,
        StrategyName.MOMENTUM_RUNNER,
        StrategyName.SMART_WALLET_FOLLOWER,
    ]

    stages = list(CapitalStage)

    print("=" * 80)
    print(" CAPITAL SCALING REPORT")
    print("=" * 80)

    # Stage configuration
    print(f"\n{'─' * 80}")
    print(" CAPITAL STAGE CONFIGURATION")
    print(f"{'─' * 80}")

    for stage in stages:
        low, high = CAPITAL_STAGE_BOUNDARIES[stage]
        rules = STAGE_RULES[stage]
        high_str = f"{high:.2f}" if high != float("inf") else "∞"
        print(
            f"\n  {stage.value.upper()} ({low:.2f} – {high_str} SOL):"
        )
        print(f"    Max position:   {rules['max_position_percent']:.0f}% / {rules['max_position_sol']:.2f} SOL")
        print(f"    Max slippage:   {rules['max_slippage_bps']} bps")
        print(f"    Max impact:     {rules['max_impact_bps']} bps")
        print(f"    Min liquidity:  ${rules['min_liquidity_usd']:,}")

    # Per-stage strategy performance
    print(f"\n{'=' * 80}")
    print(" STRATEGY PERFORMANCE BY CAPITAL STAGE")
    print(f"{'=' * 80}")

    for stage in stages:
        print(f"\n{'━' * 80}")
        print(f" {stage.value.upper()}")
        print(f"{'━' * 80}")

        header = f"  {'Strategy':<25} {'Trades':>7} {'WR':>6} {'Expect':>8} {'PF':>6} {'PnL':>8}"
        print(header)
        print(f"  {'─' * 60}")

        for s in strategies:
            stage_stats = db.get_stage_stats(s, stage)
            if stage_stats.total_trades == 0:
                print(f"  {s.value:<25} {'—':>7} {'—':>6} {'—':>8} {'—':>6} {'—':>8}")
                continue

            overall = stage_stats.get_stats()
            print(
                f"  {s.value:<25} {overall.trades:>7} "
                f"{overall.win_rate:>5.0%} {overall.expectancy:>+7.1f}% "
                f"{overall.profit_factor:>5.2f} {overall.total_net_pnl:>+7.1f}%"
            )

    # Execution cost analysis
    print(f"\n{'=' * 80}")
    print(" EXECUTION COST ANALYSIS")
    print(f"{'=' * 80}")
    print(f"\n  Estimated costs at different position sizes and liquidity levels:\n")

    print(f"  {'Position':>10} {'Liquidity':>12} {'Slippage':>10} {'Impact':>10} {'Total':>10} {'Viable':>8}")
    print(f"  {'─' * 65}")

    test_cases = [
        (0.05, 5000),
        (0.10, 10000),
        (0.10, 50000),
        (0.25, 10000),
        (0.25, 50000),
        (0.50, 25000),
        (0.50, 100000),
        (1.00, 50000),
        (1.00, 200000),
    ]

    for pos_sol, liq_usd in test_cases:
        capital_engine.update_balance(pos_sol * 3)  # Assume 3x position = capital
        result = capital_engine.calculate_position(
            StrategyName.FAST_SCALPER, liq_usd
        )
        viable = "✅" if result.is_viable else "❌"
        print(
            f"  {pos_sol:>8.2f} SOL "
            f"${liq_usd:>10,} "
            f"{result.estimated_slippage_bps:>8.0f}bps "
            f"{result.estimated_impact_bps:>8.0f}bps "
            f"{result.estimated_total_cost_bps:>8.0f}bps "
            f"{viable:>8}"
        )

    print()


if __name__ == "__main__":
    main()

"""
Strategy comparison report — CLI tool.
Usage: python -m backend.research.compare_strategies
"""

from __future__ import annotations


def main():
    """Generate a side-by-side strategy comparison report."""
    from backend.adaptive.performance.database import StrategyPerformanceDB
    from backend.adaptive.intelligence.fitness import StrategyFitnessEngine
    from backend.adaptive.intelligence.confidence import wilson_lower_bound
    from backend.models.adaptive import AdaptiveConfig, StrategyName

    config = AdaptiveConfig()
    db = StrategyPerformanceDB()
    fitness_engine = StrategyFitnessEngine(config)

    strategies = [
        StrategyName.FAST_SCALPER,
        StrategyName.MOMENTUM_RUNNER,
        StrategyName.SMART_WALLET_FOLLOWER,
    ]

    print("=" * 80)
    print(" STRATEGY COMPARISON REPORT")
    print("=" * 80)

    # Header
    header = f"{'Metric':<25}"
    for s in strategies:
        header += f" {s.value:>16}"
    print(f"\n{header}")
    print("─" * 80)

    # Collect data
    data = {}
    for s in strategies:
        stats = db.get_overall_stats(s)
        fitness = fitness_engine.calculate(s, stats)
        overall = stats.get_stats()
        data[s] = {"fitness": fitness, "overall": overall, "stats": stats}

    # Rows
    metrics = [
        ("Trades", lambda d: f"{d['fitness'].trades}"),
        ("Win Rate", lambda d: f"{d['fitness'].win_rate:.1%}"),
        ("Wilson LB (95%)", lambda d: (
            f"{wilson_lower_bound(d['overall'].wins, d['overall'].trades):.1%}"
        )),
        ("Expectancy", lambda d: f"{d['fitness'].expectancy:+.1f}%"),
        ("Profit Factor", lambda d: f"{d['fitness'].profit_factor:.2f}"),
        ("Max Drawdown", lambda d: f"{d['fitness'].max_drawdown:.1f}%"),
        ("Total PnL", lambda d: f"{d['overall'].total_net_pnl:+.1f}%"),
        ("Avg Winner", lambda d: f"{d['overall'].avg_winner:.1f}%"),
        ("Avg Loser", lambda d: f"{d['overall'].avg_loser:.1f}%"),
        ("Max Consec Loss", lambda d: f"{d['overall'].max_consecutive_losses}"),
        ("Target Hit Rate", lambda d: f"{d['overall'].target_hit_rate:.0%}"),
        ("Emergency Rate", lambda d: f"{d['overall'].emergency_exit_rate:.0%}"),
        ("Avg Hold Time", lambda d: f"{d['overall'].avg_holding_seconds:.0f}s"),
        ("Avg Slippage", lambda d: f"{d['overall'].avg_slippage_bps:.0f}bps"),
        ("", lambda d: ""),
        ("Fitness Score", lambda d: f"{d['fitness'].fitness_score:.1f}"),
        ("Conf-Adjusted", lambda d: f"{d['fitness'].confidence_adjusted_fitness:.1f}"),
        ("Status", lambda d: d['fitness'].status.value),
    ]

    for name, getter in metrics:
        row = f"{name:<25}"
        for s in strategies:
            try:
                val = getter(data[s])
            except (ZeroDivisionError, AttributeError):
                val = "N/A"
            row += f" {val:>16}"
        print(row)

    # Degradation analysis
    print(f"\n{'=' * 80}")
    print(" DEGRADATION ANALYSIS")
    print("─" * 80)

    for s in strategies:
        stats = data[s]["stats"]
        if stats.total_trades >= 50:
            is_degraded, reason = stats.detect_degradation()
            status = "⚠️  DEGRADED" if is_degraded else "✅ HEALTHY"
            print(f" {s.value:<25} {status}: {reason}")
        else:
            print(f" {s.value:<25} ⏳ Insufficient data")

    # Verdict
    print(f"\n{'=' * 80}")
    print(" VERDICT")
    print("─" * 80)

    ranked = sorted(
        strategies,
        key=lambda s: data[s]["fitness"].confidence_adjusted_fitness,
        reverse=True,
    )

    for i, s in enumerate(ranked, 1):
        f = data[s]["fitness"]
        print(
            f" {i}. {s.value:<25} "
            f"adj_fitness={f.confidence_adjusted_fitness:.1f}"
        )

    if ranked:
        print(
            f"\n BEST SUITED UNDER CURRENT CONDITIONS: "
            f"{ranked[0].value.upper()}"
        )

    # Statistical significance warning
    min_trades = min(data[s]["fitness"].trades for s in strategies)
    if min_trades < 50:
        print(
            "\n ⚠️  WARNING: Differences may not be statistically meaningful "
            f"(min sample = {min_trades} trades)"
        )

    print()


if __name__ == "__main__":
    main()

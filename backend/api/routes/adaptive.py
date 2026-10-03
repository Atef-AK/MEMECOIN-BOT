"""
Strategy Intelligence API routes.
Dashboard endpoints for the adaptive multi-strategy engine.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/adaptive", tags=["adaptive"])


@router.get("/intelligence")
async def get_strategy_intelligence(request: Request):
    """
    Get comprehensive strategy intelligence.

    Returns:
    - Current best-suited strategy
    - Capital stage and balance
    - Market regime
    - All strategy fitness scores
    - Selection history
    - Counterfactual results
    - Loss protection status
    - Smart wallet stats
    """
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return adaptive.get_strategy_intelligence()


@router.get("/fitness")
async def get_fitness_table(request: Request):
    """
    Get strategy fitness comparison table.

    Returns fitness scores for all strategies with:
    - Win rate, expectancy, profit factor
    - Max drawdown
    - Raw and confidence-adjusted fitness
    - Status (experimental/developing/established/degraded/disabled)
    """
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return {"strategies": adaptive.get_strategy_fitness_table()}


@router.get("/regime")
async def get_market_regime(request: Request):
    """Get current market regime classification."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return adaptive.regime.to_dict()


@router.get("/capital")
async def get_capital_info(request: Request):
    """Get capital stage and position sizing info."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return adaptive.capital.to_dict()


@router.get("/performance")
async def get_performance(request: Request):
    """Get strategy performance database summary."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return adaptive.performance_db.to_dict()


@router.get("/performance/{strategy}")
async def get_strategy_performance(request: Request, strategy: str):
    """Get detailed performance for a specific strategy."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    from backend.models.adaptive import StrategyName
    try:
        strat_name = StrategyName(strategy)
    except ValueError:
        return {"error": f"Unknown strategy: {strategy}"}

    stats = adaptive.performance_db.get_overall_stats(strat_name)
    windows = stats.get_all_windows()

    return {
        "strategy": strategy,
        "total_trades": stats.total_trades,
        "windows": {
            str(k): {
                "trades": v.trades,
                "win_rate": f"{v.win_rate:.1%}",
                "expectancy": f"{v.expectancy:.1f}%",
                "profit_factor": f"{v.profit_factor:.2f}",
                "max_drawdown": f"{v.max_drawdown:.1f}%",
                "total_pnl": f"{v.total_net_pnl:.1f}%",
                "avg_holding": f"{v.avg_holding_seconds:.0f}s",
                "target_hit_rate": f"{v.target_hit_rate:.0%}",
                "emergency_rate": f"{v.emergency_exit_rate:.0%}",
            }
            for k, v in windows.items()
        },
    }


@router.get("/counterfactual")
async def get_counterfactual(request: Request):
    """Get counterfactual (same-opportunity benchmarking) results."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return {
        "comparison": adaptive.counterfactual.get_strategy_comparison(),
        "recent": adaptive.counterfactual.get_recent_results(limit=20),
    }


@router.get("/protection")
async def get_loss_protection(request: Request):
    """Get loss protection status."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return adaptive.loss_protection.to_dict()


@router.get("/smart-wallets")
async def get_smart_wallets(request: Request):
    """Get smart wallet database summary."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    return adaptive.smart_wallets.to_dict()


@router.post("/reset-daily")
async def reset_daily_protection(request: Request):
    """Reset daily loss protection counters."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    adaptive.loss_protection.reset_daily()
    return {"status": "ok", "message": "Daily counters reset"}


@router.post("/enable-strategy/{strategy}")
async def enable_strategy(request: Request, strategy: str):
    """Re-enable a disabled strategy."""
    adaptive = request.app.state.adaptive_manager
    if not adaptive:
        return {"error": "Adaptive engine not initialized"}

    from backend.models.adaptive import StrategyName
    try:
        strat_name = StrategyName(strategy)
    except ValueError:
        return {"error": f"Unknown strategy: {strategy}"}

    adaptive.loss_protection.re_enable_strategy(strat_name)
    return {"status": "ok", "message": f"Strategy {strategy} re-enabled"}

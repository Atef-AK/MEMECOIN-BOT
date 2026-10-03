"""
REST API routes for the dashboard and bot control.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config.settings import get_settings
from backend.core.kill_switch import get_kill_switch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# ── Response Models ──────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    mode: str
    uptime_seconds: float
    kill_switch_active: bool
    rpc_connected: bool
    telegram_connected: bool
    jupiter_connected: bool
    discovery_running: bool
    active_positions: int
    total_trades: int
    timestamp: str


class StatsResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl_sol: float
    expectancy: float
    profit_factor: float
    max_drawdown_sol: float
    max_consecutive_losses: int
    average_winner_sol: float
    average_loser_sol: float
    tokens_detected: int
    tokens_rejected: int


class KillSwitchRequest(BaseModel):
    action: str  # "activate" or "deactivate"
    reason: str = ""
    confirmation: str = ""


class ConfigResponse(BaseModel):
    paper_trading: bool
    live_trading: bool
    position_size_sol: float
    net_target_percent: float
    min_liquidity_usd: float
    max_top10_holder_percent: float
    max_single_holder_percent: float
    min_score: int
    max_slippage_bps: int
    max_daily_loss_percent: float
    max_consecutive_losses: int
    max_trades_per_hour: int


# ── Global state (set by main app) ──────────────────────

_strategy_manager = None
_start_time = datetime.utcnow()


def set_strategy_manager(manager: Any) -> None:
    global _strategy_manager
    _strategy_manager = manager


from backend.api.websocket.handler import get_recent_logs

# ── Health ───────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse)
async def health():
    settings = get_settings()
    kill_switch = get_kill_switch()
    now = datetime.utcnow()

    return HealthResponse(
        status="online",
        mode=settings.effective_mode,
        uptime_seconds=(now - _start_time).total_seconds(),
        kill_switch_active=kill_switch.is_active,
        rpc_connected=True,  # Would check actual connection
        telegram_connected=bool(settings.telegram_bot_token),
        jupiter_connected=bool(settings.jupiter_api_key),
        discovery_running=_strategy_manager is not None,
        active_positions=_strategy_manager.monitor.position_count if _strategy_manager else 0,
        total_trades=_strategy_manager.stats.total_trades if _strategy_manager else 0,
        timestamp=now.isoformat(),
    )


# ── Logs ─────────────────────────────────────────────────

@router.get("/logs")
async def get_logs():
    """Retrieve recent log events for live dashboard streaming."""
    return {"logs": get_recent_logs()}



# ── Stats ────────────────────────────────────────────────

@router.get("/stats", response_model=StatsResponse)
async def get_stats():
    if not _strategy_manager:
        raise HTTPException(status_code=503, detail="Bot not running")

    stats = _strategy_manager.stats
    return StatsResponse(
        total_trades=stats.total_trades,
        winning_trades=stats.winning_trades,
        losing_trades=stats.losing_trades,
        win_rate=stats.win_rate,
        total_pnl_sol=stats.total_pnl_sol,
        expectancy=stats.expectancy,
        profit_factor=stats.profit_factor,
        max_drawdown_sol=stats.max_drawdown_sol,
        max_consecutive_losses=stats.max_consecutive_losses,
        average_winner_sol=stats.average_winner_sol,
        average_loser_sol=stats.average_loser_sol,
        tokens_detected=_strategy_manager._tokens_detected,
        tokens_rejected=_strategy_manager._tokens_rejected,
    )


# ── Trades ───────────────────────────────────────────────

@router.get("/trades")
async def get_trades(limit: int = 50, offset: int = 0):
    if not _strategy_manager:
        return {"trades": []}

    trades = _strategy_manager.completed_trades
    total = len(trades)
    page = trades[offset: offset + limit]

    return {
        "trades": [t.model_dump() for t in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Positions ────────────────────────────────────────────

@router.get("/positions")
async def get_positions():
    if not _strategy_manager:
        return {"positions": []}

    positions = _strategy_manager.monitor.active_positions
    return {
        "positions": [
            {
                "trade_id": tid,
                "symbol": state.trade.symbol,
                "mint_address": state.trade.mint_address,
                "entry_price": state.trade.entry_price_sol,
                "current_price": state.current_price_sol,
                "unrealized_pnl_percent": state.unrealized_pnl_percent,
                "checks_count": state.checks_count,
                "entry_time": state.trade.entry_time.isoformat() if state.trade.entry_time else None,
            }
            for tid, state in positions.items()
        ]
    }


# ── Candidates ───────────────────────────────────────────

@router.get("/candidates")
async def get_candidates(limit: int = 100, status: str = ""):
    if not _strategy_manager:
        return {"candidates": []}

    candidates = _strategy_manager.candidates
    if status:
        candidates = [c for c in candidates if c.status.value == status]

    return {
        "candidates": [
            {
                "mint_address": c.token.mint_address,
                "symbol": c.token.symbol,
                "name": c.token.name,
                "status": c.status.value,
                "total_score": c.total_score,
                "security_score": c.security_score,
                "liquidity_score": c.liquidity_score,
                "holder_score": c.holder_score,
                "dev_score": c.dev_score,
                "social_score": c.social_score,
                "market_score": c.market_score,
                "rejection_reason": c.rejection_reason.value if c.rejection_reason else None,
                "rejection_detail": c.rejection_detail or ("; ".join(c.critical_failures) if c.critical_failures else (c.rejection_reason.value if c.rejection_reason else None)),
                "liquidity_usd": c.token.current_liquidity_usd,
                "dex": c.token.dex.value,
                "discovered_at": c.token.discovered_at.isoformat(),
            }
            for c in candidates[-limit:]
        ],
        "total": len(candidates),
    }


# ── Kill Switch ──────────────────────────────────────────

@router.get("/kill-switch")
async def get_kill_switch_status():
    ks = get_kill_switch()
    return ks.to_dict()


@router.post("/kill-switch")
async def control_kill_switch(request: KillSwitchRequest):
    ks = get_kill_switch()

    if request.action == "activate":
        await ks.activate(reason=request.reason or "API activation")
        return {"status": "activated", "reason": request.reason}
    elif request.action == "deactivate":
        success = await ks.deactivate(confirmation=request.confirmation)
        if success:
            return {"status": "deactivated"}
        else:
            raise HTTPException(
                status_code=403,
                detail="Invalid confirmation. Use 'CONFIRM_RESET_KILL_SWITCH'",
            )
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


# ── Configuration ────────────────────────────────────────

@router.get("/config", response_model=ConfigResponse)
async def get_config():
    settings = get_settings()
    return ConfigResponse(
        paper_trading=settings.paper_trading,
        live_trading=settings.live_trading,
        position_size_sol=settings.position_size_sol,
        net_target_percent=settings.net_target_percent,
        min_liquidity_usd=settings.min_liquidity_usd,
        max_top10_holder_percent=settings.max_top10_holder_percent,
        max_single_holder_percent=settings.max_single_holder_percent,
        min_score=settings.min_score,
        max_slippage_bps=settings.max_slippage_bps,
        max_daily_loss_percent=settings.max_daily_loss_percent,
        max_consecutive_losses=settings.max_consecutive_losses,
        max_trades_per_hour=settings.max_trades_per_hour,
    )


# ── Discovery Stats ─────────────────────────────────────

@router.get("/discovery/stats")
async def get_discovery_stats():
    if not _strategy_manager:
        return {}
    return _strategy_manager.discovery.stats

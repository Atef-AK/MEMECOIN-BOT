"""
Pydantic schemas for trade execution and tracking.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TradeType(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(str, Enum):
    PENDING = "pending"
    QUOTING = "quoting"
    EXECUTING = "executing"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class ExitReason(str, Enum):
    TARGET_HIT = "target_hit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    PARTIAL_TAKE_PROFIT = "partial_take_profit"
    EMERGENCY_MINT_CHANGE = "emergency_mint_change"
    EMERGENCY_FREEZE_CHANGE = "emergency_freeze_change"
    EMERGENCY_LIQUIDITY_REMOVAL = "emergency_liquidity_removal"
    EMERGENCY_DEV_SELL = "emergency_dev_sell"
    EMERGENCY_INSIDER_DUMP = "emergency_insider_dump"
    EMERGENCY_PRICE_CRASH = "emergency_price_crash"
    EMERGENCY_TRADE_IMPOSSIBLE = "emergency_trade_impossible"
    EMERGENCY_DANGEROUS_EXTENSION = "emergency_dangerous_extension"
    EMERGENCY_UNKNOWN_EVENT = "emergency_unknown_event"
    MANUAL = "manual"
    KILL_SWITCH = "kill_switch"
    TIMEOUT = "timeout"
    RISK_LIMIT = "risk_limit"


class SwapQuote(BaseModel):
    """Executable swap quote from Jupiter."""

    input_mint: str
    output_mint: str
    input_amount_lamports: int = 0
    output_amount_raw: int = 0

    input_amount_sol: float = 0.0
    expected_output_tokens: float = 0.0
    price_impact_percent: float = 0.0
    slippage_bps: int = 0

    # Costs
    priority_fee_lamports: int = 0
    platform_fee_lamports: int = 0
    estimated_total_cost_sol: float = 0.0

    # Derived
    effective_price: float = 0.0
    break_even_price: float = 0.0
    target_price: float = 0.0  # +10% NET

    # Route info
    route_plan: str = ""
    route_dexes: list[str] = Field(default_factory=list)

    # Quote metadata
    quoted_at: datetime = Field(default_factory=datetime.utcnow)
    is_executable: bool = False
    rejection_reason: str = ""


class TradeRecord(BaseModel):
    """Complete trade record for paper or live trades."""

    id: str = ""
    trade_type: TradeType = TradeType.PAPER
    mint_address: str = ""
    pool_address: str = ""
    symbol: str = ""

    # Entry
    entry_time: Optional[datetime] = None
    entry_price_sol: float = 0.0
    entry_price_usd: float = 0.0
    entry_amount_sol: float = 0.0
    entry_tokens: float = 0.0
    entry_slippage_bps: float = 0.0
    entry_fee_sol: float = 0.0
    entry_tx_signature: str = ""
    entry_status: TradeStatus = TradeStatus.PENDING

    # Exit
    exit_time: Optional[datetime] = None
    exit_price_sol: float = 0.0
    exit_price_usd: float = 0.0
    exit_amount_sol: float = 0.0
    exit_slippage_bps: float = 0.0
    exit_fee_sol: float = 0.0
    exit_tx_signature: str = ""
    exit_status: TradeStatus = TradeStatus.PENDING
    exit_reason: Optional[ExitReason] = None

    # PnL
    gross_pnl_sol: float = 0.0
    total_fees_sol: float = 0.0
    total_slippage_sol: float = 0.0
    net_pnl_sol: float = 0.0
    net_pnl_percent: float = 0.0

    # Context at trade time
    score: float = 0.0
    liquidity_usd: float = 0.0
    market_cap: float = 0.0
    holder_top10_percent: float = 0.0
    token_age_seconds: float = 0.0
    dex: str = ""
    strategy_name: str = "Fast Scalper"
    exit_decision: str = ""

    # Timing
    time_to_exit_seconds: float = 0.0
    execution_latency_ms: float = 0.0


class PositionState(BaseModel):
    """Active position being monitored."""

    trade: TradeRecord
    current_price_sol: float = 0.0
    current_price_usd: float = 0.0
    unrealized_pnl_sol: float = 0.0
    unrealized_pnl_percent: float = 0.0

    # Monitoring
    last_checked: datetime = Field(default_factory=datetime.utcnow)
    checks_count: int = 0
    highest_price_sol: float = 0.0
    lowest_price_sol: float = 0.0

    # Emergency state
    emergency_triggered: bool = False
    emergency_reason: str = ""


class TradingStats(BaseModel):
    """Aggregate trading statistics."""

    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    total_pnl_sol: float = 0.0
    average_winner_sol: float = 0.0
    average_loser_sol: float = 0.0

    expectancy: float = 0.0  # (win_rate * avg_win) - (loss_rate * avg_loss)
    profit_factor: float = 0.0

    max_drawdown_sol: float = 0.0
    max_drawdown_percent: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    current_consecutive_losses: int = 0

    median_time_to_target_seconds: float = 0.0
    median_time_to_emergency_seconds: float = 0.0

    average_slippage_bps: float = 0.0
    average_fees_sol: float = 0.0

    daily_loss_percent: float = 0.0
    trades_this_hour: int = 0

    # Per-day tracking
    today_trades: int = 0
    today_pnl_sol: float = 0.0

"""
SQLAlchemy ORM models for the adaptive strategy engine.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, Index,
    Integer, String, Text,
)
from backend.database.models import Base


class StrategyTradeDB(Base):
    """Every strategy trade (real and counterfactual)."""
    __tablename__ = "strategy_trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), unique=True, nullable=False, index=True)
    strategy = Column(String(32), nullable=False, index=True)
    mint_address = Column(String(64), nullable=False, index=True)
    pool_address = Column(String(64), default="")
    symbol = Column(String(32), default="")
    timestamp = Column(DateTime, default=datetime.utcnow)

    # Context
    market_regime = Column(String(32), default="unknown")
    capital_stage = Column(String(16), default="stage_1")
    bonding_curve_state = Column(String(32), default="not_applicable")

    # Trade data
    entry_price_sol = Column(Float, default=0.0)
    exit_price_sol = Column(Float, default=0.0)
    position_size_sol = Column(Float, default=0.0)
    gross_pnl_sol = Column(Float, default=0.0)
    fees_sol = Column(Float, default=0.0)
    slippage_sol = Column(Float, default=0.0)
    net_pnl_sol = Column(Float, default=0.0)
    net_pnl_percent = Column(Float, default=0.0)

    # Excursions
    max_favorable_excursion = Column(Float, default=0.0)
    max_adverse_excursion = Column(Float, default=0.0)

    # Timing
    time_in_trade_seconds = Column(Float, default=0.0)
    exit_reason = Column(String(64), default="")
    exit_type = Column(String(32), default="")

    # Scores
    security_score = Column(Float, default=0.0)
    liquidity_usd = Column(Float, default=0.0)
    organic_demand_score = Column(Float, default=0.0)
    smart_wallet_score = Column(Float, default=0.0)
    momentum_score = Column(Float, default=0.0)

    # Execution
    execution_success = Column(Boolean, default=True)
    execution_latency_ms = Column(Float, default=0.0)

    # Selection
    was_selected = Column(Boolean, default=True)
    selector_fitness = Column(Float, default=0.0)
    selector_confidence = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_strat_trades_strat_regime", "strategy", "market_regime"),
        Index("ix_strat_trades_strat_stage", "strategy", "capital_stage"),
        Index("ix_strat_trades_time", "timestamp"),
    )


class SmartWalletDB(Base):
    """Tracked smart wallets with performance data."""
    __tablename__ = "smart_wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(String(64), unique=True, nullable=False, index=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)

    total_trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    avg_winner_percent = Column(Float, default=0.0)
    avg_loser_percent = Column(Float, default=0.0)
    median_winner_percent = Column(Float, default=0.0)
    median_loser_percent = Column(Float, default=0.0)
    expectancy_percent = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)
    total_pnl_sol = Column(Float, default=0.0)

    median_holding_seconds = Column(Float, default=0.0)
    avg_entry_token_age = Column(Float, default=0.0)
    avg_exit_token_age = Column(Float, default=0.0)
    max_drawdown_percent = Column(Float, default=0.0)
    successful_launches = Column(Integer, default=0)
    suspicious_clusters = Column(Integer, default=0)
    wash_trading_score = Column(Float, default=0.0)

    is_qualified = Column(Boolean, default=False, index=True)
    smart_wallet_score = Column(Float, default=0.0)


class MarketRegimeSnapshotDB(Base):
    """Point-in-time market regime snapshots."""
    __tablename__ = "market_regime_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_at = Column(DateTime, default=datetime.utcnow, index=True)
    regime = Column(String(32), nullable=False)
    confidence = Column(Float, default=0.0)
    metrics = Column(JSON, default=dict)


class CounterfactualResultDB(Base):
    """What-if results for strategies not selected."""
    __tablename__ = "counterfactual_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(String(64), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    strategy = Column(String(32), nullable=False, index=True)
    would_trade = Column(Boolean, default=False)
    estimated_entry_sol = Column(Float, default=0.0)
    estimated_exit_sol = Column(Float, default=0.0)
    estimated_net_pnl_percent = Column(Float, default=0.0)
    actual_selected_strategy = Column(String(32), default="")
    actual_net_pnl_percent = Column(Float, default=0.0)
    market_regime = Column(String(32), default="")
    capital_stage = Column(String(16), default="")
    data = Column(JSON, default=dict)

    __table_args__ = (
        Index("ix_cf_strat_time", "strategy", "timestamp"),
    )


class StrategyPerformanceSnapshotDB(Base):
    """Periodic snapshots of strategy performance."""
    __tablename__ = "strategy_performance_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_at = Column(DateTime, default=datetime.utcnow, index=True)
    strategy = Column(String(32), nullable=False, index=True)
    window_size = Column(Integer, default=0)
    market_regime = Column(String(32), default="all")
    capital_stage = Column(String(16), default="all")

    trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    avg_winner = Column(Float, default=0.0)
    avg_loser = Column(Float, default=0.0)
    median_winner = Column(Float, default=0.0)
    median_loser = Column(Float, default=0.0)
    expectancy = Column(Float, default=0.0)
    profit_factor = Column(Float, default=0.0)
    total_net_pnl = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    max_loss = Column(Float, default=0.0)
    consecutive_losses = Column(Integer, default=0)
    avg_holding_seconds = Column(Float, default=0.0)
    target_hit_rate = Column(Float, default=0.0)
    emergency_exit_rate = Column(Float, default=0.0)
    execution_failure_rate = Column(Float, default=0.0)
    avg_slippage = Column(Float, default=0.0)
    fitness_score = Column(Float, default=0.0)
    confidence_adjusted_fitness = Column(Float, default=0.0)
    status = Column(String(16), default="experimental")

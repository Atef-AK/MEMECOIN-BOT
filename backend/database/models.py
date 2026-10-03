"""
SQLAlchemy ORM models for all database tables.
Every detected token, trade, and event is stored for analysis.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class TokenDB(Base):
    """Every discovered token, whether traded or not."""

    __tablename__ = "tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(256), default="")
    symbol = Column(String(32), default="")
    decimals = Column(Integer, default=0)
    token_program = Column(String(32), default="unknown")
    created_at = Column(DateTime, nullable=True)
    discovered_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String(64), default="")

    # Status
    status = Column(String(32), default="discovered", index=True)
    rejection_reason = Column(String(64), nullable=True)
    rejection_detail = Column(Text, default="")

    # Score
    total_score = Column(Float, default=0.0)
    security_score = Column(Float, default=0.0)
    liquidity_score = Column(Float, default=0.0)
    holder_score = Column(Float, default=0.0)
    dev_score = Column(Float, default=0.0)
    social_score = Column(Float, default=0.0)
    market_score = Column(Float, default=0.0)

    # Market data at discovery
    initial_liquidity_usd = Column(Float, default=0.0)
    initial_market_cap = Column(Float, default=0.0)
    initial_price_usd = Column(Float, default=0.0)
    initial_price_sol = Column(Float, default=0.0)

    # Metadata
    website = Column(String(512), default="")
    twitter = Column(String(256), default="")
    telegram_link = Column(String(256), default="")
    discord = Column(String(256), default="")
    description = Column(Text, default="")
    image_url = Column(String(512), default="")

    # Relationships
    pools = relationship("PoolDB", back_populates="token", cascade="all, delete-orphan")
    security_reports = relationship("SecurityReportDB", back_populates="token", cascade="all, delete-orphan")
    trades = relationship("TradeDB", back_populates="token", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_tokens_status_score", "status", "total_score"),
        Index("ix_tokens_discovered_at", "discovered_at"),
    )


class PoolDB(Base):
    """Liquidity pool data."""

    __tablename__ = "pools"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pool_address = Column(String(64), unique=True, nullable=False, index=True)
    mint_address = Column(String(64), ForeignKey("tokens.mint_address"), nullable=False)
    dex = Column(String(32), default="")
    quote_token = Column(String(64), default="")
    created_at = Column(DateTime, nullable=True)

    # Liquidity
    liquidity_usd = Column(Float, default=0.0)
    base_liquidity = Column(Float, default=0.0)
    quote_liquidity = Column(Float, default=0.0)

    # LP lock
    lp_locked = Column(Boolean, default=False)
    lp_lock_provider = Column(String(64), default="")
    lp_lock_percent = Column(Float, default=0.0)
    lp_lock_expiry = Column(DateTime, nullable=True)

    token = relationship("TokenDB", back_populates="pools")


class SecurityReportDB(Base):
    """Security analysis reports."""

    __tablename__ = "security_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(String(64), ForeignKey("tokens.mint_address"), nullable=False, index=True)
    checked_at = Column(DateTime, default=datetime.utcnow)

    token_program = Column(String(32), default="")
    mint_authority = Column(String(64), nullable=True)
    mint_authority_disabled = Column(Boolean, default=False)
    freeze_authority = Column(String(64), nullable=True)
    freeze_authority_disabled = Column(Boolean, default=False)

    total_supply = Column(String(64), default="0")
    decimals = Column(Integer, default=0)

    is_token_2022 = Column(Boolean, default=False)
    extensions = Column(JSON, default=list)
    risky_extensions = Column(JSON, default=list)

    has_metadata = Column(Boolean, default=False)
    metadata_valid = Column(Boolean, default=False)

    passed = Column(Boolean, default=False)
    score = Column(Float, default=0.0)
    critical_failures = Column(JSON, default=list)
    checks = Column(JSON, default=list)

    token = relationship("TokenDB", back_populates="security_reports")


class LiquiditySnapshotDB(Base):
    """Point-in-time liquidity snapshots."""

    __tablename__ = "liquidity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(String(64), nullable=False, index=True)
    pool_address = Column(String(64), nullable=False)
    snapshot_at = Column(DateTime, default=datetime.utcnow)
    liquidity_usd = Column(Float, default=0.0)
    base_liquidity = Column(Float, default=0.0)
    quote_liquidity = Column(Float, default=0.0)
    change_percent = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_liq_snapshots_mint_time", "mint_address", "snapshot_at"),
    )


class HolderSnapshotDB(Base):
    """Point-in-time holder distribution snapshots."""

    __tablename__ = "holder_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(String(64), nullable=False, index=True)
    snapshot_at = Column(DateTime, default=datetime.utcnow)
    total_holders = Column(Integer, default=0)
    top1_percent = Column(Float, default=0.0)
    top5_percent = Column(Float, default=0.0)
    top10_percent = Column(Float, default=0.0)
    top20_percent = Column(Float, default=0.0)
    largest_non_lp_percent = Column(Float, default=0.0)
    data = Column(JSON, default=dict)


class WalletDB(Base):
    """Tracked wallets (creators, insiders, etc.)."""

    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    address = Column(String(64), unique=True, nullable=False, index=True)
    label = Column(String(64), default="")
    is_creator = Column(Boolean, default=False)
    is_suspicious = Column(Boolean, default=False)
    first_seen = Column(DateTime, default=datetime.utcnow)
    launches_count = Column(Integer, default=0)
    rugs_count = Column(Integer, default=0)
    notes = Column(Text, default="")


class WalletRelationshipDB(Base):
    """Relationships between wallets (funding, transfers)."""

    __tablename__ = "wallet_relationships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_wallet = Column(String(64), nullable=False, index=True)
    target_wallet = Column(String(64), nullable=False, index=True)
    relationship_type = Column(String(32), default="")  # funded_by, transferred_to
    mint_address = Column(String(64), nullable=True)
    amount = Column(Float, default=0.0)
    detected_at = Column(DateTime, default=datetime.utcnow)


class SocialProfileDB(Base):
    """Social/website verification records."""

    __tablename__ = "social_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(String(64), nullable=False, index=True)
    checked_at = Column(DateTime, default=datetime.utcnow)
    has_website = Column(Boolean, default=False)
    website_url = Column(String(512), default="")
    website_responds = Column(Boolean, default=False)
    has_twitter = Column(Boolean, default=False)
    has_telegram = Column(Boolean, default=False)
    has_discord = Column(Boolean, default=False)
    social_count = Column(Integer, default=0)
    score = Column(Float, default=0.0)


class TradeDB(Base):
    """All paper and live trades."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), unique=True, nullable=False, index=True)
    trade_type = Column(String(16), default="paper")  # paper / live
    mint_address = Column(String(64), ForeignKey("tokens.mint_address"), nullable=False)
    pool_address = Column(String(64), default="")
    symbol = Column(String(32), default="")

    # Entry
    entry_time = Column(DateTime, nullable=True)
    entry_price_sol = Column(Float, default=0.0)
    entry_price_usd = Column(Float, default=0.0)
    entry_amount_sol = Column(Float, default=0.0)
    entry_tokens = Column(Float, default=0.0)
    entry_slippage_bps = Column(Float, default=0.0)
    entry_fee_sol = Column(Float, default=0.0)
    entry_tx = Column(String(128), default="")
    entry_status = Column(String(16), default="pending")

    # Exit
    exit_time = Column(DateTime, nullable=True)
    exit_price_sol = Column(Float, default=0.0)
    exit_price_usd = Column(Float, default=0.0)
    exit_amount_sol = Column(Float, default=0.0)
    exit_slippage_bps = Column(Float, default=0.0)
    exit_fee_sol = Column(Float, default=0.0)
    exit_tx = Column(String(128), default="")
    exit_status = Column(String(16), default="pending")
    exit_reason = Column(String(64), nullable=True)

    # PnL
    gross_pnl_sol = Column(Float, default=0.0)
    total_fees_sol = Column(Float, default=0.0)
    total_slippage_sol = Column(Float, default=0.0)
    net_pnl_sol = Column(Float, default=0.0)
    net_pnl_percent = Column(Float, default=0.0)

    # Context
    score = Column(Float, default=0.0)
    liquidity_usd = Column(Float, default=0.0)
    market_cap = Column(Float, default=0.0)
    holder_top10_percent = Column(Float, default=0.0)
    token_age_seconds = Column(Float, default=0.0)
    dex = Column(String(32), default="")
    strategy_name = Column(String(64), default="Fast Scalper")
    exit_decision = Column(String(256), default="")

    # Timing
    time_to_exit_seconds = Column(Float, default=0.0)
    execution_latency_ms = Column(Float, default=0.0)

    token = relationship("TokenDB", back_populates="trades")

    __table_args__ = (
        Index("ix_trades_type_time", "trade_type", "entry_time"),
        Index("ix_trades_exit_reason", "exit_reason"),
    )


class ExecutionAttemptDB(Base):
    """Individual execution attempt records for debugging."""

    __tablename__ = "execution_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), nullable=False, index=True)
    attempt_at = Column(DateTime, default=datetime.utcnow)
    side = Column(String(8), default="buy")
    status = Column(String(16), default="pending")
    error = Column(Text, default="")
    latency_ms = Column(Float, default=0.0)
    quote_data = Column(JSON, default=dict)
    tx_signature = Column(String(128), default="")


class RiskEventDB(Base):
    """Risk events and emergency triggers."""

    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_at = Column(DateTime, default=datetime.utcnow, index=True)
    mint_address = Column(String(64), nullable=True)
    event_type = Column(String(64), nullable=False)
    severity = Column(String(16), default="info")  # info, warn, high, critical
    detail = Column(Text, default="")
    action_taken = Column(String(64), default="")
    data = Column(JSON, default=dict)


class PriceSnapshotDB(Base):
    """Price snapshots for monitoring and analysis."""

    __tablename__ = "price_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mint_address = Column(String(64), nullable=False, index=True)
    snapshot_at = Column(DateTime, default=datetime.utcnow)
    price_sol = Column(Float, default=0.0)
    price_usd = Column(Float, default=0.0)
    volume_usd = Column(Float, default=0.0)
    liquidity_usd = Column(Float, default=0.0)
    buys = Column(Integer, default=0)
    sells = Column(Integer, default=0)

    __table_args__ = (
        Index("ix_price_snapshots_mint_time", "mint_address", "snapshot_at"),
    )


class ConfigVersionDB(Base):
    """Configuration version tracking."""

    __tablename__ = "configuration_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    saved_at = Column(DateTime, default=datetime.utcnow)
    config_data = Column(JSON, nullable=False)
    changed_by = Column(String(64), default="system")
    notes = Column(Text, default="")


class SystemEventDB(Base):
    """System events for observability."""

    __tablename__ = "system_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_at = Column(DateTime, default=datetime.utcnow, index=True)
    event_type = Column(String(64), nullable=False)
    component = Column(String(64), default="")
    message = Column(Text, default="")
    data = Column(JSON, default=dict)
    level = Column(String(16), default="info")

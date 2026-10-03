"""
Data access layer for persisting bot state to the database.
Handles saving tokens, trades, security reports, risk events,
and system events via async SQLAlchemy sessions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import (
    ExecutionAttemptDB,
    LiquiditySnapshotDB,
    HolderSnapshotDB,
    PoolDB,
    PriceSnapshotDB,
    RiskEventDB,
    SecurityReportDB,
    SocialProfileDB,
    SystemEventDB,
    TokenDB,
    TradeDB,
    WalletDB,
)
from backend.database.session import get_session_factory
from backend.models.security import (
    HolderReport,
    LiquidityReport,
    SecurityReport,
    SocialReport,
)
from backend.models.token import DiscoveredToken, TokenCandidate
from backend.models.trade import TradeRecord

logger = logging.getLogger(__name__)


class Repository:
    """
    Centralized data access layer.
    All database writes go through this class.
    """

    def __init__(self) -> None:
        self._factory = None

    def _get_factory(self):
        if self._factory is None:
            self._factory = get_session_factory()
        return self._factory

    # ── Token Persistence ────────────────────────────────────

    async def save_discovered_token(self, token: DiscoveredToken) -> None:
        """Save or update a discovered token."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                # Check if token already exists
                stmt = select(TokenDB).where(
                    TokenDB.mint_address == token.mint_address
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # Update existing token
                    existing.name = token.name or existing.name
                    existing.symbol = token.symbol or existing.symbol
                    existing.decimals = token.decimals or existing.decimals
                    existing.initial_liquidity_usd = (
                        token.initial_liquidity_usd or existing.initial_liquidity_usd
                    )
                    existing.initial_market_cap = (
                        token.initial_market_cap or existing.initial_market_cap
                    )
                    existing.initial_price_usd = (
                        token.price_usd or existing.initial_price_usd
                    )
                    existing.initial_price_sol = (
                        token.price_sol or existing.initial_price_sol
                    )
                    existing.website = token.website or existing.website
                    existing.twitter = token.twitter or existing.twitter
                    existing.telegram_link = token.telegram or existing.telegram_link
                    existing.discord = token.discord or existing.discord
                    existing.description = token.description or existing.description
                    existing.image_url = token.image_url or existing.image_url
                else:
                    db_token = TokenDB(
                        mint_address=token.mint_address,
                        name=token.name,
                        symbol=token.symbol,
                        decimals=token.decimals,
                        created_at=token.created_at,
                        discovered_at=token.discovered_at,
                        source=token.source,
                        status="discovered",
                        initial_liquidity_usd=token.initial_liquidity_usd,
                        initial_market_cap=token.initial_market_cap,
                        initial_price_usd=token.price_usd,
                        initial_price_sol=token.price_sol,
                        website=token.website,
                        twitter=token.twitter,
                        telegram_link=token.telegram,
                        discord=token.discord,
                        description=token.description,
                        image_url=token.image_url,
                    )
                    session.add(db_token)

                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to save token {token.mint_address[:12]}: {e}")

    async def update_token_status(
        self,
        mint_address: str,
        status: str,
        candidate: TokenCandidate | None = None,
        rejection_reason: str = "",
        rejection_detail: str = "",
    ) -> None:
        """Update token status and scores after analysis."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                stmt = (
                    update(TokenDB)
                    .where(TokenDB.mint_address == mint_address)
                    .values(
                        status=status,
                        rejection_reason=rejection_reason or None,
                        rejection_detail=rejection_detail,
                        **(
                            {
                                "total_score": candidate.total_score,
                                "security_score": candidate.security_score,
                                "liquidity_score": candidate.liquidity_score,
                                "holder_score": candidate.holder_score,
                                "dev_score": candidate.dev_score,
                                "social_score": candidate.social_score,
                                "market_score": candidate.market_score,
                            }
                            if candidate
                            else {}
                        ),
                    )
                )
                await session.execute(stmt)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to update token status {mint_address[:12]}: {e}")

    # ── Pool Persistence ─────────────────────────────────────

    async def save_pool(
        self, mint_address: str, pool_address: str, dex: str, liquidity_usd: float
    ) -> None:
        """Save pool data for a token."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                stmt = select(PoolDB).where(PoolDB.pool_address == pool_address)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.liquidity_usd = liquidity_usd
                else:
                    pool = PoolDB(
                        pool_address=pool_address,
                        mint_address=mint_address,
                        dex=dex,
                        liquidity_usd=liquidity_usd,
                    )
                    session.add(pool)

                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to save pool {pool_address[:12]}: {e}")

    # ── Security Report Persistence ──────────────────────────

    async def save_security_report(self, report: SecurityReport) -> None:
        """Save a security analysis report."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                db_report = SecurityReportDB(
                    mint_address=report.mint_address,
                    checked_at=report.checked_at,
                    token_program=report.token_program.value,
                    mint_authority=report.mint_authority,
                    mint_authority_disabled=report.mint_authority_disabled,
                    freeze_authority=report.freeze_authority,
                    freeze_authority_disabled=report.freeze_authority_disabled,
                    total_supply=str(report.total_supply),
                    decimals=report.decimals,
                    is_token_2022=report.is_token_2022,
                    extensions=report.extensions,
                    risky_extensions=report.risky_extensions,
                    has_metadata=report.has_metadata,
                    metadata_valid=report.metadata_valid,
                    passed=report.passed,
                    score=report.score,
                    critical_failures=report.critical_failures,
                    checks=[c.model_dump() for c in report.checks],
                )
                session.add(db_report)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Failed to save security report for {report.mint_address[:12]}: {e}"
                )

    # ── Trade Persistence ────────────────────────────────────

    async def save_trade(self, trade: TradeRecord) -> None:
        """Save or update a trade record."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                # Check if trade already exists
                stmt = select(TradeDB).where(TradeDB.trade_id == trade.id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    # Update with exit data
                    existing.exit_time = trade.exit_time
                    existing.exit_price_sol = trade.exit_price_sol
                    existing.exit_price_usd = trade.exit_price_usd
                    existing.exit_amount_sol = trade.exit_amount_sol
                    existing.exit_slippage_bps = trade.exit_slippage_bps
                    existing.exit_fee_sol = trade.exit_fee_sol
                    existing.exit_tx = trade.exit_tx_signature
                    existing.exit_status = (
                        trade.exit_status.value if trade.exit_status else "pending"
                    )
                    existing.exit_reason = (
                        trade.exit_reason.value if trade.exit_reason else None
                    )
                    existing.exit_decision = trade.exit_decision
                    existing.gross_pnl_sol = trade.gross_pnl_sol
                    existing.total_fees_sol = trade.total_fees_sol
                    existing.total_slippage_sol = trade.total_slippage_sol
                    existing.net_pnl_sol = trade.net_pnl_sol
                    existing.net_pnl_percent = trade.net_pnl_percent
                    existing.time_to_exit_seconds = trade.time_to_exit_seconds
                else:
                    db_trade = TradeDB(
                        trade_id=trade.id,
                        trade_type=trade.trade_type.value,
                        mint_address=trade.mint_address,
                        pool_address=trade.pool_address,
                        symbol=trade.symbol,
                        entry_time=trade.entry_time,
                        entry_price_sol=trade.entry_price_sol,
                        entry_price_usd=trade.entry_price_usd,
                        entry_amount_sol=trade.entry_amount_sol,
                        entry_tokens=trade.entry_tokens,
                        entry_slippage_bps=trade.entry_slippage_bps,
                        entry_fee_sol=trade.entry_fee_sol,
                        entry_tx=trade.entry_tx_signature,
                        entry_status=trade.entry_status.value,
                        score=trade.score,
                        liquidity_usd=trade.liquidity_usd,
                        market_cap=trade.market_cap,
                        holder_top10_percent=trade.holder_top10_percent,
                        token_age_seconds=trade.token_age_seconds,
                        dex=trade.dex,
                        strategy_name=trade.strategy_name or "Fast Scalper",
                        exit_decision=trade.exit_decision,
                    )
                    session.add(db_trade)

                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.error(f"Failed to save trade {trade.id[:12]}: {e}")

    async def get_recent_trades(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        """Get recent completed trades from DB."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                stmt = (
                    select(TradeDB)
                    .where(TradeDB.exit_time.isnot(None))
                    .order_by(desc(TradeDB.exit_time))
                    .offset(offset)
                    .limit(limit)
                )
                result = await session.execute(stmt)
                trades = result.scalars().all()
                return [self._trade_db_to_dict(t) for t in trades]
            except Exception as e:
                logger.error(f"Failed to get recent trades: {e}")
                return []

    async def get_trade_count(self) -> int:
        """Get total number of completed trades."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                from sqlalchemy import func

                stmt = select(func.count()).select_from(TradeDB).where(
                    TradeDB.exit_time.isnot(None)
                )
                result = await session.execute(stmt)
                return result.scalar() or 0
            except Exception as e:
                logger.error(f"Failed to count trades: {e}")
                return 0

    # ── Price Snapshots ──────────────────────────────────────

    async def save_price_snapshot(
        self,
        mint_address: str,
        price_sol: float,
        price_usd: float = 0.0,
        volume_usd: float = 0.0,
        liquidity_usd: float = 0.0,
        buys: int = 0,
        sells: int = 0,
    ) -> None:
        """Save a price snapshot for a monitored token."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                snapshot = PriceSnapshotDB(
                    mint_address=mint_address,
                    price_sol=price_sol,
                    price_usd=price_usd,
                    volume_usd=volume_usd,
                    liquidity_usd=liquidity_usd,
                    buys=buys,
                    sells=sells,
                )
                session.add(snapshot)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.debug(f"Failed to save price snapshot: {e}")

    # ── Risk Events ──────────────────────────────────────────

    async def save_risk_event(
        self,
        event_type: str,
        severity: str = "info",
        mint_address: str = "",
        detail: str = "",
        action_taken: str = "",
        data: dict | None = None,
    ) -> None:
        """Log a risk event."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                event = RiskEventDB(
                    event_type=event_type,
                    severity=severity,
                    mint_address=mint_address or None,
                    detail=detail,
                    action_taken=action_taken,
                    data=data or {},
                )
                session.add(event)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.debug(f"Failed to save risk event: {e}")

    # ── System Events ────────────────────────────────────────

    async def save_system_event(
        self,
        event_type: str,
        component: str = "",
        message: str = "",
        level: str = "info",
        data: dict | None = None,
    ) -> None:
        """Log a system event."""
        factory = self._get_factory()
        async with factory() as session:
            try:
                event = SystemEventDB(
                    event_type=event_type,
                    component=component,
                    message=message,
                    level=level,
                    data=data or {},
                )
                session.add(event)
                await session.commit()
            except Exception as e:
                await session.rollback()
                logger.debug(f"Failed to save system event: {e}")

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _trade_db_to_dict(t: TradeDB) -> dict:
        return {
            "id": t.trade_id,
            "trade_type": t.trade_type,
            "mint_address": t.mint_address,
            "pool_address": t.pool_address,
            "symbol": t.symbol,
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "entry_price_sol": t.entry_price_sol,
            "entry_amount_sol": t.entry_amount_sol,
            "entry_tokens": t.entry_tokens,
            "entry_fee_sol": t.entry_fee_sol,
            "entry_status": t.entry_status,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "exit_price_sol": t.exit_price_sol,
            "exit_amount_sol": t.exit_amount_sol,
            "exit_fee_sol": t.exit_fee_sol,
            "exit_status": t.exit_status,
            "exit_reason": t.exit_reason,
            "gross_pnl_sol": t.gross_pnl_sol,
            "total_fees_sol": t.total_fees_sol,
            "net_pnl_sol": t.net_pnl_sol,
            "net_pnl_percent": t.net_pnl_percent,
            "score": t.score,
            "liquidity_usd": t.liquidity_usd,
            "market_cap": t.market_cap,
            "dex": t.dex,
            "time_to_exit_seconds": t.time_to_exit_seconds,
            "strategy_name": t.strategy_name or "Fast Scalper",
            "exit_decision": t.exit_decision or (t.exit_reason or "—"),
        }

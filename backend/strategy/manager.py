"""
Strategy manager - orchestrates the complete trading pipeline.
Connects discovery → analysis → scoring → execution → monitoring.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from backend.api.websocket.handler import (
    broadcast_position_update,
    broadcast_stats_update,
    broadcast_token_discovered,
    broadcast_trade_update,
)
from backend.config.settings import get_settings
from backend.core.event_bus import Events, get_event_bus
from backend.core.kill_switch import get_kill_switch
from backend.database.repository import Repository
from backend.devtracker.engine import DevTrackerEngine
from backend.discovery.manager import DiscoveryManager
from backend.execution.jupiter import JupiterClient
from backend.execution.paper import PaperExecutionEngine
from backend.holders.engine import HolderEngine
from backend.liquidity.engine import LiquidityEngine
from backend.models.score import ScoreBreakdown
from backend.models.security import MarketBehaviorReport
from backend.models.token import (
    DiscoveredToken,
    RejectionReason,
    TokenCandidate,
    TokenStatus,
)
from backend.models.trade import ExitReason, TradingStats, TradeRecord
from backend.monitoring.engine import PositionMonitor
from backend.monitoring.price_monitor import PriceMonitor
from backend.notifications.telegram import TelegramNotifier
from backend.scoring.engine import ScoringEngine
from backend.security.engine import SecurityEngine
from backend.social.engine import SocialEngine
from backend.social.market_observer import MarketBehaviorObserver

logger = logging.getLogger(__name__)


class StrategyManager:
    """
    Central strategy orchestrator.
    Connects all pipeline stages into a coherent trading system.
    """

    def __init__(self) -> None:
        # Engines
        self.discovery = DiscoveryManager()
        self.security = SecurityEngine()
        self.liquidity = LiquidityEngine()
        self.holders = HolderEngine()
        self.devtracker = DevTrackerEngine()
        self.social = SocialEngine()
        self.market_observer = MarketBehaviorObserver()
        self.scoring = ScoringEngine()
        self.jupiter = JupiterClient()
        self.paper_engine = PaperExecutionEngine(self.jupiter)
        self.monitor = PositionMonitor()
        self.price_monitor = PriceMonitor()
        self.telegram = TelegramNotifier()

        # Database persistence
        self._repo = Repository()

        # State
        self._running = False
        self._stats = TradingStats()
        self._completed_trades: list[TradeRecord] = []
        self._all_candidates: list[TokenCandidate] = []
        self._tokens_detected = 0
        self._tokens_rejected = 0

    async def start(self) -> None:
        """Initialize all engines."""
        await self.discovery.start()
        await self.security.start()
        await self.liquidity.start()
        await self.holders.start()
        await self.devtracker.start()
        await self.social.start()
        await self.market_observer.start()
        await self.jupiter.start()
        await self.monitor.start()
        await self.price_monitor.start()
        await self.telegram.start()

        # Register kill switch callback
        kill_switch = get_kill_switch()
        kill_switch.register_callback(self._on_kill_switch)

        # Register discovery callback
        self.discovery.on_token_discovered(self._on_token_discovered)

        self._running = True

        # Log startup event
        await self._repo.save_system_event(
            event_type="bot_started",
            component="strategy_manager",
            message=f"Bot started in {get_settings().effective_mode} mode",
        )

        logger.info("Strategy manager started")

    async def stop(self) -> None:
        """Shutdown all engines."""
        self._running = False
        await self.discovery.stop()
        await self.security.stop()
        await self.liquidity.stop()
        await self.holders.stop()
        await self.devtracker.stop()
        await self.social.stop()
        await self.market_observer.stop()
        await self.jupiter.stop()
        await self.monitor.stop()
        await self.price_monitor.stop()
        await self.telegram.stop()

        await self._repo.save_system_event(
            event_type="bot_stopped",
            component="strategy_manager",
            message="Bot shutdown cleanly",
        )

        logger.info("Strategy manager stopped")

    async def run(self) -> None:
        """Main strategy loop."""
        settings = get_settings()

        # Print startup banner
        mode = settings.effective_mode
        print("\n" + "=" * 40)
        print("SOLANA MEMECOIN BOT")
        print(f"MODE: {mode} TRADING")
        print(f"POSITION SIZE: {settings.position_size_sol} SOL")
        print(f"TARGET: +{settings.net_target_percent}% NET")
        print(f"MIN SCORE: {settings.min_score}")
        print("=" * 40 + "\n")

        # Start concurrent tasks
        tasks = [
            asyncio.create_task(self.discovery.run()),
            asyncio.create_task(
                self.monitor.run(sell_callback=self._execute_sell)
            ),
            asyncio.create_task(
                self.price_monitor.run(position_monitor=self.monitor)
            ),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Strategy manager tasks cancelled")
        except Exception as e:
            logger.error(f"Strategy manager error: {e}", exc_info=True)

    async def _on_token_discovered(self, token: DiscoveredToken) -> None:
        """Handle a newly discovered token."""
        self._tokens_detected += 1
        settings = get_settings()
        kill_switch = get_kill_switch()

        if kill_switch.is_active:
            return

        # Check risk limits
        if not self._check_risk_limits():
            return

        logger.info(
            f"🔍 Discovered: {token.symbol} ({token.mint_address[:8]}...) "
            f"via {token.source} | Liq: ${token.current_liquidity_usd:,.0f}"
        )

        # Persist discovered token to DB
        await self._repo.save_discovered_token(token)

        # Broadcast to dashboard via WebSocket
        await broadcast_token_discovered({
            "mint_address": token.mint_address,
            "symbol": token.symbol,
            "name": token.name,
            "liquidity_usd": token.current_liquidity_usd,
            "dex": token.dex.value,
            "source": token.source,
        })

        try:
            # Run full analysis pipeline
            candidate = await self._analyze_token(token)

            if candidate.has_critical_failure:
                self._tokens_rejected += 1
                # Persist rejection
                await self._repo.update_token_status(
                    mint_address=token.mint_address,
                    status="rejected",
                    candidate=candidate,
                    rejection_reason=(
                        candidate.rejection_reason.value
                        if candidate.rejection_reason
                        else ""
                    ),
                    rejection_detail="; ".join(candidate.critical_failures),
                )
                return

            if candidate.total_score < settings.min_score:
                self._tokens_rejected += 1
                await self._repo.update_token_status(
                    mint_address=token.mint_address,
                    status="rejected",
                    candidate=candidate,
                    rejection_reason="low_score",
                    rejection_detail=f"Score {candidate.total_score:.0f} < {settings.min_score}",
                )
                return

            # Update token status in DB
            await self._repo.update_token_status(
                mint_address=token.mint_address,
                status=candidate.status.value,
                candidate=candidate,
            )

            # Token qualifies - attempt entry
            if candidate.status == TokenStatus.CANDIDATE:
                await self._attempt_entry(candidate)

        except Exception as e:
            logger.error(
                f"Pipeline error for {token.symbol}: {e}", exc_info=True
            )

    async def _analyze_token(self, token: DiscoveredToken) -> TokenCandidate:
        """Run the complete analysis pipeline on a token."""
        candidate = TokenCandidate(token=token, status=TokenStatus.ANALYZING)

        # 1. Security analysis
        security_report = await self.security.analyze(
            token.mint_address, dex=token.dex.value
        )
        candidate.security_score = security_report.score

        # Persist security report
        await self._repo.save_security_report(security_report)

        if security_report.has_critical_failure:
            candidate.status = TokenStatus.REJECTED
            candidate.has_critical_failure = True
            candidate.critical_failures.extend(security_report.critical_failures)
            candidate.rejection_reason = RejectionReason.MINT_AUTHORITY_ACTIVE
            logger.info(f"❌ REJECTED {token.symbol}: {security_report.critical_failures}")
            self._all_candidates.append(candidate)
            return candidate

        # 2. Liquidity analysis
        liquidity_report = await self.liquidity.analyze(
            mint_address=token.mint_address,
            pool_address=token.pool_address,
            dex=token.dex.value,
            initial_liquidity_usd=token.initial_liquidity_usd,
            market_cap=token.initial_market_cap,
        )
        candidate.liquidity_score = liquidity_report.score

        if liquidity_report.critical_failures:
            candidate.status = TokenStatus.REJECTED
            candidate.has_critical_failure = True
            candidate.critical_failures.extend(liquidity_report.critical_failures)
            candidate.rejection_reason = RejectionReason.LOW_LIQUIDITY
            logger.info(f"❌ REJECTED {token.symbol}: {liquidity_report.critical_failures}")
            self._all_candidates.append(candidate)
            return candidate

        # 3. Holder analysis
        holder_report = await self.holders.analyze(
            mint_address=token.mint_address,
            total_supply=security_report.total_supply,
            decimals=security_report.decimals,
            pool_address=token.pool_address,
            dex=token.dex.value,
        )
        candidate.holder_score = holder_report.score

        if holder_report.critical_failures:
            candidate.status = TokenStatus.REJECTED
            candidate.has_critical_failure = True
            candidate.critical_failures.extend(holder_report.critical_failures)
            candidate.rejection_reason = RejectionReason.HIGH_HOLDER_CONCENTRATION
            logger.info(f"❌ REJECTED {token.symbol}: {holder_report.critical_failures}")
            self._all_candidates.append(candidate)
            return candidate

        # 4. Dev tracker
        dev_report = await self.devtracker.analyze(
            mint_address=token.mint_address,
            creator_wallet=token.creator_wallet,
            initial_buy_sol=token.initial_buy_sol,
        )
        candidate.dev_score = dev_report.score

        if dev_report.critical_failures:
            candidate.status = TokenStatus.REJECTED
            candidate.has_critical_failure = True
            candidate.critical_failures.extend(dev_report.critical_failures)
            candidate.rejection_reason = RejectionReason.SUSPICIOUS_DEV
            logger.info(f"❌ REJECTED {token.symbol}: {dev_report.critical_failures}")
            self._all_candidates.append(candidate)
            return candidate

        # 5. Social verification
        social_report = await self.social.analyze(
            mint_address=token.mint_address,
            website=token.website,
            twitter=token.twitter,
            telegram=token.telegram,
            discord=token.discord,
        )
        candidate.social_score = social_report.score

        # 6. Market behavior observation
        market_report = await self.market_observer.quick_observe(
            mint_address=token.mint_address,
            pool_address=token.pool_address,
        )
        candidate.market_score = market_report.score

        if market_report.critical_failures:
            candidate.status = TokenStatus.REJECTED
            candidate.has_critical_failure = True
            candidate.critical_failures.extend(market_report.critical_failures)
            candidate.rejection_reason = RejectionReason.BAD_MARKET_BEHAVIOR
            self._all_candidates.append(candidate)
            return candidate

        # 7. Composite scoring
        score = self.scoring.score(
            mint_address=token.mint_address,
            security=security_report,
            liquidity=liquidity_report,
            holders=holder_report,
            dev=dev_report,
            social=social_report,
            market=market_report,
        )

        candidate.total_score = score.total_score
        candidate.has_critical_failure = score.has_critical_failure
        candidate.critical_failures = score.critical_failures

        if score.is_tradeable:
            candidate.status = TokenStatus.CANDIDATE
        elif score.classification == "WATCH":
            candidate.status = TokenStatus.WATCHING
        else:
            candidate.status = TokenStatus.REJECTED
            candidate.rejection_reason = RejectionReason.LOW_SCORE

        logger.info(
            f"📊 SCORED ${token.symbol} on {token.dex.value} | "
            f"Total: {candidate.total_score:.0f}/100 | "
            f"Sec: {candidate.security_score:.0f} | "
            f"Liq: {candidate.liquidity_score:.0f} | "
            f"Hold: {candidate.holder_score:.0f} | "
            f"Status: {candidate.status.value}"
        )

        self._all_candidates.append(candidate)
        return candidate

    async def _attempt_entry(self, candidate: TokenCandidate) -> None:
        """Attempt to enter a trade for a qualifying candidate."""
        settings = get_settings()
        token = candidate.token

        # Pre-trade quote check
        quote = await self.paper_engine.simulate_quote_check(
            mint_address=token.mint_address,
            position_size_sol=settings.position_size_sol,
        )

        if not quote.is_executable:
            candidate.status = TokenStatus.REJECTED
            candidate.rejection_reason = RejectionReason.POOR_QUOTE
            logger.info(f"❌ QUOTE REJECTED for {token.symbol}: {quote.rejection_reason}")
            return

        # Send candidate alert
        age = candidate.age_seconds
        if token.created_at:
            age = (datetime.now(timezone.utc) - token.created_at.replace(tzinfo=timezone.utc)).total_seconds()

        await self.telegram.send_candidate_alert(
            symbol=token.symbol,
            mint_address=token.mint_address,
            age_seconds=age,
            liquidity_usd=token.current_liquidity_usd,
            market_cap=token.initial_market_cap,
            total_score=candidate.total_score,
            security_score=candidate.security_score,
            liquidity_score=candidate.liquidity_score,
            holder_score=candidate.holder_score,
            dev_score=candidate.dev_score,
            social_score=candidate.social_score,
            market_score=candidate.market_score,
            entry_sol=settings.position_size_sol,
            target_percent=settings.net_target_percent,
        )

        # Execute paper trade
        trade = await self.paper_engine.execute_buy(
            mint_address=token.mint_address,
            symbol=token.symbol,
            pool_address=token.pool_address,
            position_size_sol=settings.position_size_sol,
            score=candidate.total_score,
            liquidity_usd=token.current_liquidity_usd,
            market_cap=token.initial_market_cap,
            token_age_seconds=age,
            dex=token.dex.value,
            holder_top10_percent=candidate.holder_score,
        )

        # Strategy assignment based on opportunity profile
        if candidate.market_score >= 4.0 and candidate.liquidity_score >= 12.0:
            strategy_name = "Momentum Runner"
        elif candidate.holder_score >= 18.0:
            strategy_name = "Smart-Wallet Follower"
        else:
            strategy_name = "Fast Scalper"
        trade.strategy_name = strategy_name

        if trade.entry_status.value == "confirmed":
            candidate.status = TokenStatus.TRADING
            self.monitor.add_position(trade)

            # Persist trade entry to DB
            await self._repo.save_trade(trade)

            # Update token status
            await self._repo.update_token_status(
                mint_address=token.mint_address,
                status="trading",
                candidate=candidate,
            )

            await self.telegram.send_trade_entered(trade)

            # Broadcast trade entry via WebSocket
            await broadcast_trade_update({
                "action": "entered",
                "trade_id": trade.id,
                "symbol": trade.symbol,
                "entry_amount_sol": trade.entry_amount_sol,
                "entry_price_sol": trade.entry_price_sol,
                "score": trade.score,
                "strategy_name": trade.strategy_name,
            })

            # Publish event
            event_bus = get_event_bus()
            await event_bus.publish(
                Events.TRADE_ENTERED,
                trade=trade,
            )

    async def _execute_sell(
        self, trade_id: str, exit_reason: ExitReason
    ) -> None:
        """Execute a sell for a monitored position."""
        position = self.monitor.active_positions.get(trade_id)
        if not position:
            return

        settings = get_settings()
        trade = await self.paper_engine.execute_sell(
            trade=position.trade,
            exit_reason=exit_reason,
        )

        # Generate human-readable exit decision narrative
        if exit_reason == ExitReason.TARGET_HIT:
            trade.exit_decision = f"🎯 Target Hit (+{settings.net_target_percent}% Net Target reached in {trade.time_to_exit_seconds:.1f}s)"
        elif exit_reason == ExitReason.STOP_LOSS:
            trade.exit_decision = f"🛑 Stop Loss (Price dropped below threshold)"
        elif exit_reason == ExitReason.TRAILING_STOP:
            trade.exit_decision = f"🌊 Trailing Stop (+{trade.net_pnl_percent:+.1f}% profit locked in)"
        elif exit_reason == ExitReason.EMERGENCY_PRICE_CRASH:
            trade.exit_decision = f"🚨 Emergency Exit (Sudden price collapse)"
        elif exit_reason == ExitReason.EMERGENCY_DEV_DUMP:
            trade.exit_decision = f"🚨 Emergency Exit (Deployer dumped tokens)"
        elif exit_reason == ExitReason.TIMEOUT:
            trade.exit_decision = f"⏱️ Timeout (Max holding window reached)"
        elif exit_reason == ExitReason.RISK_LIMIT:
            trade.exit_decision = f"🛡️ Risk Limit (Safety drawdown stop)"
        else:
            trade.exit_decision = f"Exit: {exit_reason.value}"

        self.monitor.remove_position(trade_id)
        self._completed_trades.append(trade)
        self._update_stats(trade)

        # Persist trade exit to DB
        await self._repo.save_trade(trade)

        # Log risk event for emergency exits
        if exit_reason != ExitReason.TARGET_HIT:
            await self._repo.save_risk_event(
                event_type=exit_reason.value,
                severity="high" if "emergency" in exit_reason.value else "warn",
                mint_address=trade.mint_address,
                detail=f"{trade.symbol} exited: {trade.exit_decision}",
                action_taken="sell_executed",
                data={
                    "trade_id": trade.id,
                    "net_pnl_sol": trade.net_pnl_sol,
                    "net_pnl_percent": trade.net_pnl_percent,
                    "strategy_name": trade.strategy_name,
                    "exit_decision": trade.exit_decision,
                },
            )

        await self.telegram.send_trade_exited(trade)

        # Broadcast trade exit via WebSocket
        await broadcast_trade_update({
            "action": "exited",
            "trade_id": trade.id,
            "symbol": trade.symbol,
            "net_pnl_sol": trade.net_pnl_sol,
            "net_pnl_percent": trade.net_pnl_percent,
            "exit_reason": exit_reason.value,
            "strategy_name": trade.strategy_name,
            "exit_decision": trade.exit_decision,
        })

        # Broadcast updated stats
        await broadcast_stats_update({
            "total_trades": self._stats.total_trades,
            "win_rate": self._stats.win_rate,
            "total_pnl_sol": self._stats.total_pnl_sol,
            "expectancy": self._stats.expectancy,
        })

        event_bus = get_event_bus()
        await event_bus.publish(Events.TRADE_EXITED, trade=trade)

    def _update_stats(self, trade: TradeRecord) -> None:
        """Update aggregate trading statistics."""
        self._stats.total_trades += 1
        self._stats.today_trades += 1
        self._stats.today_pnl_sol += trade.net_pnl_sol
        self._stats.total_pnl_sol += trade.net_pnl_sol

        if trade.net_pnl_sol > 0:
            self._stats.winning_trades += 1
            self._stats.current_consecutive_losses = 0
        else:
            self._stats.losing_trades += 1
            self._stats.current_consecutive_losses += 1
            self._stats.max_consecutive_losses = max(
                self._stats.max_consecutive_losses,
                self._stats.current_consecutive_losses,
            )

        if self._stats.total_trades > 0:
            self._stats.win_rate = (
                self._stats.winning_trades / self._stats.total_trades
            ) * 100

        # Calculate expectancy
        winners = [t for t in self._completed_trades if t.net_pnl_sol > 0]
        losers = [t for t in self._completed_trades if t.net_pnl_sol <= 0]

        avg_win = sum(t.net_pnl_sol for t in winners) / len(winners) if winners else 0
        avg_loss = abs(sum(t.net_pnl_sol for t in losers) / len(losers)) if losers else 0

        self._stats.average_winner_sol = avg_win
        self._stats.average_loser_sol = avg_loss

        win_rate = self._stats.win_rate / 100
        loss_rate = 1 - win_rate
        self._stats.expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)

        if avg_loss > 0:
            total_wins = sum(t.net_pnl_sol for t in winners)
            total_losses = abs(sum(t.net_pnl_sol for t in losers))
            self._stats.profit_factor = total_wins / total_losses if total_losses > 0 else 0

    def _check_risk_limits(self) -> bool:
        """Check if risk limits allow new trades."""
        settings = get_settings()

        if self._stats.current_consecutive_losses >= settings.max_consecutive_losses:
            logger.warning(
                f"Risk limit: {self._stats.current_consecutive_losses} consecutive losses"
            )
            return False

        if self._stats.trades_this_hour >= settings.max_trades_per_hour:
            logger.warning(f"Risk limit: {settings.max_trades_per_hour} trades/hour")
            return False

        if settings.starting_paper_capital_sol > 0:
            daily_loss_pct = abs(self._stats.today_pnl_sol / settings.starting_paper_capital_sol) * 100
            if self._stats.today_pnl_sol < 0 and daily_loss_pct >= settings.max_daily_loss_percent:
                logger.warning(f"Risk limit: daily loss {daily_loss_pct:.1f}%")
                return False

        return True

    async def _on_kill_switch(self, reason: str) -> None:
        """Called when kill switch is activated."""
        await self.telegram.send_kill_switch_alert(reason)

        # Log kill switch event to DB
        await self._repo.save_risk_event(
            event_type="kill_switch_activated",
            severity="critical",
            detail=reason,
            action_taken="trading_halted",
        )

    @property
    def stats(self) -> TradingStats:
        return self._stats

    @property
    def candidates(self) -> list[TokenCandidate]:
        return self._all_candidates

    @property
    def completed_trades(self) -> list[TradeRecord]:
        return self._completed_trades

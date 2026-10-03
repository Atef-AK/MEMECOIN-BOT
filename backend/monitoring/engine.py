"""
Position monitoring and emergency exit engine.
Monitors open positions for target hits and emergency conditions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from backend.config.constants import DEXSCREENER_BASE_URL, WSOL_MINT, LAMPORTS_PER_SOL
from backend.config.settings import get_settings
from backend.core.event_bus import Events, get_event_bus
from backend.core.kill_switch import get_kill_switch
from backend.models.trade import ExitReason, PositionState, TradeRecord, TradeStatus

logger = logging.getLogger(__name__)


class PositionMonitor:
    """
    Monitors active positions in real-time.

    Checks for:
    1. +10% NET target hit → auto-sell
    2. Emergency conditions → immediate exit
    3. Unknown events → freeze + evaluate
    """

    def __init__(self) -> None:
        self._positions: dict[str, PositionState] = {}
        self._exiting_trades: set[str] = set()
        self._running = False
        self._check_interval = 3.0  # seconds between checks

    @property
    def active_positions(self) -> dict[str, PositionState]:
        return self._positions

    @property
    def position_count(self) -> int:
        return len(self._positions)

    def add_position(self, trade: TradeRecord) -> None:
        """Add a new position to monitor."""
        state = PositionState(
            trade=trade,
            current_price_sol=trade.entry_price_sol,
            highest_price_sol=trade.entry_price_sol,
            lowest_price_sol=trade.entry_price_sol,
        )
        self._positions[trade.id] = state
        self._exiting_trades.discard(trade.id)
        logger.info(f"Monitoring position: {trade.symbol} ({trade.id[:8]})")

    def remove_position(self, trade_id: str) -> None:
        """Remove a position from monitoring."""
        self._positions.pop(trade_id, None)
        self._exiting_trades.discard(trade_id)

    async def start(self) -> None:
        self._running = True
        logger.info("Position monitor started")

    async def stop(self) -> None:
        self._running = False
        logger.info("Position monitor stopped")

    async def run(self, sell_callback) -> None:
        """
        Main monitoring loop.
        sell_callback: async function(trade_id, exit_reason) -> TradeRecord
        """
        settings = get_settings()
        kill_switch = get_kill_switch()
        event_bus = get_event_bus()

        while self._running:
            if kill_switch.is_active:
                # Emergency: exit all positions
                for trade_id, state in list(self._positions.items()):
                    if trade_id in self._exiting_trades:
                        continue
                    self._exiting_trades.add(trade_id)
                    try:
                        await sell_callback(trade_id, ExitReason.KILL_SWITCH)
                        await event_bus.publish(
                            Events.EMERGENCY_EXIT,
                            trade_id=trade_id,
                            reason="kill_switch",
                        )
                    except Exception as e:
                        logger.error(f"Kill switch exit error: {e}")
                await asyncio.sleep(self._check_interval)
                continue

            for trade_id, state in list(self._positions.items()):
                if trade_id in self._exiting_trades:
                    continue
                try:
                    await self._check_position(state, sell_callback)
                except Exception as e:
                    logger.error(
                        f"Position check error for {trade_id}: {e}",
                        exc_info=True,
                    )

            await asyncio.sleep(self._check_interval)

    async def _check_position(
        self,
        state: PositionState,
        sell_callback,
    ) -> None:
        """Check a single position for exit conditions."""
        settings = get_settings()
        trade = state.trade

        if trade.id in self._exiting_trades:
            return

        # Update check count
        state.checks_count += 1
        state.last_checked = datetime.utcnow()

        # Check +10% NET target
        if state.current_price_sol > 0 and trade.entry_price_sol > 0:
            # Estimate NET PnL
            gross_return_sol = (state.current_price_sol / trade.entry_price_sol) * trade.entry_amount_sol
            est_exit_fees = trade.entry_fee_sol  # Approximate exit cost same as entry
            net_return_sol = gross_return_sol - trade.entry_amount_sol - trade.entry_fee_sol - est_exit_fees
            net_pnl_percent = (net_return_sol / trade.entry_amount_sol) * 100 if trade.entry_amount_sol > 0 else 0

            state.unrealized_pnl_sol = net_return_sol
            state.unrealized_pnl_percent = net_pnl_percent

            # Track high/low
            state.highest_price_sol = max(state.highest_price_sol, state.current_price_sol)
            state.lowest_price_sol = min(state.lowest_price_sol, state.current_price_sol)

            # 1. Target hit (+10% default)
            if net_pnl_percent >= settings.net_target_percent:
                logger.info(
                    f"🎯 TARGET HIT: {trade.symbol} at {net_pnl_percent:+.1f}% NET"
                )
                self._exiting_trades.add(trade.id)
                await sell_callback(trade.id, ExitReason.TARGET_HIT)
                return

            # 2. Trailing stop (if peak was >= +15% and has pulled back by 8%)
            peak_gain_percent = 0.0
            if state.highest_price_sol > trade.entry_price_sol and trade.entry_price_sol > 0:
                peak_gain_percent = ((state.highest_price_sol - trade.entry_price_sol) / trade.entry_price_sol) * 100.0

            if peak_gain_percent >= 15.0 and (peak_gain_percent - net_pnl_percent) >= 8.0:
                logger.info(
                    f"🌊 TRAILING STOP: {trade.symbol} at {net_pnl_percent:+.1f}% (Peak: +{peak_gain_percent:.1f}%)"
                )
                self._exiting_trades.add(trade.id)
                await sell_callback(trade.id, ExitReason.TRAILING_STOP)
                return

            # 3. Stop loss (-15% threshold)
            if net_pnl_percent <= -15.0 and net_pnl_percent > -50.0:
                logger.info(
                    f"🛑 STOP LOSS: {trade.symbol} at {net_pnl_percent:+.1f}%"
                )
                self._exiting_trades.add(trade.id)
                await sell_callback(trade.id, ExitReason.STOP_LOSS)
                return

            # 4. Catastrophic loss (> 50% drop)
            if net_pnl_percent <= -50.0:
                logger.warning(
                    f"💀 CATASTROPHIC LOSS: {trade.symbol} at {net_pnl_percent:+.1f}%"
                )
                self._exiting_trades.add(trade.id)
                await sell_callback(trade.id, ExitReason.EMERGENCY_PRICE_CRASH)
                return

    async def update_price(self, trade_id: str, price_sol: float) -> None:
        """Update the current price for a monitored position."""
        if trade_id in self._positions:
            self._positions[trade_id].current_price_sol = price_sol

    async def trigger_emergency(
        self,
        mint_address: str,
        reason: ExitReason,
        sell_callback,
    ) -> None:
        """Trigger emergency exit for all positions of a specific token."""
        for trade_id, state in list(self._positions.items()):
            if state.trade.mint_address == mint_address:
                state.emergency_triggered = True
                state.emergency_reason = reason.value
                await sell_callback(trade_id, reason)

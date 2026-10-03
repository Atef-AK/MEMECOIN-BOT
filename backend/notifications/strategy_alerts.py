"""
Telegram alerts for the adaptive strategy engine.
Sends formatted strategy selection, degradation, switch, and capital alerts.
"""

from __future__ import annotations

import logging

from backend.models.adaptive import (
    CapitalStage,
    MarketRegime,
    SelectionDecision,
    StrategyName,
)

logger = logging.getLogger(__name__)


class StrategyAlerts:
    """
    Formats and sends Telegram alerts for strategy events.

    Alerts:
    - STRATEGY SELECTED
    - STRATEGY SWITCHED
    - STRATEGY DEGRADED
    - STRATEGY DISABLED
    - NO TRADE
    - CAPITAL STAGE CHANGED
    """

    def __init__(self, notifier=None) -> None:
        self._notifier = notifier  # TelegramNotifier instance

    async def send_selection(self, decision: SelectionDecision) -> None:
        """Send strategy selection alert."""
        if decision.selected_strategy == StrategyName.NO_TRADE:
            await self._send_no_trade(decision)
            return

        # Format fitness comparison
        fitness_lines = []
        for name, score in sorted(
            decision.fitness_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            adj = decision.confidence_adjusted_scores.get(name, 0)
            marker = " ← SELECTED" if name == decision.selected_strategy.value else ""
            fitness_lines.append(f"  {name}: {score:.1f} (adj: {adj:.1f}){marker}")

        msg = (
            f"🧠 STRATEGY SELECTOR\n\n"
            f"Capital: {decision.position_size_sol:.4f} SOL\n"
            f"Market: {decision.market_regime.value.upper()}\n\n"
            f"Selected:\n"
            f"  {decision.selected_strategy.value.upper()}\n\n"
            f"Fitness: {decision.strategy_score:.1f}\n"
            f"Confidence: {decision.confidence:.0f}%\n"
            f"{'Mode: EXPLORATION' if decision.is_exploration else ''}\n\n"
            f"Strategy Fitness:\n"
            + "\n".join(fitness_lines)
            + f"\n\nReason:\n"
            + "\n".join(f"  • {r}" for r in decision.reasons[:5])
        )

        await self._send(msg)

    async def _send_no_trade(self, decision: SelectionDecision) -> None:
        """Send no-trade alert."""
        msg = (
            f"⚠️ NO TRADE\n\n"
            f"Market: {decision.market_regime.value.upper()}\n"
            f"Capital: {decision.capital_stage.value}\n\n"
            f"Reasons:\n"
            + "\n".join(f"  • {r}" for r in decision.reasons[:5])
        )
        await self._send(msg)

    async def send_strategy_switch(
        self,
        from_strategy: StrategyName,
        to_strategy: StrategyName,
        improvement_pct: float,
    ) -> None:
        """Send strategy switch alert."""
        msg = (
            f"🔄 STRATEGY SWITCHED\n\n"
            f"From: {from_strategy.value.upper()}\n"
            f"To: {to_strategy.value.upper()}\n"
            f"Improvement: +{improvement_pct:.0f}%\n"
        )
        await self._send(msg)

    async def send_strategy_degraded(
        self,
        strategy: StrategyName,
        reason: str,
    ) -> None:
        """Send strategy degradation alert."""
        msg = (
            f"📉 STRATEGY DEGRADED\n\n"
            f"Strategy: {strategy.value.upper()}\n"
            f"Reason: {reason}\n"
        )
        await self._send(msg)

    async def send_strategy_disabled(
        self,
        strategy: StrategyName,
        reason: str,
    ) -> None:
        """Send strategy disabled alert."""
        msg = (
            f"🛑 STRATEGY DISABLED\n\n"
            f"Strategy: {strategy.value.upper()}\n"
            f"Reason: {reason}\n"
            f"\nThe strategy will not be used until re-enabled."
        )
        await self._send(msg)

    async def send_capital_stage_change(
        self,
        from_stage: CapitalStage,
        to_stage: CapitalStage,
        balance_sol: float,
    ) -> None:
        """Send capital stage change alert."""
        msg = (
            f"💰 CAPITAL STAGE CHANGED\n\n"
            f"From: {from_stage.value}\n"
            f"To: {to_stage.value}\n"
            f"Balance: {balance_sol:.4f} SOL\n"
        )
        await self._send(msg)

    async def send_loss_protection(self, reason: str) -> None:
        """Send loss protection halt alert."""
        msg = (
            f"🛑 TRADING HALTED\n\n"
            f"Reason: {reason}\n"
            f"\nAll new trades are suspended."
        )
        await self._send(msg)

    async def _send(self, message: str) -> None:
        """Send via Telegram notifier if available."""
        if self._notifier:
            try:
                await self._notifier.send_message(message)
            except Exception as e:
                logger.debug(f"Telegram alert failed: {e}")
        else:
            logger.info(f"[TELEGRAM] {message}")

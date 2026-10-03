"""
Telegram notification system for bot alerts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.trade import TradeRecord

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Sends alerts to Telegram using the Bot API."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()
        self._enabled = False

    async def start(self) -> None:
        settings = get_settings()
        if settings.telegram_bot_token and settings.telegram_chat_id:
            self._client = httpx.AsyncClient(
                base_url=f"https://api.telegram.org/bot{settings.telegram_bot_token}",
                timeout=httpx.Timeout(10.0),
            )
            self._enabled = True
            logger.info("Telegram notifier started")
        else:
            logger.warning("Telegram not configured (missing token or chat ID)")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured chat."""
        if not self._enabled or not self._client:
            logger.debug(f"Telegram disabled, would send: {text[:100]}...")
            return False

        settings = get_settings()
        try:
            await self._rate_limiter.acquire("telegram")
            response = await self._client.post(
                "/sendMessage",
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    async def send_candidate_alert(
        self,
        symbol: str,
        mint_address: str,
        age_seconds: float,
        liquidity_usd: float,
        market_cap: float,
        total_score: float,
        security_score: float,
        liquidity_score: float,
        holder_score: float,
        dev_score: float,
        social_score: float,
        market_score: float,
        entry_sol: float,
        target_percent: float,
    ) -> None:
        msg = (
            f"🚨 <b>TRADE CANDIDATE</b>\n\n"
            f"Token: <b>${symbol}</b>\n"
            f"Age: {age_seconds:.0f}s\n"
            f"Liquidity: ${liquidity_usd:,.0f}\n"
            f"Market Cap: ${market_cap:,.0f}\n\n"
            f"Score: <b>{total_score:.0f}/100</b>\n\n"
            f"Security: {security_score:.0f}/30\n"
            f"Liquidity: {liquidity_score:.0f}/20\n"
            f"Holders: {holder_score:.0f}/20\n"
            f"Dev: {dev_score:.0f}/15\n"
            f"Social: {social_score:.0f}/10\n"
            f"Market: {market_score:.0f}/5\n\n"
            f"Entry: {entry_sol:.4f} SOL\n"
            f"Target: +{target_percent:.0f}% NET\n\n"
            f"Token: <code>{mint_address}</code>\n"
            f"Explorer: https://solscan.io/token/{mint_address}"
        )
        await self.send_message(msg)

    async def send_trade_entered(self, trade: TradeRecord) -> None:
        msg = (
            f"✅ <b>TRADE ENTERED</b> ({trade.trade_type.value.upper()})\n\n"
            f"Token: <b>${trade.symbol}</b>\n"
            f"Entry: {trade.entry_amount_sol:.4f} SOL\n"
            f"Price: {trade.entry_price_sol:.12f} SOL/token\n"
            f"Score: {trade.score:.0f}/100"
        )
        await self.send_message(msg)

    async def send_trade_exited(self, trade: TradeRecord) -> None:
        emoji = "🎯" if trade.net_pnl_sol > 0 else "🔴"
        msg = (
            f"{emoji} <b>TRADE EXITED</b> ({trade.trade_type.value.upper()})\n\n"
            f"Token: <b>${trade.symbol}</b>\n"
            f"Net PnL: {trade.net_pnl_sol:+.4f} SOL ({trade.net_pnl_percent:+.1f}%)\n"
            f"Reason: {trade.exit_reason.value if trade.exit_reason else 'unknown'}\n"
            f"Duration: {trade.time_to_exit_seconds:.0f}s"
        )
        await self.send_message(msg)

    async def send_emergency_alert(
        self, symbol: str, mint_address: str, reason: str, detail: str = ""
    ) -> None:
        msg = (
            f"🚨🚨 <b>EMERGENCY EXIT</b>\n\n"
            f"Token: <b>${symbol}</b>\n"
            f"Reason: {reason}\n"
            f"Detail: {detail}\n\n"
            f"Token: <code>{mint_address}</code>"
        )
        await self.send_message(msg)

    async def send_kill_switch_alert(self, reason: str) -> None:
        msg = (
            f"🛑🛑🛑 <b>KILL SWITCH ACTIVATED</b>\n\n"
            f"Reason: {reason}\n"
            f"All trading is STOPPED."
        )
        await self.send_message(msg)

    async def send_daily_summary(
        self,
        total_trades: int,
        wins: int,
        losses: int,
        net_pnl: float,
        win_rate: float,
        tokens_detected: int,
        tokens_rejected: int,
    ) -> None:
        msg = (
            f"📊 <b>DAILY SUMMARY</b>\n\n"
            f"Trades: {total_trades}\n"
            f"Wins: {wins} | Losses: {losses}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"Net PnL: {net_pnl:+.4f} SOL\n\n"
            f"Tokens Detected: {tokens_detected}\n"
            f"Tokens Rejected: {tokens_rejected}"
        )
        await self.send_message(msg)

    async def send_bot_error(self, component: str, error: str) -> None:
        msg = (
            f"⚠️ <b>BOT ERROR</b>\n\n"
            f"Component: {component}\n"
            f"Error: {error}"
        )
        await self.send_message(msg)

    async def health_check(self) -> bool:
        if not self._enabled or not self._client:
            return False
        try:
            response = await self._client.get("/getMe")
            return response.status_code == 200
        except Exception:
            return False

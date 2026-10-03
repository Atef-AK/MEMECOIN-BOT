"""
Pump.fun real-time discovery provider via WebSocket stream.
Connects to PumpPortal / Pump.fun live data feeds to detect newly minted
tokens sub-second from creation (<10s sniping capable).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import websockets

from backend.config.constants import WSOL_MINT
from backend.models.token import DexType, DiscoveredToken

from .base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)

PUMP_PORTAL_WS_URL = "wss://pumpportal.fun/api/data"


class PumpFunDiscoveryProvider(BaseDiscoveryProvider):
    """
    Discovers brand new tokens created on Pump.fun via real-time WebSocket.
    Subscribes to 'subscribeNewToken' events.
    """

    @property
    def name(self) -> str:
        return "pump_fun_ws"

    def __init__(self, ws_url: str = PUMP_PORTAL_WS_URL) -> None:
        self._ws_url = ws_url
        self._running = False
        self._queue: asyncio.Queue[DiscoveredToken] = asyncio.Queue(maxsize=1000)
        self._listener_task: asyncio.Task | None = None
        self._seen_mints: set[str] = set()
        self._connected = False

    async def start(self) -> None:
        self._running = True
        self._listener_task = asyncio.create_task(self._listen_loop())
        logger.info(f"PumpFun WebSocket discovery provider started ({self._ws_url})")

    async def stop(self) -> None:
        self._running = False
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        self._connected = False
        logger.info("PumpFun WebSocket discovery provider stopped")

    async def _listen_loop(self) -> None:
        """Continuously connect and listen to PumpPortal WebSocket with auto-reconnect."""
        while self._running:
            try:
                logger.info(f"Connecting to Pump.fun WebSocket at {self._ws_url}...")
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                ) as ws:
                    self._connected = True
                    # Subscribe to new token creation events
                    payload = {"method": "subscribeNewToken"}
                    await ws.send(json.dumps(payload))
                    logger.info("⚡ Subscribed to Pump.fun new token launch stream")

                    while self._running:
                        message = await ws.recv()
                        try:
                            data = json.loads(message)
                            token = self._parse_pump_token(data)
                            if token:
                                if token.mint_address not in self._seen_mints:
                                    self._seen_mints.add(token.mint_address)
                                    if len(self._seen_mints) > 20000:
                                        self._seen_mints.clear()
                                    try:
                                        self._queue.put_nowait(token)
                                        logger.info(
                                            f"⚡ [PUMP.FUN LIVE] Detected new mint: ${token.symbol} "
                                            f"({token.mint_address[:8]}...) | Bonding: {token.pool_address[:8]}..."
                                        )
                                    except asyncio.QueueFull:
                                        try:
                                            self._queue.get_nowait()
                                            self._queue.put_nowait(token)
                                        except Exception:
                                            pass
                        except json.JSONDecodeError:
                            continue
                        except Exception as e:
                            logger.debug(f"Error parsing Pump.fun message: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                if self._running:
                    logger.warning(f"Pump.fun WebSocket disconnected ({e}), reconnecting in 3s...")
                    await asyncio.sleep(3.0)

    def _parse_pump_token(self, data: dict) -> DiscoveredToken | None:
        """Parse raw websocket payload from pumpportal into a DiscoveredToken."""
        if not isinstance(data, dict):
            return None

        mint = data.get("mint")
        if not mint:
            return None

        name = data.get("name", "").strip()
        symbol = data.get("symbol", "").strip() or "PUMP"
        bonding_curve = data.get("bondingCurveKey", "")
        v_sol = data.get("vSolInBondingCurve", 30000000000)
        market_cap_sol = data.get("marketCapSol", 27.95)

        # Estimate initial liquidity in USD (virtual curve is ~30 SOL, assume SOL ~$150-$200 -> ~$5,000)
        # Pump.fun standard initial virtual reserve is 30 SOL (~$4,500 - $6,000)
        sol_price_est = 150.0
        initial_liq_usd = max(4500.0, float(v_sol) / 1e9 * sol_price_est)
        initial_mcap_usd = float(market_cap_sol) * sol_price_est

        trader_pubkey = data.get("traderPublicKey", "")
        initial_buy = float(data.get("initialBuy", 0.0) or 0.0)

        return DiscoveredToken(
            mint_address=mint,
            name=name,
            symbol=symbol,
            pool_address=bonding_curve,
            dex=DexType.PUMP_FUN,
            quote_token=WSOL_MINT,
            created_at=now,
            discovered_at=now,
            initial_liquidity_usd=initial_liq_usd,
            current_liquidity_usd=initial_liq_usd,
            initial_market_cap=initial_mcap_usd,
            fdv=initial_mcap_usd,
            price_sol=float(market_cap_sol) / 1_000_000_000.0 if market_cap_sol else 0.00000003,
            price_usd=float(market_cap_sol) * sol_price_est / 1_000_000_000.0,
            buys=1 if initial_buy > 0 else 0,
            sells=0,
            tx_count=1 if initial_buy > 0 else 0,
            creator_wallet=trader_pubkey,
            initial_buy_sol=initial_buy,
            source="pump_fun_ws",
        )

    async def discover(self) -> AsyncIterator[DiscoveredToken]:
        """Drain queued newly discovered tokens."""
        while not self._queue.empty():
            try:
                token = self._queue.get_nowait()
                yield token
            except asyncio.QueueEmpty:
                break

    async def health_check(self) -> bool:
        return self._connected or self._running

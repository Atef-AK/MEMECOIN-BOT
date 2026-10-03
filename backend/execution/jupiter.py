"""
Jupiter Swap API v2 integration.
Uses the current Meta-Aggregator path (/order + /execute).
Legacy v6 API is SUNSET - do not use.

Docs: https://developers.jup.ag/
Base URL: https://api.jup.ag/swap/v2
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from backend.config.constants import WSOL_MINT, LAMPORTS_PER_SOL, DEFAULT_PRIORITY_FEE_LAMPORTS
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.trade import SwapQuote

logger = logging.getLogger(__name__)


class JupiterClient:
    """
    Client for the Jupiter Swap API v2 (Meta-Aggregator).

    Two paths available:
    1. Meta-Aggregator: /order + /execute (recommended, Jupiter handles tx landing)
    2. Router: /build + /submit (full control, requires own RPC)

    This implementation uses the Meta-Aggregator path.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()

    async def start(self) -> None:
        settings = get_settings()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if settings.jupiter_api_key:
            headers["x-api-key"] = settings.jupiter_api_key

        self._client = httpx.AsyncClient(
            base_url=settings.jupiter_api_url,
            timeout=httpx.Timeout(30.0),
            headers=headers,
        )
        logger.info("Jupiter client started")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int | None = None,
    ) -> SwapQuote:
        """
        Get an executable swap quote from Jupiter.

        For BUY: input_mint=WSOL, output_mint=token, amount=SOL in lamports
        For SELL: input_mint=token, output_mint=WSOL, amount=token amount in raw units
        """
        settings = get_settings()
        if slippage_bps is None:
            slippage_bps = settings.max_slippage_bps

        quote = SwapQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            input_amount_lamports=amount_lamports,
            slippage_bps=slippage_bps,
        )

        if not self._client:
            quote.rejection_reason = "Jupiter client not initialized"
            return quote

        try:
            await self._rate_limiter.acquire("jupiter")
            start_time = time.monotonic()

            # Request quote via /order endpoint
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount_lamports),
                "slippageBps": str(slippage_bps),
                "onlyDirectRoutes": "false",
                "asLegacyTransaction": "false",
            }

            response = await self._client.get("/order", params=params)
            elapsed_ms = (time.monotonic() - start_time) * 1000

            if response.status_code != 200:
                quote.rejection_reason = f"Jupiter API error: {response.status_code}"
                logger.warning(f"Jupiter quote failed: {response.status_code} - {response.text[:200]}")
                return quote

            data = response.json()

            # Parse quote data
            quote.output_amount_raw = int(data.get("outAmount", 0))
            quote.price_impact_percent = float(data.get("priceImpactPct", 0))
            quote.priority_fee_lamports = DEFAULT_PRIORITY_FEE_LAMPORTS

            # Calculate human-readable amounts
            if input_mint == WSOL_MINT:
                quote.input_amount_sol = amount_lamports / LAMPORTS_PER_SOL
            else:
                quote.input_amount_sol = 0  # Selling tokens

            # Calculate effective price
            if quote.output_amount_raw > 0 and quote.input_amount_lamports > 0:
                quote.is_executable = True

            # Estimate total cost
            quote.estimated_total_cost_sol = (
                quote.priority_fee_lamports / LAMPORTS_PER_SOL
                + 5000 / LAMPORTS_PER_SOL  # Base tx fee
            )

            # Calculate break-even and target prices for buys
            if input_mint == WSOL_MINT and quote.output_amount_raw > 0:
                # SPL memecoins (Pump.fun & Raydium) use 6 decimals (1 token = 1,000,000 atomic units)
                whole_tokens = quote.output_amount_raw / 1_000_000.0
                total_cost_sol = quote.input_amount_sol + quote.estimated_total_cost_sol

                # Effective price per whole token (in SOL) matching DexScreener priceNative
                quote.effective_price = total_cost_sol / whole_tokens if whole_tokens > 0 else 0.0

                # Break-even price includes exit costs
                exit_cost_estimate = quote.estimated_total_cost_sol  # Approximate
                quote.break_even_price = (total_cost_sol + exit_cost_estimate) / whole_tokens if whole_tokens > 0 else 0.0

                # +10% NET target price
                target_pct = settings.net_target_percent / 100.0
                quote.target_price = quote.break_even_price * (1.0 + target_pct)

            # Parse route info
            route_plan = data.get("routePlan", [])
            if isinstance(route_plan, list):
                dexes = []
                for step in route_plan:
                    swap_info = step.get("swapInfo", {})
                    label = swap_info.get("label", "")
                    if label:
                        dexes.append(label)
                quote.route_dexes = dexes
                quote.route_plan = " → ".join(dexes) if dexes else "direct"

            # Reject if price impact is too high
            if abs(quote.price_impact_percent) > 5.0:
                quote.is_executable = False
                quote.rejection_reason = f"Price impact too high: {quote.price_impact_percent:.2f}%"

            logger.debug(
                f"Jupiter quote: {input_mint[:8]}→{output_mint[:8]} "
                f"in={amount_lamports} out={quote.output_amount_raw} "
                f"impact={quote.price_impact_percent:.2f}% "
                f"latency={elapsed_ms:.0f}ms"
            )

        except httpx.RequestError as e:
            quote.rejection_reason = f"Jupiter request error: {str(e)}"
            logger.warning(f"Jupiter quote request error: {e}")
        except Exception as e:
            quote.rejection_reason = f"Jupiter quote error: {str(e)}"
            logger.error(f"Jupiter quote error: {e}", exc_info=True)

        return quote

    async def execute_swap(self, order_data: dict, signed_tx: bytes) -> dict:
        """
        Execute a signed swap transaction via Jupiter /execute endpoint.

        NOTE: This is only used for LIVE trading. Paper trading simulates execution.
        """
        if not self._client:
            return {"error": "Jupiter client not initialized"}

        try:
            await self._rate_limiter.acquire("jupiter")

            import base64
            tx_base64 = base64.b64encode(signed_tx).decode("utf-8")

            payload = {
                "signedTransaction": tx_base64,
            }

            response = await self._client.post("/execute", json=payload)

            if response.status_code != 200:
                return {"error": f"Execute failed: {response.status_code}", "detail": response.text[:500]}

            return response.json()

        except Exception as e:
            return {"error": f"Execute error: {str(e)}"}

    async def health_check(self) -> bool:
        """Check if Jupiter API is reachable."""
        if not self._client:
            return False

        try:
            # Try a simple SOL→USDC quote as health check
            response = await self._client.get(
                "/order",
                params={
                    "inputMint": WSOL_MINT,
                    "outputMint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "amount": "1000000",  # 0.001 SOL
                    "slippageBps": "50",
                },
            )
            return response.status_code == 200
        except Exception:
            return False

"""
Solana RPC discovery provider.
Monitors on-chain events for new pool creation across DEXes.
Uses Solana JSON-RPC and WebSocket APIs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from backend.config.constants import (
    RAYDIUM_AMM_V4_PROGRAM_ID,
    RAYDIUM_CPMM_PROGRAM_ID,
    ORCA_WHIRLPOOL_PROGRAM_ID,
    METEORA_DLMM_PROGRAM_ID,
    PUMP_FUN_BONDING_PROGRAM_ID,
    PUMPSWAP_AMM_PROGRAM_ID,
    WSOL_MINT,
)
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.token import DexType, DiscoveredToken

from .base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)


class SolanaRpcProvider(BaseDiscoveryProvider):
    """
    Discovers new tokens by polling Solana RPC for recent program logs
    from known DEX pool-creation programs.

    For production, this should be upgraded to WebSocket subscription
    using logsSubscribe for real-time event streaming.
    """

    @property
    def name(self) -> str:
        return "solana_rpc"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._running = False
        self._seen_signatures: set[str] = set()
        self._rate_limiter = get_rate_limiter_registry()

    async def start(self) -> None:
        settings = get_settings()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers={"Content-Type": "application/json"},
        )
        self._running = True
        logger.info(f"Solana RPC discovery provider started (endpoint: {settings.rpc_url})")

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Solana RPC discovery provider stopped")

    async def discover(self) -> AsyncIterator[DiscoveredToken]:
        """
        Poll for recent transactions involving DEX pool-creation programs.
        Yields DiscoveredToken for each new pool detected.
        """
        if not self._client:
            return

        settings = get_settings()

        # Check each DEX program for recent signatures
        programs = [
            (RAYDIUM_AMM_V4_PROGRAM_ID, DexType.RAYDIUM),
            (RAYDIUM_CPMM_PROGRAM_ID, DexType.RAYDIUM),
            (PUMPSWAP_AMM_PROGRAM_ID, DexType.PUMPSWAP),
        ]

        for program_id, dex_type in programs:
            try:
                await self._rate_limiter.acquire("solana_rpc")
                signatures = await self._get_recent_signatures(
                    settings.rpc_url, program_id, limit=10
                )

                for sig_info in signatures:
                    sig = sig_info.get("signature", "")
                    if not sig or sig in self._seen_signatures:
                        continue

                    self._seen_signatures.add(sig)

                    # Limit seen set size
                    if len(self._seen_signatures) > 10000:
                        # Keep only last 5000
                        excess = list(self._seen_signatures)[:5000]
                        for s in excess:
                            self._seen_signatures.discard(s)

                    # Parse transaction for token mint
                    token = await self._parse_pool_creation(
                        settings.rpc_url, sig, dex_type
                    )
                    if token:
                        yield token

            except Exception as e:
                logger.debug(f"RPC discovery error for {program_id}: {e}")

    async def _get_recent_signatures(
        self, rpc_url: str, program_id: str, limit: int = 10
    ) -> list[dict]:
        """Get recent transaction signatures for a program."""
        if not self._client:
            return []

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                program_id,
                {"limit": limit, "commitment": "confirmed"},
            ],
        }

        response = await self._client.post(rpc_url, json=payload)
        response.raise_for_status()
        result = response.json()
        return result.get("result", [])

    async def _parse_pool_creation(
        self, rpc_url: str, signature: str, dex_type: DexType
    ) -> DiscoveredToken | None:
        """
        Parse a transaction to extract pool creation details.
        Returns a DiscoveredToken if a new pool creation is detected.
        """
        if not self._client:
            return None

        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0,
                        "commitment": "confirmed",
                    },
                ],
            }

            response = await self._client.post(rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result")

            if not result:
                return None

            # Extract account keys and look for token mints
            meta = result.get("meta", {})
            if meta.get("err") is not None:
                return None  # Failed transaction

            tx = result.get("transaction", {})
            message = tx.get("message", {})
            account_keys = message.get("accountKeys", [])

            # Look for new token accounts in postTokenBalances
            post_balances = meta.get("postTokenBalances", [])
            pre_balances = meta.get("preTokenBalances", [])

            # Find token mints that appear in post but not pre (new allocations)
            pre_mints = {b.get("mint") for b in pre_balances if b.get("mint")}
            new_mints = set()

            for balance in post_balances:
                mint = balance.get("mint", "")
                if mint and mint != WSOL_MINT and mint not in pre_mints:
                    new_mints.add(mint)

            # For simplicity, take the first non-SOL mint
            for mint in new_mints:
                # Get block time
                block_time = result.get("blockTime")
                created_at = None
                if block_time:
                    created_at = datetime.fromtimestamp(block_time, tz=timezone.utc)

                return DiscoveredToken(
                    mint_address=mint,
                    dex=dex_type,
                    quote_token=WSOL_MINT,
                    created_at=created_at,
                    source="solana_rpc",
                )

            return None

        except Exception as e:
            logger.debug(f"Failed to parse tx {signature}: {e}")
            return None

    async def get_mint_info(self, rpc_url: str, mint_address: str) -> dict | None:
        """Fetch mint account info from RPC."""
        if not self._client:
            return None

        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    mint_address,
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result")

            if result and result.get("value"):
                return result["value"]
            return None

        except Exception as e:
            logger.debug(f"Failed to fetch mint info for {mint_address}: {e}")
            return None

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            settings = get_settings()
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getHealth",
            }
            response = await self._client.post(settings.rpc_url, json=payload)
            result = response.json()
            return result.get("result") == "ok"
        except Exception:
            return False

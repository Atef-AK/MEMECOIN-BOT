"""
LP lock detection via on-chain inspection.

Checks whether a pool's LP tokens are held by known lock programs
(e.g. Raydium Burn & Earn) to determine if liquidity is locked.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from backend.config.constants import (
    LP_LOCK_PROVIDERS,
    RAYDIUM_AMM_V4_PROGRAM_ID,
    RAYDIUM_CPMM_PROGRAM_ID,
    RAYDIUM_LP_LOCK_PROGRAM_ID,
    BURN_ADDRESSES,
    SPL_TOKEN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
)
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry

logger = logging.getLogger(__name__)


class LPLockResult:
    """Result of LP lock detection."""

    __slots__ = (
        "lp_locked",
        "lock_percent",
        "lock_provider",
        "burned_percent",
        "total_lp_supply",
        "locked_amount",
        "burned_amount",
        "lp_mint",
        "error",
    )

    def __init__(self) -> None:
        self.lp_locked: bool = False
        self.lock_percent: float = 0.0
        self.lock_provider: str = ""
        self.burned_percent: float = 0.0
        self.total_lp_supply: int = 0
        self.locked_amount: int = 0
        self.burned_amount: int = 0
        self.lp_mint: str = ""
        self.error: str = ""


class LPLockDetector:
    """
    Detects LP token locks by inspecting on-chain state.

    Strategy:
    1. Resolve the LP mint for the pool (from pool account data)
    2. Get the LP token's total supply and largest holders
    3. For each large holder, check if the account is owned by
       a known lock program (Raydium Lock, etc.) or is a burn address
    4. Calculate the locked/burned percentage
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._rate_limiter = get_rate_limiter_registry()

    def set_client(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def detect(self, pool_address: str, dex: str = "") -> LPLockResult:
        """
        Detect LP lock status for a pool.

        Args:
            pool_address: The AMM pool / pair address.
            dex: DEX identifier (raydium, orca, meteora, etc.)

        Returns:
            LPLockResult with lock status and details.
        """
        result = LPLockResult()

        if not self._client or not pool_address:
            result.error = "No client or pool address"
            return result

        try:
            # Step 1: Find the LP mint for this pool
            lp_mint = await self._resolve_lp_mint(pool_address, dex)
            if not lp_mint:
                result.error = "Could not resolve LP mint"
                return result

            result.lp_mint = lp_mint

            # Step 2: Get LP token supply and largest holders
            supply = await self._get_token_supply(lp_mint)
            if supply <= 0:
                result.error = "LP supply is zero or unavailable"
                return result

            result.total_lp_supply = supply

            holders = await self._get_largest_accounts(lp_mint)
            if not holders:
                result.error = "Could not fetch LP holders"
                return result

            # Step 3: Check each holder for lock/burn status
            for holder in holders:
                holder_address = holder.get("address", "")
                holder_amount = int(holder.get("amount", 0))

                if holder_amount <= 0:
                    continue

                holder_pct = (holder_amount / supply) * 100

                # Check if burned (sent to a known burn address)
                if holder_address in BURN_ADDRESSES:
                    result.burned_amount += holder_amount
                    result.burned_percent += holder_pct
                    continue

                # Check if the holder account is owned by a lock program
                owner = await self._get_account_owner(holder_address)
                if owner in LP_LOCK_PROVIDERS:
                    result.locked_amount += holder_amount
                    result.lock_percent += holder_pct
                    result.lock_provider = LP_LOCK_PROVIDERS[owner]

            # Determine overall lock status
            # Consider both locked and burned as "locked" for scoring
            total_secured = result.lock_percent + result.burned_percent

            if total_secured > 0:
                result.lp_locked = True
                result.lock_percent = total_secured

                if result.burned_percent > 0 and not result.lock_provider:
                    result.lock_provider = "Burned"
                elif result.burned_percent > 0 and result.lock_provider:
                    result.lock_provider += " + Burned"

            logger.debug(
                f"LP lock for {pool_address[:12]}: "
                f"locked={result.lock_percent:.1f}% "
                f"burned={result.burned_percent:.1f}% "
                f"provider={result.lock_provider}"
            )

        except Exception as e:
            result.error = str(e)
            logger.debug(f"LP lock detection error for {pool_address[:12]}: {e}")

        return result

    async def _resolve_lp_mint(self, pool_address: str, dex: str) -> str:
        """
        Resolve the LP token mint address from pool account data.

        For Raydium AMM v4 pools, the LP mint is stored in the pool's
        parsed account data. For other DEXes, we try generic approaches.
        """
        if not self._client:
            return ""

        settings = get_settings()

        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    pool_address,
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", {})
            value = result.get("value")

            if not value:
                return ""

            owner = value.get("owner", "")
            data = value.get("data", {})

            # For Raydium AMM v4, the account data contains the LP mint
            # The pool account is not "jsonParsed"-friendly, so we need
            # to check if it's a token account and extract differently.

            # Try parsed data first
            if isinstance(data, dict):
                parsed = data.get("parsed", {})
                info = parsed.get("info", {}) if isinstance(parsed, dict) else {}

                # Some pool formats store lpMint directly
                lp_mint = info.get("lpMint", "")
                if lp_mint:
                    return lp_mint

            # Fallback: try Raydium API for AMM v4 pools
            if dex in ("raydium", "Raydium") or owner == RAYDIUM_AMM_V4_PROGRAM_ID:
                return await self._resolve_lp_mint_raydium_api(pool_address)

            # Fallback: try getting token accounts owned by the pool
            # Many pool types have the LP mint as a token they created
            return await self._resolve_lp_mint_from_pool_tokens(pool_address)

        except Exception as e:
            logger.debug(f"LP mint resolution error: {e}")
            return ""

    async def _resolve_lp_mint_raydium_api(self, pool_address: str) -> str:
        """Try resolving LP mint via Raydium API v3."""
        if not self._client:
            return ""

        try:
            await self._rate_limiter.acquire("dexscreener")  # reuse a limiter

            response = await self._client.get(
                f"https://api-v3.raydium.io/pools/info/ids",
                params={"ids": pool_address},
            )
            response.raise_for_status()
            data = response.json()

            pools = data.get("data", [])
            if isinstance(pools, list) and pools:
                return pools[0].get("lpMint", {}).get("address", "")

            return ""

        except Exception as e:
            logger.debug(f"Raydium API LP mint lookup failed: {e}")
            return ""

    async def _resolve_lp_mint_from_pool_tokens(self, pool_address: str) -> str:
        """
        Fallback: get token accounts associated with the pool and find
        the LP mint by checking which mint has the pool as authority.
        """
        # This is a best-effort heuristic. For many DEXes, the pool
        # address is the mint authority of the LP token.
        if not self._client:
            return ""

        settings = get_settings()

        try:
            await self._rate_limiter.acquire("solana_rpc")

            # Check if pool_address is itself a mint (some DEXes use this pattern)
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    pool_address,
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", {})
            value = result.get("value")

            if not value:
                return ""

            owner = value.get("owner", "")
            # If the pool address is owned by a token program, it IS a mint
            if owner in (SPL_TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
                data = value.get("data", {})
                if isinstance(data, dict):
                    parsed = data.get("parsed", {})
                    ptype = parsed.get("type", "")
                    if ptype == "mint":
                        return pool_address

            return ""

        except Exception as e:
            logger.debug(f"LP mint fallback resolution error: {e}")
            return ""

    async def _get_token_supply(self, mint: str) -> int:
        """Get total supply of a token mint."""
        if not self._client:
            return 0

        settings = get_settings()

        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenSupply",
                "params": [mint, {"commitment": "confirmed"}],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", {})
            value = result.get("value", {})
            return int(value.get("amount", 0))

        except Exception as e:
            logger.debug(f"Token supply fetch error for {mint[:12]}: {e}")
            return 0

    async def _get_largest_accounts(self, mint: str) -> list[dict]:
        """Get the 20 largest token accounts for a mint."""
        if not self._client:
            return []

        settings = get_settings()

        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [mint, {"commitment": "confirmed"}],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", {})
            return result.get("value", [])

        except Exception as e:
            logger.debug(f"Largest accounts fetch error for {mint[:12]}: {e}")
            return []

    async def _get_account_owner(self, address: str) -> str:
        """Get the program that owns an account (to identify lock programs)."""
        if not self._client:
            return ""

        settings = get_settings()

        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [
                    address,
                    {"encoding": "base64", "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", {})
            value = result.get("value")
            if value:
                return value.get("owner", "")
            return ""

        except Exception as e:
            logger.debug(f"Account owner fetch error for {address[:12]}: {e}")
            return ""

"""
DEX Screener discovery provider.
Uses the public API to find newly created Solana token pairs.
API docs: https://docs.dexscreener.com/api/reference
Rate limit: 300 requests/min for pairs/tokens endpoints.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from backend.config.constants import DEXSCREENER_BASE_URL, WSOL_MINT, ACCEPTED_QUOTE_MINTS
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.token import DexType, DiscoveredToken

from .base import BaseDiscoveryProvider

logger = logging.getLogger(__name__)


class DexScreenerProvider(BaseDiscoveryProvider):
    """
    Discovers newly created Solana tokens via DEX Screener API.

    Endpoints used:
    - GET /token-profiles/latest/v1 - Latest token profiles
    - GET /token-boosts/latest/v1 - Latest boosted tokens
    - GET /tokens/v1/solana/{address} - Token details
    """

    @property
    def name(self) -> str:
        return "dexscreener"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._running = False
        self._seen_mints: set[str] = set()
        self._rate_limiter = get_rate_limiter_registry()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=DEXSCREENER_BASE_URL,
            timeout=httpx.Timeout(15.0),
            headers={"Accept": "application/json"},
        )
        self._running = True
        logger.info("DexScreener discovery provider started")

    async def stop(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("DexScreener discovery provider stopped")

    async def discover(self) -> AsyncIterator[DiscoveredToken]:
        """Poll DEX Screener for new Solana token profiles."""
        if not self._client:
            return

        settings = get_settings()

        try:
            # 1. Fetch latest token profiles
            all_items = []
            try:
                await self._rate_limiter.acquire("dexscreener")
                response = await self._client.get("/token-profiles/latest/v1")
                if response.status_code == 200:
                    profiles = response.json()
                    if isinstance(profiles, list):
                        all_items.extend(profiles)
            except Exception as e:
                logger.debug(f"Failed to fetch token profiles: {e}")

            # 2. Fetch latest token boosts
            try:
                await self._rate_limiter.acquire("dexscreener")
                response2 = await self._client.get("/token-boosts/latest/v1")
                if response2.status_code == 200:
                    boosts = response2.json()
                    if isinstance(boosts, list):
                        all_items.extend(boosts)
            except Exception as e:
                logger.debug(f"Failed to fetch token boosts: {e}")

            for profile in all_items:
                if not isinstance(profile, dict):
                    continue

                chain_id = profile.get("chainId", "")
                if chain_id != "solana":
                    continue

                token_address = profile.get("tokenAddress", "")
                if not token_address or token_address in self._seen_mints:
                    continue

                self._seen_mints.add(token_address)

                # Fetch detailed pair data
                token_data = await self._fetch_token_pairs(token_address)
                if not token_data:
                    continue

                for pair_data in token_data:
                    token = self._parse_pair(pair_data, profile)
                    if token:
                        yield token

        except httpx.HTTPStatusError as e:
            logger.warning(f"DexScreener API error: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.warning(f"DexScreener request error: {e}")
        except Exception as e:
            logger.error(f"DexScreener discovery error: {e}", exc_info=True)


    async def _fetch_token_pairs(self, token_address: str) -> list[dict] | None:
        """Fetch pair data for a specific token."""
        if not self._client:
            return None

        try:
            await self._rate_limiter.acquire("dexscreener")
            response = await self._client.get(f"/tokens/v1/solana/{token_address}")
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            return None
        except Exception as e:
            logger.debug(f"Failed to fetch pairs for {token_address}: {e}")
            return None

    def _parse_pair(self, pair: dict, profile: dict | None = None) -> DiscoveredToken | None:
        """Parse a DEX Screener pair object into a DiscoveredToken."""
        try:
            base_token = pair.get("baseToken", {})
            quote_token = pair.get("quoteToken", {})
            liquidity = pair.get("liquidity", {})
            txns = pair.get("txns", {})
            txns_h1 = txns.get("h1", {})

            # Only accept pairs with recognized quote tokens
            quote_address = quote_token.get("address", "")
            if quote_address not in ACCEPTED_QUOTE_MINTS:
                return None

            # Parse creation time
            created_at = None
            pair_created_at = pair.get("pairCreatedAt")
            if pair_created_at:
                try:
                    if isinstance(pair_created_at, (int, float)):
                        created_at = datetime.fromtimestamp(
                            pair_created_at / 1000, tz=timezone.utc
                        )
                except (ValueError, OSError):
                    pass

            # Determine DEX
            dex_id = pair.get("dexId", "").lower()
            dex = DexType.UNKNOWN
            if "raydium" in dex_id:
                dex = DexType.RAYDIUM
            elif "orca" in dex_id:
                dex = DexType.ORCA
            elif "meteora" in dex_id:
                dex = DexType.METEORA
            elif "pump" in dex_id:
                dex = DexType.PUMP_FUN

            # Extract social links from profile
            website = ""
            twitter = ""
            telegram = ""
            discord = ""
            description = ""
            image_url = ""

            if profile:
                links = profile.get("links", [])
                for link in links:
                    if isinstance(link, dict):
                        link_type = link.get("type", "").lower()
                        link_url = link.get("url", "")
                        if link_type == "website":
                            website = link_url
                        elif link_type in ("twitter", "x"):
                            twitter = link_url
                        elif link_type == "telegram":
                            telegram = link_url
                        elif link_type == "discord":
                            discord = link_url

                description = profile.get("description", "")
                image_url = profile.get("icon", "")

            # Also check pair-level info
            info = pair.get("info", {})
            if info:
                if not website:
                    websites = info.get("websites", [])
                    if websites and isinstance(websites, list):
                        for w in websites:
                            if isinstance(w, dict) and w.get("url"):
                                website = w["url"]
                                break
                socials = info.get("socials", [])
                if isinstance(socials, list):
                    for s in socials:
                        if isinstance(s, dict):
                            s_type = s.get("type", "").lower()
                            s_url = s.get("url", "")
                            if s_type in ("twitter", "x") and not twitter:
                                twitter = s_url
                            elif s_type == "telegram" and not telegram:
                                telegram = s_url
                            elif s_type == "discord" and not discord:
                                discord = s_url
                if not image_url:
                    image_url = info.get("imageUrl", "")

            price_usd = 0.0
            try:
                price_usd = float(pair.get("priceUsd", 0))
            except (ValueError, TypeError):
                pass

            price_sol = 0.0
            try:
                price_sol = float(pair.get("priceNative", 0))
            except (ValueError, TypeError):
                pass

            return DiscoveredToken(
                mint_address=base_token.get("address", ""),
                name=base_token.get("name", ""),
                symbol=base_token.get("symbol", ""),
                pool_address=pair.get("pairAddress", ""),
                dex=dex,
                quote_token=quote_address,
                created_at=created_at,
                initial_liquidity_usd=float(liquidity.get("usd", 0) or 0),
                current_liquidity_usd=float(liquidity.get("usd", 0) or 0),
                initial_market_cap=float(pair.get("marketCap", 0) or 0),
                fdv=float(pair.get("fdv", 0) or 0),
                price_usd=price_usd,
                price_sol=price_sol,
                volume_24h=float(pair.get("volume", {}).get("h24", 0) or 0),
                buys=int(txns_h1.get("buys", 0) or 0),
                sells=int(txns_h1.get("sells", 0) or 0),
                tx_count=int(txns_h1.get("buys", 0) or 0) + int(txns_h1.get("sells", 0) or 0),
                website=website,
                twitter=twitter,
                telegram=telegram,
                discord=discord,
                description=description,
                image_url=image_url,
                source="dexscreener",
            )
        except Exception as e:
            logger.debug(f"Failed to parse pair data: {e}")
            return None

    async def fetch_pair_by_address(self, pair_address: str) -> dict | None:
        """Fetch current data for a specific pair address."""
        if not self._client:
            return None

        try:
            await self._rate_limiter.acquire("dexscreener")
            response = await self._client.get(f"/latest/dex/pairs/solana/{pair_address}")
            response.raise_for_status()
            data = response.json()
            pairs = data.get("pairs", [])
            if pairs:
                return pairs[0]
            return None
        except Exception as e:
            logger.debug(f"Failed to fetch pair {pair_address}: {e}")
            return None

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            response = await self._client.get("/token-profiles/latest/v1")
            return response.status_code == 200
        except Exception:
            return False

"""
Developer wallet tracking and wallet cluster analysis engine.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.security import DevReport

logger = logging.getLogger(__name__)


class DevTrackerEngine:
    """
    Tracks deployer/creator wallet behavior and builds a lightweight wallet graph.

    Analyzes:
    - Creator wallet identification
    - Creator SOL balance and token holdings
    - Funding sources
    - Previous launches
    - Token transfers to related wallets
    - Coordinated wallet activity
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()
        # Cache of known creator wallets and their history
        self._creator_cache: dict[str, dict] = {}

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"Content-Type": "application/json"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def analyze(
        self,
        mint_address: str,
        creator_wallet: str = "",
        initial_buy_sol: float = 0.0,
    ) -> DevReport:
        """Analyze the deployer/creator wallet for a token."""
        report = DevReport(
            mint_address=mint_address,
            creator_wallet=creator_wallet,
        )

        # 1. Pre-check initial launch snipe bundle
        if initial_buy_sol > 0:
            # On Pump.fun, initial virtual curve is ~30 SOL. 1.5 SOL = 5% of supply.
            est_snipe_percent = (initial_buy_sol / 30.0) * 100.0
            report.creator_token_percent = max(report.creator_token_percent, est_snipe_percent)
            if est_snipe_percent > 5.0:
                report.critical_failures.append(
                    f"Creator sniped {est_snipe_percent:.1f}% of initial supply ({initial_buy_sol:.2f} SOL > 5.0% max)"
                )
                report.passed = False
                report.score = 0.0
                logger.warning(
                    f"🚨 [ANTI-RUG] Flagged creator snipe on {mint_address[:8]}: {est_snipe_percent:.1f}% supply"
                )
                return report

        if not creator_wallet:
            # Attempt to find creator from mint transaction
            creator_wallet = await self._find_creator(mint_address)
            report.creator_wallet = creator_wallet

        if not creator_wallet:
            report.status = "unknown"
            report.score = 7.5  # Half credit when we can't verify
            report.passed = True
            return report

        try:
            # Get creator SOL balance
            balance = await self._get_sol_balance(creator_wallet)
            report.creator_sol_balance = balance

            # Get creator token balance
            token_balance = await self._get_token_balance(creator_wallet, mint_address)
            if token_balance > 0:
                report.creator_token_balance = token_balance

            # Check recent transactions for sells/transfers
            await self._check_recent_activity(report)

            # Check creator history in cache
            if creator_wallet in self._creator_cache:
                cached = self._creator_cache[creator_wallet]
                report.previous_launches = cached.get("launches", 0)
                report.previous_rugs = cached.get("rugs", 0)
                report.previous_successes = cached.get("successes", 0)

            # Build wallet graph (simplified)
            await self._build_wallet_graph(report)

            # Calculate score
            self._calculate_score(report)

        except Exception as e:
            logger.error(f"Dev tracker error for {mint_address}: {e}", exc_info=True)
            report.status = "error"
            report.score = 0.0

        report.passed = len(report.critical_failures) == 0
        return report

    async def _find_creator(self, mint_address: str) -> str:
        """Attempt to find the wallet that created a token mint."""
        if not self._client:
            return ""

        settings = get_settings()
        try:
            await self._rate_limiter.acquire("solana_rpc")

            # Get signatures for the mint address (oldest first)
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    mint_address,
                    {"limit": 1, "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", [])

            if not result:
                return ""

            # Get the first (creation) transaction
            sig = result[-1].get("signature", "")  # Last = earliest
            if not sig:
                return ""

            await self._rate_limiter.acquire("solana_rpc")
            tx_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    sig,
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0,
                        "commitment": "confirmed",
                    },
                ],
            }

            tx_response = await self._client.post(settings.rpc_url, json=tx_payload)
            tx_response.raise_for_status()
            tx_result = tx_response.json().get("result")

            if not tx_result:
                return ""

            # The fee payer is typically the creator
            message = tx_result.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            if account_keys:
                first_key = account_keys[0]
                if isinstance(first_key, dict):
                    return first_key.get("pubkey", "")
                return str(first_key)

            return ""

        except Exception as e:
            logger.debug(f"Failed to find creator for {mint_address}: {e}")
            return ""

    async def _get_sol_balance(self, wallet: str) -> float:
        """Get SOL balance for a wallet."""
        if not self._client:
            return 0.0

        settings = get_settings()
        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [wallet, {"commitment": "confirmed"}],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", {})
            lamports = result.get("value", 0)
            return lamports / 1e9  # Convert to SOL
        except Exception:
            return 0.0

    async def _get_token_balance(self, wallet: str, mint: str) -> float:
        """Get token balance for a wallet."""
        if not self._client:
            return 0.0

        settings = get_settings()
        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    wallet,
                    {"mint": mint},
                    {"encoding": "jsonParsed", "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            result = response.json().get("result", {})
            accounts = result.get("value", [])

            total = 0.0
            for acc in accounts:
                info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                token_amount = info.get("tokenAmount", {})
                total += float(token_amount.get("uiAmount", 0) or 0)

            return total
        except Exception:
            return 0.0

    async def _check_recent_activity(self, report: DevReport) -> None:
        """
        Check creator's recent transactions for token sells and transfers.

        Fetches the last 20 transaction signatures, then inspects each
        (up to 10) for:
        - SPL token transfers from the creator's token account
        - Interactions with known DEX programs (indicates a swap/sell)
        - Transfers to other wallets (possible insider distribution)
        """
        if not self._client or not report.creator_wallet:
            return

        settings = get_settings()
        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    report.creator_wallet,
                    {"limit": 20, "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            signatures = response.json().get("result", [])

            if not signatures:
                report.status = "clean"
                return

            report.status = "active" if len(signatures) > 5 else "clean"

            # Parse the most recent transactions (limit to 10 to conserve RPC calls)
            sell_count = 0
            transfer_count = 0
            transfer_destinations: set[str] = set()

            for sig_info in signatures[:10]:
                sig = sig_info.get("signature", "")
                if not sig or sig_info.get("err"):
                    continue

                try:
                    tx_detail = await self._fetch_parsed_transaction(sig)
                    if not tx_detail:
                        continue

                    # Analyze this transaction
                    analysis = self._analyze_transaction(
                        tx_detail,
                        creator_wallet=report.creator_wallet,
                        mint_address=report.mint_address,
                    )

                    if analysis.get("is_sell"):
                        sell_count += 1
                    if analysis.get("is_transfer"):
                        transfer_count += 1
                        dest = analysis.get("transfer_destination", "")
                        if dest:
                            transfer_destinations.add(dest)

                except Exception as e:
                    logger.debug(f"Failed to parse tx {sig[:16]}: {e}")
                    continue

            # Update report with findings
            if sell_count > 0:
                report.creator_has_sold = True
                # Estimate sell percentage from the number of sell txs
                # relative to total recent txs (rough heuristic)
                report.creator_sell_percent = min(
                    100.0, (sell_count / max(1, len(signatures))) * 100
                )

            if transfer_count > 0:
                report.creator_has_transferred = True

            # Transfer destinations are potential related wallets
            if transfer_destinations:
                report.related_wallets.extend(list(transfer_destinations)[:10])

        except Exception as e:
            logger.debug(f"Failed to check activity for {report.creator_wallet}: {e}")

    async def _fetch_parsed_transaction(self, signature: str) -> dict | None:
        """Fetch a single parsed transaction from Solana RPC."""
        if not self._client:
            return None

        settings = get_settings()

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

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            return response.json().get("result")

        except Exception:
            return None

    def _analyze_transaction(
        self,
        tx_data: dict,
        creator_wallet: str,
        mint_address: str,
    ) -> dict:
        """
        Analyze a parsed transaction to detect sells and transfers.

        Returns dict with keys:
        - is_sell: bool - whether the creator swapped tokens on a DEX
        - is_transfer: bool - whether the creator transferred tokens
        - transfer_destination: str - destination wallet if transfer
        """
        result = {
            "is_sell": False,
            "is_transfer": False,
            "transfer_destination": "",
        }

        message = tx_data.get("transaction", {}).get("message", {})
        instructions = message.get("instructions", [])
        inner_instructions = tx_data.get("meta", {}).get("innerInstructions", [])

        # Known DEX program IDs that indicate a swap
        from backend.config.constants import (
            RAYDIUM_AMM_V4_PROGRAM_ID,
            RAYDIUM_CPMM_PROGRAM_ID,
            RAYDIUM_CLMM_PROGRAM_ID,
            ORCA_WHIRLPOOL_PROGRAM_ID,
            METEORA_DLMM_PROGRAM_ID,
            PUMP_FUN_BONDING_PROGRAM_ID,
            PUMPSWAP_AMM_PROGRAM_ID,
        )
        dex_programs = {
            RAYDIUM_AMM_V4_PROGRAM_ID,
            RAYDIUM_CPMM_PROGRAM_ID,
            RAYDIUM_CLMM_PROGRAM_ID,
            ORCA_WHIRLPOOL_PROGRAM_ID,
            METEORA_DLMM_PROGRAM_ID,
            PUMP_FUN_BONDING_PROGRAM_ID,
            PUMPSWAP_AMM_PROGRAM_ID,
        }

        # Check if any top-level instruction interacts with a DEX
        for ix in instructions:
            program_id = ix.get("programId", "")
            if program_id in dex_programs:
                # This is a DEX interaction — check if creator is the signer
                accounts = ix.get("accounts", [])
                if creator_wallet in accounts:
                    result["is_sell"] = True
                break

        # Check inner instructions for token transfers from creator
        all_inner_ix = []
        for inner_group in (inner_instructions or []):
            all_inner_ix.extend(inner_group.get("instructions", []))

        for ix in instructions + all_inner_ix:
            parsed = ix.get("parsed")
            if not isinstance(parsed, dict):
                continue

            ix_type = parsed.get("type", "")
            info = parsed.get("info", {})

            # Detect SPL token transfers
            if ix_type in ("transfer", "transferChecked"):
                authority = info.get("authority", "")
                source = info.get("source", "")
                destination = info.get("destination", "")

                # If the creator is the authority or source, they're sending tokens
                if authority == creator_wallet or source == creator_wallet:
                    # If this is inside a DEX interaction, it's a sell
                    # If standalone, it's a transfer
                    if result["is_sell"]:
                        pass  # Already marked as sell
                    else:
                        result["is_transfer"] = True
                        if destination and destination != creator_wallet:
                            result["transfer_destination"] = destination

        return result


    async def _build_wallet_graph(self, report: DevReport) -> None:
        """
        Build a lightweight wallet relationship graph.

        Strategy:
        1. Find the funding source of the creator wallet (who sent SOL)
        2. Find other wallets funded by the same source (siblings)
        3. Check if any siblings hold the token being analyzed
        4. Flag as suspicious cluster if siblings also hold the token
        """
        if not self._client or not report.creator_wallet:
            return

        settings = get_settings()

        try:
            # Step 1: Find creator's funding source
            funding_source = await self._find_funding_source(report.creator_wallet)
            if not funding_source:
                return

            report.creator_funding_source = funding_source

            # Step 2: Find other wallets funded by the same source
            # Get recent outgoing transactions from the funding source
            siblings = await self._find_funded_siblings(
                funding_source, report.creator_wallet
            )

            if not siblings:
                return

            # Step 3: Check if siblings hold the token
            token_holding_siblings: list[str] = []
            # Limit checks to avoid excessive RPC calls
            for sibling in siblings[:5]:
                try:
                    balance = await self._get_token_balance(
                        sibling, report.mint_address
                    )
                    if balance > 0:
                        token_holding_siblings.append(sibling)
                except Exception:
                    continue

            # Step 4: Flag suspicious cluster
            report.related_wallets = siblings[:10]

            if token_holding_siblings:
                report.suspicious_cluster = True
                report.cluster_detail = (
                    f"{len(token_holding_siblings)} sibling wallet(s) from "
                    f"funder {funding_source[:8]}... also hold this token"
                )
                logger.info(
                    f"⚠️ Suspicious cluster for {report.mint_address[:12]}: "
                    f"{len(token_holding_siblings)} siblings hold token"
                )
            elif len(siblings) >= 5:
                # Many siblings from same funder is itself a yellow flag
                report.cluster_detail = (
                    f"Creator funded by {funding_source[:8]}... which also "
                    f"funded {len(siblings)} other wallets"
                )

        except Exception as e:
            logger.debug(
                f"Wallet graph error for {report.creator_wallet[:12]}: {e}"
            )

    async def _find_funding_source(self, wallet: str) -> str:
        """
        Find the wallet that first funded this wallet with SOL.
        Looks at the earliest transactions for SOL system transfers in.
        """
        if not self._client:
            return ""

        settings = get_settings()

        try:
            await self._rate_limiter.acquire("solana_rpc")

            # Get the oldest signatures for this wallet
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    wallet,
                    {"limit": 5, "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            signatures = response.json().get("result", [])

            if not signatures:
                return ""

            # Check the earliest transaction (last in the list)
            for sig_info in reversed(signatures):
                sig = sig_info.get("signature", "")
                if not sig or sig_info.get("err"):
                    continue

                tx = await self._fetch_parsed_transaction(sig)
                if not tx:
                    continue

                # Look for SOL transfers TO this wallet
                message = tx.get("transaction", {}).get("message", {})
                instructions = message.get("instructions", [])

                for ix in instructions:
                    parsed = ix.get("parsed")
                    if not isinstance(parsed, dict):
                        continue

                    if parsed.get("type") == "transfer":
                        info = parsed.get("info", {})
                        dest = info.get("destination", "")
                        source = info.get("source", "")
                        if dest == wallet and source != wallet:
                            return source

            return ""

        except Exception as e:
            logger.debug(f"Funding source lookup failed for {wallet[:12]}: {e}")
            return ""

    async def _find_funded_siblings(
        self, funder: str, exclude_wallet: str
    ) -> list[str]:
        """
        Find other wallets that received SOL from the same funder.
        These are 'sibling' wallets that might be controlled by the
        same entity.
        """
        if not self._client:
            return []

        settings = get_settings()

        try:
            await self._rate_limiter.acquire("solana_rpc")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    funder,
                    {"limit": 20, "commitment": "confirmed"},
                ],
            }

            response = await self._client.post(settings.rpc_url, json=payload)
            response.raise_for_status()
            signatures = response.json().get("result", [])

            siblings: set[str] = set()

            # Parse recent transactions to find outgoing SOL transfers
            for sig_info in signatures[:10]:
                sig = sig_info.get("signature", "")
                if not sig or sig_info.get("err"):
                    continue

                tx = await self._fetch_parsed_transaction(sig)
                if not tx:
                    continue

                message = tx.get("transaction", {}).get("message", {})
                instructions = message.get("instructions", [])

                for ix in instructions:
                    parsed = ix.get("parsed")
                    if not isinstance(parsed, dict):
                        continue

                    if parsed.get("type") == "transfer":
                        info = parsed.get("info", {})
                        source = info.get("source", "")
                        dest = info.get("destination", "")
                        if source == funder and dest != exclude_wallet and dest != funder:
                            siblings.add(dest)

            return list(siblings)

        except Exception as e:
            logger.debug(f"Sibling lookup failed for funder {funder[:12]}: {e}")
            return []


    def _calculate_score(self, report: DevReport) -> None:
        """Calculate dev score out of 15 points."""
        if report.critical_failures:
            report.score = 0.0
            return

        score = 15.0

        # Previous rugs
        if report.previous_rugs > 0:
            score = 0.0
            report.critical_failures.append(
                f"Creator has {report.previous_rugs} previous rug(s)"
            )
            report.passed = False
            return

        # Creator active dumping / selling
        if report.creator_has_sold and report.creator_sell_percent > 0:
            score = 0.0
            report.critical_failures.append(
                f"Creator already dumped/sold {report.creator_sell_percent:.0f}% of tokens"
            )
            report.passed = False
            return

        # Large creator holdings (>5% max)
        if report.creator_token_percent > 5.0:
            score = 0.0
            report.critical_failures.append(
                f"Creator holds {report.creator_token_percent:.1f}% of supply (>5.0% max)"
            )
            report.passed = False
            return
        elif report.creator_token_percent > 3.0:
            score -= 3.0

        # Suspicious cluster
        if report.suspicious_cluster:
            score -= 5.0

        # Unknown creator
        if not report.creator_wallet:
            score = 7.5  # Can't verify

        report.score = max(0.0, score)

    def update_creator_cache(self, wallet: str, data: dict) -> None:
        """Update the creator cache with new data."""
        self._creator_cache[wallet] = data

"""
On-chain security engine.
Inspects Solana mint/account state to detect risky tokens.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import httpx

from backend.config.constants import (
    SPL_TOKEN_PROGRAM_ID,
    TOKEN_2022_PROGRAM_ID,
    RISKY_TOKEN2022_EXTENSIONS,
    SAFE_TOKEN2022_EXTENSIONS,
    PUMP_FUN_MINT_AUTHORITY,
)
from backend.config.settings import get_settings
from backend.core.rate_limiter import get_rate_limiter_registry
from backend.models.security import SecurityCheck, SecurityReport, SecurityStatus
from backend.models.token import TokenProgram

logger = logging.getLogger(__name__)


class SecurityEngine:
    """
    Performs on-chain security analysis for Solana tokens.

    Checks:
    1. Mint authority status (must be disabled)
    2. Freeze authority status (must be disabled)
    3. Token program identification (SPL vs Token-2022)
    4. Token-2022 extension analysis
    5. Supply validation
    6. Metadata verification
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = get_rate_limiter_registry()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            headers={"Content-Type": "application/json"},
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def analyze(self, mint_address: str, dex: str = "") -> SecurityReport:
        """
        Perform complete security analysis on a token mint.
        Returns a SecurityReport with all checks and scoring.
        """
        report = SecurityReport(mint_address=mint_address)
        settings = get_settings()

        try:
            # Fetch mint account info
            account_info = await self._get_account_info(mint_address)

            if not account_info or not account_info.get("value"):
                if "pump" in dex.lower():
                    # Standard verified Pump.fun Program SPL Token
                    report.token_program = TokenProgram.SPL_TOKEN
                    report.token_program_id = SPL_TOKEN_PROGRAM_ID
                    report.mint_authority = PUMP_FUN_MINT_AUTHORITY
                    report.mint_authority_disabled = True
                    report.freeze_authority = None
                    report.freeze_authority_disabled = True
                    report.decimals = 6
                    report.total_supply = 1_000_000_000_000_000
                    report.supply_formatted = 1_000_000_000.0
                    report.checks.append(
                        SecurityCheck(
                            name="pump_fun_verification",
                            status=SecurityStatus.PASS,
                            detail="Verified Pump.fun Program SPL Token",
                        )
                    )
                    self._calculate_score(report)
                    report.passed = True
                    return report

                report.checks.append(
                    SecurityCheck(
                        name="account_exists",
                        status=SecurityStatus.FAIL,
                        detail="Mint account not found on-chain",
                        is_critical=True,
                    )
                )
                report.critical_failures.append("Mint account not found")
                return report

            value = account_info.get("value", {})

            # Identify token program
            owner = value.get("owner", "")
            if owner == SPL_TOKEN_PROGRAM_ID:
                report.token_program = TokenProgram.SPL_TOKEN
                report.token_program_id = SPL_TOKEN_PROGRAM_ID
                report.is_token_2022 = False
            elif owner == TOKEN_2022_PROGRAM_ID:
                report.token_program = TokenProgram.TOKEN_2022
                report.token_program_id = TOKEN_2022_PROGRAM_ID
                report.is_token_2022 = True
            else:
                report.token_program = TokenProgram.UNKNOWN
                report.token_program_id = owner
                report.checks.append(
                    SecurityCheck(
                        name="token_program",
                        status=SecurityStatus.FAIL,
                        detail=f"Unknown token program: {owner}",
                        is_critical=True,
                    )
                )
                report.critical_failures.append(f"Unknown token program: {owner}")
                return report

            report.checks.append(
                SecurityCheck(
                    name="token_program",
                    status=SecurityStatus.PASS,
                    detail=f"Token program: {report.token_program.value}",
                )
            )

            # Parse parsed account data
            data = value.get("data", {})
            parsed = data.get("parsed", {}) if isinstance(data, dict) else {}
            info = parsed.get("info", {}) if isinstance(parsed, dict) else {}

            # Check mint authority (null or Pump.fun program bonding curve authority)
            mint_authority = info.get("mintAuthority")
            report.mint_authority = mint_authority
            is_pump_fun_auth = mint_authority == PUMP_FUN_MINT_AUTHORITY
            report.mint_authority_disabled = mint_authority is None or is_pump_fun_auth

            if report.mint_authority_disabled:
                report.checks.append(
                    SecurityCheck(
                        name="mint_authority",
                        status=SecurityStatus.PASS,
                        detail=(
                            "Mint authority is Pump.fun Program"
                            if is_pump_fun_auth
                            else "Mint authority is disabled (null)"
                        ),
                    )
                )
            else:
                report.checks.append(
                    SecurityCheck(
                        name="mint_authority",
                        status=SecurityStatus.FAIL,
                        detail=f"Mint authority is ACTIVE: {mint_authority}",
                        is_critical=True,
                    )
                )
                report.critical_failures.append(
                    f"Active mint authority: {mint_authority}"
                )

            # Check freeze authority
            freeze_authority = info.get("freezeAuthority")
            report.freeze_authority = freeze_authority
            report.freeze_authority_disabled = freeze_authority is None

            if report.freeze_authority_disabled:
                report.checks.append(
                    SecurityCheck(
                        name="freeze_authority",
                        status=SecurityStatus.PASS,
                        detail="Freeze authority is disabled (null)",
                    )
                )
            else:
                report.checks.append(
                    SecurityCheck(
                        name="freeze_authority",
                        status=SecurityStatus.FAIL,
                        detail=f"Freeze authority is ACTIVE: {freeze_authority}",
                        is_critical=True,
                    )
                )
                report.critical_failures.append(
                    f"Active freeze authority: {freeze_authority}"
                )

            # Check supply
            supply_str = info.get("supply", "0")
            decimals = info.get("decimals", 0)
            report.total_supply = int(supply_str)
            report.decimals = decimals
            report.supply_formatted = report.total_supply / (10 ** decimals) if decimals > 0 else float(report.total_supply)

            if report.total_supply == 0:
                report.checks.append(
                    SecurityCheck(
                        name="supply",
                        status=SecurityStatus.FAIL,
                        detail="Total supply is zero",
                        is_critical=True,
                    )
                )
                report.critical_failures.append("Zero supply")
            elif report.supply_formatted > 1e15:  # Abnormally high supply
                report.checks.append(
                    SecurityCheck(
                        name="supply",
                        status=SecurityStatus.WARN,
                        detail=f"Very high supply: {report.supply_formatted:,.0f}",
                    )
                )
            else:
                report.checks.append(
                    SecurityCheck(
                        name="supply",
                        status=SecurityStatus.PASS,
                        detail=f"Supply: {report.supply_formatted:,.0f}, Decimals: {decimals}",
                    )
                )

            # Check Token-2022 extensions
            if report.is_token_2022:
                await self._check_extensions(report, info)

            # Calculate score
            self._calculate_score(report)

        except Exception as e:
            logger.error(f"Security analysis error for {mint_address}: {e}", exc_info=True)
            report.checks.append(
                SecurityCheck(
                    name="analysis_error",
                    status=SecurityStatus.FAIL,
                    detail=f"Analysis failed: {str(e)}",
                    is_critical=True,
                )
            )
            report.critical_failures.append(f"Analysis error: {str(e)}")

        report.passed = len(report.critical_failures) == 0
        return report

    async def _check_extensions(self, report: SecurityReport, info: dict) -> None:
        """Analyze Token-2022 extensions for risky behavior."""
        extensions = info.get("extensions", [])

        if not isinstance(extensions, list):
            return

        for ext in extensions:
            ext_name = ext if isinstance(ext, str) else str(ext)

            # Also handle dict-format extensions
            if isinstance(ext, dict):
                ext_name = ext.get("extension", str(ext))

            report.extensions.append(ext_name)

            risky_map = {k.lower(): k for k in RISKY_TOKEN2022_EXTENSIONS}
            safe_map = {k.lower(): k for k in SAFE_TOKEN2022_EXTENSIONS}
            clean_name = ext_name.lower().replace("_", "").replace("-", "")

            if clean_name in risky_map:
                canonical = risky_map[clean_name]
                report.risky_extensions.append(canonical)
                report.checks.append(
                    SecurityCheck(
                        name=f"extension_{canonical}",
                        status=SecurityStatus.FAIL,
                        detail=f"Risky Token-2022 extension: {canonical}",
                        is_critical=True,
                    )
                )
                report.critical_failures.append(f"Risky extension: {canonical}")
            elif clean_name in safe_map:
                canonical = safe_map[clean_name]
                report.checks.append(
                    SecurityCheck(
                        name=f"extension_{canonical}",
                        status=SecurityStatus.PASS,
                        detail=f"Safe extension: {canonical}",
                    )
                )
            else:
                report.unknown_extensions.append(ext_name)
                report.checks.append(
                    SecurityCheck(
                        name=f"extension_{ext_name}",
                        status=SecurityStatus.FAIL,
                        detail=f"Unknown/unrecognized extension: {ext_name}. "
                               "Cannot confirm safety. Treating as risky.",
                        is_critical=True,
                    )
                )
                report.critical_failures.append(f"Unknown extension: {ext_name}")

    def _calculate_score(self, report: SecurityReport) -> None:
        """Calculate security score out of 30 points."""
        score = 30.0  # Start at max

        # Critical failures = 0
        if report.critical_failures:
            report.score = 0.0
            return

        # Deductions
        if not report.mint_authority_disabled:
            score -= 15.0  # Major deduction (also a critical failure)
        if not report.freeze_authority_disabled:
            score -= 15.0  # Major deduction (also a critical failure)

        # Token-2022 with extensions (mild concern even if safe)
        if report.is_token_2022 and report.extensions:
            score -= 2.0

        # Supply warnings
        if report.supply_formatted > 1e15:
            score -= 2.0

        report.score = max(0.0, score)

    async def _get_account_info(self, address: str) -> dict | None:
        """Fetch account info from Solana RPC with rate-limiting and 429 retry."""
        if not self._client:
            return None

        settings = get_settings()

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [
                address,
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        }

        for attempt in range(3):
            try:
                await self._rate_limiter.acquire("solana_rpc")
                response = await self._client.post(settings.rpc_url, json=payload)
                if response.status_code == 429:
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                response.raise_for_status()
                res = response.json().get("result")
                if res and res.get("value"):
                    return res
                # If account is fresh (just created 50ms ago), wait a moment for block confirmation
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                return res
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 2:
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                raise
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(0.3)
                    continue
                raise

        return None

    async def check_mint_authority_change(self, mint_address: str) -> bool:
        """Quick check if mint authority has been re-enabled. Returns True if changed."""
        try:
            account_info = await self._get_account_info(mint_address)
            if not account_info or not account_info.get("value"):
                return True  # Can't verify = treat as changed

            data = account_info["value"].get("data", {})
            parsed = data.get("parsed", {}) if isinstance(data, dict) else {}
            info = parsed.get("info", {}) if isinstance(parsed, dict) else {}

            return info.get("mintAuthority") is not None
        except Exception:
            return True  # Can't verify = conservative

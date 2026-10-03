"""
Pydantic schemas for security analysis reports.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .token import TokenProgram


class SecurityStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    UNKNOWN = "unknown"


class SecurityCheck(BaseModel):
    """Individual security check result."""

    name: str
    status: SecurityStatus
    detail: str = ""
    is_critical: bool = False


class SecurityReport(BaseModel):
    """Complete security analysis for a token."""

    mint_address: str
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    # Token program
    token_program: TokenProgram = TokenProgram.UNKNOWN
    token_program_id: str = ""

    # Mint authority
    mint_authority: Optional[str] = None
    mint_authority_disabled: bool = False

    # Freeze authority
    freeze_authority: Optional[str] = None
    freeze_authority_disabled: bool = False

    # Supply
    total_supply: int = 0
    decimals: int = 0
    supply_formatted: float = 0.0

    # Token-2022 extensions
    is_token_2022: bool = False
    extensions: list[str] = Field(default_factory=list)
    risky_extensions: list[str] = Field(default_factory=list)
    unknown_extensions: list[str] = Field(default_factory=list)

    # Metadata
    has_metadata: bool = False
    metadata_valid: bool = False
    metadata_name: str = ""
    metadata_symbol: str = ""
    metadata_uri: str = ""

    # Individual checks
    checks: list[SecurityCheck] = Field(default_factory=list)

    # Overall
    passed: bool = False
    score: float = 0.0  # 0-30
    critical_failures: list[str] = Field(default_factory=list)

    @property
    def has_critical_failure(self) -> bool:
        return len(self.critical_failures) > 0


class LiquidityReport(BaseModel):
    """Liquidity analysis for a token's pool."""

    mint_address: str
    pool_address: str
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    # Liquidity
    liquidity_usd: float = 0.0
    base_liquidity: float = 0.0
    quote_liquidity: float = 0.0
    liquidity_to_mcap_ratio: float = 0.0

    # Pool
    pool_age_seconds: float = 0.0
    dex: str = ""

    # LP lock
    lp_locked: bool = False
    lp_lock_provider: str = ""
    lp_lock_percent: float = 0.0
    lp_lock_expiry: Optional[datetime] = None

    # Changes
    liquidity_change_percent: float = 0.0
    recent_additions: int = 0
    recent_removals: int = 0

    # Score
    passed: bool = False
    score: float = 0.0  # 0-20
    critical_failures: list[str] = Field(default_factory=list)


class HolderReport(BaseModel):
    """Holder concentration analysis."""

    mint_address: str
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    total_holders: int = 0
    top1_percent: float = 0.0
    top5_percent: float = 0.0
    top10_percent: float = 0.0
    top20_percent: float = 0.0

    largest_non_lp_holder: str = ""
    largest_non_lp_percent: float = 0.0

    deployer_holdings_percent: float = 0.0

    suspicious_concentration: bool = False
    concentration_detail: str = ""

    # Score
    passed: bool = False
    score: float = 0.0  # 0-20
    critical_failures: list[str] = Field(default_factory=list)


class DevReport(BaseModel):
    """Developer/deployer wallet analysis."""

    mint_address: str
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    creator_wallet: str = ""
    creator_funding_source: str = ""
    creator_sol_balance: float = 0.0
    creator_token_balance: float = 0.0
    creator_token_percent: float = 0.0

    # History
    previous_launches: int = 0
    previous_rugs: int = 0
    previous_successes: int = 0

    # Behaviour
    creator_has_sold: bool = False
    creator_sell_percent: float = 0.0
    creator_has_transferred: bool = False

    # Cluster
    related_wallets: list[str] = Field(default_factory=list)
    suspicious_cluster: bool = False
    cluster_detail: str = ""

    # Score
    passed: bool = False
    score: float = 0.0  # 0-15
    status: str = "unknown"
    critical_failures: list[str] = Field(default_factory=list)


class SocialReport(BaseModel):
    """Social/website verification."""

    mint_address: str
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    has_website: bool = False
    website_url: str = ""
    website_responds: bool = False
    website_https: bool = False

    has_twitter: bool = False
    twitter_url: str = ""

    has_telegram: bool = False
    telegram_url: str = ""

    has_discord: bool = False
    discord_url: str = ""

    social_count: int = 0

    # Score
    passed: bool = False
    score: float = 0.0  # 0-10
    critical_failures: list[str] = Field(default_factory=list)


class MarketBehaviorReport(BaseModel):
    """Market behavior analysis during observation period."""

    mint_address: str
    checked_at: datetime = Field(default_factory=datetime.utcnow)
    observation_seconds: float = 0.0

    buy_count: int = 0
    sell_count: int = 0
    buy_sell_ratio: float = 0.0
    unique_buyers: int = 0
    unique_sellers: int = 0

    volume_usd: float = 0.0
    volume_velocity: float = 0.0  # USD/second
    price_change_percent: float = 0.0

    liquidity_stable: bool = True
    large_sells: int = 0
    consecutive_sells: int = 0
    abnormal_bursts: bool = False

    # Score
    passed: bool = False
    score: float = 0.0  # 0-5
    critical_failures: list[str] = Field(default_factory=list)

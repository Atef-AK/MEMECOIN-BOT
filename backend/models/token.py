"""
Pydantic schemas for token data throughout the pipeline.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TokenProgram(str, Enum):
    SPL_TOKEN = "spl_token"
    TOKEN_2022 = "token_2022"
    UNKNOWN = "unknown"


class DexType(str, Enum):
    RAYDIUM = "raydium"
    ORCA = "orca"
    METEORA = "meteora"
    PUMP_FUN = "pump_fun"
    PUMPSWAP = "pumpswap"
    UNKNOWN = "unknown"


class TokenStatus(str, Enum):
    DISCOVERED = "discovered"
    ANALYZING = "analyzing"
    REJECTED = "rejected"
    WATCHING = "watching"
    CANDIDATE = "candidate"
    TRADING = "trading"
    COMPLETED = "completed"
    EMERGENCY_EXIT = "emergency_exit"
    ERROR = "error"


class RejectionReason(str, Enum):
    MINT_AUTHORITY_ACTIVE = "mint_authority_active"
    FREEZE_AUTHORITY_ACTIVE = "freeze_authority_active"
    RISKY_EXTENSION = "risky_extension"
    LOW_LIQUIDITY = "low_liquidity"
    HIGH_HOLDER_CONCENTRATION = "high_holder_concentration"
    SUSPICIOUS_DEV = "suspicious_dev"
    NO_SOCIAL = "no_social"
    LOW_SCORE = "low_score"
    POOR_QUOTE = "poor_quote"
    TOO_OLD = "too_old"
    TOO_YOUNG = "too_young"
    KILL_SWITCH = "kill_switch"
    RISK_LIMIT = "risk_limit"
    UNKNOWN_EVENT = "unknown_event"
    LP_NOT_LOCKED = "lp_not_locked"
    EXECUTION_FAILED = "execution_failed"
    INSUFFICIENT_LIQUIDITY = "insufficient_liquidity"
    BAD_MARKET_BEHAVIOR = "bad_market_behavior"


class DiscoveredToken(BaseModel):
    """Raw token data from discovery sources."""

    mint_address: str
    name: str = ""
    symbol: str = ""
    decimals: int = 0
    created_at: Optional[datetime] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)

    # Pool info
    pool_address: str = ""
    dex: DexType = DexType.UNKNOWN
    quote_token: str = ""

    # Market data
    initial_liquidity_usd: float = 0.0
    current_liquidity_usd: float = 0.0
    initial_market_cap: float = 0.0
    fdv: float = 0.0
    price_usd: float = 0.0
    price_sol: float = 0.0
    volume_24h: float = 0.0
    buys: int = 0
    sells: int = 0
    tx_count: int = 0

    # Metadata
    website: str = ""
    twitter: str = ""
    telegram: str = ""
    discord: str = ""
    description: str = ""
    image_url: str = ""

    # Creator & Launch bundle
    creator_wallet: str = ""
    initial_buy_sol: float = 0.0

    # Source tracking
    source: str = ""


class TokenCandidate(BaseModel):
    """Token that has passed initial discovery filters."""

    token: DiscoveredToken
    status: TokenStatus = TokenStatus.DISCOVERED
    rejection_reason: Optional[RejectionReason] = None
    rejection_detail: str = ""
    age_seconds: float = 0.0

    # Filled during analysis
    security_score: float = 0.0
    liquidity_score: float = 0.0
    holder_score: float = 0.0
    dev_score: float = 0.0
    social_score: float = 0.0
    market_score: float = 0.0
    total_score: float = 0.0

    # Critical failure flags
    has_critical_failure: bool = False
    critical_failures: list[str] = Field(default_factory=list)

    # Full analysis reports and built opportunity
    reports: dict = Field(default_factory=dict)
    opportunity: Optional[Any] = None


DiscoveredToken.model_rebuild()
TokenCandidate.model_rebuild()

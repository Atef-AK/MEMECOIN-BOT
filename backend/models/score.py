"""
Pydantic schemas for scoring.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .security import (
    DevReport,
    HolderReport,
    LiquidityReport,
    MarketBehaviorReport,
    SecurityReport,
    SocialReport,
)


class ScoreBreakdown(BaseModel):
    """Detailed score breakdown for a token."""

    mint_address: str
    scored_at: datetime = Field(default_factory=datetime.utcnow)

    # Component scores (out of their max)
    security_score: float = 0.0  # /30
    liquidity_score: float = 0.0  # /20
    holder_score: float = 0.0  # /20
    dev_score: float = 0.0  # /15
    social_score: float = 0.0  # /10
    market_score: float = 0.0  # /5

    total_score: float = 0.0  # /100

    # Classification
    classification: str = "REJECT"  # REJECT, WATCH, TRADE CANDIDATE, HIGH-CONFIDENCE

    # Critical failures override
    has_critical_failure: bool = False
    critical_failures: list[str] = Field(default_factory=list)

    # Reports (optional, for detailed view)
    security_report: Optional[SecurityReport] = None
    liquidity_report: Optional[LiquidityReport] = None
    holder_report: Optional[HolderReport] = None
    dev_report: Optional[DevReport] = None
    social_report: Optional[SocialReport] = None
    market_report: Optional[MarketBehaviorReport] = None

    @property
    def is_tradeable(self) -> bool:
        """Whether this token qualifies for trading."""
        return (
            not self.has_critical_failure
            and self.classification in ("TRADE CANDIDATE", "HIGH-CONFIDENCE")
        )

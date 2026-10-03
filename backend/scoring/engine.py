"""
Token scoring engine.
Combines all analysis reports into a final 100-point score.
Critical failures override the score regardless of total.
"""

from __future__ import annotations

import logging

from backend.config.constants import SCORE_WEIGHTS
from backend.config.settings import get_settings
from backend.models.score import ScoreBreakdown
from backend.models.security import (
    DevReport,
    HolderReport,
    LiquidityReport,
    MarketBehaviorReport,
    SecurityReport,
    SocialReport,
)

logger = logging.getLogger(__name__)


class ScoringEngine:
    """
    Combines all analysis reports into a final score with classification.

    Score weights:
    - Security: 30/100
    - Liquidity: 20/100
    - Holders: 20/100
    - Dev History: 15/100
    - Social: 10/100
    - Market Behavior: 5/100

    Classifications:
    - <70: REJECT
    - 70-79: WATCH
    - 80-89: TRADE CANDIDATE
    - 90-100: HIGH-CONFIDENCE CANDIDATE

    IMPORTANT: A score is NOT a probability of success.
    Critical failures automatically override to REJECT.
    """

    def score(
        self,
        mint_address: str,
        security: SecurityReport,
        liquidity: LiquidityReport,
        holders: HolderReport,
        dev: DevReport,
        social: SocialReport,
        market: MarketBehaviorReport | None = None,
    ) -> ScoreBreakdown:
        """Calculate the composite score for a token."""

        breakdown = ScoreBreakdown(
            mint_address=mint_address,
            security_score=security.score,
            liquidity_score=liquidity.score,
            holder_score=holders.score,
            dev_score=dev.score,
            social_score=social.score,
            market_score=market.score if market else 0.0,
            security_report=security,
            liquidity_report=liquidity,
            holder_report=holders,
            dev_report=dev,
            social_report=social,
            market_report=market,
        )

        # Collect all critical failures
        all_critical: list[str] = []
        all_critical.extend(security.critical_failures)
        all_critical.extend(liquidity.critical_failures)
        all_critical.extend(holders.critical_failures)
        all_critical.extend(dev.critical_failures)
        # Social never has critical failures
        if market:
            all_critical.extend(market.critical_failures)

        breakdown.critical_failures = all_critical
        breakdown.has_critical_failure = len(all_critical) > 0

        # Calculate total score
        breakdown.total_score = (
            breakdown.security_score
            + breakdown.liquidity_score
            + breakdown.holder_score
            + breakdown.dev_score
            + breakdown.social_score
            + breakdown.market_score
        )

        # Classification using configured settings
        settings = get_settings()
        trade_threshold = float(settings.min_score)
        watch_threshold = max(50.0, trade_threshold - 10.0)

        if breakdown.has_critical_failure:
            breakdown.classification = "REJECT"
        elif breakdown.total_score >= 90:
            breakdown.classification = "HIGH-CONFIDENCE"
        elif breakdown.total_score >= trade_threshold:
            breakdown.classification = "TRADE CANDIDATE"
        elif breakdown.total_score >= watch_threshold:
            breakdown.classification = "WATCH"
        else:
            breakdown.classification = "REJECT"

        logger.info(
            f"Token {mint_address} scored {breakdown.total_score:.1f}/100 "
            f"[{breakdown.classification}] "
            f"(sec={breakdown.security_score:.0f} liq={breakdown.liquidity_score:.0f} "
            f"hold={breakdown.holder_score:.0f} dev={breakdown.dev_score:.0f} "
            f"soc={breakdown.social_score:.0f} mkt={breakdown.market_score:.0f})"
        )

        if breakdown.has_critical_failure:
            logger.warning(
                f"Token {mint_address} has critical failures: {all_critical}"
            )

        return breakdown

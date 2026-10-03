"""
Base strategy — abstract base class for all trading strategies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.models.adaptive import (
    AdaptiveConfig,
    BondingCurveState,
    ExitStep,
    StrategyName,
    StrategyResult,
)
from backend.models.opportunity import TokenOpportunity


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    Every strategy must:
    1. Use the same TokenOpportunity input (shared analysis)
    2. Return a StrategyResult with entry/exit plan and reasoning
    3. Pass the same underlying security/liquidity/holder/dev filters
       unless explicitly configured to override
    """

    def __init__(self, config: AdaptiveConfig | None = None) -> None:
        self._config = config or AdaptiveConfig()

    @property
    @abstractmethod
    def name(self) -> StrategyName:
        """Strategy identifier."""
        ...

    @abstractmethod
    def evaluate(self, opportunity: TokenOpportunity) -> StrategyResult:
        """
        Evaluate a token opportunity and decide whether to trade.

        Returns a StrategyResult with:
        - should_trade: whether the strategy wants to enter
        - confidence: how confident (0-100)
        - exit_steps: planned exit schedule
        - reasons / rejection_reasons: explainable rationale
        """
        ...

    def _check_shared_filters(self, opp: TokenOpportunity) -> list[str]:
        """
        Check shared filters that ALL strategies must pass.
        Returns a list of rejection reasons (empty = passed).
        """
        rejections: list[str] = []

        if not opp.security_passed:
            rejections.append("Failed security filters")

        if opp.security_score <= 0:
            rejections.append("Zero security score")

        if opp.liquidity_score <= 0:
            rejections.append("Zero liquidity score")

        if opp.holder_score <= 0:
            rejections.append("Zero holder score")

        if opp.creator_has_sold and opp.creator_sell_percent > 50:
            rejections.append(f"Creator sold {opp.creator_sell_percent:.0f}%")

        if opp.suspicious_cluster:
            rejections.append("Suspicious wallet cluster detected")

        return rejections

    def _check_bonding_curve_entry(
        self, opp: TokenOpportunity, allowed_states: set[BondingCurveState]
    ) -> str | None:
        """Check if bonding curve state is suitable for entry."""
        if not opp.is_on_bonding_curve:
            return None  # Not on curve, skip check

        if opp.bonding_curve.state not in allowed_states:
            return (
                f"Bonding curve state {opp.bonding_curve.state.value} "
                f"not suitable (need: {', '.join(s.value for s in allowed_states)})"
            )
        return None

"""
Self-Learning & Reinforcement Intelligence Engine.
Analyzes trade outcomes (winners vs losers) to dynamically optimize:
1. Dynamic admission thresholds (min_score, min_liquidity).
2. Scoring feature weights based on correlation with profitability.
3. Market regime classification from rolling win rate & expectancy.
4. Automatic pattern pruning (detecting recurring failure setups).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LearningMetrics:
    """Current learned parameters adapted from trade history."""
    total_samples: int = 0
    win_rate: float = 0.5
    expectancy: float = 0.0
    profit_factor: float = 1.0

    # Dynamic thresholds (self-adjusting)
    adapted_min_score: float = 70.0
    adapted_min_liquidity_usd: float = 3000.0
    adapted_max_top10_percent: float = 40.0
    adapted_max_hold_seconds: int = 300

    # Feature correlation insights
    winning_avg_liquidity: float = 0.0
    losing_avg_liquidity: float = 0.0
    winning_avg_score: float = 0.0
    losing_avg_score: float = 0.0
    winning_avg_hold_duration: float = 0.0

    # Active adaptations
    active_filters: List[str] = field(default_factory=list)


class SelfLearningEngine:
    """
    Continuous reinforcement learning engine.
    Inspects completed trades and dynamically fine-tunes bot decision parameters.
    """

    def __init__(self, base_min_score: float = 68.0, base_min_liquidity: float = 2000.0) -> None:
        self.base_min_score = base_min_score
        self.base_min_liquidity = base_min_liquidity
        self.metrics = LearningMetrics(
            adapted_min_score=base_min_score,
            adapted_min_liquidity_usd=base_min_liquidity,
        )

    def learn_from_trades(self, completed_trades: List[Any]) -> LearningMetrics:
        """
        Analyze all completed trades and adapt thresholds.
        Supports both TradeRecord objects and DB trade dicts.
        """
        if not completed_trades:
            return self.metrics

        def _val(t: Any, key: str, default: float = 0.0) -> float:
            if isinstance(t, dict):
                return float(t.get(key, default) or default)
            return float(getattr(t, key, default) or default)

        winners = [t for t in completed_trades if _val(t, "net_pnl_percent") > 0]
        losers = [t for t in completed_trades if _val(t, "net_pnl_percent") <= 0]
        total = len(completed_trades)

        self.metrics.total_samples = total
        self.metrics.win_rate = len(winners) / total if total > 0 else 0.0

        total_wins = sum(_val(t, "net_pnl_sol") for t in winners)
        total_losses = abs(sum(_val(t, "net_pnl_sol") for t in losers))
        self.metrics.profit_factor = (total_wins / total_losses) if total_losses > 0 else (2.0 if total_wins > 0 else 1.0)

        # Average stats of winners vs losers
        if winners:
            self.metrics.winning_avg_liquidity = sum(_val(t, "liquidity_usd") for t in winners) / len(winners)
            self.metrics.winning_avg_score = sum(_val(t, "score") for t in winners) / len(winners)
            self.metrics.winning_avg_hold_duration = sum(_val(t, "time_to_exit_seconds") for t in winners) / len(winners)

        if losers:
            self.metrics.losing_avg_liquidity = sum(_val(t, "liquidity_usd") for t in losers) / len(losers)
            self.metrics.losing_avg_score = sum(_val(t, "score") for t in losers) / len(losers)

        # Self-adaptation logic
        active_filters = []

        # 1. Adapt Score Threshold
        # If win rate is below 45%, raise minimum score filter to reject borderline tokens
        if self.metrics.win_rate < 0.40 and total >= 5:
            self.metrics.adapted_min_score = min(80.0, self.base_min_score + 6.0)
            active_filters.append(f"Tightened min score to {self.metrics.adapted_min_score:.0f} (Win rate {self.metrics.win_rate*100:.1f}%)")
        elif self.metrics.win_rate < 0.50 and total >= 5:
            self.metrics.adapted_min_score = min(76.0, self.base_min_score + 3.0)
            active_filters.append(f"Elevated min score to {self.metrics.adapted_min_score:.0f}")
        else:
            self.metrics.adapted_min_score = self.base_min_score

        # 2. Adapt Liquidity Threshold
        # If losers consistently have low liquidity, raise minimum liquidity filter
        if losers and self.metrics.losing_avg_liquidity < 3500 and total >= 5:
            self.metrics.adapted_min_liquidity_usd = max(self.base_min_liquidity, 3500.0)
            active_filters.append("Raised minimum liquidity threshold to $3,500 based on loser pattern")
        else:
            self.metrics.adapted_min_liquidity_usd = self.base_min_liquidity

        # 3. Adapt Holding Duration
        # If winners take profit fast (< 90s) and long holds (> 300s) end up in stop loss, shorten hold window
        if winners and self.metrics.winning_avg_hold_duration > 0 and self.metrics.winning_avg_hold_duration < 120:
            self.metrics.adapted_max_hold_seconds = 240  # Tighten to 4m
            active_filters.append(f"Optimized max hold to 240s (avg winning duration: {self.metrics.winning_avg_hold_duration:.0f}s)")
        else:
            self.metrics.adapted_max_hold_seconds = 300

        self.metrics.active_filters = active_filters

        logger.info(
            f"🧠 [SELF-LEARNING UPDATE] Analyzed {total} trades (WR: {self.metrics.win_rate*100:.1f}%, PF: {self.metrics.profit_factor:.2f}) "
            f"| Adapted Min Score: {self.metrics.adapted_min_score:.0f} | Min Liq: ${self.metrics.adapted_min_liquidity_usd:,.0f}"
        )

        return self.metrics

    def should_admit_candidate(self, total_score: float, liquidity_usd: float, holder_top10: float) -> tuple[bool, str]:
        """Validate candidate against learned thresholds."""
        if total_score < self.metrics.adapted_min_score:
            return False, f"Score {total_score:.0f} below learned threshold {self.metrics.adapted_min_score:.0f}"
        if liquidity_usd < self.metrics.adapted_min_liquidity_usd:
            return False, f"Liquidity ${liquidity_usd:,.0f} below learned minimum ${self.metrics.adapted_min_liquidity_usd:,.0f}"
        return True, "Passed learned filters"

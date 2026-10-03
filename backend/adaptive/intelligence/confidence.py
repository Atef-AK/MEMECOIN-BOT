"""
Statistical confidence calculations.
Wilson interval for win rate, confidence penalties for small samples.
"""

from __future__ import annotations

import math


def wilson_lower_bound(
    wins: int,
    total: int,
    z: float = 1.96,  # 95% confidence
) -> float:
    """
    Wilson score confidence interval — lower bound.

    Returns the lower bound of the confidence interval for the win rate.
    This penalizes small sample sizes: a 100% win rate with 5 trades
    will return a much lower bound than 80% with 200 trades.

    Args:
        wins: number of successes
        total: total trials
        z: z-score for confidence level (1.96 = 95%, 1.64 = 90%)

    Returns:
        Lower bound of Wilson confidence interval (0 to 1).
    """
    if total == 0:
        return 0.0

    p = wins / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)

    return max(0.0, (centre - spread) / denominator)


def wilson_upper_bound(
    wins: int,
    total: int,
    z: float = 1.96,
) -> float:
    """Wilson score confidence interval — upper bound."""
    if total == 0:
        return 0.0

    p = wins / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)

    return min(1.0, (centre + spread) / denominator)


def sample_confidence_penalty(
    trades: int,
    min_trades: int = 50,
    confident_trades: int = 200,
) -> float:
    """
    Calculate a confidence penalty based on sample size.

    Returns a multiplier (0.0 to 1.0) that should be applied
    to fitness scores to penalize small samples.

    - < min_trades:      heavy penalty (0.3 - 0.7)
    - min to confident:  moderate penalty (0.7 - 0.95)
    - >= confident:      no penalty (1.0)
    """
    if trades <= 0:
        return 0.0

    if trades >= confident_trades:
        return 1.0

    if trades < min_trades:
        # Heavy exponential penalty for very small samples
        # At 1 trade: ~0.30, at 25 trades: ~0.55, at 49: ~0.69
        return 0.3 + 0.4 * (trades / min_trades)

    # Between min and confident: linear ramp
    # At 50: 0.70, at 125: 0.85, at 200: 1.0
    progress = (trades - min_trades) / (confident_trades - min_trades)
    return 0.70 + 0.30 * progress


def expectancy_confidence_interval(
    win_rate: float,
    avg_winner: float,
    avg_loser: float,
    total_trades: int,
    z: float = 1.96,
) -> tuple[float, float, float]:
    """
    Approximate confidence interval for expectancy.

    Returns (lower_bound, point_estimate, upper_bound).

    Uses Wilson lower/upper bounds for win rate combined with
    the average win/loss magnitudes.
    """
    if total_trades == 0:
        return 0.0, 0.0, 0.0

    wins = int(win_rate * total_trades)

    # Point estimate
    point = win_rate * avg_winner - (1 - win_rate) * avg_loser

    # Use Wilson bounds for win rate
    wr_lower = wilson_lower_bound(wins, total_trades, z)
    wr_upper = wilson_upper_bound(wins, total_trades, z)

    # Lower bound: lower win rate, same avg win/loss
    lower = wr_lower * avg_winner - (1 - wr_lower) * avg_loser

    # Upper bound: upper win rate, same avg win/loss
    upper = wr_upper * avg_winner - (1 - wr_upper) * avg_loser

    return lower, point, upper

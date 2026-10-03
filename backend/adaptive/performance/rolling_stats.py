"""
Rolling window statistics for strategy performance tracking.
Maintains separate windows (20/50/100/200 trades) and detects degradation.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass
class TradeResult:
    """Minimal trade result for rolling statistics."""
    net_pnl_percent: float
    is_win: bool
    time_in_trade: float = 0.0
    exit_type: str = ""
    execution_success: bool = True
    slippage_bps: float = 0.0


@dataclass
class WindowStats:
    """Statistics for a single rolling window."""
    window_size: int = 0
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    median_winner: float = 0.0
    median_loser: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    total_net_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_loss: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 0
    avg_holding_seconds: float = 0.0
    target_hit_rate: float = 0.0
    emergency_exit_rate: float = 0.0
    execution_failure_rate: float = 0.0
    avg_slippage_bps: float = 0.0


class RollingStats:
    """
    Maintains rolling window statistics across multiple window sizes.

    Prevents both recency bias (tiny window) and history bias
    (all-time average hiding recent degradation).
    """

    def __init__(self, windows: list[int] | None = None) -> None:
        self._windows = windows or [20, 50, 100, 200]
        self._all_trades: deque[TradeResult] = deque(maxlen=max(self._windows))
        self._trade_count = 0

    @property
    def total_trades(self) -> int:
        return self._trade_count

    def add_trade(self, result: TradeResult) -> None:
        """Record a new trade result."""
        self._all_trades.append(result)
        self._trade_count += 1

    def get_stats(self, window: int | None = None) -> WindowStats:
        """
        Get statistics for a specific window size.
        If window is None, returns stats for all available trades.
        """
        if window is not None:
            trades = list(self._all_trades)[-window:]
        else:
            trades = list(self._all_trades)

        return self._compute_stats(trades, window or len(trades))

    def get_all_windows(self) -> dict[int, WindowStats]:
        """Get statistics for all configured windows."""
        result: dict[int, WindowStats] = {}
        all_trades = list(self._all_trades)

        for w in self._windows:
            window_trades = all_trades[-w:]
            result[w] = self._compute_stats(window_trades, w)

        return result

    def detect_degradation(
        self,
        short_window: int = 20,
        long_window: int = 100,
        threshold_drop_percent: float = 50.0,
    ) -> tuple[bool, str]:
        """
        Detect if recent performance is significantly worse than
        longer-term performance.

        Returns (is_degraded, reason).
        """
        if len(self._all_trades) < long_window:
            return False, "Insufficient data"

        short_stats = self.get_stats(short_window)
        long_stats = self.get_stats(long_window)

        reasons: list[str] = []

        # Expectancy degradation
        if long_stats.expectancy > 0 and short_stats.expectancy <= 0:
            reasons.append(
                f"Expectancy turned negative: "
                f"{long_stats.expectancy:.1f}% → {short_stats.expectancy:.1f}%"
            )
        elif long_stats.expectancy > 0:
            drop = (1 - short_stats.expectancy / long_stats.expectancy) * 100
            if drop > threshold_drop_percent:
                reasons.append(
                    f"Expectancy dropped {drop:.0f}%: "
                    f"{long_stats.expectancy:.1f}% → {short_stats.expectancy:.1f}%"
                )

        # Win rate degradation
        if long_stats.win_rate > 0 and short_stats.win_rate < long_stats.win_rate * 0.6:
            reasons.append(
                f"Win rate dropped: "
                f"{long_stats.win_rate:.0%} → {short_stats.win_rate:.0%}"
            )

        # Consecutive loss streak
        if short_stats.max_consecutive_losses >= 5:
            reasons.append(
                f"Consecutive losses: {short_stats.max_consecutive_losses}"
            )

        is_degraded = len(reasons) > 0
        return is_degraded, "; ".join(reasons) if reasons else "Healthy"

    def _compute_stats(
        self, trades: list[TradeResult], window_size: int
    ) -> WindowStats:
        """Compute statistics from a list of trade results."""
        stats = WindowStats(window_size=window_size)

        if not trades:
            return stats

        stats.trades = len(trades)

        winners = [t.net_pnl_percent for t in trades if t.is_win]
        losers = [abs(t.net_pnl_percent) for t in trades if not t.is_win]

        stats.wins = len(winners)
        stats.losses = len(losers)
        stats.win_rate = stats.wins / stats.trades if stats.trades > 0 else 0

        stats.avg_winner = sum(winners) / len(winners) if winners else 0
        stats.avg_loser = sum(losers) / len(losers) if losers else 0

        # Median
        stats.median_winner = _median(winners)
        stats.median_loser = _median(losers)

        # Expectancy
        stats.expectancy = (
            stats.win_rate * stats.avg_winner
            - (1 - stats.win_rate) * stats.avg_loser
        )

        # Profit factor
        total_wins = sum(winners)
        total_losses = sum(losers)
        stats.profit_factor = (
            total_wins / total_losses if total_losses > 0
            else float("inf") if total_wins > 0
            else 0
        )

        # Total net PnL
        stats.total_net_pnl = sum(t.net_pnl_percent for t in trades)

        # Max drawdown (from equity curve)
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            equity += t.net_pnl_percent
            peak = max(peak, equity)
            dd = peak - equity
            max_dd = max(max_dd, dd)
        stats.max_drawdown = max_dd

        # Max single loss
        stats.max_loss = max(losers) if losers else 0

        # Consecutive losses
        current_streak = 0
        max_streak = 0
        for t in trades:
            if not t.is_win:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        stats.consecutive_losses = current_streak  # Current streak
        stats.max_consecutive_losses = max_streak

        # Holding time
        times = [t.time_in_trade for t in trades if t.time_in_trade > 0]
        stats.avg_holding_seconds = sum(times) / len(times) if times else 0

        # Exit type rates
        target_hits = sum(
            1 for t in trades if t.exit_type in ("target_hit", "partial_profit")
        )
        emergencies = sum(1 for t in trades if t.exit_type == "emergency")
        failures = sum(1 for t in trades if not t.execution_success)

        stats.target_hit_rate = target_hits / stats.trades if stats.trades > 0 else 0
        stats.emergency_exit_rate = emergencies / stats.trades if stats.trades > 0 else 0
        stats.execution_failure_rate = failures / stats.trades if stats.trades > 0 else 0

        # Average slippage
        slippages = [t.slippage_bps for t in trades if t.slippage_bps > 0]
        stats.avg_slippage_bps = sum(slippages) / len(slippages) if slippages else 0

        return stats


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]

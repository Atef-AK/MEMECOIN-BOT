"""
Loss protection and strategy-specific kill switches.
Global controls and per-strategy degradation detection.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from backend.adaptive.performance.database import StrategyPerformanceDB
from backend.adaptive.performance.rolling_stats import RollingStats
from backend.models.adaptive import AdaptiveConfig, StrategyName, StrategyStatus

logger = logging.getLogger(__name__)


class LossProtection:
    """
    Global and per-strategy loss protection.

    Global controls:
    - MAX_DAILY_LOSS_PERCENT
    - MAX_CONSECUTIVE_LOSSES
    - MAX_TRADES_PER_HOUR

    Per-strategy controls:
    - Consecutive loss limit
    - Rolling expectancy threshold
    - Execution degradation detection
    - Automatic disable + recovery
    """

    def __init__(
        self,
        config: AdaptiveConfig | None = None,
        performance_db: StrategyPerformanceDB | None = None,
    ) -> None:
        self._config = config or AdaptiveConfig()
        self._perf_db = performance_db or StrategyPerformanceDB()

        # Global state
        self._daily_pnl_percent: float = 0.0
        self._daily_trades: int = 0
        self._global_consecutive_losses: int = 0
        self._hourly_trade_timestamps: list[float] = []
        self._global_halted: bool = False
        self._halt_reason: str = ""

        # Per-strategy state
        self._strategy_disabled: dict[StrategyName, bool] = {}
        self._strategy_disable_reason: dict[StrategyName, str] = {}
        self._strategy_disable_time: dict[StrategyName, datetime] = {}
        self._strategy_consecutive_losses: dict[StrategyName, int] = defaultdict(int)

        # Configuration
        self.max_daily_loss_percent: float = 10.0
        self.max_consecutive_losses: int = 5
        self.max_trades_per_hour: int = 20

    @property
    def is_halted(self) -> bool:
        return self._global_halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def can_trade(self, strategy: StrategyName | None = None) -> tuple[bool, str]:
        """
        Check if trading is allowed.

        Returns (can_trade, reason).
        """
        # Global checks
        if self._global_halted:
            return False, f"GLOBAL HALT: {self._halt_reason}"

        # Daily loss limit
        if self._daily_pnl_percent < -self.max_daily_loss_percent:
            self._halt("Daily loss limit exceeded: "
                       f"{self._daily_pnl_percent:.1f}%")
            return False, self._halt_reason

        # Global consecutive losses
        if self._global_consecutive_losses >= self.max_consecutive_losses:
            self._halt(f"Global consecutive losses: "
                       f"{self._global_consecutive_losses}")
            return False, self._halt_reason

        # Hourly trade limit
        now = datetime.now(timezone.utc).timestamp()
        one_hour_ago = now - 3600
        self._hourly_trade_timestamps = [
            t for t in self._hourly_trade_timestamps if t > one_hour_ago
        ]
        if len(self._hourly_trade_timestamps) >= self.max_trades_per_hour:
            return False, (
                f"Hourly trade limit reached: "
                f"{len(self._hourly_trade_timestamps)}/{self.max_trades_per_hour}"
            )

        # Per-strategy check
        if strategy and self._strategy_disabled.get(strategy, False):
            reason = self._strategy_disable_reason.get(strategy, "Unknown")
            return False, f"Strategy {strategy.value} disabled: {reason}"

        return True, "OK"

    def record_trade_result(
        self,
        strategy: StrategyName,
        net_pnl_percent: float,
    ) -> None:
        """Record a trade result and check for halt/disable conditions."""
        now = datetime.now(timezone.utc)

        # Update global state
        self._daily_pnl_percent += net_pnl_percent
        self._daily_trades += 1
        self._hourly_trade_timestamps.append(now.timestamp())

        if net_pnl_percent < 0:
            self._global_consecutive_losses += 1
            self._strategy_consecutive_losses[strategy] += 1
        else:
            self._global_consecutive_losses = 0
            self._strategy_consecutive_losses[strategy] = 0

        # Check strategy-specific conditions
        self._check_strategy_health(strategy)

    def _check_strategy_health(self, strategy: StrategyName) -> None:
        """Check if a strategy should be disabled."""
        # Consecutive losses
        consec = self._strategy_consecutive_losses.get(strategy, 0)
        if consec >= self._config.max_strategy_consecutive_losses:
            self._disable_strategy(
                strategy,
                f"Consecutive losses: {consec}",
            )
            return

        # Rolling expectancy check
        stats = self._perf_db.get_overall_stats(strategy)
        if stats.total_trades >= 20:
            recent = stats.get_stats(self._config.degradation_window)
            if recent.expectancy < self._config.degradation_expectancy_threshold:
                self._disable_strategy(
                    strategy,
                    f"Rolling {self._config.degradation_window}-trade "
                    f"expectancy: {recent.expectancy:.1f}% "
                    f"(threshold: {self._config.degradation_expectancy_threshold}%)",
                )
                return

            # Execution degradation
            if recent.execution_failure_rate > 0.3:
                self._disable_strategy(
                    strategy,
                    f"Execution failure rate: {recent.execution_failure_rate:.0%}",
                )
                return

    def _disable_strategy(self, strategy: StrategyName, reason: str) -> None:
        """Disable a strategy temporarily."""
        if not self._strategy_disabled.get(strategy, False):
            logger.warning(
                f"🛑 Strategy DISABLED: {strategy.value} — {reason}"
            )
        self._strategy_disabled[strategy] = True
        self._strategy_disable_reason[strategy] = reason
        self._strategy_disable_time[strategy] = datetime.now(timezone.utc)

    def _halt(self, reason: str) -> None:
        """Halt all trading globally."""
        if not self._global_halted:
            logger.warning(f"🛑 GLOBAL TRADING HALT: {reason}")
        self._global_halted = True
        self._halt_reason = reason

    def re_enable_strategy(self, strategy: StrategyName) -> None:
        """Manually re-enable a disabled strategy."""
        if self._strategy_disabled.get(strategy, False):
            logger.info(f"✅ Strategy re-enabled: {strategy.value}")
            self._strategy_disabled[strategy] = False
            self._strategy_disable_reason.pop(strategy, None)
            self._strategy_consecutive_losses[strategy] = 0

    def reset_daily(self) -> None:
        """Reset daily counters (call at start of each trading day)."""
        self._daily_pnl_percent = 0.0
        self._daily_trades = 0
        self._global_consecutive_losses = 0
        self._global_halted = False
        self._halt_reason = ""
        logger.info("📊 Daily loss protection counters reset")

    def check_auto_recovery(self, cooldown_minutes: float = 30.0) -> None:
        """
        Check if any disabled strategies can be auto-recovered.
        Strategies disabled for more than cooldown_minutes can be re-enabled
        if their recent performance has improved.
        """
        now = datetime.now(timezone.utc)

        for strategy, disabled in list(self._strategy_disabled.items()):
            if not disabled:
                continue

            disable_time = self._strategy_disable_time.get(strategy)
            if not disable_time:
                continue

            elapsed_minutes = (now - disable_time).total_seconds() / 60

            if elapsed_minutes >= cooldown_minutes:
                # Check if recent performance has improved
                stats = self._perf_db.get_overall_stats(strategy)
                if stats.total_trades > 0:
                    recent = stats.get_stats(10)
                    if recent.expectancy > 0:
                        self.re_enable_strategy(strategy)
                        logger.info(
                            f"🔄 Auto-recovered {strategy.value} after "
                            f"{elapsed_minutes:.0f}m cooldown"
                        )

    def get_strategy_status(self, strategy: StrategyName) -> StrategyStatus:
        """Get the current status of a strategy including disable state."""
        if self._strategy_disabled.get(strategy, False):
            return StrategyStatus.DISABLED

        # Check for degradation
        stats = self._perf_db.get_overall_stats(strategy)
        if stats.total_trades >= 20:
            is_degraded, _ = stats.detect_degradation()
            if is_degraded:
                return StrategyStatus.DEGRADED

        return StrategyStatus.ESTABLISHED

    def to_dict(self) -> dict:
        return {
            "global_halted": self._global_halted,
            "halt_reason": self._halt_reason,
            "daily_pnl": f"{self._daily_pnl_percent:.1f}%",
            "daily_trades": self._daily_trades,
            "consecutive_losses": self._global_consecutive_losses,
            "hourly_trades": len(self._hourly_trade_timestamps),
            "disabled_strategies": {
                k.value: self._strategy_disable_reason.get(k, "")
                for k, v in self._strategy_disabled.items()
                if v
            },
        }

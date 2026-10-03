"""
Market regime classifier.
Determines the current market regime from the bot's own collected data
and recent token statistics.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone

from backend.models.adaptive import MarketRegime

logger = logging.getLogger(__name__)


class MarketRegimeEngine:
    """
    Classifies the current memecoin market regime using the bot's own
    observations rather than external indicators.

    Regimes:
    - QUIET:              Few new tokens, low volume across the board
    - NORMAL:             Typical discovery rate and volume
    - HIGH_MOMENTUM:      Many tokens launching with strong buying
    - HIGH_VOLATILITY:    Large price swings, mixed signals
    - RISK_OFF:           High failure/rug rate, sell-dominated
    - EXTREME_SPECULATION: Parabolic launches, very high volume

    Uses rolling windows of recent token observations to classify.
    """

    def __init__(self, window_size: int = 100) -> None:
        self._window_size = window_size
        self._observations: deque[_MarketObservation] = deque(maxlen=window_size)
        self._current_regime = MarketRegime.UNKNOWN
        self._regime_since: datetime = datetime.now(timezone.utc)
        self._regime_confidence: float = 0.0

        # Baselines (calibrated during first ~50 observations)
        self._baseline_discovery_rate: float = 0.0  # tokens per minute
        self._baseline_avg_volume: float = 0.0
        self._baseline_pass_rate: float = 0.5
        self._calibrated = False
        self._calibration_start: float = time.monotonic()

    @property
    def current_regime(self) -> MarketRegime:
        return self._current_regime

    @property
    def regime_confidence(self) -> float:
        return self._regime_confidence

    @property
    def regime_since(self) -> datetime:
        return self._regime_since

    def record_observation(
        self,
        *,
        passed_filters: bool,
        was_rug: bool = False,
        volume_5m_usd: float = 0.0,
        buy_sell_ratio: float = 0.5,
        price_change_5m: float = 0.0,
        liquidity_usd: float = 0.0,
        buyer_acceleration: float = 1.0,
    ) -> None:
        """Record a single token observation for regime classification."""
        obs = _MarketObservation(
            timestamp=time.monotonic(),
            passed_filters=passed_filters,
            was_rug=was_rug,
            volume_5m_usd=volume_5m_usd,
            buy_sell_ratio=buy_sell_ratio,
            price_change_5m=price_change_5m,
            liquidity_usd=liquidity_usd,
            buyer_acceleration=buyer_acceleration,
        )
        self._observations.append(obs)

        # Update regime every 10 observations
        if len(self._observations) >= 10 and len(self._observations) % 5 == 0:
            self._update_regime()

    def _update_regime(self) -> None:
        """Reclassify the market regime from recent observations."""
        obs = list(self._observations)
        n = len(obs)
        if n < 10:
            return

        # Calculate metrics from recent observations
        recent = obs[-min(30, n):]  # last 30 or all

        # Discovery rate (tokens per minute)
        if len(obs) >= 2:
            time_span = obs[-1].timestamp - obs[0].timestamp
            discovery_rate = (n / time_span * 60) if time_span > 0 else 0
        else:
            discovery_rate = 0

        avg_volume = sum(o.volume_5m_usd for o in recent) / len(recent)
        avg_buy_sell = sum(o.buy_sell_ratio for o in recent) / len(recent)
        avg_price_change = sum(o.price_change_5m for o in recent) / len(recent)
        avg_acceleration = sum(o.buyer_acceleration for o in recent) / len(recent)
        pass_rate = sum(1 for o in recent if o.passed_filters) / len(recent)
        rug_rate = sum(1 for o in recent if o.was_rug) / len(recent)
        vol_variance = self._variance([o.price_change_5m for o in recent])

        # Calibrate baselines on first ~50 observations
        if not self._calibrated and n >= 50:
            self._baseline_discovery_rate = discovery_rate
            self._baseline_avg_volume = avg_volume
            self._baseline_pass_rate = pass_rate
            self._calibrated = True

        # Use baselines if calibrated, otherwise use absolute thresholds
        base_disc = self._baseline_discovery_rate if self._calibrated else 5.0
        base_vol = self._baseline_avg_volume if self._calibrated else 10000.0

        # Classify regime
        new_regime, confidence = self._classify(
            discovery_rate=discovery_rate,
            base_discovery_rate=base_disc,
            avg_volume=avg_volume,
            base_volume=base_vol,
            avg_buy_sell=avg_buy_sell,
            avg_price_change=avg_price_change,
            avg_acceleration=avg_acceleration,
            pass_rate=pass_rate,
            rug_rate=rug_rate,
            vol_variance=vol_variance,
        )

        if new_regime != self._current_regime:
            logger.info(
                f"📊 Market regime: {self._current_regime.value} → {new_regime.value} "
                f"(confidence: {confidence:.0f}%)"
            )
            self._current_regime = new_regime
            self._regime_since = datetime.now(timezone.utc)
        self._regime_confidence = confidence

    def _classify(
        self,
        discovery_rate: float,
        base_discovery_rate: float,
        avg_volume: float,
        base_volume: float,
        avg_buy_sell: float,
        avg_price_change: float,
        avg_acceleration: float,
        pass_rate: float,
        rug_rate: float,
        vol_variance: float,
    ) -> tuple[MarketRegime, float]:
        """Score each regime and return the best fit."""
        scores: dict[MarketRegime, float] = {}

        disc_ratio = discovery_rate / base_discovery_rate if base_discovery_rate > 0 else 1.0
        vol_ratio = avg_volume / base_volume if base_volume > 0 else 1.0

        # QUIET: low discovery, low volume
        quiet_score = 0.0
        if disc_ratio < 0.5:
            quiet_score += 40
        if vol_ratio < 0.5:
            quiet_score += 30
        if avg_acceleration < 0.8:
            quiet_score += 30
        scores[MarketRegime.QUIET] = quiet_score

        # NORMAL: close to baseline
        normal_score = 0.0
        if 0.5 <= disc_ratio <= 2.0:
            normal_score += 30
        if 0.5 <= vol_ratio <= 2.0:
            normal_score += 25
        if 0.4 <= avg_buy_sell <= 0.65:
            normal_score += 25
        if vol_variance < 500:
            normal_score += 20
        scores[MarketRegime.NORMAL] = normal_score

        # HIGH_MOMENTUM: high acceleration, buying dominant
        momentum_score = 0.0
        if avg_acceleration > 1.5:
            momentum_score += 35
        if avg_buy_sell > 0.6:
            momentum_score += 25
        if avg_price_change > 5:
            momentum_score += 20
        if disc_ratio > 1.5:
            momentum_score += 20
        scores[MarketRegime.HIGH_MOMENTUM] = momentum_score

        # HIGH_VOLATILITY: high variance, mixed signals
        volatility_score = 0.0
        if vol_variance > 300:
            volatility_score += 40
        if abs(avg_price_change) > 10:
            volatility_score += 20
        if 0.35 <= avg_buy_sell <= 0.65:
            volatility_score += 20
        if vol_ratio > 1.5:
            volatility_score += 20
        scores[MarketRegime.HIGH_VOLATILITY] = volatility_score

        # RISK_OFF: high rug rate, sell-dominated
        risk_off_score = 0.0
        if rug_rate > 0.3:
            risk_off_score += 35
        if avg_buy_sell < 0.4:
            risk_off_score += 25
        if pass_rate < 0.3:
            risk_off_score += 25
        if avg_price_change < -5:
            risk_off_score += 15
        scores[MarketRegime.RISK_OFF] = risk_off_score

        # EXTREME_SPECULATION: very high volume, high discovery, parabolic moves
        speculation_score = 0.0
        if disc_ratio > 3.0:
            speculation_score += 30
        if vol_ratio > 3.0:
            speculation_score += 25
        if avg_acceleration > 2.5:
            speculation_score += 25
        if avg_price_change > 20:
            speculation_score += 20
        scores[MarketRegime.EXTREME_SPECULATION] = speculation_score

        # Select highest scoring regime
        best = max(scores, key=scores.get)  # type: ignore
        best_score = scores[best]

        # Confidence = how much better the winner is vs. the runner-up
        sorted_scores = sorted(scores.values(), reverse=True)
        if len(sorted_scores) >= 2 and sorted_scores[0] > 0:
            margin = sorted_scores[0] - sorted_scores[1]
            confidence = min(100.0, (margin / sorted_scores[0]) * 100 + 30)
        else:
            confidence = 50.0

        return best, confidence

    @staticmethod
    def _variance(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    def to_dict(self) -> dict:
        return {
            "regime": self._current_regime.value,
            "confidence": self._regime_confidence,
            "since": self._regime_since.isoformat(),
            "observations": len(self._observations),
            "calibrated": self._calibrated,
        }


class _MarketObservation:
    """Internal struct for market regime observations."""
    __slots__ = (
        "timestamp",
        "passed_filters",
        "was_rug",
        "volume_5m_usd",
        "buy_sell_ratio",
        "price_change_5m",
        "liquidity_usd",
        "buyer_acceleration",
    )

    def __init__(
        self,
        timestamp: float,
        passed_filters: bool,
        was_rug: bool,
        volume_5m_usd: float,
        buy_sell_ratio: float,
        price_change_5m: float,
        liquidity_usd: float,
        buyer_acceleration: float,
    ):
        self.timestamp = timestamp
        self.passed_filters = passed_filters
        self.was_rug = was_rug
        self.volume_5m_usd = volume_5m_usd
        self.buy_sell_ratio = buy_sell_ratio
        self.price_change_5m = price_change_5m
        self.liquidity_usd = liquidity_usd
        self.buyer_acceleration = buyer_acceleration


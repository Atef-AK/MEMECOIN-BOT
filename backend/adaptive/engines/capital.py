"""
Capital-aware position sizing engine.
Determines capital stage, calculates position sizes, and estimates
execution costs to reject strategies where costs are unacceptable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.config.settings import get_settings
from backend.models.adaptive import (
    AdaptiveConfig,
    CapitalStage,
    StrategyName,
    get_capital_stage,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# POSITION SIZING RULES PER CAPITAL STAGE
# ═══════════════════════════════════════════════════════════════

# (max_position_percent, max_position_sol, max_slippage_bps, max_impact_bps)
STAGE_RULES: dict[CapitalStage, dict] = {
    CapitalStage.STAGE_1: {
        "max_position_percent": 100.0,  # All-in is OK for micro accounts
        "max_position_sol": 0.20,
        "max_slippage_bps": 300,
        "max_impact_bps": 200,
        "min_liquidity_usd": 5_000,
    },
    CapitalStage.STAGE_2: {
        "max_position_percent": 50.0,
        "max_position_sol": 0.25,
        "max_slippage_bps": 250,
        "max_impact_bps": 150,
        "min_liquidity_usd": 10_000,
    },
    CapitalStage.STAGE_3: {
        "max_position_percent": 30.0,
        "max_position_sol": 0.30,
        "max_slippage_bps": 200,
        "max_impact_bps": 100,
        "min_liquidity_usd": 20_000,
    },
    CapitalStage.STAGE_4: {
        "max_position_percent": 20.0,
        "max_position_sol": 1.00,
        "max_slippage_bps": 150,
        "max_impact_bps": 75,
        "min_liquidity_usd": 30_000,
    },
    CapitalStage.STAGE_5: {
        "max_position_percent": 10.0,
        "max_position_sol": 2.50,
        "max_slippage_bps": 100,
        "max_impact_bps": 50,
        "min_liquidity_usd": 50_000,
    },
}


@dataclass
class PositionSizeResult:
    """Result of position sizing calculation."""
    position_size_sol: float
    capital_stage: CapitalStage
    available_capital_sol: float
    reserve_sol: float

    # Execution cost estimates
    estimated_slippage_bps: float = 0.0
    estimated_impact_bps: float = 0.0
    estimated_fees_sol: float = 0.0
    estimated_total_cost_bps: float = 0.0

    # Liquidity assessment
    liquidity_sufficient: bool = True
    liquidity_capacity_ratio: float = 0.0  # position / liquidity

    # Viability
    is_viable: bool = True
    rejection_reasons: list[str] | None = None

    def __post_init__(self):
        if self.rejection_reasons is None:
            self.rejection_reasons = []


class CapitalEngine:
    """
    Capital-aware position sizing engine.

    Key principles:
    - Never increase position size solely because balance doubled
    - Position size can only increase at new capital stage boundaries
    - Strategy must be statistically healthy at the new size
    - Execution costs must be acceptable
    - Always maintain a configurable reserve
    """

    def __init__(self, config: AdaptiveConfig | None = None) -> None:
        self._config = config or AdaptiveConfig()
        self._account_balance_sol: float = 0.0
        self._strategy_health: dict[StrategyName, bool] = {}

    def update_balance(self, balance_sol: float) -> None:
        """Update the current account balance."""
        self._account_balance_sol = balance_sol

    def update_strategy_health(
        self, strategy: StrategyName, is_healthy: bool
    ) -> None:
        """Update whether a strategy is currently healthy."""
        self._strategy_health[strategy] = is_healthy

    @property
    def current_stage(self) -> CapitalStage:
        return get_capital_stage(self._account_balance_sol)

    @property
    def balance(self) -> float:
        return self._account_balance_sol

    def calculate_position(
        self,
        strategy: StrategyName,
        liquidity_usd: float,
        sol_price_usd: float = 150.0,
    ) -> PositionSizeResult:
        """
        Calculate the optimal position size for a strategy given
        current capital and token liquidity.

        Returns a PositionSizeResult with viability assessment.
        """
        stage = self.current_stage
        rules = STAGE_RULES[stage]
        reserve_percent = self._config.capital_reserve_percent

        # Available capital after reserve
        reserve_sol = self._account_balance_sol * (reserve_percent / 100)
        available = max(0, self._account_balance_sol - reserve_sol)

        result = PositionSizeResult(
            position_size_sol=0.0,
            capital_stage=stage,
            available_capital_sol=available,
            reserve_sol=reserve_sol,
        )

        if available <= 0:
            result.is_viable = False
            result.rejection_reasons.append("No capital available after reserve")
            return result

        # Calculate position size
        max_by_percent = available * (rules["max_position_percent"] / 100)
        max_by_sol = rules["max_position_sol"]
        position_sol = min(max_by_percent, max_by_sol, available)

        # Estimate execution costs
        position_usd = position_sol * sol_price_usd
        liquidity_ratio = position_usd / liquidity_usd if liquidity_usd > 0 else 1.0

        # Slippage estimate: roughly proportional to position/liquidity ratio
        # Small positions in deep liquidity = low slippage
        estimated_slippage_bps = min(500, liquidity_ratio * 1000)
        estimated_impact_bps = min(500, liquidity_ratio * 500)

        # Solana base fees
        base_fee_sol = 0.000005
        priority_fee_sol = 0.0001
        estimated_fees = base_fee_sol + priority_fee_sol

        total_cost_bps = estimated_slippage_bps + estimated_impact_bps

        result.position_size_sol = position_sol
        result.estimated_slippage_bps = estimated_slippage_bps
        result.estimated_impact_bps = estimated_impact_bps
        result.estimated_fees_sol = estimated_fees
        result.estimated_total_cost_bps = total_cost_bps
        result.liquidity_capacity_ratio = liquidity_ratio

        # Viability checks
        rejections: list[str] = []

        # Liquidity minimum
        if liquidity_usd < rules["min_liquidity_usd"]:
            rejections.append(
                f"Liquidity ${liquidity_usd:,.0f} below "
                f"stage {stage.value} minimum ${rules['min_liquidity_usd']:,.0f}"
            )

        # Slippage too high
        if estimated_slippage_bps > rules["max_slippage_bps"]:
            rejections.append(
                f"Estimated slippage {estimated_slippage_bps:.0f}bps "
                f"exceeds {rules['max_slippage_bps']}bps limit"
            )

        # Impact too high
        if estimated_impact_bps > rules["max_impact_bps"]:
            rejections.append(
                f"Estimated impact {estimated_impact_bps:.0f}bps "
                f"exceeds {rules['max_impact_bps']}bps limit"
            )

        # Strategy health
        if not self._strategy_health.get(strategy, True):
            rejections.append(f"Strategy {strategy.value} is not healthy")

        # Position too small to be meaningful after fees
        net_after_fees = position_sol - estimated_fees
        if net_after_fees < 0.005:  # Less than 0.005 SOL after fees
            rejections.append("Position too small after fees")

        result.is_viable = len(rejections) == 0
        result.rejection_reasons = rejections
        result.liquidity_sufficient = liquidity_usd >= rules["min_liquidity_usd"]

        return result

    def can_increase_position(
        self,
        strategy: StrategyName,
        strategy_trades: int,
        strategy_expectancy: float,
        drawdown_percent: float,
        max_drawdown_limit: float = 20.0,
    ) -> bool:
        """
        Check if position size can safely increase.

        Requires:
        1. Strategy is statistically healthy
        2. Sufficient sample at current size
        3. Positive expectancy
        4. Controlled drawdown
        """
        if strategy_trades < 50:
            return False
        if strategy_expectancy <= 0:
            return False
        if drawdown_percent > max_drawdown_limit:
            return False
        if not self._strategy_health.get(strategy, False):
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "balance_sol": self._account_balance_sol,
            "stage": self.current_stage.value,
            "strategy_health": {
                k.value: v for k, v in self._strategy_health.items()
            },
        }


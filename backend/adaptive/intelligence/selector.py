"""
Adaptive strategy selector.
Before every potential entry, determines the best strategy using
capital stage, market regime, token characteristics, and historical
performance with confidence-adjusted fitness.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timezone

from backend.adaptive.engines.capital import CapitalEngine, PositionSizeResult
from backend.adaptive.engines.market_regime import MarketRegimeEngine
from backend.adaptive.intelligence.confidence import sample_confidence_penalty
from backend.adaptive.intelligence.fitness import FitnessResult, StrategyFitnessEngine
from backend.adaptive.performance.database import StrategyPerformanceDB
from backend.adaptive.strategies.base import BaseStrategy
from backend.adaptive.strategies.fast_scalper import FastScalperStrategy
from backend.adaptive.strategies.momentum_runner import MomentumRunnerStrategy
from backend.adaptive.strategies.smart_wallet_follower import SmartWalletFollowerStrategy
from backend.models.adaptive import (
    AdaptiveConfig,
    CapitalStage,
    MarketRegime,
    SelectionDecision,
    StrategyName,
    StrategyResult,
    StrategyStatus,
)
from backend.models.opportunity import TokenOpportunity

logger = logging.getLogger(__name__)


class AdaptiveStrategySelector:
    """
    Adaptive strategy selector using confidence-adjusted fitness.

    Before every potential entry:
    1. Determine current capital stage
    2. Determine current market regime
    3. Evaluate all strategies against the token
    4. Check historical performance for current conditions
    5. Calculate confidence-adjusted fitness
    6. Apply exploration vs. exploitation
    7. Select strategy only if confidence threshold is met

    Uses Upper Confidence Bound (UCB) for exploration.
    """

    def __init__(
        self,
        config: AdaptiveConfig | None = None,
        performance_db: StrategyPerformanceDB | None = None,
        capital_engine: CapitalEngine | None = None,
        regime_engine: MarketRegimeEngine | None = None,
    ) -> None:
        self._config = config or AdaptiveConfig()
        self._perf_db = performance_db or StrategyPerformanceDB()
        self._capital = capital_engine or CapitalEngine(self._config)
        self._regime = regime_engine or MarketRegimeEngine()
        self._fitness_engine = StrategyFitnessEngine(self._config)

        # Strategies
        self._strategies: dict[StrategyName, BaseStrategy] = {
            StrategyName.FAST_SCALPER: FastScalperStrategy(self._config),
            StrategyName.MOMENTUM_RUNNER: MomentumRunnerStrategy(self._config),
            StrategyName.SMART_WALLET_FOLLOWER: SmartWalletFollowerStrategy(self._config),
        }

        # Current preferred strategy (can change over time)
        self._preferred_strategy: StrategyName | None = None
        self._preferred_since: datetime = datetime.now(timezone.utc)
        self._total_selections: int = 0
        self._exploration_count: int = 0

    @property
    def preferred_strategy(self) -> StrategyName | None:
        return self._preferred_strategy

    @property
    def exploration_rate(self) -> float:
        """Current exploration rate (decays with total trades)."""
        base = self._config.exploration_rate
        min_rate = self._config.min_exploration_rate
        total = sum(
            self._perf_db.get_trade_count(s)
            for s in self._strategies
        )
        decay = max(0, 1 - total / self._config.exploration_decay_trades)
        return max(min_rate, base * decay)

    def select(
        self,
        opportunity: TokenOpportunity,
        sol_price_usd: float = 150.0,
    ) -> SelectionDecision:
        """
        Select the best strategy for a token opportunity.

        Returns a SelectionDecision with:
        - selected_strategy (may be NO_TRADE)
        - strategy_score
        - confidence
        - reasons (explainable)
        """
        self._total_selections += 1
        regime = self._regime.current_regime
        stage = self._capital.current_stage

        decision = SelectionDecision(
            selected_strategy=StrategyName.NO_TRADE,
            capital_stage=stage,
            market_regime=regime,
        )

        # Step 1: Evaluate all strategies against this token
        strategy_results: dict[StrategyName, StrategyResult] = {}
        for name, strategy in self._strategies.items():
            result = strategy.evaluate(opportunity)
            strategy_results[name] = result

        decision.strategy_results = {
            k.value: v for k, v in strategy_results.items()
        }

        # Step 2: Filter to strategies that want to trade
        willing = {
            name: result
            for name, result in strategy_results.items()
            if result.should_trade
        }

        if not willing:
            decision.reasons = [
                "No strategy wants to trade this token",
                *[
                    f"{n.value}: {', '.join(r.rejection_reasons)}"
                    for n, r in strategy_results.items()
                    if r.rejection_reasons
                ],
            ]
            return decision

        # Step 3: Check capital viability for each willing strategy
        viable: dict[StrategyName, tuple[StrategyResult, PositionSizeResult]] = {}
        for name, result in willing.items():
            pos = self._capital.calculate_position(
                strategy=name,
                liquidity_usd=opportunity.liquidity_usd,
                sol_price_usd=sol_price_usd,
            )
            if pos.is_viable:
                viable[name] = (result, pos)
            else:
                decision.reasons.append(
                    f"{name.value}: capital rejected — "
                    + "; ".join(pos.rejection_reasons or [])
                )

        if not viable:
            decision.reasons.insert(0, "No strategy viable at current capital")
            return decision

        # Step 4: Calculate fitness for each viable strategy
        fitness_scores: dict[StrategyName, FitnessResult] = {}
        for name in viable:
            # Get regime-specific stats if available
            regime_stats = self._perf_db.get_regime_stats(name, regime)
            overall_stats = self._perf_db.get_overall_stats(name)

            # Use regime stats if we have enough, otherwise overall
            if regime_stats.total_trades >= 20:
                fitness = self._fitness_engine.calculate(name, regime_stats)
            else:
                fitness = self._fitness_engine.calculate(name, overall_stats)

            fitness_scores[name] = fitness

        decision.fitness_scores = {
            k.value: v.fitness_score for k, v in fitness_scores.items()
        }
        decision.confidence_adjusted_scores = {
            k.value: v.confidence_adjusted_fitness
            for k, v in fitness_scores.items()
        }

        # Step 5: Exploration vs. exploitation
        is_exploration = random.random() < self.exploration_rate
        selected: StrategyName | None = None

        if is_exploration:
            # UCB exploration: select strategy with highest upper confidence bound
            selected = self._ucb_select(fitness_scores, viable)
            decision.is_exploration = True
            decision.exploration_method = "UCB"
            self._exploration_count += 1
        else:
            # Exploitation: select strategy with highest confidence-adjusted fitness
            selected = max(
                fitness_scores,
                key=lambda s: fitness_scores[s].confidence_adjusted_fitness,
            )

        if selected is None:
            decision.reasons.append("UCB exploration found no viable strategy")
            return decision

        best_fitness = fitness_scores[selected]
        strat_result, position = viable[selected]

        # Step 6: Check minimum fitness threshold
        min_fitness = self._config.min_selector_fitness
        if best_fitness.confidence_adjusted_fitness < min_fitness and not is_exploration:
            decision.reasons = [
                f"Best fitness {best_fitness.confidence_adjusted_fitness:.1f} "
                f"below minimum {min_fitness:.0f}",
                f"Best candidate: {selected.value} "
                f"(raw={best_fitness.fitness_score:.1f}, "
                f"adjusted={best_fitness.confidence_adjusted_fitness:.1f})",
            ]
            return decision

        # Step 7: Build decision
        decision.selected_strategy = selected
        decision.strategy_score = best_fitness.fitness_score
        decision.confidence = best_fitness.confidence_adjusted_fitness
        decision.position_size_sol = position.position_size_sol

        # Build reasons
        reasons = [
            f"Strategy {selected.value} selected "
            f"({'EXPLORATION' if is_exploration else 'EXPLOITATION'})",
            f"Fitness: {best_fitness.fitness_score:.1f} "
            f"(confidence-adjusted: {best_fitness.confidence_adjusted_fitness:.1f})",
            f"Status: {best_fitness.status.value} ({best_fitness.trades} trades)",
        ]

        # Add regime context
        regime_trades = self._perf_db.get_regime_stats(
            selected, regime
        ).total_trades
        if regime_trades > 0:
            reasons.append(
                f"{regime_trades} comparable trades in {regime.value} regime"
            )

        # Add expectancy and profit factor
        reasons.append(
            f"Expectancy: {best_fitness.expectancy:+.1f}%, "
            f"PF: {best_fitness.profit_factor:.2f}"
        )

        # Add capital context
        reasons.append(
            f"Position: {position.position_size_sol:.4f} SOL "
            f"(est. cost: {position.estimated_total_cost_bps:.0f}bps)"
        )

        # Add strategy-specific reasons
        reasons.extend(strat_result.reasons)

        decision.reasons = reasons

        # Update preferred strategy if warranted
        self._maybe_update_preferred(selected, best_fitness)

        return decision

    def _ucb_select(
        self,
        fitness_scores: dict[StrategyName, FitnessResult],
        viable: dict[StrategyName, tuple],
    ) -> StrategyName | None:
        """
        Upper Confidence Bound selection for exploration.

        UCB1 = fitness + C * sqrt(ln(N) / n_i)

        Where:
        - fitness = confidence-adjusted fitness (normalized 0-1)
        - N = total selections across all strategies
        - n_i = selections for strategy i
        - C = exploration coefficient
        """
        C = 1.5  # Exploration coefficient
        total_n = max(1, self._total_selections)

        best_ucb = -1.0
        best_strategy: StrategyName | None = None

        for name in viable:
            fitness = fitness_scores.get(name)
            if not fitness:
                continue

            n_i = max(1, self._perf_db.get_trade_count(name))
            normalized_fitness = fitness.confidence_adjusted_fitness / 100

            ucb = normalized_fitness + C * math.sqrt(math.log(total_n) / n_i)

            if ucb > best_ucb:
                best_ucb = ucb
                best_strategy = name

        return best_strategy

    def _maybe_update_preferred(
        self,
        selected: StrategyName,
        fitness: FitnessResult,
    ) -> None:
        """
        Update the preferred strategy only if the new one is
        materially better (MIN_STRATEGY_SWITCH_IMPROVEMENT).
        """
        if self._preferred_strategy is None:
            self._preferred_strategy = selected
            self._preferred_since = datetime.now(timezone.utc)
            return

        if selected == self._preferred_strategy:
            return

        # Check if the new strategy is materially better
        current_stats = self._perf_db.get_overall_stats(self._preferred_strategy)
        current_fitness = self._fitness_engine.calculate(
            self._preferred_strategy, current_stats
        )

        improvement = (
            fitness.confidence_adjusted_fitness
            - current_fitness.confidence_adjusted_fitness
        )
        improvement_pct = (
            (improvement / current_fitness.confidence_adjusted_fitness * 100)
            if current_fitness.confidence_adjusted_fitness > 0
            else 100
        )

        if improvement_pct >= self._config.min_strategy_switch_improvement:
            logger.info(
                f"🔄 Strategy switch: {self._preferred_strategy.value} → "
                f"{selected.value} (+{improvement_pct:.0f}% fitness)"
            )
            self._preferred_strategy = selected
            self._preferred_since = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "preferred_strategy": (
                self._preferred_strategy.value
                if self._preferred_strategy
                else "none"
            ),
            "total_selections": self._total_selections,
            "exploration_count": self._exploration_count,
            "exploration_rate": f"{self.exploration_rate:.1%}",
            "strategies": list(self._strategies.keys()),
        }

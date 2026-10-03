"""
Adaptive strategy manager — orchestrates the complete adaptive engine.
Integrates with the existing StrategyManager to provide strategy selection,
counterfactual testing, performance tracking, and loss protection.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from backend.adaptive.engines.bonding_curve import BondingCurveEngine
from backend.adaptive.engines.capital import CapitalEngine
from backend.adaptive.engines.market_regime import MarketRegimeEngine
from backend.adaptive.engines.opportunity_builder import OpportunityBuilder
from backend.adaptive.engines.smart_wallet_db import SmartWalletDB
from backend.adaptive.intelligence.counterfactual import CounterfactualEngine
from backend.adaptive.intelligence.fitness import StrategyFitnessEngine
from backend.adaptive.intelligence.loss_protection import LossProtection
from backend.adaptive.intelligence.selector import AdaptiveStrategySelector
from backend.adaptive.performance.database import StrategyPerformanceDB
from backend.models.adaptive import (
    AdaptiveConfig,
    CapitalStage,
    ExitType,
    MarketRegime,
    SelectionDecision,
    StrategyName,
    StrategyStatus,
    StrategyTradeRecord,
)
from backend.models.opportunity import TokenOpportunity
from backend.models.security import (
    DevReport,
    HolderReport,
    LiquidityReport,
    MarketBehaviorReport,
    SecurityReport,
    SocialReport,
)
from backend.models.token import DiscoveredToken

logger = logging.getLogger(__name__)


class AdaptiveManager:
    """
    Central orchestrator for the adaptive multi-strategy engine.

    Lifecycle:
    1. Token discovered → build TokenOpportunity
    2. Feed opportunity to AdaptiveStrategySelector
    3. Selector returns SelectionDecision (strategy or NO_TRADE)
    4. If trade, execute via paper/live engine
    5. Record result in PerformanceDB
    6. Run counterfactual analysis
    7. Update regime, capital, and loss protection
    """

    def __init__(self, config: AdaptiveConfig | None = None) -> None:
        self._config = config or AdaptiveConfig()

        # Core engines
        self.bonding_curve = BondingCurveEngine()
        self.regime = MarketRegimeEngine()
        self.capital = CapitalEngine(self._config)
        self.smart_wallets = SmartWalletDB(self._config)

        # Performance tracking
        self.performance_db = StrategyPerformanceDB(
            windows=self._config.rolling_windows
        )

        # Intelligence
        self.fitness = StrategyFitnessEngine(self._config)
        self.selector = AdaptiveStrategySelector(
            config=self._config,
            performance_db=self.performance_db,
            capital_engine=self.capital,
            regime_engine=self.regime,
        )
        self.counterfactual = CounterfactualEngine(
            config=self._config,
            performance_db=self.performance_db,
        )
        self.loss_protection = LossProtection(
            config=self._config,
            performance_db=self.performance_db,
        )

        # State
        self._started = False

    async def start(self) -> None:
        """Initialize all adaptive engine components."""
        await self.bonding_curve.start()
        await self.smart_wallets.start()
        self._started = True
        logger.info("🧠 Adaptive strategy engine started")

    async def stop(self) -> None:
        """Shut down all adaptive engine components."""
        await self.bonding_curve.stop()
        await self.smart_wallets.stop()
        self._started = False
        logger.info("🧠 Adaptive strategy engine stopped")

    async def build_opportunity(
        self,
        token: DiscoveredToken,
        security: SecurityReport,
        liquidity: LiquidityReport,
        holders: HolderReport,
        dev: DevReport,
        social: SocialReport,
        market: MarketBehaviorReport,
    ) -> TokenOpportunity:
        """
        Build a TokenOpportunity from all analysis outputs.
        Enhances the base opportunity with bonding curve and smart wallet data.
        """
        # Bonding curve analysis
        bc_metrics = await self.bonding_curve.analyze(
            pool_address=token.pool_address,
            dex=token.dex.value,
            liquidity_usd=liquidity.liquidity_usd,
            token_age_seconds=0,
        )

        # Build opportunity
        opp = OpportunityBuilder.build(
            token=token,
            security=security,
            liquidity=liquidity,
            holders=holders,
            dev=dev,
            social=social,
            market=market,
            bonding_curve=bc_metrics,
            market_regime=self.regime.current_regime,
        )

        # Feed to market regime classifier
        self.regime.record_observation(
            passed_filters=opp.security_passed,
            volume_5m_usd=opp.momentum.volume_5m_usd,
            buy_sell_ratio=opp.momentum.buy_sell_ratio,
            price_change_5m=opp.momentum.price_change_5m_percent,
            liquidity_usd=opp.liquidity_usd,
            buyer_acceleration=opp.momentum.buyer_acceleration,
        )

        # Check smart wallet participation
        wallets_to_check: list[str] = []
        if hasattr(holders, "largest_non_lp_holder") and holders.largest_non_lp_holder:
            wallets_to_check.append(holders.largest_non_lp_holder)
        if hasattr(dev, "related_wallets") and dev.related_wallets:
            wallets_to_check.extend(dev.related_wallets)
        
        if wallets_to_check:
            opp.smart_wallet_signal = self.smart_wallets.check_token_participation(
                mint_address=token.mint_address,
                participating_wallets=wallets_to_check,
            )

        return opp

    def select_strategy(
        self,
        opportunity: TokenOpportunity,
        sol_price_usd: float = 150.0,
    ) -> SelectionDecision:
        """
        Select the best strategy for a token opportunity.

        Checks loss protection first, then delegates to the selector.
        """
        # Check global loss protection
        can_trade, reason = self.loss_protection.can_trade()
        if not can_trade:
            return SelectionDecision(
                selected_strategy=StrategyName.NO_TRADE,
                market_regime=self.regime.current_regime,
                capital_stage=self.capital.current_stage,
                reasons=[f"Loss protection: {reason}"],
            )

        # Auto-recovery check
        self.loss_protection.check_auto_recovery()

        # Select strategy
        decision = self.selector.select(opportunity, sol_price_usd)

        # Check per-strategy loss protection
        if decision.selected_strategy != StrategyName.NO_TRADE:
            can_trade, reason = self.loss_protection.can_trade(
                decision.selected_strategy
            )
            if not can_trade:
                decision.selected_strategy = StrategyName.NO_TRADE
                decision.reasons.insert(0, f"Strategy disabled: {reason}")

        return decision

    def record_trade_complete(
        self,
        strategy: StrategyName,
        opportunity: TokenOpportunity,
        net_pnl_percent: float,
        net_pnl_sol: float = 0.0,
        position_size_sol: float = 0.0,
        entry_price_sol: float = 0.0,
        exit_price_sol: float = 0.0,
        fees_sol: float = 0.0,
        slippage_sol: float = 0.0,
        time_in_trade: float = 0.0,
        exit_type: ExitType = ExitType.TARGET_HIT,
        exit_reason: str = "",
        execution_success: bool = True,
        max_favorable: float = 0.0,
        max_adverse: float = 0.0,
        selector_fitness: float = 0.0,
        selector_confidence: float = 0.0,
    ) -> None:
        """
        Record a completed trade and update all tracking systems.
        """
        trade = StrategyTradeRecord(
            id=f"trade-{uuid.uuid4().hex[:12]}",
            strategy=strategy,
            mint_address=opportunity.mint_address,
            symbol=opportunity.symbol,
            pool_address=opportunity.pool_address,
            timestamp=datetime.now(timezone.utc),
            market_regime=self.regime.current_regime,
            capital_stage=self.capital.current_stage,
            bonding_curve_state=opportunity.bonding_curve.state,
            entry_price_sol=entry_price_sol,
            exit_price_sol=exit_price_sol,
            position_size_sol=position_size_sol,
            gross_pnl_sol=net_pnl_sol + fees_sol + slippage_sol,
            fees_sol=fees_sol,
            slippage_sol=slippage_sol,
            net_pnl_sol=net_pnl_sol,
            net_pnl_percent=net_pnl_percent,
            max_favorable_excursion_percent=max_favorable,
            max_adverse_excursion_percent=max_adverse,
            time_in_trade_seconds=time_in_trade,
            exit_reason=exit_reason,
            exit_type=exit_type,
            security_score=opportunity.security_score,
            liquidity_usd=opportunity.liquidity_usd,
            organic_demand_score=opportunity.momentum.organic_demand_score,
            smart_wallet_score=(
                opportunity.smart_wallet_signal.signal_strength
                if opportunity.smart_wallet_signal else 0
            ),
            momentum_score=opportunity.momentum.momentum_score,
            execution_success=execution_success,
            was_selected=True,
            selector_fitness=selector_fitness,
            selector_confidence=selector_confidence,
        )

        # Update performance DB
        self.performance_db.record_trade(trade)

        # Update loss protection
        self.loss_protection.record_trade_result(strategy, net_pnl_percent)

        # Run counterfactual analysis
        self.counterfactual.evaluate_all(
            opportunity=opportunity,
            selected_strategy=strategy,
            actual_pnl_percent=net_pnl_percent,
            market_regime=self.regime.current_regime,
            capital_stage=self.capital.current_stage,
        )

        logger.info(
            f"📝 Trade recorded: {strategy.value} on {opportunity.symbol} "
            f"→ {net_pnl_percent:+.1f}% "
            f"({self.regime.current_regime.value}/{self.capital.current_stage.value})"
        )

    def update_balance(self, balance_sol: float) -> None:
        """Update account balance for capital-aware sizing."""
        self.capital.update_balance(balance_sol)

    def get_startup_banner(self, balance_sol: float) -> str:
        """Generate the startup banner."""
        self.update_balance(balance_sol)
        stage = self.capital.current_stage
        preferred = self.selector.preferred_strategy

        lines = [
            "=========================================",
            " SOLANA ADAPTIVE MEMECOIN BOT",
            " MODE: PAPER TRADING",
            "",
            f" Capital: {balance_sol:.2f} SOL",
            f" Current Strategy: {preferred.value.upper() if preferred else 'AUTO'}",
            f" Market Regime: {self.regime.current_regime.value.upper()}",
            f" Exploration: {self.selector.exploration_rate:.0%}",
            "",
            " Strategies:",
            "  1. FAST SCALPER",
            "  2. MOMENTUM RUNNER",
            "  3. SMART-WALLET FOLLOWER",
            "",
            " Selector: ACTIVE",
            " Live Trading: DISABLED",
            "=========================================",
        ]
        return "\n".join(lines)

    def get_strategy_intelligence(self) -> dict:
        """Get comprehensive strategy intelligence for dashboard."""
        return {
            "capital": self.capital.to_dict(),
            "regime": self.regime.to_dict(),
            "selector": self.selector.to_dict(),
            "performance": self.performance_db.to_dict(),
            "counterfactual": self.counterfactual.to_dict(),
            "loss_protection": self.loss_protection.to_dict(),
            "smart_wallets": self.smart_wallets.to_dict(),
        }

    def get_strategy_fitness_table(self) -> list[dict]:
        """Get fitness scores for all strategies — for dashboard table."""
        table = []
        for strategy_name in [
            StrategyName.FAST_SCALPER,
            StrategyName.MOMENTUM_RUNNER,
            StrategyName.SMART_WALLET_FOLLOWER,
        ]:
            stats = self.performance_db.get_overall_stats(strategy_name)
            fitness = self.fitness.calculate(strategy_name, stats)
            status = self.loss_protection.get_strategy_status(strategy_name)

            table.append({
                "strategy": strategy_name.value,
                "status": status.value,
                "trades": fitness.trades,
                "win_rate": f"{fitness.win_rate:.0%}",
                "expectancy": f"{fitness.expectancy:+.1f}%",
                "profit_factor": f"{fitness.profit_factor:.2f}",
                "max_drawdown": f"{fitness.max_drawdown:.1f}%",
                "fitness": f"{fitness.fitness_score:.1f}",
                "confidence_adjusted": f"{fitness.confidence_adjusted_fitness:.1f}",
                "confidence_penalty": f"{fitness.confidence_penalty:.0%}",
            })

        return table

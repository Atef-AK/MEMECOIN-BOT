"""
Enums, constants, and Pydantic models for the adaptive multi-strategy engine.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════


class MarketRegime(str, Enum):
    QUIET = "quiet"
    NORMAL = "normal"
    HIGH_MOMENTUM = "high_momentum"
    HIGH_VOLATILITY = "high_volatility"
    RISK_OFF = "risk_off"
    EXTREME_SPECULATION = "extreme_speculation"
    UNKNOWN = "unknown"


class CapitalStage(str, Enum):
    STAGE_1 = "stage_1"  # 0.05–0.20 SOL
    STAGE_2 = "stage_2"  # 0.20–0.50 SOL
    STAGE_3 = "stage_3"  # 0.50–1.00 SOL
    STAGE_4 = "stage_4"  # 1.00–5.00 SOL
    STAGE_5 = "stage_5"  # 5.00+ SOL


class BondingCurveState(str, Enum):
    EARLY = "early"
    BUILDING = "building"
    ACCELERATING = "accelerating"
    PARABOLIC = "parabolic"
    STALLING = "stalling"
    REVERSING = "reversing"
    GRADUATING = "graduating"
    POST_GRADUATION = "post_graduation"
    NOT_APPLICABLE = "not_applicable"


class StrategyName(str, Enum):
    FAST_SCALPER = "fast_scalper"
    MOMENTUM_RUNNER = "momentum_runner"
    SMART_WALLET_FOLLOWER = "smart_wallet_follower"
    NO_TRADE = "no_trade"


class StrategyStatus(str, Enum):
    EXPERIMENTAL = "experimental"  # < 50 trades
    DEVELOPING = "developing"     # 50-199 trades
    ESTABLISHED = "established"   # 200+ trades
    DEGRADED = "degraded"
    DISABLED = "disabled"


class ExitType(str, Enum):
    TARGET_HIT = "target_hit"
    PARTIAL_PROFIT = "partial_profit"
    TRAILING_STOP = "trailing_stop"
    EMERGENCY = "emergency"
    TIME_LIMIT = "time_limit"
    MANUAL = "manual"


# ═══════════════════════════════════════════════════════════════
# CAPITAL STAGE BOUNDARIES
# ═══════════════════════════════════════════════════════════════

CAPITAL_STAGE_BOUNDARIES = {
    CapitalStage.STAGE_1: (0.05, 0.20),
    CapitalStage.STAGE_2: (0.20, 0.50),
    CapitalStage.STAGE_3: (0.50, 1.00),
    CapitalStage.STAGE_4: (1.00, 5.00),
    CapitalStage.STAGE_5: (5.00, float("inf")),
}


def get_capital_stage(balance_sol: float) -> CapitalStage:
    for stage, (low, high) in CAPITAL_STAGE_BOUNDARIES.items():
        if low <= balance_sol < high:
            return stage
    if balance_sol < 0.05:
        return CapitalStage.STAGE_1
    return CapitalStage.STAGE_5


# ═══════════════════════════════════════════════════════════════
# STRATEGY CONFIGURATION
# ═══════════════════════════════════════════════════════════════


class ExitStep(BaseModel):
    """A single profit-taking step for multi-exit strategies."""
    target_percent: float
    sell_percent: float  # percentage of remaining position to sell


class FastScalperConfig(BaseModel):
    """Configuration for Strategy A — Fast Scalper."""
    target_percent: float = 10.0
    exit_percent: float = 100.0  # sell 100% at target
    max_hold_seconds: float = 300.0  # 5 min max hold
    min_observation_seconds: float = 10.0


class MomentumRunnerConfig(BaseModel):
    """Configuration for Strategy B — Momentum Runner."""
    exit_steps: list[ExitStep] = Field(default_factory=lambda: [
        ExitStep(target_percent=10.0, sell_percent=30.0),
        ExitStep(target_percent=25.0, sell_percent=30.0),
        ExitStep(target_percent=50.0, sell_percent=20.0),
    ])
    trailing_stop_percent: float = 15.0  # trailing stop from peak
    remaining_position_percent: float = 20.0
    max_hold_seconds: float = 900.0  # 15 min max hold
    min_buyer_acceleration: float = 1.5  # 1.5x buyers vs previous period
    min_buy_sell_ratio: float = 0.55
    min_volume_acceleration: float = 1.3


class SmartWalletConfig(BaseModel):
    """Configuration for Strategy C — Smart Wallet Follower."""
    min_smart_wallets: int = 1
    min_wallet_score: float = 60.0
    min_wallet_trades: int = 30
    min_wallet_expectancy: float = 0.0
    min_wallet_profit_factor: float = 1.0
    max_wallet_avg_loss_percent: float = 20.0
    target_percent: float = 15.0
    trailing_stop_percent: float = 10.0
    max_hold_seconds: float = 600.0


# ═══════════════════════════════════════════════════════════════
# ADAPTIVE ENGINE CONFIGURATION
# ═══════════════════════════════════════════════════════════════


class AdaptiveConfig(BaseModel):
    """Master configuration for the adaptive engine."""
    # Strategy samples
    strategy_min_trades: int = 50
    strategy_confident_trades: int = 200

    # Exploration
    exploration_rate: float = 0.10
    min_exploration_rate: float = 0.02
    exploration_decay_trades: int = 500

    # Strategy switching
    min_strategy_switch_improvement: float = 10.0  # percent
    min_selector_fitness: float = 75.0  # min fitness to trade

    # Rolling windows
    rolling_windows: list[int] = Field(default_factory=lambda: [20, 50, 100, 200])

    # Capital
    capital_reserve_percent: float = 50.0

    # Degradation
    degradation_window: int = 20
    degradation_expectancy_threshold: float = -2.0  # percent
    max_strategy_consecutive_losses: int = 5

    # Fitness weights
    fitness_weight_expectancy: float = 0.30
    fitness_weight_profit_factor: float = 0.20
    fitness_weight_risk_adjusted: float = 0.15
    fitness_weight_drawdown: float = 0.15
    fitness_weight_win_rate: float = 0.10
    fitness_weight_execution: float = 0.05
    fitness_weight_confidence: float = 0.05

    # Strategy configs
    fast_scalper: FastScalperConfig = Field(default_factory=FastScalperConfig)
    momentum_runner: MomentumRunnerConfig = Field(default_factory=MomentumRunnerConfig)
    smart_wallet: SmartWalletConfig = Field(default_factory=SmartWalletConfig)


# ═══════════════════════════════════════════════════════════════
# STRATEGY RESULT
# ═══════════════════════════════════════════════════════════════


class StrategyResult(BaseModel):
    """Result of a strategy evaluating a token opportunity."""
    strategy: StrategyName
    should_trade: bool = False
    confidence: float = 0.0  # 0-100

    # Entry plan
    position_size_sol: float = 0.0
    estimated_entry_price_sol: float = 0.0
    estimated_slippage_bps: float = 0.0
    estimated_fees_sol: float = 0.0
    estimated_net_cost_sol: float = 0.0

    # Exit plan
    exit_steps: list[ExitStep] = Field(default_factory=list)
    trailing_stop_percent: float = 0.0
    max_hold_seconds: float = 0.0

    # Reasoning
    reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)

    # Token fit metrics
    momentum_score: float = 0.0
    organic_demand_score: float = 0.0
    smart_wallet_score: float = 0.0
    bonding_curve_suitability: float = 0.0


# ═══════════════════════════════════════════════════════════════
# SELECTION DECISION
# ═══════════════════════════════════════════════════════════════


class SelectionDecision(BaseModel):
    """Explainable decision from the adaptive selector."""
    selected_strategy: StrategyName
    strategy_score: float = 0.0
    confidence: float = 0.0
    fitness_scores: dict[str, float] = Field(default_factory=dict)
    confidence_adjusted_scores: dict[str, float] = Field(default_factory=dict)

    # Context
    capital_stage: CapitalStage = CapitalStage.STAGE_1
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    position_size_sol: float = 0.0

    # Explanation
    reasons: list[str] = Field(default_factory=list)
    is_exploration: bool = False
    exploration_method: str = ""

    # All strategy results
    strategy_results: dict[str, StrategyResult] = Field(default_factory=dict)

    timestamp: datetime = Field(default_factory=datetime.utcnow)

    def summary(self) -> str:
        lines = [
            f"Selected: {self.selected_strategy.value.upper()}",
            f"Fitness: {self.strategy_score:.1f}",
            f"Confidence: {self.confidence:.0f}%",
            f"Capital: {self.capital_stage.value} ({self.position_size_sol:.4f} SOL)",
            f"Regime: {self.market_regime.value}",
        ]
        if self.is_exploration:
            lines.append(f"Mode: EXPLORATION ({self.exploration_method})")
        lines.append("Reasons:")
        for r in self.reasons:
            lines.append(f"  • {r}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# STRATEGY TRADE RECORD
# ═══════════════════════════════════════════════════════════════


class StrategyTradeRecord(BaseModel):
    """Complete record of a strategy trade for performance tracking."""
    id: str = ""
    strategy: StrategyName = StrategyName.FAST_SCALPER
    mint_address: str = ""
    pool_address: str = ""
    symbol: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Context at entry
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    capital_stage: CapitalStage = CapitalStage.STAGE_1
    bonding_curve_state: BondingCurveState = BondingCurveState.NOT_APPLICABLE

    # Trade data
    entry_price_sol: float = 0.0
    exit_price_sol: float = 0.0
    position_size_sol: float = 0.0
    gross_pnl_sol: float = 0.0
    fees_sol: float = 0.0
    slippage_sol: float = 0.0
    net_pnl_sol: float = 0.0
    net_pnl_percent: float = 0.0

    # Excursions
    max_favorable_excursion_percent: float = 0.0  # best unrealized gain
    max_adverse_excursion_percent: float = 0.0    # worst unrealized loss

    # Timing
    time_in_trade_seconds: float = 0.0
    exit_reason: str = ""
    exit_type: ExitType = ExitType.TARGET_HIT

    # Context scores
    security_score: float = 0.0
    liquidity_usd: float = 0.0
    organic_demand_score: float = 0.0
    smart_wallet_score: float = 0.0
    momentum_score: float = 0.0

    # Execution
    execution_success: bool = True
    execution_latency_ms: float = 0.0

    # Selection
    was_selected: bool = True  # False for counterfactual
    selector_fitness: float = 0.0
    selector_confidence: float = 0.0


# ═══════════════════════════════════════════════════════════════
# SMART WALLET
# ═══════════════════════════════════════════════════════════════


class SmartWalletProfile(BaseModel):
    """Profile of a tracked wallet with performance metrics."""
    address: str
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)

    # Performance
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    average_winner_percent: float = 0.0
    average_loser_percent: float = 0.0
    median_winner_percent: float = 0.0
    median_loser_percent: float = 0.0
    expectancy_percent: float = 0.0
    profit_factor: float = 0.0
    total_pnl_sol: float = 0.0

    # Behaviour
    median_holding_seconds: float = 0.0
    avg_entry_token_age_seconds: float = 0.0
    avg_exit_token_age_seconds: float = 0.0
    max_drawdown_percent: float = 0.0
    successful_launches: int = 0

    # Risk flags
    suspicious_clusters: int = 0
    wash_trading_score: float = 0.0

    # Qualification
    is_qualified: bool = False
    smart_wallet_score: float = 0.0  # 0-100 risk-adjusted quality


class SmartWalletSignal(BaseModel):
    """Signal that qualified smart wallets are entering a token."""
    mint_address: str
    qualified_wallets: list[SmartWalletProfile] = Field(default_factory=list)
    total_entry_sol: float = 0.0
    avg_wallet_score: float = 0.0
    independent_entries: int = 0  # entries from different funding clusters
    cluster_overlap: bool = False  # same funding cluster
    signal_strength: float = 0.0  # 0-100

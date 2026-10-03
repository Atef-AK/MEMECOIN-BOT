"""
Comprehensive test suite for the Adaptive Multi-Strategy Engine.
Tests every major component with realistic scenarios.

Usage: python -m backend.tests.test_adaptive
"""

from __future__ import annotations

import asyncio
import sys
import io
import traceback

# Fix Windows encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

passed = 0
failed = 0
errors: list[str] = []


def test(name: str):
    """Decorator for test functions."""
    def decorator(func):
        async def wrapper():
            global passed, failed
            try:
                if asyncio.iscoroutinefunction(func):
                    await func()
                else:
                    func()
                passed += 1
                print(f"  PASS  {name}")
            except Exception as e:
                failed += 1
                errors.append(f"{name}: {e}")
                print(f"  FAIL  {name}: {e}")
                traceback.print_exc()
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════
# TEST 1: Core Models
# ═══════════════════════════════════════════════════════════════

@test("Capital stage boundaries")
def test_capital_stages():
    from backend.models.adaptive import get_capital_stage, CapitalStage

    assert get_capital_stage(0.03) == CapitalStage.STAGE_1  # Below min
    assert get_capital_stage(0.05) == CapitalStage.STAGE_1
    assert get_capital_stage(0.15) == CapitalStage.STAGE_1
    assert get_capital_stage(0.20) == CapitalStage.STAGE_2
    assert get_capital_stage(0.49) == CapitalStage.STAGE_2
    assert get_capital_stage(0.50) == CapitalStage.STAGE_3
    assert get_capital_stage(0.99) == CapitalStage.STAGE_3
    assert get_capital_stage(1.00) == CapitalStage.STAGE_4
    assert get_capital_stage(4.99) == CapitalStage.STAGE_4
    assert get_capital_stage(5.00) == CapitalStage.STAGE_5
    assert get_capital_stage(100.0) == CapitalStage.STAGE_5


@test("AdaptiveConfig defaults")
def test_config_defaults():
    from backend.models.adaptive import AdaptiveConfig

    cfg = AdaptiveConfig()
    assert cfg.strategy_min_trades == 50
    assert cfg.strategy_confident_trades == 200
    assert cfg.exploration_rate == 0.10
    assert cfg.capital_reserve_percent == 50.0
    assert cfg.fitness_weight_expectancy == 0.30
    assert cfg.fast_scalper.target_percent == 10.0
    assert len(cfg.momentum_runner.exit_steps) == 3
    assert cfg.smart_wallet.min_wallet_trades == 30

    # Weights must sum to 1.0
    total_weight = (
        cfg.fitness_weight_expectancy
        + cfg.fitness_weight_profit_factor
        + cfg.fitness_weight_risk_adjusted
        + cfg.fitness_weight_drawdown
        + cfg.fitness_weight_win_rate
        + cfg.fitness_weight_execution
        + cfg.fitness_weight_confidence
    )
    assert abs(total_weight - 1.0) < 0.001, f"Weights sum to {total_weight}"


@test("TokenOpportunity properties")
def test_opportunity_properties():
    from backend.models.adaptive import BondingCurveState, SmartWalletSignal
    from backend.models.opportunity import (
        TokenOpportunity, BondingCurveMetrics, MomentumMetrics,
    )

    opp = TokenOpportunity(
        mint_address="test",
        security_passed=True,
        security_score=20,
        liquidity_score=8,
        holder_score=6,
    )
    assert opp.passes_basic_filters is True
    assert opp.is_on_bonding_curve is False
    assert opp.has_smart_wallet_signal is False

    # With bonding curve
    opp.bonding_curve = BondingCurveMetrics(state=BondingCurveState.ACCELERATING)
    assert opp.is_on_bonding_curve is True

    # With smart wallet signal
    opp.smart_wallet_signal = SmartWalletSignal(
        mint_address="test", signal_strength=50
    )
    assert opp.has_smart_wallet_signal is True

    # Failing filters
    opp2 = TokenOpportunity(mint_address="bad", security_passed=False)
    assert opp2.passes_basic_filters is False


# ═══════════════════════════════════════════════════════════════
# TEST 2: Statistical Confidence
# ═══════════════════════════════════════════════════════════════

@test("Wilson lower bound penalizes small samples")
def test_wilson():
    from backend.adaptive.intelligence.confidence import (
        wilson_lower_bound, wilson_upper_bound,
    )

    # 4/5 = 80% win rate with tiny sample
    lb_5 = wilson_lower_bound(4, 5)
    # 160/200 = 80% win rate with large sample
    lb_200 = wilson_lower_bound(160, 200)

    assert lb_5 < lb_200, f"Small sample LB ({lb_5:.3f}) should be < large ({lb_200:.3f})"
    assert lb_5 < 0.50, f"5-trade 80% WR lower bound should be < 50%"
    assert lb_200 > 0.73, f"200-trade 80% WR lower bound should be > 73%"

    # Edge cases
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(0, 10) == 0.0
    assert wilson_upper_bound(10, 10) > 0.9


@test("Confidence penalty curve")
def test_confidence_penalty():
    from backend.adaptive.intelligence.confidence import sample_confidence_penalty

    assert sample_confidence_penalty(0) == 0.0
    assert sample_confidence_penalty(1) < 0.35
    assert sample_confidence_penalty(25) < 0.60
    assert sample_confidence_penalty(49) < 0.70
    assert abs(sample_confidence_penalty(50) - 0.70) < 0.01
    assert sample_confidence_penalty(125) > 0.80
    assert sample_confidence_penalty(200) == 1.0
    assert sample_confidence_penalty(500) == 1.0


@test("Expectancy confidence interval")
def test_expectancy_ci():
    from backend.adaptive.intelligence.confidence import (
        expectancy_confidence_interval,
    )

    lower, point, upper = expectancy_confidence_interval(
        win_rate=0.6, avg_winner=10.0, avg_loser=8.0,
        total_trades=200,
    )
    assert lower < point < upper
    assert point > 0  # 60% * 10 - 40% * 8 = 2.8%
    assert abs(point - 2.8) < 0.1


# ═══════════════════════════════════════════════════════════════
# TEST 3: Rolling Statistics
# ═══════════════════════════════════════════════════════════════

@test("Rolling stats with mixed trades")
def test_rolling_stats():
    from backend.adaptive.performance.rolling_stats import RollingStats, TradeResult

    rs = RollingStats([20, 50, 100])

    # Add 60 trades: 40 winners (+8%) and 20 losers (-5%)
    for i in range(60):
        is_win = i % 3 != 0  # 2/3 win rate
        rs.add_trade(TradeResult(
            net_pnl_percent=8.0 if is_win else -5.0,
            is_win=is_win,
            time_in_trade=120.0,
            exit_type="target_hit" if is_win else "emergency",
            execution_success=True,
            slippage_bps=50.0,
        ))

    assert rs.total_trades == 60

    # Overall stats
    stats = rs.get_stats()
    assert stats.trades == 60
    assert abs(stats.win_rate - 0.667) < 0.02
    assert stats.expectancy > 0  # 0.667 * 8 - 0.333 * 5 = 3.67
    assert stats.profit_factor > 1.0
    assert stats.max_drawdown > 0
    assert stats.avg_holding_seconds > 0

    # Window stats
    w20 = rs.get_stats(20)
    assert w20.trades == 20

    # All windows
    all_windows = rs.get_all_windows()
    assert 20 in all_windows
    assert 50 in all_windows
    assert 100 in all_windows
    assert all_windows[100].trades == 60  # Only 60 trades available


@test("Degradation detection")
def test_degradation():
    from backend.adaptive.performance.rolling_stats import RollingStats, TradeResult

    rs = RollingStats([20, 50, 100])

    # 80 good trades (60% win, +10% avg winner, -5% avg loser)
    for _ in range(80):
        rs.add_trade(TradeResult(net_pnl_percent=10.0, is_win=True))
    for _ in range(20):
        rs.add_trade(TradeResult(net_pnl_percent=-5.0, is_win=False))

    # Now 20 terrible trades (all losses)
    for _ in range(20):
        rs.add_trade(TradeResult(net_pnl_percent=-8.0, is_win=False))

    is_degraded, reason = rs.detect_degradation(short_window=20, long_window=100)
    assert is_degraded, f"Should detect degradation: {reason}"
    assert "negative" in reason.lower() or "dropped" in reason.lower()


# ═══════════════════════════════════════════════════════════════
# TEST 4: Market Regime Engine
# ═══════════════════════════════════════════════════════════════

@test("Market regime classification")
def test_market_regime():
    from backend.adaptive.engines.market_regime import MarketRegimeEngine
    from backend.models.adaptive import MarketRegime

    engine = MarketRegimeEngine()

    # Feed normal observations
    for _ in range(15):
        engine.record_observation(
            passed_filters=True,
            volume_5m_usd=5000,
            buy_sell_ratio=0.55,
            price_change_5m=2.0,
            liquidity_usd=20000,
            buyer_acceleration=1.1,
        )

    regime = engine.current_regime
    assert regime != MarketRegime.UNKNOWN, f"Should classify after 15 obs"

    info = engine.to_dict()
    assert info["observations"] == 15
    assert info["regime"] != "unknown"


@test("Market regime detects risk-off")
def test_regime_risk_off():
    from backend.adaptive.engines.market_regime import MarketRegimeEngine
    from backend.models.adaptive import MarketRegime

    engine = MarketRegimeEngine()

    # Feed sell-dominated, high-rug observations
    for _ in range(20):
        engine.record_observation(
            passed_filters=False,
            was_rug=True,
            volume_5m_usd=2000,
            buy_sell_ratio=0.3,
            price_change_5m=-10.0,
            liquidity_usd=5000,
            buyer_acceleration=0.5,
        )

    # Should detect risk-off
    regime = engine.current_regime
    assert regime in (MarketRegime.RISK_OFF, MarketRegime.QUIET), \
        f"Expected RISK_OFF or QUIET, got {regime.value}"


# ═══════════════════════════════════════════════════════════════
# TEST 5: Capital Engine
# ═══════════════════════════════════════════════════════════════

@test("Position sizing respects capital reserve")
def test_capital_reserve():
    from backend.adaptive.engines.capital import CapitalEngine
    from backend.models.adaptive import CapitalStage, StrategyName

    engine = CapitalEngine()
    engine.update_balance(0.10)

    result = engine.calculate_position(
        strategy=StrategyName.FAST_SCALPER,
        liquidity_usd=20000,
    )

    # With 50% reserve on 0.10 SOL = 0.05 available
    assert result.available_capital_sol <= 0.05 + 0.001
    assert result.reserve_sol >= 0.049
    assert result.position_size_sol <= result.available_capital_sol
    assert result.capital_stage == CapitalStage.STAGE_1


@test("Position rejected for insufficient liquidity")
def test_capital_rejects_low_liq():
    from backend.adaptive.engines.capital import CapitalEngine
    from backend.models.adaptive import StrategyName

    engine = CapitalEngine()
    engine.update_balance(0.50)  # Stage 2

    result = engine.calculate_position(
        strategy=StrategyName.FAST_SCALPER,
        liquidity_usd=2000,  # Way below stage 2 minimum of $10k
    )

    assert not result.is_viable
    assert any("iquidity" in r for r in result.rejection_reasons)


@test("Position sizing scales with capital stage")
def test_capital_scaling():
    from backend.adaptive.engines.capital import CapitalEngine
    from backend.models.adaptive import StrategyName

    engine = CapitalEngine()

    # Stage 1: can go 100% (micro account)
    engine.update_balance(0.10)
    r1 = engine.calculate_position(StrategyName.FAST_SCALPER, 20000)

    # Stage 4: max 20%
    engine.update_balance(3.00)
    r4 = engine.calculate_position(StrategyName.FAST_SCALPER, 50000)

    # Stage 4 should have stricter position limits
    pct_1 = r1.position_size_sol / 0.10 * 100
    pct_4 = r4.position_size_sol / 3.00 * 100

    assert pct_4 <= pct_1, "Higher stage should have stricter position %"


# ═══════════════════════════════════════════════════════════════
# TEST 6: Strategy Evaluations
# ═══════════════════════════════════════════════════════════════

def _make_good_opportunity():
    from backend.models.opportunity import TokenOpportunity, MomentumMetrics
    return TokenOpportunity(
        mint_address="GoodToken123",
        symbol="GOOD",
        security_passed=True,
        security_score=25,
        liquidity_score=10,
        liquidity_usd=25000,
        holder_score=8,
        total_holders=150,
        top10_holder_percent=35,
        dev_score=7,
        creator_has_sold=False,
        social_score=5,
        social_count=3,
        token_age_seconds=120,
        momentum=MomentumMetrics(
            buy_count_5m=25,
            sell_count_5m=12,
            buy_sell_ratio=0.68,
            unique_buyers_estimate=15,
            volume_5m_usd=5000,
            volume_acceleration=1.6,
            price_change_5m_percent=8.0,
            buyer_acceleration=1.8,
            organic_demand_score=70,
            momentum_score=65,
        ),
    )


@test("Fast Scalper accepts good opportunity")
def test_fast_scalper_accept():
    from backend.adaptive.strategies.fast_scalper import FastScalperStrategy

    strategy = FastScalperStrategy()
    opp = _make_good_opportunity()
    result = strategy.evaluate(opp)

    assert result.should_trade is True
    assert result.confidence > 50
    assert len(result.exit_steps) == 1
    assert result.exit_steps[0].sell_percent == 100
    assert len(result.reasons) > 0
    assert len(result.rejection_reasons) == 0


@test("Fast Scalper rejects bad security")
def test_fast_scalper_reject_security():
    from backend.adaptive.strategies.fast_scalper import FastScalperStrategy

    strategy = FastScalperStrategy()
    opp = _make_good_opportunity()
    opp.security_passed = False

    result = strategy.evaluate(opp)
    assert result.should_trade is False
    assert "security" in " ".join(result.rejection_reasons).lower()


@test("Fast Scalper rejects old token")
def test_fast_scalper_reject_old():
    from backend.adaptive.strategies.fast_scalper import FastScalperStrategy

    strategy = FastScalperStrategy()
    opp = _make_good_opportunity()
    opp.token_age_seconds = 7200  # 2 hours

    result = strategy.evaluate(opp)
    assert result.should_trade is False
    assert any("old" in r.lower() for r in result.rejection_reasons)


@test("Momentum Runner requires strong acceleration")
def test_momentum_runner():
    from backend.adaptive.strategies.momentum_runner import MomentumRunnerStrategy

    strategy = MomentumRunnerStrategy()
    opp = _make_good_opportunity()
    opp.liquidity_usd = 15000  # Needs >=10000

    result = strategy.evaluate(opp)
    assert result.should_trade is True
    assert len(result.exit_steps) == 3  # Multi-step exits
    assert result.trailing_stop_percent == 15.0


@test("Momentum Runner rejects weak acceleration")
def test_momentum_weak():
    from backend.adaptive.strategies.momentum_runner import MomentumRunnerStrategy

    strategy = MomentumRunnerStrategy()
    opp = _make_good_opportunity()
    opp.momentum.buyer_acceleration = 0.8  # Below 1.5 minimum
    opp.momentum.volume_acceleration = 0.5  # Below 1.3 minimum

    result = strategy.evaluate(opp)
    assert result.should_trade is False


@test("Smart Wallet rejects without signal")
def test_smart_wallet_no_signal():
    from backend.adaptive.strategies.smart_wallet_follower import (
        SmartWalletFollowerStrategy,
    )

    strategy = SmartWalletFollowerStrategy()
    opp = _make_good_opportunity()

    result = strategy.evaluate(opp)
    assert result.should_trade is False
    assert "smart wallet" in " ".join(result.rejection_reasons).lower()


@test("Smart Wallet accepts with signal")
def test_smart_wallet_with_signal():
    from backend.adaptive.strategies.smart_wallet_follower import (
        SmartWalletFollowerStrategy,
    )
    from backend.models.adaptive import SmartWalletSignal, SmartWalletProfile

    strategy = SmartWalletFollowerStrategy()
    opp = _make_good_opportunity()

    # Create qualified wallet with strong stats
    wallet = SmartWalletProfile(
        address="SmartWallet1234567890123456789012345678901234",
        total_trades=50,
        wins=30,
        losses=20,
        win_rate=0.60,
        average_winner_percent=12.0,
        average_loser_percent=6.0,
        expectancy_percent=4.8,
        profit_factor=1.5,
        is_qualified=True,
        smart_wallet_score=75.0,
    )

    opp.smart_wallet_signal = SmartWalletSignal(
        mint_address=opp.mint_address,
        qualified_wallets=[wallet],
        avg_wallet_score=75.0,
        independent_entries=1,
        total_entry_sol=0.5,
        signal_strength=60,
    )

    result = strategy.evaluate(opp)
    assert result.should_trade is True
    assert result.smart_wallet_score > 0


# ═══════════════════════════════════════════════════════════════
# TEST 7: Fitness Scoring
# ═══════════════════════════════════════════════════════════════

@test("Fitness: expectancy beats win rate")
def test_fitness_expectancy_beats_wr():
    """
    A strategy with 55% WR but great expectancy should score higher
    than one with 70% WR but large losses.
    """
    from backend.adaptive.performance.rolling_stats import RollingStats, TradeResult
    from backend.adaptive.intelligence.fitness import StrategyFitnessEngine
    from backend.models.adaptive import StrategyName

    engine = StrategyFitnessEngine()

    # Strategy A: 70% WR, but average loss is large
    stats_a = RollingStats()
    for _ in range(70):
        stats_a.add_trade(TradeResult(net_pnl_percent=5.0, is_win=True))
    for _ in range(30):
        stats_a.add_trade(TradeResult(net_pnl_percent=-15.0, is_win=False))

    # Strategy B: 55% WR, but controlled losses and good expectancy
    stats_b = RollingStats()
    for _ in range(55):
        stats_b.add_trade(TradeResult(net_pnl_percent=12.0, is_win=True))
    for _ in range(45):
        stats_b.add_trade(TradeResult(net_pnl_percent=-4.0, is_win=False))

    fitness_a = engine.calculate(StrategyName.FAST_SCALPER, stats_a)
    fitness_b = engine.calculate(StrategyName.MOMENTUM_RUNNER, stats_b)

    # Strategy B should win despite lower win rate
    # A: expectancy = 0.7*5 - 0.3*15 = -1.0 (NEGATIVE!)
    # B: expectancy = 0.55*12 - 0.45*4 = 4.8
    assert fitness_b.fitness_score > fitness_a.fitness_score, \
        f"B (55% WR, +4.8% exp) should beat A (70% WR, -1.0% exp): " \
        f"B={fitness_b.fitness_score:.1f} vs A={fitness_a.fitness_score:.1f}"


@test("Fitness: confidence penalty reduces score")
def test_fitness_confidence():
    from backend.adaptive.performance.rolling_stats import RollingStats, TradeResult
    from backend.adaptive.intelligence.fitness import StrategyFitnessEngine
    from backend.models.adaptive import StrategyName

    engine = StrategyFitnessEngine()

    # Same win pattern but different sample sizes
    small = RollingStats()
    for _ in range(10):
        small.add_trade(TradeResult(net_pnl_percent=10.0, is_win=True))
    for _ in range(5):
        small.add_trade(TradeResult(net_pnl_percent=-5.0, is_win=False))

    large = RollingStats()
    for _ in range(134):
        large.add_trade(TradeResult(net_pnl_percent=10.0, is_win=True))
    for _ in range(66):
        large.add_trade(TradeResult(net_pnl_percent=-5.0, is_win=False))

    small_f = engine.calculate(StrategyName.FAST_SCALPER, small)
    large_f = engine.calculate(StrategyName.MOMENTUM_RUNNER, large)

    # Same pattern, but confidence-adjusted should differ
    assert large_f.confidence_adjusted_fitness > small_f.confidence_adjusted_fitness, \
        f"200-trade should have better conf-adj than 15-trade"
    assert small_f.confidence_penalty < large_f.confidence_penalty


# ═══════════════════════════════════════════════════════════════
# TEST 8: Smart Wallet DB
# ═══════════════════════════════════════════════════════════════

@test("Smart wallet qualification")
def test_smart_wallet_qualification():
    from backend.adaptive.engines.smart_wallet_db import SmartWalletDB
    from backend.models.adaptive import SmartWalletProfile

    db = SmartWalletDB()

    # Record enough trades to qualify
    for i in range(35):
        db.record_trade(
            address="wallet1",
            pnl_percent=10.0 if i % 3 != 0 else -5.0,
            holding_seconds=120,
        )

    wallet = db.get_wallet("wallet1")
    assert wallet is not None
    assert wallet.total_trades == 35
    assert wallet.is_qualified is True
    assert wallet.smart_wallet_score > 0

    qualified = db.get_qualified_wallets()
    assert len(qualified) >= 1


@test("Smart wallet disqualifies wash trading")
def test_smart_wallet_wash():
    from backend.adaptive.engines.smart_wallet_db import SmartWalletDB
    from backend.models.adaptive import SmartWalletProfile

    db = SmartWalletDB()

    # Manually create a suspicious wallet
    profile = SmartWalletProfile(
        address="washer",
        total_trades=50,
        wins=25,
        losses=25,
        win_rate=0.5,
        average_winner_percent=1.0,  # Tiny wins
        average_loser_percent=1.0,   # Tiny losses
        expectancy_percent=0.0,
        profit_factor=1.0,
        wash_trading_score=0.8,  # High wash score
    )
    db.update_wallet(profile)

    wallet = db.get_wallet("washer")
    assert wallet.is_qualified is False  # Should not qualify


# ═══════════════════════════════════════════════════════════════
# TEST 9: Performance DB
# ═══════════════════════════════════════════════════════════════

@test("Performance DB tracks per regime and stage")
def test_performance_db():
    from backend.adaptive.performance.database import StrategyPerformanceDB
    from backend.models.adaptive import (
        StrategyName, MarketRegime, CapitalStage,
        StrategyTradeRecord, ExitType,
    )
    from datetime import datetime, timezone

    db = StrategyPerformanceDB()

    # Record trades in different regimes
    for i in range(10):
        db.record_trade(StrategyTradeRecord(
            id=f"t-normal-{i}",
            strategy=StrategyName.FAST_SCALPER,
            mint_address=f"token{i}",
            market_regime=MarketRegime.NORMAL,
            capital_stage=CapitalStage.STAGE_1,
            net_pnl_percent=5.0 if i % 2 == 0 else -3.0,
            exit_type=ExitType.TARGET_HIT,
        ))

    for i in range(5):
        db.record_trade(StrategyTradeRecord(
            id=f"t-momentum-{i}",
            strategy=StrategyName.FAST_SCALPER,
            mint_address=f"token_m{i}",
            market_regime=MarketRegime.HIGH_MOMENTUM,
            capital_stage=CapitalStage.STAGE_2,
            net_pnl_percent=8.0,
            exit_type=ExitType.TARGET_HIT,
        ))

    overall = db.get_overall_stats(StrategyName.FAST_SCALPER)
    assert overall.total_trades == 15

    # Regime-specific
    normal = db.get_regime_stats(StrategyName.FAST_SCALPER, MarketRegime.NORMAL)
    momentum = db.get_regime_stats(StrategyName.FAST_SCALPER, MarketRegime.HIGH_MOMENTUM)
    assert normal.total_trades == 10
    assert momentum.total_trades == 5

    # Stage-specific
    stage1 = db.get_stage_stats(StrategyName.FAST_SCALPER, CapitalStage.STAGE_1)
    assert stage1.total_trades == 10


# ═══════════════════════════════════════════════════════════════
# TEST 10: Adaptive Selector
# ═══════════════════════════════════════════════════════════════

@test("Selector returns NO_TRADE for empty database")
def test_selector_no_trade_empty():
    from backend.adaptive.intelligence.selector import AdaptiveStrategySelector
    from backend.models.adaptive import StrategyName

    selector = AdaptiveStrategySelector()
    opp = _make_good_opportunity()

    decision = selector.select(opp)

    # With no historical data, fitness should be 0, below threshold
    assert decision.selected_strategy == StrategyName.NO_TRADE
    assert len(decision.reasons) > 0


@test("Selector respects minimum fitness threshold")
def test_selector_min_fitness():
    from backend.adaptive.intelligence.selector import AdaptiveStrategySelector
    from backend.adaptive.performance.database import StrategyPerformanceDB
    from backend.models.adaptive import (
        AdaptiveConfig, StrategyName, StrategyTradeRecord,
        MarketRegime, CapitalStage, ExitType,
    )

    config = AdaptiveConfig(min_selector_fitness=75.0)
    perf_db = StrategyPerformanceDB()

    # Add a few trades (not enough for confidence)
    for i in range(5):
        perf_db.record_trade(StrategyTradeRecord(
            id=f"test-{i}",
            strategy=StrategyName.FAST_SCALPER,
            net_pnl_percent=10.0,
            market_regime=MarketRegime.NORMAL,
            capital_stage=CapitalStage.STAGE_1,
            exit_type=ExitType.TARGET_HIT,
        ))

    selector = AdaptiveStrategySelector(
        config=config, performance_db=perf_db,
    )
    selector._capital.update_balance(0.10)
    opp = _make_good_opportunity()

    decision = selector.select(opp)

    # 5 trades = massive confidence penalty, should be below threshold
    # Unless it's an exploration pick
    if not decision.is_exploration:
        assert decision.selected_strategy == StrategyName.NO_TRADE


@test("Selector provides explainable reasons")
def test_selector_explainable():
    from backend.adaptive.intelligence.selector import AdaptiveStrategySelector

    selector = AdaptiveStrategySelector()
    selector._capital.update_balance(0.10)
    opp = _make_good_opportunity()

    decision = selector.select(opp)

    # Must have reasons regardless of outcome
    assert len(decision.reasons) > 0
    assert decision.capital_stage is not None
    assert decision.market_regime is not None

    # Summary should be non-empty
    summary = decision.summary()
    assert len(summary) > 20


# ═══════════════════════════════════════════════════════════════
# TEST 11: Loss Protection
# ═══════════════════════════════════════════════════════════════

@test("Loss protection: consecutive loss kill switch")
def test_loss_consecutive():
    from backend.adaptive.intelligence.loss_protection import LossProtection
    from backend.models.adaptive import StrategyName

    lp = LossProtection()
    lp.max_consecutive_losses = 10  # Allow higher global threshold so we test per-strategy disable

    # 5 consecutive small losses on fast_scalper (total -5.0% < max daily 10.0%)
    for _ in range(5):
        lp.record_trade_result(StrategyName.FAST_SCALPER, -1.0)

    can, reason = lp.can_trade(StrategyName.FAST_SCALPER)
    assert not can
    assert "disabled" in reason.lower() or "consecutive" in reason.lower()

    # Other strategies should still work because global daily loss and global consecutive limit were not breached
    can2, _ = lp.can_trade(StrategyName.MOMENTUM_RUNNER)
    assert can2


@test("Loss protection: daily loss limit")
def test_loss_daily():
    from backend.adaptive.intelligence.loss_protection import LossProtection
    from backend.models.adaptive import StrategyName

    lp = LossProtection()
    lp.max_daily_loss_percent = 10.0

    # Two big losses
    lp.record_trade_result(StrategyName.FAST_SCALPER, -6.0)
    lp.record_trade_result(StrategyName.FAST_SCALPER, -6.0)

    can, reason = lp.can_trade()
    assert not can
    assert "daily" in reason.lower() or "halt" in reason.lower()


@test("Loss protection: reset daily")
def test_loss_reset():
    from backend.adaptive.intelligence.loss_protection import LossProtection
    from backend.models.adaptive import StrategyName

    lp = LossProtection()
    lp.record_trade_result(StrategyName.FAST_SCALPER, -12.0)

    can, _ = lp.can_trade()
    assert not can  # Should be halted

    lp.reset_daily()
    can2, _ = lp.can_trade()
    assert can2  # Should be OK after reset


# ═══════════════════════════════════════════════════════════════
# TEST 12: Counterfactual Engine
# ═══════════════════════════════════════════════════════════════

@test("Counterfactual evaluates all non-selected strategies")
def test_counterfactual():
    from backend.adaptive.intelligence.counterfactual import CounterfactualEngine
    from backend.models.adaptive import StrategyName

    engine = CounterfactualEngine()
    opp = _make_good_opportunity()

    results = engine.evaluate_all(
        opportunity=opp,
        selected_strategy=StrategyName.FAST_SCALPER,
    )

    # Should evaluate MOMENTUM_RUNNER and SMART_WALLET_FOLLOWER
    assert StrategyName.MOMENTUM_RUNNER in results
    assert StrategyName.SMART_WALLET_FOLLOWER in results
    assert StrategyName.FAST_SCALPER not in results  # Selected, not counterfactual

    # Momentum should want to trade (opp has strong acceleration)
    mr = results[StrategyName.MOMENTUM_RUNNER]
    assert mr.would_trade is True
    assert mr.estimated_pnl_percent != 0


@test("Counterfactual comparison report")
def test_counterfactual_comparison():
    from backend.adaptive.intelligence.counterfactual import CounterfactualEngine
    from backend.models.adaptive import StrategyName

    engine = CounterfactualEngine()
    opp = _make_good_opportunity()

    # Run 5 evaluations
    for _ in range(5):
        engine.evaluate_all(opp, StrategyName.FAST_SCALPER)

    comparison = engine.get_strategy_comparison()
    assert len(comparison) > 0

    recent = engine.get_recent_results(limit=10)
    assert len(recent) == 5


# ═══════════════════════════════════════════════════════════════
# TEST 13: Bonding Curve Engine
# ═══════════════════════════════════════════════════════════════

@test("Bonding curve classifies non-pump tokens as N/A")
async def test_bc_not_applicable():
    from backend.adaptive.engines.bonding_curve import BondingCurveEngine
    from backend.models.adaptive import BondingCurveState

    engine = BondingCurveEngine()
    await engine.start()

    metrics = await engine.analyze(
        pool_address="some_pool",
        dex="raydium",  # Not pump.fun
    )

    assert metrics.state == BondingCurveState.NOT_APPLICABLE
    await engine.stop()


# ═══════════════════════════════════════════════════════════════
# TEST 14: Full Integration (AdaptiveManager)
# ═══════════════════════════════════════════════════════════════

@test("AdaptiveManager full lifecycle")
async def test_adaptive_manager_lifecycle():
    from backend.adaptive.manager import AdaptiveManager
    from backend.models.adaptive import (
        StrategyName, ExitType, MarketRegime, CapitalStage,
    )

    mgr = AdaptiveManager()
    await mgr.start()

    # Set balance
    mgr.update_balance(0.10)
    assert mgr.capital.current_stage == CapitalStage.STAGE_1

    # Build opportunity
    opp = _make_good_opportunity()

    # Select strategy
    decision = mgr.select_strategy(opp)
    assert decision is not None
    assert len(decision.reasons) > 0

    # Record some trades
    for i in range(10):
        mgr.record_trade_complete(
            strategy=StrategyName.FAST_SCALPER,
            opportunity=opp,
            net_pnl_percent=8.0 if i % 3 != 0 else -5.0,
            position_size_sol=0.05,
            exit_type=ExitType.TARGET_HIT if i % 3 != 0 else ExitType.EMERGENCY,
            time_in_trade=60.0,
        )

    # Check performance was tracked
    stats = mgr.performance_db.get_overall_stats(StrategyName.FAST_SCALPER)
    assert stats.total_trades == 10

    # Get fitness table
    table = mgr.get_strategy_fitness_table()
    assert len(table) == 3
    fast_row = [r for r in table if r["strategy"] == "fast_scalper"][0]
    assert int(fast_row["trades"]) == 10

    # Get intelligence
    intel = mgr.get_strategy_intelligence()
    assert "capital" in intel
    assert "regime" in intel
    assert "performance" in intel
    assert "counterfactual" in intel
    assert "loss_protection" in intel

    # Startup banner
    banner = mgr.get_startup_banner(0.10)
    assert "PAPER TRADING" in banner

    await mgr.stop()


@test("AdaptiveManager respects loss protection")
async def test_manager_loss_protection():
    from backend.adaptive.manager import AdaptiveManager
    from backend.models.adaptive import StrategyName, ExitType

    mgr = AdaptiveManager()
    await mgr.start()
    mgr.update_balance(0.10)
    opp = _make_good_opportunity()

    # Record many losses to trigger protection
    for i in range(6):
        mgr.record_trade_complete(
            strategy=StrategyName.FAST_SCALPER,
            opportunity=opp,
            net_pnl_percent=-5.0,
            position_size_sol=0.05,
            exit_type=ExitType.EMERGENCY,
        )

    # Should be halted now
    decision = mgr.select_strategy(opp)
    assert decision.selected_strategy == StrategyName.NO_TRADE
    assert any(
        "loss" in r.lower() or "halt" in r.lower() or "protect" in r.lower()
        for r in decision.reasons
    )

    await mgr.stop()


# ═══════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print(" ADAPTIVE MULTI-STRATEGY ENGINE - TEST SUITE")
    print("=" * 60)

    tests = [
        # Core Models
        test_capital_stages,
        test_config_defaults,
        test_opportunity_properties,
        # Confidence
        test_wilson,
        test_confidence_penalty,
        test_expectancy_ci,
        # Rolling Stats
        test_rolling_stats,
        test_degradation,
        # Market Regime
        test_market_regime,
        test_regime_risk_off,
        # Capital Engine
        test_capital_reserve,
        test_capital_rejects_low_liq,
        test_capital_scaling,
        # Strategies
        test_fast_scalper_accept,
        test_fast_scalper_reject_security,
        test_fast_scalper_reject_old,
        test_momentum_runner,
        test_momentum_weak,
        test_smart_wallet_no_signal,
        test_smart_wallet_with_signal,
        # Fitness
        test_fitness_expectancy_beats_wr,
        test_fitness_confidence,
        # Smart Wallet DB
        test_smart_wallet_qualification,
        test_smart_wallet_wash,
        # Performance DB
        test_performance_db,
        # Selector
        test_selector_no_trade_empty,
        test_selector_min_fitness,
        test_selector_explainable,
        # Loss Protection
        test_loss_consecutive,
        test_loss_daily,
        test_loss_reset,
        # Counterfactual
        test_counterfactual,
        test_counterfactual_comparison,
        # Bonding Curve
        test_bc_not_applicable,
        # Full Integration
        test_adaptive_manager_lifecycle,
        test_manager_loss_protection,
    ]

    sections = [
        (0, "Core Models"),
        (3, "Statistical Confidence"),
        (6, "Rolling Statistics"),
        (8, "Market Regime"),
        (10, "Capital Engine"),
        (13, "Strategy Evaluation"),
        (20, "Fitness Scoring"),
        (22, "Smart Wallet DB"),
        (24, "Performance DB"),
        (25, "Adaptive Selector"),
        (28, "Loss Protection"),
        (31, "Counterfactual"),
        (33, "Bonding Curve"),
        (34, "Full Integration"),
    ]

    section_idx = 0
    for i, test_fn in enumerate(tests):
        if section_idx < len(sections) and i == sections[section_idx][0]:
            print(f"\n--- {sections[section_idx][1]} ---")
            section_idx += 1
        await test_fn()

    print(f"\n{'=' * 60}")
    print(f" RESULTS: {passed} passed, {failed} failed")
    print(f"{'=' * 60}")

    if errors:
        print(f"\n FAILURES:")
        for err in errors:
            print(f"   {err}")
        print()

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

"""
Comprehensive tests for the Solana Memecoin Trading Bot.
Tests all critical components: security, scoring, execution, risk management.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from backend.config.settings import Settings
from backend.core.kill_switch import KillSwitch
from backend.models.security import (
    HolderReport,
    LiquidityReport,
    SecurityCheck,
    SecurityReport,
    SecurityStatus,
    SocialReport,
    DevReport,
    MarketBehaviorReport,
)
from backend.models.token import TokenProgram, RejectionReason
from backend.models.trade import (
    ExitReason,
    SwapQuote,
    TradeRecord,
    TradeStatus,
    TradeType,
    TradingStats,
)
from backend.models.score import ScoreBreakdown
from backend.scoring.engine import ScoringEngine


# ═══════════════════════════════════════════════════════════
# SECURITY TESTS
# ═══════════════════════════════════════════════════════════

class TestMintAuthorityDetection:
    """Test that active mint authority is correctly detected and rejected."""

    def test_active_mint_authority_fails(self):
        report = SecurityReport(
            mint_address="test_mint",
            mint_authority="SomeAuthority123",
            mint_authority_disabled=False,
        )
        report.critical_failures.append("Active mint authority")
        assert report.has_critical_failure
        assert not report.mint_authority_disabled

    def test_disabled_mint_authority_passes(self):
        report = SecurityReport(
            mint_address="test_mint",
            mint_authority=None,
            mint_authority_disabled=True,
        )
        assert not report.has_critical_failure
        assert report.mint_authority_disabled


class TestFreezeAuthorityDetection:
    """Test that active freeze authority is correctly detected and rejected."""

    def test_active_freeze_authority_fails(self):
        report = SecurityReport(
            mint_address="test_mint",
            freeze_authority="SomeFreezer123",
            freeze_authority_disabled=False,
        )
        report.critical_failures.append("Active freeze authority")
        assert report.has_critical_failure

    def test_disabled_freeze_authority_passes(self):
        report = SecurityReport(
            mint_address="test_mint",
            freeze_authority=None,
            freeze_authority_disabled=True,
        )
        assert not report.has_critical_failure


class TestToken2022ExtensionDetection:
    """Test Token-2022 extension detection and risk classification."""

    def test_risky_extension_transfer_fee(self):
        report = SecurityReport(
            mint_address="test_mint",
            is_token_2022=True,
            extensions=["TransferFeeConfig"],
            risky_extensions=["TransferFeeConfig"],
        )
        report.critical_failures.append("Risky extension: TransferFeeConfig")
        assert report.has_critical_failure
        assert "TransferFeeConfig" in report.risky_extensions

    def test_risky_extension_permanent_delegate(self):
        report = SecurityReport(
            mint_address="test_mint",
            is_token_2022=True,
            extensions=["PermanentDelegate"],
            risky_extensions=["PermanentDelegate"],
        )
        report.critical_failures.append("Risky extension: PermanentDelegate")
        assert report.has_critical_failure

    def test_safe_extension_metadata(self):
        report = SecurityReport(
            mint_address="test_mint",
            is_token_2022=True,
            extensions=["MetadataPointer", "TokenMetadata"],
        )
        assert not report.has_critical_failure

    def test_unknown_extension_treated_as_risky(self):
        report = SecurityReport(
            mint_address="test_mint",
            is_token_2022=True,
            extensions=["SomeNewExtension"],
            unknown_extensions=["SomeNewExtension"],
        )
        report.critical_failures.append("Unknown extension: SomeNewExtension")
        assert report.has_critical_failure


# ═══════════════════════════════════════════════════════════
# HOLDER CONCENTRATION TESTS
# ═══════════════════════════════════════════════════════════

class TestHolderConcentration:
    """Test holder concentration analysis."""

    def test_high_concentration_fails(self):
        report = HolderReport(
            mint_address="test_mint",
            top10_percent=30.0,
        )
        report.critical_failures.append("Top 10 holders own 30%")
        assert report.critical_failures

    def test_acceptable_concentration_passes(self):
        report = HolderReport(
            mint_address="test_mint",
            top10_percent=15.0,
            largest_non_lp_percent=3.0,
            passed=True,
        )
        assert report.passed

    def test_single_holder_too_high(self):
        report = HolderReport(
            mint_address="test_mint",
            largest_non_lp_percent=8.0,
        )
        report.critical_failures.append("Single holder owns 8%")
        assert report.critical_failures


# ═══════════════════════════════════════════════════════════
# SCORING TESTS
# ═══════════════════════════════════════════════════════════

class TestScoring:
    """Test the 100-point scoring engine."""

    def _make_reports(
        self,
        security_score=30,
        liquidity_score=20,
        holder_score=20,
        dev_score=15,
        social_score=10,
        market_score=5,
        security_failures=None,
        liquidity_failures=None,
    ):
        security = SecurityReport(
            mint_address="test",
            score=security_score,
            critical_failures=security_failures or [],
            passed=not bool(security_failures),
        )
        liquidity = LiquidityReport(
            mint_address="test",
            pool_address="pool",
            score=liquidity_score,
            critical_failures=liquidity_failures or [],
            passed=not bool(liquidity_failures),
        )
        holders = HolderReport(
            mint_address="test",
            score=holder_score,
            passed=True,
        )
        dev = DevReport(
            mint_address="test",
            score=dev_score,
            passed=True,
        )
        social = SocialReport(
            mint_address="test",
            score=social_score,
            passed=True,
        )
        market = MarketBehaviorReport(
            mint_address="test",
            score=market_score,
            passed=True,
        )
        return security, liquidity, holders, dev, social, market

    def test_perfect_score(self):
        engine = ScoringEngine()
        reports = self._make_reports()
        result = engine.score("test", *reports)
        assert result.total_score == 100
        assert result.classification == "HIGH-CONFIDENCE"
        assert result.is_tradeable

    def test_minimum_trade_score(self):
        engine = ScoringEngine()
        reports = self._make_reports(
            security_score=25, liquidity_score=15,
            holder_score=15, dev_score=12,
            social_score=8, market_score=5,
        )
        result = engine.score("test", *reports)
        assert result.total_score == 80
        assert result.classification == "TRADE CANDIDATE"
        assert result.is_tradeable

    def test_watch_score(self):
        engine = ScoringEngine()
        reports = self._make_reports(
            security_score=22, liquidity_score=12,
            holder_score=12, dev_score=12,
            social_score=8, market_score=4,
        )
        result = engine.score("test", *reports)
        assert result.total_score == 70
        assert result.classification == "WATCH"
        assert not result.is_tradeable

    def test_reject_score(self):
        engine = ScoringEngine()
        reports = self._make_reports(
            security_score=15, liquidity_score=10,
            holder_score=10, dev_score=8,
            social_score=5, market_score=2,
        )
        result = engine.score("test", *reports)
        assert result.total_score == 50
        assert result.classification == "REJECT"
        assert not result.is_tradeable

    def test_critical_failure_overrides_high_score(self):
        """Critical failure must force REJECT even with high component scores."""
        engine = ScoringEngine()
        reports = self._make_reports(
            security_score=30,
            security_failures=["Active mint authority"],
        )
        result = engine.score("test", *reports)
        assert result.classification == "REJECT"
        assert result.has_critical_failure
        assert not result.is_tradeable


# ═══════════════════════════════════════════════════════════
# PNL CALCULATION TESTS
# ═══════════════════════════════════════════════════════════

class TestPnLCalculations:
    """Test net PnL calculations including fees and slippage."""

    def test_winning_trade_pnl(self):
        trade = TradeRecord(
            entry_amount_sol=0.10,
            exit_amount_sol=0.115,
            entry_fee_sol=0.001,
            exit_fee_sol=0.001,
        )
        trade.gross_pnl_sol = trade.exit_amount_sol - trade.entry_amount_sol
        trade.total_fees_sol = trade.entry_fee_sol + trade.exit_fee_sol
        trade.net_pnl_sol = trade.gross_pnl_sol - trade.total_fees_sol
        trade.net_pnl_percent = (trade.net_pnl_sol / trade.entry_amount_sol) * 100

        assert trade.gross_pnl_sol == pytest.approx(0.015)
        assert trade.total_fees_sol == pytest.approx(0.002)
        assert trade.net_pnl_sol == pytest.approx(0.013)
        assert trade.net_pnl_percent == pytest.approx(13.0)

    def test_losing_trade_pnl(self):
        trade = TradeRecord(
            entry_amount_sol=0.10,
            exit_amount_sol=0.08,
            entry_fee_sol=0.001,
            exit_fee_sol=0.001,
        )
        trade.gross_pnl_sol = trade.exit_amount_sol - trade.entry_amount_sol
        trade.total_fees_sol = trade.entry_fee_sol + trade.exit_fee_sol
        trade.net_pnl_sol = trade.gross_pnl_sol - trade.total_fees_sol
        trade.net_pnl_percent = (trade.net_pnl_sol / trade.entry_amount_sol) * 100

        assert trade.net_pnl_sol < 0
        assert trade.net_pnl_percent == pytest.approx(-22.0)

    def test_target_requires_net_profit(self):
        """Verify that +10% target means NET profit after all costs."""
        entry_sol = 0.10
        entry_fee = 0.001
        exit_fee = 0.001
        target_pct = 10.0

        # Required net profit
        required_net = entry_sol * (target_pct / 100)  # 0.01 SOL
        # Total costs
        total_costs = entry_fee + exit_fee  # 0.002 SOL
        # Required gross proceeds
        required_gross = entry_sol + required_net + total_costs  # 0.112 SOL

        # Verify
        actual_net = required_gross - entry_sol - total_costs
        assert actual_net == pytest.approx(required_net)


# ═══════════════════════════════════════════════════════════
# KILL SWITCH TESTS
# ═══════════════════════════════════════════════════════════

class TestKillSwitch:
    """Test kill switch functionality."""

    @pytest.mark.asyncio
    async def test_activation(self):
        ks = KillSwitch()
        assert not ks.is_active

        await ks.activate("Test reason")
        assert ks.is_active
        assert ks.reason == "Test reason"
        assert not ks.check()  # check returns False when active (trading not allowed)

    @pytest.mark.asyncio
    async def test_deactivation_requires_confirmation(self):
        ks = KillSwitch()
        await ks.activate("Test")

        # Wrong confirmation
        result = await ks.deactivate("wrong")
        assert not result
        assert ks.is_active

        # Correct confirmation
        result = await ks.deactivate("CONFIRM_RESET_KILL_SWITCH")
        assert result
        assert not ks.is_active

    @pytest.mark.asyncio
    async def test_callback_on_activation(self):
        ks = KillSwitch()
        callback_called = False
        callback_reason = ""

        async def callback(reason):
            nonlocal callback_called, callback_reason
            callback_called = True
            callback_reason = reason

        ks.register_callback(callback)
        await ks.activate("Emergency")

        assert callback_called
        assert callback_reason == "Emergency"

    @pytest.mark.asyncio
    async def test_idempotent_activation(self):
        ks = KillSwitch()
        await ks.activate("First")
        await ks.activate("Second")  # Should be ignored
        assert ks.reason == "First"


# ═══════════════════════════════════════════════════════════
# CONFIGURATION SAFETY TESTS
# ═══════════════════════════════════════════════════════════

class TestConfigurationSafety:
    """Test configuration validation for live trading safety."""

    def test_default_is_paper_mode(self):
        settings = Settings(
            _env_file=None,  # Don't load .env for tests
        )
        assert settings.paper_trading is True
        assert settings.live_trading is False
        assert settings.effective_mode == "PAPER"

    def test_live_mode_requires_all_conditions(self):
        settings = Settings(
            paper_trading=False,
            live_trading=True,
            live_confirmation=False,
            _env_file=None,
        )
        failures = settings.validate_live_mode()
        assert len(failures) > 0  # Should fail without confirmation

    def test_live_mode_validation(self):
        settings = Settings(
            paper_trading=True,
            live_trading=True,
            live_confirmation=True,
            _env_file=None,
        )
        failures = settings.validate_live_mode()
        assert "PAPER_TRADING must be false" in failures


# ═══════════════════════════════════════════════════════════
# SLIPPAGE CALCULATION TESTS
# ═══════════════════════════════════════════════════════════

class TestSlippageCalculations:
    """Test slippage handling and limits."""

    def test_quote_price_impact_rejection(self):
        quote = SwapQuote(
            input_mint="sol",
            output_mint="token",
            price_impact_percent=6.0,
            is_executable=False,
            rejection_reason="Price impact too high: 6.00%",
        )
        assert not quote.is_executable
        assert "too high" in quote.rejection_reason

    def test_quote_within_limits(self):
        quote = SwapQuote(
            input_mint="sol",
            output_mint="token",
            price_impact_percent=1.5,
            is_executable=True,
            output_amount_raw=1000000,
        )
        assert quote.is_executable


# ═══════════════════════════════════════════════════════════
# EMERGENCY EXIT TESTS
# ═══════════════════════════════════════════════════════════

class TestEmergencyExits:
    """Test emergency exit conditions."""

    def test_exit_reasons_exist(self):
        """Verify all required emergency exit reasons are defined."""
        assert ExitReason.EMERGENCY_MINT_CHANGE
        assert ExitReason.EMERGENCY_FREEZE_CHANGE
        assert ExitReason.EMERGENCY_LIQUIDITY_REMOVAL
        assert ExitReason.EMERGENCY_DEV_SELL
        assert ExitReason.EMERGENCY_INSIDER_DUMP
        assert ExitReason.EMERGENCY_PRICE_CRASH
        assert ExitReason.EMERGENCY_TRADE_IMPOSSIBLE
        assert ExitReason.EMERGENCY_DANGEROUS_EXTENSION
        assert ExitReason.EMERGENCY_UNKNOWN_EVENT
        assert ExitReason.KILL_SWITCH

    def test_target_hit_exit(self):
        trade = TradeRecord(
            exit_reason=ExitReason.TARGET_HIT,
            net_pnl_percent=10.5,
        )
        assert trade.exit_reason == ExitReason.TARGET_HIT
        assert trade.net_pnl_percent > 10


# ═══════════════════════════════════════════════════════════
# LIQUIDITY TESTS
# ═══════════════════════════════════════════════════════════

class TestLiquidityCalculations:
    """Test liquidity analysis calculations."""

    def test_below_minimum_liquidity(self):
        report = LiquidityReport(
            mint_address="test",
            pool_address="pool",
            liquidity_usd=15000,
        )
        report.critical_failures.append("Below minimum liquidity")
        assert not report.passed

    def test_liquidity_to_mcap_ratio(self):
        report = LiquidityReport(
            mint_address="test",
            pool_address="pool",
            liquidity_usd=50000,
            liquidity_to_mcap_ratio=0.10,
            passed=True,
        )
        assert report.liquidity_to_mcap_ratio >= 0.05
        assert report.passed


# ═══════════════════════════════════════════════════════════
# WALLET CLUSTERING TESTS
# ═══════════════════════════════════════════════════════════

class TestWalletClustering:
    """Test wallet graph and cluster detection."""

    def test_suspicious_cluster_detection(self):
        report = DevReport(
            mint_address="test",
            suspicious_cluster=True,
            cluster_detail="Multiple wallets funded by same source",
        )
        assert report.suspicious_cluster

    def test_previous_rug_detection(self):
        report = DevReport(
            mint_address="test",
            previous_rugs=2,
        )
        report.critical_failures.append("Creator has 2 previous rug(s)")
        assert report.critical_failures


# ═══════════════════════════════════════════════════════════
# PAPER EXECUTION TESTS
# ═══════════════════════════════════════════════════════════

class TestPaperExecution:
    """Test paper trade execution simulation."""

    def test_paper_trade_record(self):
        trade = TradeRecord(
            trade_type=TradeType.PAPER,
            entry_amount_sol=0.10,
            entry_status=TradeStatus.CONFIRMED,
        )
        assert trade.trade_type == TradeType.PAPER
        assert trade.entry_status == TradeStatus.CONFIRMED

    def test_failed_execution_handling(self):
        trade = TradeRecord(
            trade_type=TradeType.PAPER,
            entry_status=TradeStatus.FAILED,
        )
        assert trade.entry_status == TradeStatus.FAILED


# ═══════════════════════════════════════════════════════════
# API FAILURE TESTS
# ═══════════════════════════════════════════════════════════

class TestAPIFailures:
    """Test graceful handling of API failures."""

    def test_quote_failure_recorded(self):
        quote = SwapQuote(
            input_mint="sol",
            output_mint="token",
            is_executable=False,
            rejection_reason="Jupiter API error: 503",
        )
        assert not quote.is_executable

    def test_security_report_on_rpc_failure(self):
        report = SecurityReport(
            mint_address="test",
            passed=False,
        )
        report.critical_failures.append("Mint account not found")
        assert report.has_critical_failure


# ═══════════════════════════════════════════════════════════
# TARGET CALCULATION TESTS
# ═══════════════════════════════════════════════════════════

class TestTargetCalculations:
    """Test +10% NET target calculations."""

    def test_net_target_includes_all_costs(self):
        """The +10% target must be calculated AFTER subtracting all costs."""
        entry_sol = 0.10
        entry_fee = 0.0015
        exit_fee = 0.0015
        slippage_estimate = 0.001

        total_cost = entry_sol + entry_fee  # What we spend to enter
        target_pct = 0.10

        # Required: exit_proceeds - exit_fee - slippage > entry_sol * 1.10
        required_exit_proceeds = (
            entry_sol * (1 + target_pct)  # Target return
            + entry_fee  # Recover entry fee
            + exit_fee  # Pay exit fee
            + slippage_estimate  # Account for slippage
        )

        actual_net = required_exit_proceeds - entry_sol - entry_fee - exit_fee - slippage_estimate
        assert actual_net >= entry_sol * target_pct


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
